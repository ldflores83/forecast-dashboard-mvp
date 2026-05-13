"""
shared/llm_adapter.py
Single adapter for all Claude API calls in the agentic pipeline.

Design rules:
  - All LLM calls in the pipeline go through call_llm_json() — nowhere else.
  - Always requests JSON output (enforced in both system prompt and API params).
  - Uses low temperature (0.2) for consistent, grounded outputs.
  - Returns a fallback dict on any failure — never raises to callers.
  - API key is read from environment — never hardcoded.
  - Model is set once here; change MODEL to update all agents at once.
"""

import json
import os
import re
import anthropic
from dotenv import load_dotenv

# Load .env for local development (no-op in Cloud Function where env vars are set directly)
load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
MODEL       = "claude-sonnet-4-5"
MAX_TOKENS  = 4096      # agents return multi-field JSON; reviewer returns all 3 schemas
TEMPERATURE = 0.2       # low = consistent, grounded, less creative


def _get_client() -> anthropic.Anthropic:
    """Returns an Anthropic client using the API key from the environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. "
            "Add it to .env for local dev or as a Cloud Function env var for deployment."
        )
    return anthropic.Anthropic(api_key=api_key)


def call_llm_json(
    system_prompt: str,
    user_message:  str,
    agent_name:    str = "unknown",
    temperature:   float = TEMPERATURE,
    max_tokens:    int   = MAX_TOKENS,
) -> dict:
    """
    Makes a single Claude API call and returns parsed JSON.

    The system prompt is expected to include the required JSON schema and an
    explicit instruction to return ONLY valid JSON. This function adds an
    additional enforcement layer by stripping markdown fences and retrying
    JSON parsing with a cleaned version of the response.

    Args:
        system_prompt: Full system prompt including JSON schema instructions.
        user_message:  User turn content (typically a JSON-serialized payload).
        agent_name:    Used for logging and fallback messages only.
        temperature:   Sampling temperature (default 0.2).
        max_tokens:    Max output tokens (default 1024).

    Returns:
        Parsed dict from the model's JSON response.
        On any failure: returns a fallback dict with error=True.

    Never raises an exception to the caller.
    """
    try:
        client = _get_client()

        response = client.messages.create(
            model=model if (model := os.environ.get("COPILOT_MODEL", MODEL)) else MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message},
            ],
        )

        raw_text = response.content[0].text.strip()

        # ── Parse JSON ────────────────────────────────────────────────────────
        parsed = _parse_json(raw_text, agent_name)
        return parsed

    except EnvironmentError as e:
        print(f"[llm_adapter] ERROR ({agent_name}): {e}")
        return _fallback(agent_name, str(e))

    except anthropic.APIConnectionError as e:
        print(f"[llm_adapter] CONNECTION ERROR ({agent_name}): {e}")
        return _fallback(agent_name, "API connection error")

    except anthropic.RateLimitError as e:
        print(f"[llm_adapter] RATE LIMIT ({agent_name}): {e}")
        return _fallback(agent_name, "Rate limit exceeded")

    except anthropic.APIStatusError as e:
        print(f"[llm_adapter] API STATUS ERROR ({agent_name}): {e.status_code} — {e.message}")
        return _fallback(agent_name, f"API error {e.status_code}")

    except Exception as e:
        print(f"[llm_adapter] UNEXPECTED ERROR ({agent_name}): {type(e).__name__}: {e}")
        return _fallback(agent_name, str(e))


def _parse_json(raw_text: str, agent_name: str) -> dict:
    """
    Attempts to parse JSON from the model's raw text response.

    Strategy:
      1. Direct JSON parse of the full response.
      2. Extract content from inside a markdown fence block (```json ... ```).
      3. Extract the first {...} block anywhere in the text.
      4. Return fallback if all attempts fail.
    """
    # Attempt 1: direct parse
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract content captured between fence markers
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw_text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Attempt 2b: fence opened but no closing fence (truncated response) — grab from opening onward
    fence_open = re.search(r"```(?:json)?\s*\n?(\{.*)", raw_text, re.DOTALL)
    if fence_open:
        try:
            return json.loads(fence_open.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Attempt 3: extract first {...} block from raw text
    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    print(f"[llm_adapter] JSON PARSE FAILED ({agent_name}). Raw text (first 200 chars): {raw_text[:200]}")
    return _fallback(agent_name, "JSON parse failed")


def _fallback(agent_name: str, reason: str) -> dict:
    """
    Returns a safe fallback dict when the LLM call or parse fails.
    The 'error' key signals to the caller that fallback values should be used.
    """
    return {
        "error":   True,
        "agent":   agent_name,
        "reason":  reason,
        "message": f"AI analysis unavailable for {agent_name}. Please try again.",
    }


# ── TOOL PHASE ────────────────────────────────────────────────────────────────

def run_tool_phase(tools: list, fiscal_quarter: int, state) -> None:
    """
    Runs Phase 1 (data collection) via Claude tool use.

    Claude orchestrates which tools to call. Each tool result is written
    directly to the corresponding SharedState field. Falls back to direct
    function calls if the Anthropic API is unavailable.

    Args:
        tools:          List of tool entries from tool_registry.TOOLS.
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.
        state:          SharedState instance — tool results written in-place.
    """
    tool_defs = [t["definition"] for t in tools]
    tool_map  = {t["definition"]["name"]: t for t in tools}

    try:
        client = _get_client()
    except EnvironmentError:
        print("[llm_adapter] run_tool_phase: No API key — using direct tool calls")
        _direct_tool_calls(tools, fiscal_quarter, state)
        return

    system_prompt = (
        "You are the data collection orchestrator for a weekly revenue intelligence pipeline. "
        "Your only task is to call all available tools exactly once to collect this week's data. "
        "Pass the provided fiscal_quarter value to every tool. Do not skip any tool."
    )
    messages = [{
        "role":    "user",
        "content": f"Collect all revenue data for fiscal_quarter={fiscal_quarter}. Call each tool once.",
    }]

    try:
        model = os.environ.get("COPILOT_MODEL", MODEL)
        while True:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                system=system_prompt,
                tools=tool_defs,
                messages=messages,
            )

            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_blocks:
                break

            results = []
            for block in tool_blocks:
                entry = tool_map.get(block.name)
                if entry is None:
                    print(f"[llm_adapter] run_tool_phase: Unknown tool '{block.name}' — skipping")
                    continue
                print(f"[llm_adapter] Tool call: {block.name}(fiscal_quarter={block.input.get('fiscal_quarter')})")
                data = entry["fn"](**block.input)
                setattr(state, entry["state_field"], data)
                results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     f"OK — {entry['state_field']} collected.",
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": results})

            if response.stop_reason == "end_turn":
                break

    except Exception as e:
        print(f"[llm_adapter] run_tool_phase ERROR: {type(e).__name__}: {e} — falling back")
        _direct_tool_calls(tools, fiscal_quarter, state)


def _direct_tool_calls(tools: list, fiscal_quarter: int, state) -> None:
    """Calls all tools directly and populates state. Used as fallback for run_tool_phase."""
    for t in tools:
        data = t["fn"](fiscal_quarter)
        setattr(state, t["state_field"], data)

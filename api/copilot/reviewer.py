"""
copilot/reviewer.py
Reviewer agent — validates all agent outputs against SharedState.

Design rules:
  - Reviewer is the ONLY component that sees all three agent outputs at once.
  - Reviewer receives SharedState data (grounded facts) + all agent outputs.
  - Reviewer can: remove, rewrite, simplify, flag uncertainty.
  - Reviewer CANNOT: add new facts, invent data, or change schemas.
  - Output must preserve the original schema shape for all three agents.
  - If reviewer LLM call fails, raw agent outputs are used as-is with a warning.
"""

from .state       import SharedState
from .llm_adapter import call_llm_json
from .prompts     import reviewer_prompt
from .schemas     import (
    validate_pipeline, validate_renewal, validate_winloss, validate_reviewer,
    PIPELINE_FALLBACKS, RENEWAL_FALLBACKS, WINLOSS_FALLBACKS,
)


def validate(state: SharedState, agent_outputs: dict) -> dict:
    """
    Runs the reviewer agent over all three agent outputs.

    The reviewer receives:
      - state.to_context_summary() — grounded data facts (no agent interpretations)
      - agent_outputs — dict with keys 'pipeline', 'renewal', 'winloss'

    It validates that every claim in each agent output is supported by the
    grounded data context, removes or rewrites unsupported claims, and returns
    the reviewed output.

    Args:
        state:          SharedState instance with all tool outputs populated.
        agent_outputs:  Dict with 'pipeline', 'renewal', 'winloss' keys.

    Returns:
        Dict with keys:
            status      — 'passed' or 'flagged'
            notes       — list of reviewer observations
            corrections — list of specific corrections made
            pipeline    — validated pipeline agent output
            renewal     — validated renewal agent output
            winloss     — validated winloss agent output
            review      — metadata about the review pass
    """
    print("[reviewer] Running reviewer...")

    system_prompt, user_message = reviewer_prompt(
        state_summary=state.to_context_summary(),
        agent_outputs=agent_outputs,
    )

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="reviewer",
        max_tokens=8192,   # reviewer sees more content — needs more tokens
    )

    # ── Handle reviewer LLM failure ───────────────────────────────────────────
    if raw_output.get("error"):
        print("[reviewer] WARNING: Reviewer LLM call failed — using raw agent outputs")
        return _fallback_review(agent_outputs, reason=raw_output.get("reason", "unknown"))

    # ── Validate reviewer output structure ────────────────────────────────────
    reviewed = validate_reviewer(raw_output)

    # ── Re-validate each agent's output within the reviewer result ────────────
    # The reviewer may have returned malformed sub-outputs — re-validate each.
    reviewed["pipeline"] = validate_pipeline(reviewed.get("pipeline") or {})
    reviewed["renewal"]  = validate_renewal(reviewed.get("renewal")   or {})
    reviewed["winloss"]  = validate_winloss(reviewed.get("winloss")   or {})

    # ── Add review metadata ───────────────────────────────────────────────────
    reviewed["review"] = {
        "status":      reviewed.get("status", "passed"),
        "notes":       reviewed.get("notes", []),
        "corrections": reviewed.get("corrections", []),
    }

    print(f"[reviewer] Review complete — status: {reviewed.get('status')} | "
          f"corrections: {len(reviewed.get('corrections', []))}")
    return reviewed


def _fallback_review(agent_outputs: dict, reason: str = "unknown") -> dict:
    """
    Returns a fallback review result when the reviewer LLM call fails.
    Uses the raw agent outputs as-is, with a warning status.
    """
    print(f"[reviewer] Using fallback review — reason: {reason}")
    return {
        "status":      "error",
        "notes":       [f"Reviewer unavailable: {reason}. Raw agent outputs used without review."],
        "corrections": [],
        "pipeline":    validate_pipeline(agent_outputs.get("pipeline") or {}),
        "renewal":     validate_renewal(agent_outputs.get("renewal")   or {}),
        "winloss":     validate_winloss(agent_outputs.get("winloss")   or {}),
        "review": {
            "status":      "error",
            "notes":       [f"Reviewer unavailable: {reason}"],
            "corrections": [],
        },
    }

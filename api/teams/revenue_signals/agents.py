"""
teams/revenue_signals/agents.py
Three specialist agents for the Weekly Revenue Signals pipeline.

Design rules:
  - Each agent has ONE job, ONE prompt function, ONE schema.
  - Agents receive SharedState (read-only) and return a validated dict.
  - Agents NEVER read from other agents' outputs.
  - Agents NEVER call BigQuery directly — all data comes from SharedState.
  - All LLM calls go through llm_adapter.call_llm_json().
  - Schema validation happens after every LLM call — fallbacks prevent crashes.
"""

from shared.state       import SharedState
from shared.llm_adapter import call_llm_json
from shared.schemas     import validate_pipeline, validate_renewal, validate_winloss
from .prompts           import pipeline_sentinel_prompt, renewal_pulse_prompt, winloss_intel_prompt


# ── AGENT 1: PIPELINE SENTINEL ────────────────────────────────────────────────

def pipeline_sentinel(state: SharedState) -> dict:
    """
    Agent 1: Pipeline Sentinel.

    Analyzes open Sales pipeline (Net New, Expansion, Migration) to identify
    deals at risk this week. Framed for Sales leadership — revenue impact first.

    Perceives:
        state.pipeline_data — flagged deals, pipeline by BU/stage, flag counts

    Reasons:
        Which deals need attention? What patterns exist? Is coverage healthy?

    Returns:
        Validated dict matching PIPELINE_SCHEMA.
    """
    print("[agents] Running Pipeline Sentinel...")

    system_prompt, user_message = pipeline_sentinel_prompt(
        pipeline_data=state.pipeline_data,
        context=state.context,
    )

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="pipeline_sentinel",
    )

    # If LLM adapter returned an error fallback, validate will apply schema fallbacks
    validated = validate_pipeline(raw_output)

    print(f"[agents] Pipeline Sentinel complete — headline: {validated.get('headline', '')[:80]}...")
    return validated


# ── AGENT 2: RENEWAL PULSE ────────────────────────────────────────────────────

def renewal_pulse(state: SharedState) -> dict:
    """
    Agent 2: Renewal Pulse.

    Analyzes ARR base health through renewal signals. Framed for Sales —
    if churn exceeds new sales wins, net growth targets become impossible.

    Perceives:
        state.renewal_data — BU dynamics, high-risk accounts, recent closures

    Reasons:
        Is the ARR base protected? Which BU is most exposed? Does pipeline cover churn?

    Returns:
        Validated dict matching RENEWAL_SCHEMA.
    """
    print("[agents] Running Renewal Pulse...")

    system_prompt, user_message = renewal_pulse_prompt(
        renewal_data=state.renewal_data,
        context=state.context,
    )

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="renewal_pulse",
    )

    validated = validate_renewal(raw_output)

    print(f"[agents] Renewal Pulse complete — headline: {validated.get('headline', '')[:80]}...")
    return validated


# ── AGENT 3: WIN/LOSS INTELLIGENCE ───────────────────────────────────────────

def winloss_intel(state: SharedState) -> dict:
    """
    Agent 3: Win/Loss Intelligence.

    Analyzes patterns in closed deals over the last 90 days.
    Flags if a single loss reason is systemic (>30% of all losses).

    Perceives:
        state.winloss_data — closed deals, loss reasons, win rates, stage distribution

    Reasons:
        What are we winning? Where and why are we losing? Is there a systemic issue?

    Returns:
        Validated dict matching WINLOSS_SCHEMA.
    """
    print("[agents] Running Win/Loss Intelligence...")

    system_prompt, user_message = winloss_intel_prompt(
        winloss_data=state.winloss_data,
        context=state.context,
    )

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="winloss_intel",
    )

    validated = validate_winloss(raw_output)

    print(f"[agents] Win/Loss Intel complete — systemic_flag: {validated.get('systemic_flag')}")
    return validated

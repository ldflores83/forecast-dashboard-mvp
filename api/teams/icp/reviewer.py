"""
teams/icp/reviewer.py
Reviewer agent for the ICP Analysis pipeline.

Design rules:
  - Reviewer is the ONLY component that sees both agent outputs at once.
  - Receives ground-truth aggregated data + both agent outputs.
  - Can: remove, flag, rewrite. Cannot: invent new data.
  - If reviewer LLM call fails, raw agent outputs are used as-is with a warning.
  - MAX_TOKENS = 8192 (reviewer needs to see more content).
"""

from shared.llm_adapter import call_llm_json
from .prompts import reviewer_prompt


MAX_TOKENS = 8192


def validate(
    won_lost_data: dict,
    pipeline_data: dict,
    icp_profile: dict,
    validation: dict,
) -> dict:
    """
    Runs the reviewer over both agent outputs.

    Verifies win rates, deal counts, and ACV figures against the aggregated
    source data. Flags unsupported claims; corrects where possible.

    Args:
        won_lost_data:  Output of tools.get_won_lost_by_bu() (ground truth).
        pipeline_data:  Output of tools.get_pipeline_by_bu() (ground truth).
        icp_profile:    Output of agents.icp_discovery() (to review).
        validation:     Output of agents.icp_validator() (to review).

    Returns:
        Dict with keys:
            status          — 'passed' or 'flagged'
            notes           — list of reviewer observations
            corrections     — list of specific corrections made
            icp_profile     — reviewed (possibly corrected) discovery output
            validation      — reviewed (possibly corrected) validator output
    """
    print("[reviewer] Running ICP reviewer...")

    system_prompt, user_message = reviewer_prompt(
        won_lost_data=won_lost_data,
        pipeline_data=pipeline_data,
        icp_profile=icp_profile,
        validation=validation,
    )

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="icp_reviewer",
        max_tokens=MAX_TOKENS,
    )

    if raw_output.get("error"):
        print("[reviewer] WARNING: Reviewer LLM call failed — using raw agent outputs")
        return _fallback_review(icp_profile, validation, reason=raw_output.get("reason", "unknown"))

    # Validate reviewer output structure
    status      = str(raw_output.get("status", "passed"))
    notes       = list(raw_output.get("notes")       or [])
    corrections = list(raw_output.get("corrections") or [])

    # Prefer reviewer-corrected outputs; fall back to agent outputs if reviewer omitted them
    reviewed_profile    = raw_output.get("icp_profile")  or icp_profile
    reviewed_validation = raw_output.get("validation")   or validation

    if not isinstance(reviewed_profile, dict):
        reviewed_profile = icp_profile
    if not isinstance(reviewed_validation, dict):
        reviewed_validation = validation

    print(f"[reviewer] ICP review complete — status: {status} | "
          f"corrections: {len(corrections)}")

    return {
        "status":      status,
        "notes":       notes,
        "corrections": corrections,
        "icp_profile": reviewed_profile,
        "validation":  reviewed_validation,
    }


def _fallback_review(icp_profile: dict, validation: dict, reason: str = "unknown") -> dict:
    print(f"[reviewer] Using fallback review — reason: {reason}")
    return {
        "status":      "error",
        "notes":       [f"Reviewer unavailable: {reason}. Raw agent outputs used without review."],
        "corrections": [],
        "icp_profile": icp_profile,
        "validation":  validation,
    }

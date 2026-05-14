"""
teams/icp/agents.py
Two specialist agents for the ICP Analysis pipeline.

Design rules:
  - Each agent has ONE job — discovery OR validation.
  - Agents receive plain dicts (no SharedState) — ICP pipeline is simpler.
  - All LLM calls go through llm_adapter.call_llm_json().
  - Lightweight schema validation runs after every LLM call.
  - Fallback on any failure — never raises to orchestrator.

Agent 1: icp_discovery  — defines ICP per BU from historical win/loss
Agent 2: icp_validator  — validates open pipeline against the ICP profile
"""

from shared.llm_adapter import call_llm_json
from shared.utils import safe_float
from .prompts import icp_discovery_prompt, icp_validator_prompt

_BUS = ("ERP BU", "Supply Chain BU", "Redzone BU")

# ── FALLBACKS ─────────────────────────────────────────────────────────────────

def _bu_discovery_fallback(bu: str) -> dict:
    return {
        "icp_profile": {
            "top_verticals": [],
            "revenue_range": "Analysis unavailable",
            "bu_overall_win_rate_pct":  0.0,
            "icp_segment_win_rate_pct": 0.0,
            "avg_deal_size": 0.0,
        },
        "anti_icp": {"loss_patterns": [], "low_win_rate_segments": []},
        "sample_size": 0,
        "coverage_note": "Analysis unavailable for this cycle.",
    }


def _bu_validation_fallback(bu: str) -> dict:
    return {
        "total_pipeline_acv":   0.0,
        "total_pipeline_count": 0,
        "icp_pipeline_acv":     0.0,
        "icp_pipeline_pct":     0.0,
        "non_icp_deals":        [],
        "customer_profile_breakdown": {"ICP": 0, "ACP": 0, "UCP": 0, "Unknown": 0},
        "trend_vs_prior": "Analysis unavailable for this cycle.",
    }


def _validate_discovery(raw: dict) -> dict:
    """
    Validates discovery output — ensures each BU has required fields.
    Missing or malformed BUs get fallback values.
    """
    if not isinstance(raw, dict) or raw.get("error"):
        return {bu: _bu_discovery_fallback(bu) for bu in _BUS}

    result = {}
    for bu in _BUS:
        bu_data = raw.get(bu)
        if not isinstance(bu_data, dict):
            print(f"[agents] icp_discovery: missing BU '{bu}' in output — using fallback")
            result[bu] = _bu_discovery_fallback(bu)
            continue

        profile = bu_data.get("icp_profile")
        anti    = bu_data.get("anti_icp")

        if not isinstance(profile, dict) or not isinstance(anti, dict):
            print(f"[agents] icp_discovery: malformed BU '{bu}' — using fallback")
            result[bu] = _bu_discovery_fallback(bu)
            continue

        result[bu] = {
            "icp_profile": {
                "top_verticals":            profile.get("top_verticals", []),
                "revenue_range":            str(profile.get("revenue_range", "Unknown")),
                "bu_overall_win_rate_pct":  float(profile.get("bu_overall_win_rate_pct") or 0.0),
                "icp_segment_win_rate_pct": float(profile.get("icp_segment_win_rate_pct") or 0.0),
                "avg_deal_size":            float(profile.get("avg_deal_size") or 0.0),
            },
            "anti_icp": {
                "loss_patterns":         list(anti.get("loss_patterns") or []),
                "low_win_rate_segments": list(anti.get("low_win_rate_segments") or []),
            },
            "sample_size":   int(bu_data.get("sample_size") or 0),
            "coverage_note": str(bu_data.get("coverage_note", "")),
        }

    return result


def _validate_validation(raw: dict) -> dict:
    """
    Validates validator output — ensures each BU has required fields.
    """
    if not isinstance(raw, dict) or raw.get("error"):
        return {bu: _bu_validation_fallback(bu) for bu in _BUS}

    result = {}
    for bu in _BUS:
        bu_data = raw.get(bu)
        if not isinstance(bu_data, dict):
            print(f"[agents] icp_validator: missing BU '{bu}' in output — using fallback")
            result[bu] = _bu_validation_fallback(bu)
            continue

        raw_deals = bu_data.get("non_icp_deals") or []
        non_icp_deals = [
            {
                "deal_name":    str(d.get("deal_name") or d.get("opp_name") or ""),
                "account_name": str(d.get("account_name") or ""),
                "acv":          float(d.get("acv") or 0.0),
                "reason":       str(d.get("reason") or ""),
            }
            for d in raw_deals
            if isinstance(d, dict)
        ]

        result[bu] = {
            "total_pipeline_acv":   float(bu_data.get("total_pipeline_acv")   or 0.0),
            "total_pipeline_count": int(bu_data.get("total_pipeline_count")   or 0),
            "icp_pipeline_acv":     float(bu_data.get("icp_pipeline_acv")     or 0.0),
            "icp_pipeline_pct":     float(bu_data.get("icp_pipeline_pct")     or 0.0),
            "non_icp_deals":        non_icp_deals,
            "customer_profile_breakdown": bu_data.get("customer_profile_breakdown") or {
                "ICP": 0, "ACP": 0, "UCP": 0, "Unknown": 0
            },
            "trend_vs_prior": str(bu_data.get("trend_vs_prior") or "No prior week data."),
        }

    return result


# ── DETERMINISTIC ICP ALIGNMENT ───────────────────────────────────────────────

def _compute_icp_alignment(discovery_output: dict, pipeline_data: dict) -> dict:
    """
    Deterministically computes ICP pipeline alignment per BU in Python.

    A deal is ICP-aligned if ALL of:
    - Primary_Vertical is non-null AND matches a BU top_vertical (case-insensitive)
    - revenue_bucket is non-Unknown AND matches the BU revenue_range exactly

    If either field is null → not ICP-aligned (no partial credit).

    Returns:
        {bu: {icp_pipeline_acv, icp_pipeline_pct, icp_deal_count, total_pipeline_acv}}
    """
    deals = pipeline_data.get("deals", []) or []

    result = {}
    for bu, bu_profile in discovery_output.items():
        ip         = bu_profile.get("icp_profile", {})
        top_verts  = {v.strip().lower() for v in (ip.get("top_verticals") or []) if v}
        rev_range  = ip.get("revenue_range", "").strip()

        total_acv = 0.0
        icp_acv   = 0.0
        icp_count = 0

        for d in deals:
            if str(d.get("BU", "") or "") != bu:
                continue

            acv = safe_float(d.get("ACV"))
            total_acv += acv

            vertical   = d.get("Primary_Vertical")
            rev_bucket = str(d.get("revenue_bucket", "") or "").strip()

            if not vertical or rev_bucket in ("Unknown", ""):
                continue  # null vertical or unknown revenue → not ICP

            if vertical.strip().lower() in top_verts and rev_bucket == rev_range:
                icp_acv   += acv
                icp_count += 1

        icp_pct = round(icp_acv / total_acv * 100, 1) if total_acv > 0 else 0.0
        result[bu] = {
            "icp_pipeline_acv":   round(icp_acv, 2),
            "icp_pipeline_pct":   icp_pct,
            "icp_deal_count":     icp_count,
            "total_pipeline_acv": round(total_acv, 2),
        }
        print(f"[agents] ICP Alignment (Python) — {bu}: "
              f"icp={icp_pct:.1f}%, icp_acv=${icp_acv/1e6:.2f}M, "
              f"total=${total_acv/1e6:.2f}M, n={icp_count}")

    return result


# ── AGENT 1: ICP DISCOVERY ────────────────────────────────────────────────────

def icp_discovery(won_lost_data: dict, prior_week_context: dict | None = None) -> dict:
    """
    Agent 1: ICP Discovery.

    Analyzes historical win/loss patterns to define the Ideal Customer Profile
    per BU. Uses only aggregated stats (not raw deals) to stay within token budget.

    Args:
        won_lost_data:      Output of tools.get_won_lost_by_bu().
        prior_week_context: Prior week ICP profiles for trend comparison (or None).

    Returns:
        Validated dict: {bu: {icp_profile, anti_icp, sample_size, coverage_note}}
    """
    print("[agents] Running ICP Discovery...")

    system_prompt, user_message = icp_discovery_prompt(won_lost_data, prior_week_context)

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="icp_discovery",
        max_tokens=4096,
    )

    validated = _validate_discovery(raw_output)

    for bu, data in validated.items():
        profile = data.get("icp_profile", {})
        print(f"[agents] ICP Discovery — {bu}: "
              f"overall={profile.get('bu_overall_win_rate_pct', 0):.1f}%, "
              f"icp_segment={profile.get('icp_segment_win_rate_pct', 0):.1f}%, "
              f"range={profile.get('revenue_range', '?')}, "
              f"n={data.get('sample_size', 0)}")

    return validated


# ── AGENT 2: ICP VALIDATOR ────────────────────────────────────────────────────

def icp_validator(
    discovery_output: dict,
    pipeline_data: dict,
    prior_week_context: dict | None = None,
) -> dict:
    """
    Agent 2: ICP Validator.

    Validates the current open pipeline against the ICP profile from discovery.
    Quantifies ICP-aligned vs non-ICP pipeline ACV per BU.

    Args:
        discovery_output:   Output of icp_discovery() (validated).
        pipeline_data:      Output of tools.get_pipeline_by_bu().
        prior_week_context: Prior week validation context for trend comparison (or None).

    Returns:
        Validated dict: {bu: {total_pipeline_acv, icp_pipeline_acv, icp_pipeline_pct,
                               non_icp_deals, customer_profile_breakdown, trend_vs_prior}}
    """
    # Step 1: Compute alignment deterministically — never trust the LLM for this
    print("[agents] Computing ICP alignment (deterministic Python)...")
    icp_alignment = _compute_icp_alignment(discovery_output, pipeline_data)

    # Step 2: LLM handles narrative + non-ICP deal identification only
    print("[agents] Running ICP Validator (LLM — narrative and non-ICP deals)...")
    system_prompt, user_message = icp_validator_prompt(
        icp_profile=discovery_output,
        pipeline_data=pipeline_data,
        prior_week_context=prior_week_context,
        icp_alignment=icp_alignment,
    )

    raw_output = call_llm_json(
        system_prompt=system_prompt,
        user_message=user_message,
        agent_name="icp_validator",
        max_tokens=4096,
    )

    validated = _validate_validation(raw_output)

    # Step 3: Override any LLM-computed alignment with the authoritative Python values
    for bu, align in icp_alignment.items():
        if bu in validated:
            validated[bu]["icp_pipeline_acv"]   = align["icp_pipeline_acv"]
            validated[bu]["icp_pipeline_pct"]   = align["icp_pipeline_pct"]
            validated[bu]["total_pipeline_acv"] = align["total_pipeline_acv"]

    for bu, data in validated.items():
        print(f"[agents] ICP Validator — {bu}: "
              f"total=${data['total_pipeline_acv']/1e6:.1f}M, "
              f"icp={data['icp_pipeline_pct']:.1f}% (Python-computed)")

    return validated

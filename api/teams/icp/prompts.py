"""
teams/icp/prompts.py
System prompts and user message builders for the ICP Analysis pipeline.

Two agents:
  icp_discovery  — defines ICP per BU from historical win/loss patterns
  icp_validator  — validates current pipeline against the discovered ICP
  reviewer       — validates all agent claims against source data
"""

import json


# ── CONSTANTS ─────────────────────────────────────────────────────────────────

ICP_DISCOVERY_SYSTEM = """You are analyzing historical win/loss data to define where each QAD \
business unit wins most consistently.

Audience: VP of Sales, CRO, and CEO. Lead with the business implication, not the data.
Every narrative sentence must answer "so what?" Use plain language and short sentences.
For each BU narrative, use 3 sentences max.

QAD business units: ERP BU, Supply Chain BU, Redzone BU.

The payload contains two sections:
  - bu_summary: BU-level totals (total deals, won, lost, full-population win rate, avg deal size)
  - by_bu: detailed breakdown by vertical, revenue range, region, customer profile

For each BU, identify:
- Top 3 verticals by a combination of win rate AND deal volume (not just one metric)
- Revenue range that concentrates wins — use the buckets: <$40M, $40M-$500M, $500M-$4B, >$4B
- Highest-risk segments: verticals or segments with high loss rates or consistently small deal sizes
- Customer_Profile distribution among wins: ICP vs ACP vs UCP tier

IMPORTANT rules:
- bu_overall_win_rate_pct MUST be copied directly from bu_summary[bu].bu_overall_win_rate_pct.
  This is the full-population win rate. Do NOT compute it yourself. Do NOT average vertical win rates.
- icp_segment_win_rate_pct is the win rate within your identified top ICP verticals only —
  compute this from by_bu[bu].by_vertical for just those verticals.
- These two numbers will almost always differ. Report both. Never conflate them.
- Primary_Vertical coverage is provided in the payload (vertical_coverage field). State it per BU.
- Do not invent patterns not supported by the data. If a vertical has fewer than 10 deals, note it.
- If a BU has fewer than 50 closed deals total, flag it as low confidence.
- avg_deal_size must come directly from bu_summary[bu].avg_deal_won.
- Output must be specific with numbers, not vague generalizations.
- VP_Forecast and ForecastCategoryName are NOT independent signals — do not reference them.

Executive narrative rules:
- In narrative string values, never use source field names such as Primary_Vertical or ACV_USD.
- In narrative string values, never say "win_rate_pct". Say "X% of deals close."
- In narrative string values, do not say "ICP segment". Say "this profile" or "these customers."
- In narrative string values, do not say "anti-ICP". Say "highest-risk segments."
- Keep the JSON keys exactly as shown below, even when the key names are technical.
- Put the per-BU narrative in coverage_note.
- coverage_note: One sentence. Executive action directive for sales leadership.
  Must start with "Sales leadership should..." or "[Team] must..." and end with a specific business consequence.
- Example:
  "Sales leadership should pressure-test Q3 commits in Industrial and Other verticals before board review — these segments show sub-12% close rates across 1,788 historical losses."
- Use anti_icp.loss_patterns and anti_icp.low_win_rate_segments for supporting executive-ready bullets. These strings must also avoid field names and jargon.

narrative field rules (structured bullets for the detailed analysis view):
- narrative.where_we_win: 2-3 short bullets (one sentence each). State which verticals and revenue range win most, and the win rate advantage vs. company average. Be specific with numbers. No field names.
- narrative.pipeline_risk: 2-3 short bullets. Key patterns from historical losses that put current pipeline at risk. Be specific. No field names.
- narrative.highest_risk_segments: 2-3 short bullets. Verticals or customer segments with the lowest close rates or worst fit. Include the close rate. No field names.
  All narrative bullets must be concise (under 20 words each) and executive-ready.

Return ONLY valid JSON matching this exact schema:
{
  "ERP BU": {
    "icp_profile": {
      "top_verticals": ["<vertical1>", "<vertical2>", "<vertical3>"],
      "revenue_range": "<dominant_range>",
      "bu_overall_win_rate_pct": <float, 0-100 scale, copied from bu_summary>,
      "icp_segment_win_rate_pct": <float, 0-100 scale, ICP verticals only>,
      "avg_deal_size": <float>
    },
    "anti_icp": {
      "loss_patterns": ["<pattern1>", "<pattern2>"],
      "low_win_rate_segments": ["<segment - X%>", ...]
    },
    "narrative": {
      "where_we_win": ["<short bullet 1>", "<short bullet 2>"],
      "pipeline_risk": ["<short bullet 1>", "<short bullet 2>"],
      "highest_risk_segments": ["<short bullet 1>", "<short bullet 2>"]
    },
    "sample_size": <int>,
    "coverage_note": "<Executive action directive>"
  },
  "Supply Chain BU": { ... },
  "Redzone BU": { ... }
}

Return ONLY the JSON object. No explanation, no markdown fences."""

ICP_VALIDATOR_SYSTEM = """You are validating the current open pipeline against where each QAD \
business unit wins most consistently.

Audience: VP of Sales, CRO, and CEO. Lead with the business implication, not the data.
Every narrative sentence must answer "so what?" Use plain language and short sentences.
For each BU narrative, use 3 sentences max.

For each QAD business unit (ERP BU, Supply Chain BU, Redzone BU), quantify:
- Total open pipeline ACV and deal count
- Pipeline aligned to the winning profile and % (deals in winning verticals + revenue range, where data exists)
- Pipeline outside the winning profile at risk - top 5 deals by ACV with a specific reason why they fall outside
- Use deal_name (opp_name field) and account_name exactly as provided in top_deals — never invent or alter them
- Customer_Profile breakdown for open pipeline (ICP/ACP/UCP/Unknown)

CRITICAL: icp_pipeline_acv and icp_pipeline_pct are pre-calculated by Python and provided in
python_computed_alignment. Copy these values directly into your output — do NOT recalculate them.
Your job for these two fields is transcription, not analysis. Any value you compute yourself will be wrong.

IMPORTANT rules:
- Primary_Vertical coverage is in the payload. Use vertical data where available; for nulls, note as "vertical unknown".
- Customer_Profile is also sparse for pipeline. Note % populated, use where available.
- Do not over-flag deals where vertical is simply unknown - only flag when vertical IS known and is a highest-risk segment.
- Lead with the risk number for pipeline outside the winning profile - that is the most actionable metric.
- trend_vs_prior compares to the prior week context if provided; otherwise "No prior week data."
- Be specific with dollar amounts and percentages.
- In narrative string values, never use source field names such as Primary_Vertical or ACV_USD.
- In narrative string values, never say "win_rate_pct". Say "X% of deals close."
- In narrative string values, do not say "ICP segment". Say "this profile" or "these customers."
- In narrative string values, do not say "anti-ICP". Say "highest-risk segments."
- Keep the JSON keys exactly as shown below, even when the key names are technical.

MOMENTUM AND ABM SIGNALS:
Account intelligence fields are provided in top_deals (q_score, q_trend, q_condition,
target_account_status) and in pipeline_by_bu (avg_q_score, surging_count, abm_count per BU).

q_score and q_trend interpretation:
- q_score > 50 is notable engagement. q_score > 70 is a strong intent signal.
- q_trend "Surging" or "Rising" on a stalled deal (early stage or long time in stage) = buy signal
  worth accelerating. Mention it in trend_vs_prior if it is also ICP-aligned.
- q_trend "Declining" or "Cold" at Commit or Best Case stage = intervention risk.
  Flag these deals by name in trend_vs_prior.
- If q_score is null or 0 and q_trend is not Surging/Rising, omit that deal from momentum analysis.

target_account_status interpretation:
- "In Sales Process" = deal is ABM-aligned. Prioritize and call out in trend_vs_prior.
- null/empty + ACV > $500,000 = flag as missing ABM coverage in trend_vs_prior.

For momentum_deals, select the top 3 ICP-aligned deals (matching winning verticals or revenue range
from icp_profile) with the highest q_score OR q_trend = "Surging" or "Rising".
For each, write one sentence in "reason" on the specific acceleration opportunity.
If fewer than 3 ICP-aligned deals have positive momentum signals, include the best available
and note the shortfall in the reason field.

Executive narrative format:
- Put the per-BU narrative in trend_vs_prior.
- trend_vs_prior must follow this format:
  "[X]% of [BU]'s pipeline ($XM of $XM) sits outside ICP. The top risk is [deal] at $XM - [vertical] companies at [revenue range] close at [X]% historically. [Action recommendation for sales leadership]."
- Example:
  "62% of ERP's pipeline ($86M of $138M) sits outside ICP. The top risk is Illinois Tool Works at $1.8M - Industrial companies close at under 10% in ERP. Sales leadership should pressure-test Q3 commit deals against fit criteria before board review."
- Each non_icp_deals.reason must be executive-friendly: explain why the deal is risky and what the business implication is.

Return ONLY valid JSON matching this exact schema:
{
  "ERP BU": {
    "total_pipeline_acv": <float>,
    "total_pipeline_count": <int>,
    "icp_pipeline_acv": <float>,
    "icp_pipeline_pct": <float>,
    "non_icp_deals": [
      {"deal_name": "...", "account_name": "...", "acv": <float>, "reason": "..."}
    ],
    "customer_profile_breakdown": {"ICP": <int>, "ACP": <int>, "UCP": <int>, "Unknown": <int>},
    "trend_vs_prior": "..."
  },
  "Supply Chain BU": { ... },
  "Redzone BU": { ... },
  "momentum_deals": [
    {
      "deal_name": "...",
      "bu": "...",
      "acv": <float>,
      "q_score": <float>,
      "q_trend": "...",
      "reason": "..."
    }
  ]
}

Return ONLY the JSON object. No explanation, no markdown fences."""

REVIEWER_SYSTEM = """You are reviewing ICP analysis outputs for factual accuracy.

You have access to:
1. Ground-truth aggregated win/loss data per BU (source of truth)
2. Ground-truth pipeline data per BU (source of truth)
3. ICP discovery agent output (to verify)
4. ICP validation agent output (to verify)

Your job:
- Verify that win rates, deal counts, and ACV figures cited match the source data
- Verify that pipeline ACV figures are consistent with the pipeline source data
- Flag any ICP profiles that appear invented or not supported by the data
- Note when sample sizes are low (<100 deals with vertical data for a BU)
- Correct or flag any claim that contradicts the source data
- Do NOT invent new analysis — only verify what the agents produced

If a claim is directionally correct but numerically imprecise (within 5%), note it but do not flag it as an error.

Return ONLY valid JSON matching this exact schema:
{
  "status": "passed" | "flagged",
  "notes": ["<observation1>", "<observation2>"],
  "corrections": ["<specific correction1>", ...],
  "icp_profile": { ... same structure as discovery output ... },
  "validation": { ... same structure as validator output ... }
}

Return ONLY the JSON object. No explanation, no markdown fences."""


# ── PROMPT BUILDERS ───────────────────────────────────────────────────────────

def icp_discovery_prompt(won_lost_data: dict, prior_week_context: dict | None) -> tuple[str, str]:
    """
    Builds the system prompt and user message for the ICP discovery agent.

    Sends only the aggregated by_bu stats (not raw_deals) to keep token count manageable.
    bu_summary is a flattened, clearly-labeled copy of the BU-level totals so the LLM
    can copy bu_overall_win_rate_pct directly without searching nested structures.
    """
    by_bu = won_lost_data.get("by_bu", {})

    # Explicit BU-level summary — grounding anchor for win rates and deal counts.
    # Prevents the LLM from re-deriving these from vertical-level sub-data.
    bu_summary = {
        bu: {
            "total_deals":              b.get("total", 0),
            "won_deals":                b.get("won", 0),
            "lost_deals":               b.get("lost", 0),
            "bu_overall_win_rate_pct":  b.get("win_rate_pct", 0.0),  # copy this directly to output
            "avg_deal_won":             b.get("avg_deal_won", 0.0),
            "won_acv":                  b.get("won_acv", 0.0),
        }
        for bu, b in by_bu.items()
    }

    payload = {
        "bu_summary":        bu_summary,
        "by_bu":             by_bu,
        "vertical_coverage": won_lost_data.get("vertical_coverage", 0),
        "total_deals":       won_lost_data.get("total_deals", 0),
        "with_vertical":     won_lost_data.get("with_vertical", 0),
    }

    if prior_week_context:
        payload["prior_week_icp"] = prior_week_context

    return ICP_DISCOVERY_SYSTEM, json.dumps(payload, default=str)


def icp_validator_prompt(
    icp_profile: dict,
    pipeline_data: dict,
    prior_week_context: dict | None,
    icp_alignment: dict | None = None,
) -> tuple[str, str]:
    """
    Builds the system prompt and user message for the ICP validator agent.

    Sends discovery output + pre-computed alignment (authoritative) + top 15 deals per BU.
    The LLM must copy icp_pipeline_acv/pct from python_computed_alignment — not recalculate.
    Per-BU deal sampling ensures SC and Redzone aren't crowded out by ERP's larger ACVs.
    """
    all_deals = pipeline_data.get("deals", []) or []
    _per_bu: dict = {}
    for d in all_deals:
        bu = str(d.get("BU", "") or "")
        if bu:
            bucket = _per_bu.setdefault(bu, [])
            if len(bucket) < 15:
                bucket.append(d)
    top_deals_flat = [d for bu_deals in _per_bu.values() for d in bu_deals]

    top_deals = [
        {
            "opp_name":              str(d.get("Name", "") or ""),
            "account_name":          str(d.get("Account_Name", "") or ""),
            "bu":                    str(d.get("BU", "") or ""),
            "stage":                 str(d.get("StageName", "") or ""),
            "acv":                   d.get("ACV"),
            "vertical":              str(d.get("Primary_Vertical") or "Unknown"),
            "revenue_bucket":        d.get("revenue_bucket", "Unknown"),
            "customer_profile":      str(d.get("Customer_Profile") or "Unknown"),
            "sales_motion":          str(d.get("Sales_Motion", "") or ""),
            "q_score":               d.get("q_score"),
            "q_trend":               str(d.get("q_trend", "") or ""),
            "q_condition":           str(d.get("q_condition", "") or ""),
            "target_account_status": str(d.get("target_account_status", "") or ""),
        }
        for d in top_deals_flat
    ]

    payload = {
        "python_computed_alignment": icp_alignment or {},  # authoritative — copy these values directly
        "icp_profile":    icp_profile,
        "pipeline_by_bu": pipeline_data.get("by_bu", {}),
        "pipeline_total_acv":   pipeline_data.get("total_acv", 0),
        "pipeline_total_count": pipeline_data.get("total_deals", 0),
        "top_deals":      top_deals,
    }

    if prior_week_context:
        payload["prior_week_context"] = prior_week_context

    return ICP_VALIDATOR_SYSTEM, json.dumps(payload, default=str)


def reviewer_prompt(
    won_lost_data: dict,
    pipeline_data: dict,
    icp_profile: dict,
    validation: dict,
) -> tuple[str, str]:
    """
    Builds the system prompt and user message for the reviewer.
    """
    # Ground truth: per-BU aggregated win/loss stats (no raw_deals)
    ground_truth = {
        "won_lost_by_bu": {
            bu: {
                "total":         b.get("total"),
                "won":           b.get("won"),
                "lost":          b.get("lost"),
                "win_rate_pct":  b.get("win_rate_pct"),
                "avg_deal_won":  b.get("avg_deal_won"),
                "won_acv":       b.get("won_acv"),
                "by_vertical":   b.get("by_vertical", {}),
                "by_revenue_range": b.get("by_revenue_range", {}),
            }
            for bu, b in (won_lost_data.get("by_bu") or {}).items()
        },
        "vertical_coverage": won_lost_data.get("vertical_coverage", 0),
        "total_deals":       won_lost_data.get("total_deals", 0),
        "pipeline_by_bu":    pipeline_data.get("by_bu", {}),
        "pipeline_total_acv": pipeline_data.get("total_acv", 0),
    }

    payload = {
        "ground_truth":  ground_truth,
        "icp_profile":   icp_profile,
        "validation":    validation,
    }

    return REVIEWER_SYSTEM, json.dumps(payload, default=str)

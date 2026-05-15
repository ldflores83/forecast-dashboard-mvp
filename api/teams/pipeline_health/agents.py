"""
teams/pipeline_health/agents.py
Three specialist agents, a synthesizer, and a reviewer for the Pipeline Health pipeline.

Design rules:
  - Each agent has ONE job, ONE system prompt, ONE output schema.
  - Agents NEVER call BigQuery directly — all data is passed as arguments.
  - All LLM calls go through llm_adapter.call_llm_json().
  - The synthesizer depends on all three agent outputs.
  - The reviewer sees synthesizer output + raw tool data and flags invented numbers.
  - On any LLM failure, safe fallbacks prevent crashes.
"""

import json

from shared.llm_adapter import call_llm_json

_COMPANY_CTX = (
    "QAD is a manufacturing ERP SaaS company. "
    "BUs are ERP BU, Supply Chain BU, and Redzone BU. "
    "Crystal is the VP of RevOps."
)


# ── AGENT 1: PIPELINE RISK AGENT ─────────────────────────────────────────────

def pipeline_risk_agent(stage_health_data: dict, push_analysis_data: dict, context: dict) -> dict:
    """
    Agent 1: Pipeline Risk Agent.

    Identifies where pipe is stalling, severity by BU/stage, and top 3 risk deals.

    Args:
        stage_health_data:  Output of tools.get_stage_health().
        push_analysis_data: Output of tools.get_push_analysis().
        context:            Week/quarter context dict.

    Returns:
        dict with key: stage_risk_analysis
    """
    print("[agents] Running Pipeline Risk Agent...")

    system_prompt = f"""You are a quantitative pipeline analyst for QAD, a manufacturing ERP SaaS company.
{_COMPANY_CTX}

Analyze stage health and push metrics to identify:
1. Which BU/stage combinations show the most stalling (high avg_days, high pct_over_max_days).
2. Severity for each BU: critical (multiple stages stalling + zombie deals), warning (1-2 flags), healthy.
3. The top 3 highest-risk individual deals by ACV with a one-sentence diagnosis.

Return ONLY valid JSON matching this exact schema:
{{
  "stage_risk_analysis": {{
    "per_bu_findings": [
      {{"bu": str, "severity": "critical|warning|healthy", "key_finding": str}}
    ],
    "top_3_risk_deals": [
      {{"deal_name": str, "bu": str, "stage": str, "acv": float, "diagnosis": str}}
    ],
    "overall_severity": "critical|warning|healthy",
    "summary": str
  }}
}}"""

    payload = {
        "context":     context,
        "stage_health": {
            "total_open_acv":   stage_health_data.get("total_open_acv"),
            "total_deal_count": stage_health_data.get("total_deal_count"),
            "stages": stage_health_data.get("stages", []),
        },
        "push_analysis": {
            "zombie_deal_count":   len(push_analysis_data.get("zombie_deals", [])),
            "pushed_5x_count":     push_analysis_data.get("pushed_5x_count"),
            "pushed_5x_acv":       push_analysis_data.get("pushed_5x_acv"),
            "overdue_close_count": push_analysis_data.get("overdue_close_count"),
            "overdue_close_acv":   push_analysis_data.get("overdue_close_acv"),
            "avg_push_by_stage":   push_analysis_data.get("avg_push_by_stage", []),
            "top_10_worst":        push_analysis_data.get("top_10_worst", []),
        },
    }

    raw = call_llm_json(
        system_prompt=system_prompt,
        user_message=json.dumps(payload, default=str),
        agent_name="pipeline_risk_agent",
    )

    if raw.get("error"):
        print("[agents] Pipeline Risk Agent fallback triggered")
        return _risk_fallback()

    result = raw if "stage_risk_analysis" in raw else {"stage_risk_analysis": raw}
    print(f"[agents] Pipeline Risk Agent complete — severity: {result.get('stage_risk_analysis', {}).get('overall_severity', '?')}")
    return result


def _risk_fallback() -> dict:
    return {
        "stage_risk_analysis": {
            "per_bu_findings": [],
            "top_3_risk_deals": [],
            "overall_severity": "unknown",
            "summary": "Pipeline risk analysis unavailable.",
        }
    }


# ── AGENT 2: MEDDPICC QUALIFICATION AGENT ────────────────────────────────────

def meddpicc_qualification_agent(meddpicc_gaps_data: dict, context: dict) -> dict:
    """
    Agent 2: MEDDPICC Qualification Agent.

    Interprets qualification gap patterns, distinguishes systemic from individual failures,
    and recommends coaching actions by stage.

    Args:
        meddpicc_gaps_data: Output of tools.get_meddpicc_gaps().
        context:            Week/quarter context dict.

    Returns:
        dict with key: qualification_analysis
    """
    print("[agents] Running MEDDPICC Qualification Agent...")

    system_prompt = f"""You are a sales methodology expert specializing in MEDDPICC for enterprise ERP sales at QAD.
{_COMPANY_CTX}

MEDDPICC criteria in scope:
  I = Identify Pain (Prospecting), M = Metrics (Discovery), E = Economic Buyer,
  D = Decision Criteria (Evaluation) / Decision Process (Contracts), C = Champion (Evaluation)

Analyze the gap data below:
1. Identify systemic gaps: criterion missing in >50% of deals at a stage = systemic.
2. Distinguish systemic (training/process) from individual (rep-level coaching) failures.
3. Recommend 1-2 specific coaching actions per gap pattern, tied to stage and criterion.

Return ONLY valid JSON matching this exact schema:
{{
  "qualification_analysis": {{
    "gap_patterns": [
      {{"stage": str, "criterion": str, "gap_pct": float, "interpretation": str}}
    ],
    "systemic_flag": bool,
    "coaching_recommendations": [
      {{"stage": str, "criterion": str, "action": str}}
    ],
    "summary": str
  }}
}}"""

    payload = {
        "context":      context,
        "by_stage":     meddpicc_gaps_data.get("by_stage", []),
        "top_offenders": meddpicc_gaps_data.get("top_offenders", []),
        "total_deals":  meddpicc_gaps_data.get("total_deals", 0),
    }

    raw = call_llm_json(
        system_prompt=system_prompt,
        user_message=json.dumps(payload, default=str),
        agent_name="meddpicc_qualification_agent",
    )

    if raw.get("error"):
        print("[agents] MEDDPICC Agent fallback triggered")
        return _meddpicc_fallback()

    result = raw if "qualification_analysis" in raw else {"qualification_analysis": raw}
    print(f"[agents] MEDDPICC Agent complete — systemic_flag: {result.get('qualification_analysis', {}).get('systemic_flag', '?')}")
    return result


def _meddpicc_fallback() -> dict:
    return {
        "qualification_analysis": {
            "gap_patterns": [],
            "systemic_flag": False,
            "coaching_recommendations": [],
            "summary": "MEDDPICC qualification analysis unavailable.",
        }
    }


# ── AGENT 3: BDE CADENCE AGENT ───────────────────────────────────────────────

def bde_cadence_agent(bde_cadence_data: dict, pipeline_by_owner_data: dict, context: dict) -> dict:
    """
    Agent 3: BDE Cadence Agent.

    Evaluates BDE pipeline health, handoff velocity, CSM-sourced expansion quality,
    and identifies top performers and bottleneck stages.

    Args:
        bde_cadence_data:       Output of tools.get_bde_cadence().
        pipeline_by_owner_data: Output of tools.get_pipeline_by_owner().
        context:                Week/quarter context dict.

    Returns:
        dict with key: cadence_analysis
    """
    print("[agents] Running BDE Cadence Agent...")

    system_prompt = f"""You are a demand generation analyst for QAD, a manufacturing ERP SaaS company.
{_COMPANY_CTX}

BDE SLA context:
  Development stage: max 90 days before leakage.
  Sales Ready stage: max 7 days before AE must accept.
  BDE roles: titles containing BDE, Business Development, LDE.
  CSM-sourced: Customer Success, CSM.

Analyze BDE pipeline health and owner-level data to:
1. Identify the bottleneck stage where BDE pipeline stalls most.
2. Evaluate leakage rate and whether it is within acceptable range (< 20% = healthy).
3. Compare BDE vs CSM vs AE pipeline quality (ACV, velocity, MEDDPICC score).
4. Name top 3 performers by total ACV with good MEDDPICC compliance.

Return ONLY valid JSON matching this exact schema:
{{
  "cadence_analysis": {{
    "source_breakdown": {{
      "BDE": {{"deal_count": int, "total_acv": float, "avg_days_in_stage": float}},
      "CSM": {{"deal_count": int, "total_acv": float, "avg_days_in_stage": float}},
      "AE":  {{"deal_count": int, "total_acv": float, "avg_days_in_stage": float}}
    }},
    "bottleneck_stage": str,
    "leakage_rate": float,
    "leakage_status": "healthy|warning|critical",
    "top_performers": [
      {{"owner_name": str, "owner_role": str, "deal_count": int, "total_acv": float, "meddpicc_score": float}}
    ],
    "summary": str
  }}
}}"""

    top_performers_raw = sorted(
        pipeline_by_owner_data.get("owners", []),
        key=lambda o: -(o.get("total_acv") or 0),
    )[:10]

    payload = {
        "context":       context,
        "bde_cadence": {
            "source_breakdown":        bde_cadence_data.get("source_breakdown", {}),
            "avg_days_in_development": bde_cadence_data.get("avg_days_in_development"),
            "avg_days_to_accept":      bde_cadence_data.get("avg_days_to_accept"),
            "leakage_rate":            bde_cadence_data.get("leakage_rate"),
            "handoff_count":           bde_cadence_data.get("handoff_count"),
            "conversion_by_source":    bde_cadence_data.get("conversion_by_source", {}),
        },
        "top_owners_by_acv": top_performers_raw,
    }

    raw = call_llm_json(
        system_prompt=system_prompt,
        user_message=json.dumps(payload, default=str),
        agent_name="bde_cadence_agent",
    )

    if raw.get("error"):
        print("[agents] BDE Cadence Agent fallback triggered")
        return _cadence_fallback()

    result = raw if "cadence_analysis" in raw else {"cadence_analysis": raw}
    print(f"[agents] BDE Cadence Agent complete — bottleneck: {result.get('cadence_analysis', {}).get('bottleneck_stage', '?')}")
    return result


def _cadence_fallback() -> dict:
    return {
        "cadence_analysis": {
            "source_breakdown": {},
            "bottleneck_stage": "unknown",
            "leakage_rate": 0.0,
            "leakage_status": "unknown",
            "top_performers": [],
            "summary": "BDE cadence analysis unavailable.",
        }
    }


# ── SYNTHESIZER ───────────────────────────────────────────────────────────────

def synthesizer(
    risk_output:          dict,
    qualification_output: dict,
    cadence_output:       dict,
    context:              dict,
) -> dict:
    """
    Synthesizer: Produces a unified executive narrative for Crystal (VP RevOps).

    Receives all three agent outputs and produces a structured plain-text narrative
    with exactly 4 sections. No markdown. Only cites numbers present in the input.

    Args:
        risk_output:          Output of pipeline_risk_agent().
        qualification_output: Output of meddpicc_qualification_agent().
        cadence_output:       Output of bde_cadence_agent().
        context:              Week/quarter context dict.

    Returns:
        dict with keys: final_narrative, bu_scorecard, top_5_stalling_deals,
                        meddpicc_patterns, demand_engine_summary
    """
    print("[agents] Running Synthesizer...")

    system_prompt = f"""You are an executive advisor to Crystal, VP of RevOps at QAD.
{_COMPANY_CTX}

Synthesize the three pipeline health analyses into a unified narrative FOR CRYSTAL.
The narrative must be PLAIN TEXT — no markdown, no bullet points, no headers with # or *.
Write in paragraph form. Structure it into exactly four labeled sections:

Section 1 — Pipeline at a Glance: Overall health scorecard per BU (healthy/warning/critical) and total open ACV context.
Section 2 — Where Deals Are Stalling: Name up to 5 specific deals, their stage, days stalled, and what that means for the quarter.
Section 3 — Systemic Gaps: MEDDPICC patterns that represent coaching opportunities, not just individual rep issues.
Section 4 — Demand Engine: BDE/CSM pipeline quality, leakage rate, handoff velocity, and what it means for future quarters.

Constraints:
  - Only cite numbers that appear in the provided data.
  - Do not invent deal names, ACVs, or percentages.
  - Keep each section to 3-5 sentences.
  - Total narrative length: 300-500 words.

Return ONLY valid JSON matching this exact schema:
{{
  "final_narrative": str,
  "bu_scorecard": {{
    "ERP BU": "healthy|warning|critical",
    "Supply Chain BU": "healthy|warning|critical",
    "Redzone BU": "healthy|warning|critical"
  }},
  "top_5_stalling_deals": [
    {{"deal_name": str, "stage": str, "days_stalled": int, "acv": float}}
  ],
  "meddpicc_patterns": [str],
  "demand_engine_summary": str
}}"""

    payload = {
        "context":              context,
        "stage_risk_analysis":  risk_output.get("stage_risk_analysis", {}),
        "qualification_analysis": qualification_output.get("qualification_analysis", {}),
        "cadence_analysis":     cadence_output.get("cadence_analysis", {}),
    }

    raw = call_llm_json(
        system_prompt=system_prompt,
        user_message=json.dumps(payload, default=str),
        agent_name="pipeline_health_synthesizer",
        max_tokens=4096,
    )

    if raw.get("error"):
        print("[agents] Synthesizer fallback triggered")
        return _synthesizer_fallback(context)

    print(f"[agents] Synthesizer complete — narrative length: {len(raw.get('final_narrative', ''))} chars")
    return raw


def _synthesizer_fallback(context: dict) -> dict:
    return {
        "final_narrative":      "Pipeline health narrative unavailable. Please re-run.",
        "bu_scorecard":         {"ERP BU": "unknown", "Supply Chain BU": "unknown", "Redzone BU": "unknown"},
        "top_5_stalling_deals": [],
        "meddpicc_patterns":    [],
        "demand_engine_summary": "Demand engine analysis unavailable.",
    }


# ── REVIEWER ──────────────────────────────────────────────────────────────────

def reviewer(
    synthesizer_output:   dict,
    stage_health_data:    dict,
    meddpicc_gaps_data:   dict,
    push_analysis_data:   dict,
    bde_cadence_data:     dict,
    pipeline_by_owner_data: dict,
) -> dict:
    """
    Reviewer: Validates that all numbers in the final_narrative exist in tool outputs.

    The reviewer can: flag, remove, or rewrite unsupported claims.
    The reviewer cannot: invent new data or change the schema.

    Args:
        synthesizer_output:     Output of synthesizer().
        stage_health_data:      Ground truth from tools.get_stage_health().
        meddpicc_gaps_data:     Ground truth from tools.get_meddpicc_gaps().
        push_analysis_data:     Ground truth from tools.get_push_analysis().
        bde_cadence_data:       Ground truth from tools.get_bde_cadence().
        pipeline_by_owner_data: Ground truth from tools.get_pipeline_by_owner().

    Returns:
        dict with keys: status, notes, corrections, final_narrative (reviewed)
    """
    print("[agents] Running Reviewer...")

    system_prompt = """You are a data accuracy reviewer for an executive pipeline health report.

Your job: verify that every specific number (ACV, deal count, percentage, days) cited in
the final_narrative exists verbatim or can be directly derived from the provided tool data.

Rules:
  - If a number appears in the narrative but NOT in the data → mark as invented, remove it.
  - If a claim is directionally correct but uses a wrong number → correct the number.
  - Deal names cited must appear in zombie_deals, top_10_worst, or top_offenders lists.
  - Do NOT change the narrative's structure or message — only fix unsupported numbers.
  - If the narrative is fully supported, return status = 'passed'.

Return ONLY valid JSON matching this exact schema:
{
  "status": "passed|flagged",
  "notes": [str],
  "corrections": [str],
  "final_narrative": str
}"""

    ground_truth = {
        "stage_health_summary": {
            "total_open_acv":   stage_health_data.get("total_open_acv"),
            "total_deal_count": stage_health_data.get("total_deal_count"),
            "stages": [
                {k: v for k, v in s.items() if k != "substage_breakdown"}
                for s in stage_health_data.get("stages", [])
            ],
        },
        "push_summary": {
            "zombie_deal_count":   len(push_analysis_data.get("zombie_deals", [])),
            "pushed_5x_count":     push_analysis_data.get("pushed_5x_count"),
            "pushed_5x_acv":       push_analysis_data.get("pushed_5x_acv"),
            "overdue_close_count": push_analysis_data.get("overdue_close_count"),
            "top_10_worst":        push_analysis_data.get("top_10_worst", []),
            "zombie_deals":        push_analysis_data.get("zombie_deals", []),
        },
        "meddpicc_by_stage": meddpicc_gaps_data.get("by_stage", []),
        "top_meddpicc_offenders": meddpicc_gaps_data.get("top_offenders", []),
        "bde_cadence": {
            "leakage_rate":            bde_cadence_data.get("leakage_rate"),
            "avg_days_in_development": bde_cadence_data.get("avg_days_in_development"),
            "avg_days_to_accept":      bde_cadence_data.get("avg_days_to_accept"),
            "handoff_count":           bde_cadence_data.get("handoff_count"),
        },
    }

    payload = {
        "final_narrative": synthesizer_output.get("final_narrative", ""),
        "ground_truth":    ground_truth,
    }

    raw = call_llm_json(
        system_prompt=system_prompt,
        user_message=json.dumps(payload, default=str),
        agent_name="pipeline_health_reviewer",
        max_tokens=8192,
    )

    if raw.get("error"):
        print("[agents] Reviewer fallback triggered — using synthesizer output as-is")
        return {
            "status":           "error",
            "notes":            [f"Reviewer unavailable: {raw.get('reason', 'unknown')}. Output used without review."],
            "corrections":      [],
            "final_narrative":  synthesizer_output.get("final_narrative", ""),
        }

    status      = str(raw.get("status", "passed"))
    notes       = list(raw.get("notes") or [])
    corrections = list(raw.get("corrections") or [])
    narrative   = str(raw.get("final_narrative") or synthesizer_output.get("final_narrative", ""))

    print(f"[agents] Reviewer complete — status: {status} | corrections: {len(corrections)}")
    return {
        "status":          status,
        "notes":           notes,
        "corrections":     corrections,
        "final_narrative": narrative,
    }

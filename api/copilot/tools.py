"""
copilot/tools.py
Deterministic BigQuery tools for the agentic pipeline.

Design rules:
  - NO LLM calls in this file — tools only fetch and normalize data.
  - Each tool returns a plain dict that populates one field in SharedState.
  - Tools are the ONLY place that talks to BigQuery in the agentic layer.
  - All monetary values are floats (USD).
  - All counts are ints.
  - NULL handling: NULLs are kept as None in the output dict and documented
    in the dict so agents and prompts can handle them correctly.
"""

import os
from datetime import date
from google.cloud import bigquery

from .utils import safe_float, safe_int, pct, fmt_currency

# ── BQ CLIENT ─────────────────────────────────────────────────────────────────
PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"

_bq_client = None


def _bq() -> bigquery.Client:
    """Lazy-initialized BigQuery client."""
    global _bq_client
    if _bq_client is None:
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


def _query(sql: str) -> list:
    """Executes a BQ query and returns rows as a list of dicts."""
    rows = list(_bq().query(sql).result())
    return [dict(r) for r in rows]


def _tbl(name: str) -> str:
    """Returns fully qualified BQ table/view reference."""
    return f"`{PROJECT}.{DATASET}.{name}`"


# ── TOOL 1: get_flagged_deals ─────────────────────────────────────────────────

def get_flagged_deals(fiscal_quarter: int = 0) -> dict:
    """
    Fetches open Sales deals with active signal flags.

    Inclusion criteria (strong flags — deal must have at least one):
        Flag_Pushed_5x          — pushed 5+ quarters (zombie deal)
        Flag_No_Activity_7d     — no logged activity in 7+ days
        Flag_Overdue_Close      — close date is in the past
        Flag_Touch_Back_Overdue — scheduled follow-up is overdue

    Renewals are included ONLY if they have Flag_Pushed_5x or
    Flag_Overdue_Close — otherwise the table is dominated by renewals.

    Flag_No_Next_Step is NOT an inclusion criterion (3,395 deals would
    qualify — too noisy). It is included as an additive signal only.

    NOTE on Last_Activity_Date: approximately 70% of open deals have NULL
    Last_Activity_Date. The null_activity_pct field in the output
    documents this so agents can handle it correctly in prompts.
    NULL is NOT assumed to mean "no activity" — it means "data missing."

    NOTE on Flag_Stagnant_Stage: Days_In_Stage is currently a placeholder
    (value = 0). This tool computes a proxy using Last_Stage_Change_Date:
    DATE_DIFF(CURRENT_DATE, Last_Stage_Change_Date, DAY) > 30.

    Args:
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.

    Returns:
        dict with keys:
            flagged_deals         — list of deal dicts (top 25 by ACV)
            pipeline_by_bu        — open ACV by BU
            pipeline_by_stage     — open deal count and ACV by stage
            total_open_sales_acv  — total open ACV (Sales motions only)
            pushed_5x_count       — int
            no_activity_count     — int
            overdue_close_count   — int
            touch_back_count      — int
            null_activity_pct     — float (pct of open deals with NULL activity date)
            stagnant_proxy_count  — int (deals with no stage change in 30+ days)
    """
    fq_filter = f"AND FiscalQuarter = {fiscal_quarter}" if fiscal_quarter > 0 else ""
    tbl = _tbl("opportunities_fy2027")

    # ── Flagged deals ──────────────────────────────────────────────────────────
    deals_sql = f"""
        SELECT
            Id                      AS opp_id,
            Name                    AS opp_name,
            Account_Name,
            BU,
            StageName               AS stage,
            Sales_Motion,
            ACV,
            ATR_Value,
            PCED,
            CloseDate,
            Owner_Name,
            Push_Count,
            Last_Activity_Date,
            Last_Stage_Change_Date,
            Next_Step,
            Opp_Age_Days,
            Flag_Pushed_5x,
            Flag_No_Activity_7d,
            Flag_Overdue_Close,
            Flag_Touch_Back_Overdue,
            Flag_No_Next_Step,
            -- Stagnant stage proxy (Days_In_Stage is currently 0)
            CASE
                WHEN Last_Stage_Change_Date IS NOT NULL
                AND DATE_DIFF(CURRENT_DATE(), DATE(Last_Stage_Change_Date), DAY) > 30
                THEN TRUE ELSE FALSE
            END AS Flag_Stagnant_Proxy
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND (
            -- Sales motions with strong flags
            (
                Sales_Motion IN ('Net New', 'Expansion', 'Migration')
                AND (
                    Flag_Pushed_5x = TRUE
                    OR Flag_No_Activity_7d = TRUE
                    OR Flag_Overdue_Close = TRUE
                    OR Flag_Touch_Back_Overdue = TRUE
                )
            )
            OR
            -- Renewals only with critical flags
            (
                Sales_Motion = 'Renewal'
                AND (Flag_Pushed_5x = TRUE OR Flag_Overdue_Close = TRUE)
            )
          )
          {fq_filter}
        ORDER BY
            CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration') THEN 0 ELSE 1 END,
            ACV DESC,
            ATR_Value DESC
        LIMIT 25
    """

    # ── Pipeline by BU ────────────────────────────────────────────────────────
    by_bu_sql = f"""
        SELECT
            BU,
            COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                              AND Is_Open = TRUE THEN ACV END), 0) AS open_sales_acv,
            COUNTIF(Sales_Motion IN ('Net New','Expansion','Migration')
                    AND Is_Open = TRUE)                            AS open_sales_count
        FROM {tbl}
        WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          AND Is_Open = TRUE
          {fq_filter}
        GROUP BY BU
        ORDER BY open_sales_acv DESC
    """

    # ── Pipeline by stage ─────────────────────────────────────────────────────
    by_stage_sql = f"""
        SELECT
            StageName AS stage,
            COALESCE(SUM(ACV), 0) AS open_acv,
            COUNT(*)              AS deal_count
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND Sales_Motion IN ('Net New','Expansion','Migration')
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          {fq_filter}
        GROUP BY StageName
        ORDER BY open_acv DESC
    """

    # ── Signal flag counts ─────────────────────────────────────────────────────
    counts_sql = f"""
        SELECT
            COUNTIF(Flag_Pushed_5x = TRUE AND Is_Open = TRUE)                AS pushed_5x_count,
            COUNTIF(Flag_No_Activity_7d = TRUE AND Is_Open = TRUE)           AS no_activity_count,
            COUNTIF(Flag_Overdue_Close = TRUE AND Is_Open = TRUE)            AS overdue_close_count,
            COUNTIF(Flag_Touch_Back_Overdue = TRUE AND Is_Open = TRUE)       AS touch_back_count,
            COUNTIF(Is_Open = TRUE)                                           AS total_open,
            COUNTIF(Last_Activity_Date IS NULL AND Is_Open = TRUE)           AS null_activity_count,
            COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                              AND Is_Open = TRUE THEN ACV END), 0)           AS total_open_sales_acv,
            COUNTIF(
                Last_Stage_Change_Date IS NOT NULL
                AND DATE_DIFF(CURRENT_DATE(), DATE(Last_Stage_Change_Date), DAY) > 30
                AND Is_Open = TRUE
                AND Sales_Motion IN ('Net New','Expansion','Migration')
            ) AS stagnant_proxy_count
        FROM {tbl}
        WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          {fq_filter}
    """

    # ── Execute all queries ───────────────────────────────────────────────────
    deals_rows   = _query(deals_sql)
    by_bu_rows   = _query(by_bu_sql)
    by_stage_rows= _query(by_stage_sql)
    counts_rows  = _query(counts_sql)

    counts = counts_rows[0] if counts_rows else {}
    total_open       = safe_int(counts.get("total_open"))
    null_act_count   = safe_int(counts.get("null_activity_count"))
    null_activity_pct = round(null_act_count / total_open * 100, 1) if total_open > 0 else 0.0

    # ── Shape deals list ──────────────────────────────────────────────────────
    flagged_deals = []
    for r in deals_rows:
        flags = []
        if r.get("Flag_Pushed_5x"):        flags.append({"key": "pushed_5x",    "label": "Pushed 5+ qtrs",    "severity": "critical"})
        if r.get("Flag_Overdue_Close"):     flags.append({"key": "overdue_close","label": "Close date past",   "severity": "critical"})
        if r.get("Flag_No_Activity_7d"):    flags.append({"key": "no_activity",  "label": "No activity 7d",    "severity": "warning"})
        if r.get("Flag_Touch_Back_Overdue"):flags.append({"key": "touch_back",   "label": "Follow-up overdue", "severity": "warning"})
        if r.get("Flag_No_Next_Step"):      flags.append({"key": "no_next_step", "label": "No next step",      "severity": "info"})
        if r.get("Flag_Stagnant_Proxy"):    flags.append({"key": "stagnant",     "label": "Stagnant 30d+",     "severity": "warning"})

        flagged_deals.append({
            "opp_id":        str(r.get("opp_id", "")),
            "opp_name":      str(r.get("opp_name", "")),
            "account_name":  str(r.get("Account_Name", "")),
            "bu":            str(r.get("BU", "")),
            "stage":         str(r.get("stage", "")),
            "sales_motion":  str(r.get("Sales_Motion", "")),
            "acv":           safe_float(r.get("ACV")),
            "atr_value":     safe_float(r.get("ATR_Value")),
            "pced":          str(r.get("PCED") or ""),
            "close_date":    str(r.get("CloseDate") or ""),
            "owner_name":    str(r.get("Owner_Name", "")),
            "push_count":    safe_int(r.get("Push_Count")),
            "last_activity": str(r.get("Last_Activity_Date") or ""),
            "next_step":     str(r.get("Next_Step") or ""),
            "opp_age_days":  safe_int(r.get("Opp_Age_Days")),
            "flags":         flags,
            "flag_count":    len(flags),
        })

    return {
        "flagged_deals":       flagged_deals,
        "pipeline_by_bu":      [{"bu": r["BU"], "open_sales_acv": safe_float(r["open_sales_acv"]),
                                  "open_sales_count": safe_int(r["open_sales_count"])} for r in by_bu_rows],
        "pipeline_by_stage":   [{"stage": r["stage"], "open_acv": safe_float(r["open_acv"]),
                                  "deal_count": safe_int(r["deal_count"])} for r in by_stage_rows],
        "total_open_sales_acv":safe_float(counts.get("total_open_sales_acv")),
        "pushed_5x_count":     safe_int(counts.get("pushed_5x_count")),
        "no_activity_count":   safe_int(counts.get("no_activity_count")),
        "overdue_close_count": safe_int(counts.get("overdue_close_count")),
        "touch_back_count":    safe_int(counts.get("touch_back_count")),
        "null_activity_pct":   null_activity_pct,
        "stagnant_proxy_count":safe_int(counts.get("stagnant_proxy_count")),
    }


# ── TOOL 2: get_renewal_health ────────────────────────────────────────────────

def get_renewal_health(fiscal_quarter: int = 0) -> dict:
    """
    Fetches renewal health data: BU dynamics, high-risk accounts, recent closures.

    Renewals are framed as a Sales signal — ARR base health directly affects
    whether net growth targets are achievable. If churn exceeds new Sales wins,
    the NRR math becomes impossible.

    Args:
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.

    Returns:
        dict with keys:
            bu_dynamics           — list of per-BU renewal metrics
            high_risk_accounts    — top 10 High tier accounts by ATR
            recent_closures       — renewals closed in last 28 days
            total_atr_at_risk     — float: total ATR in High tier accounts
            total_churn_acv       — float: total lost renewal ATR (full period)
            overall_renewal_win_rate — float: across all BUs
            sales_covers_churn    — bool: does sales won ACV exceed churn ATR?
    """
    fq_filter = f"AND fiscal_quarter = {fiscal_quarter}" if fiscal_quarter > 0 else "AND fiscal_quarter = 0"
    tbl_opps  = _tbl("opportunities_fy2027")
    vw_health = _tbl("vw_account_health")
    vw_dyn    = _tbl("vw_revenue_dynamics")

    # ── BU dynamics from vw_revenue_dynamics ──────────────────────────────────
    bu_sql = f"""
        SELECT
            bu,
            renewal_won_count,
            renewal_lost_count,
            renewal_closed_count,
            renewal_won_acv,
            renewal_lost_acv,
            sales_won_acv,
            net_new_won_acv,
            expansion_won_acv,
            sales_coverage_pct,
            renewal_win_rate_pct,
            sales_open_acv,
            renewal_open_acv
        FROM {vw_dyn}
        WHERE 1=1 {fq_filter}
          AND bu IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        ORDER BY renewal_lost_acv DESC
    """

    # ── High risk accounts ─────────────────────────────────────────────────────
    at_risk_sql = f"""
        SELECT
            account_id,
            account_name,
            bu,
            renewal_atr,
            days_to_earliest_renewal,
            earliest_renewal_date,
            p1_open,
            escalated_open,
            stale_tickets_open,
            risk_score,
            risk_tier,
            has_ticket_data,
            renewals_won_hist,
            renewals_lost_hist
        FROM {vw_health}
        WHERE risk_tier = 'High'
        ORDER BY risk_score DESC, renewal_atr DESC
        LIMIT 10
    """

    # ── Recent closures (last 28 days) ─────────────────────────────────────────
    recent_sql = f"""
        SELECT
            Name            AS opp_name,
            Account_Name,
            BU,
            Sales_Motion,
            Is_Won,
            Is_Lost,
            ACV,
            ATR_Value,
            Loss_Reason,
            CloseDate
        FROM {tbl_opps}
        WHERE Sales_Motion = 'Renewal'
          AND IsClosed = TRUE
          AND DATE(CloseDate) >= DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        ORDER BY ATR_Value DESC
        LIMIT 20
    """

    bu_rows      = _query(bu_sql)
    at_risk_rows = _query(at_risk_sql)
    recent_rows  = _query(recent_sql)

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    total_won_count = sum(safe_int(r.get("renewal_won_count")) for r in bu_rows)
    total_closed    = sum(safe_int(r.get("renewal_closed_count")) for r in bu_rows)
    total_churn     = sum(safe_float(r.get("renewal_lost_acv")) for r in bu_rows)
    total_sales_won = sum(safe_float(r.get("sales_won_acv")) for r in bu_rows)
    total_atr_risk  = sum(safe_float(r.get("renewal_atr")) for r in at_risk_rows)

    overall_win_rate = round(total_won_count / total_closed * 100, 1) if total_closed > 0 else 0.0

    return {
        "bu_dynamics": [{
            "bu":                  r.get("bu", ""),
            "renewal_won_count":   safe_int(r.get("renewal_won_count")),
            "renewal_lost_count":  safe_int(r.get("renewal_lost_count")),
            "renewal_won_acv":     safe_float(r.get("renewal_won_acv")),
            "renewal_lost_acv":    safe_float(r.get("renewal_lost_acv")),
            "sales_won_acv":       safe_float(r.get("sales_won_acv")),
            "sales_coverage_pct":  safe_float(r.get("sales_coverage_pct")),
            "renewal_win_rate_pct":safe_float(r.get("renewal_win_rate_pct")),
            "renewal_open_acv":    safe_float(r.get("renewal_open_acv")),
        } for r in bu_rows],
        "high_risk_accounts": [{
            "account_id":           str(r.get("account_id", "")),
            "account_name":         str(r.get("account_name", "")),
            "bu":                   str(r.get("bu", "")),
            "renewal_atr":          safe_float(r.get("renewal_atr")),
            "days_to_renewal":      safe_int(r.get("days_to_earliest_renewal")),
            "renewal_date":         str(r.get("earliest_renewal_date") or ""),
            "p1_open":              safe_int(r.get("p1_open")),
            "escalated_open":       safe_int(r.get("escalated_open")),
            "risk_score":           safe_int(r.get("risk_score")),
            "renewals_lost_hist":   safe_int(r.get("renewals_lost_hist")),
            "has_ticket_data":      bool(r.get("has_ticket_data", False)),
        } for r in at_risk_rows],
        "recent_closures": [{
            "opp_name":     str(r.get("opp_name", "")),
            "account_name": str(r.get("Account_Name", "")),
            "bu":           str(r.get("BU", "")),
            "is_won":       bool(r.get("Is_Won", False)),
            "is_lost":      bool(r.get("Is_Lost", False)),
            "acv":          safe_float(r.get("ACV")),
            "atr_value":    safe_float(r.get("ATR_Value")),
            "loss_reason":  str(r.get("Loss_Reason") or ""),
            "close_date":   str(r.get("CloseDate") or ""),
        } for r in recent_rows],
        "total_atr_at_risk":        total_atr_risk,
        "total_churn_acv":          total_churn,
        "total_sales_won_acv":      total_sales_won,
        "overall_renewal_win_rate": overall_win_rate,
        "sales_covers_churn":       total_sales_won >= total_churn,
    }


# ── TOOL 3: get_winloss_data ──────────────────────────────────────────────────

def get_winloss_data(fiscal_quarter: int = 0) -> dict:
    """
    Fetches win/loss patterns from all FY2027 closed deals.

    Scope: Solutions Direct only (Category='Solutions', Is_Channel=FALSE).
    Services and Channel are excluded from win rate calculations.
    Renewals are included separately for context.
    Note: includes future-dated closures which may represent pipeline cleanup decisions.

    Args:
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.

    Returns:
        dict with keys:
            closed_won_count      — int
            closed_lost_count     — int
            win_rates_by_motion   — dict {motion: win_rate_pct}
            avg_deal_by_motion    — dict {motion: avg_acv}
            top_loss_reasons      — list of {reason, count, acv, pct_of_losses}
            top_loss_reason       — str (dominant reason)
            systemic_threshold    — 30.0 (pct — if top reason exceeds this, flag as systemic)
            loss_by_stage         — list of {stage, lost_count, lost_acv}
            loss_by_bu            — list of {bu, lost_count, lost_acv}
            recent_wins           — top 5 won deals by ACV
            recent_losses         — top 5 lost deals by ACV
    """
    tbl = _tbl("opportunities_fy2027")

    # ── Aggregated win/loss by motion ──────────────────────────────────────────
    by_motion_sql = f"""
        SELECT
            Sales_Motion,
            COUNTIF(Is_Won = TRUE)                                          AS won_count,
            COUNTIF(Is_Lost = TRUE)                                         AS lost_count,
            COUNTIF(IsClosed = TRUE)                                        AS closed_count,
            COALESCE(SUM(CASE WHEN Is_Won  THEN ACV END), 0)               AS won_acv,
            COALESCE(SUM(CASE WHEN Is_Lost THEN ACV END), 0)               AS lost_acv,
            SAFE_DIVIDE(
                SUM(CASE WHEN Is_Won THEN ACV END),
                NULLIF(COUNTIF(Is_Won), 0)
            )                                                               AS avg_deal_won
        FROM {tbl}
        WHERE IsClosed = TRUE
          AND Category = 'Solutions'
          AND Is_Channel = FALSE
          AND FiscalYear = 2027
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        GROUP BY Sales_Motion
        ORDER BY closed_count DESC
    """

    # ── Top loss reasons ───────────────────────────────────────────────────────
    loss_reason_sql = f"""
        SELECT
            COALESCE(Loss_Reason, 'Unknown') AS loss_reason,
            COUNT(*)                          AS reason_count,
            COALESCE(SUM(ACV), 0)            AS reason_acv
        FROM {tbl}
        WHERE Is_Lost = TRUE
          AND FiscalYear = 2027
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        GROUP BY Loss_Reason
        ORDER BY reason_count DESC
        LIMIT 5
    """

    # ── Loss by stage ──────────────────────────────────────────────────────────
    loss_by_stage_sql = f"""
        SELECT
            StageName               AS stage,
            COUNT(*)                AS lost_count,
            COALESCE(SUM(ACV), 0)  AS lost_acv
        FROM {tbl}
        WHERE Is_Lost = TRUE
          AND FiscalYear = 2027
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        GROUP BY StageName
        ORDER BY lost_count DESC
        LIMIT 5
    """

    # ── Loss by BU ─────────────────────────────────────────────────────────────
    loss_by_bu_sql = f"""
        SELECT
            BU,
            COUNTIF(Is_Lost = TRUE) AS lost_count,
            COUNTIF(Is_Won = TRUE)  AS won_count,
            COALESCE(SUM(CASE WHEN Is_Lost THEN ACV END), 0) AS lost_acv,
            COALESCE(SUM(CASE WHEN Is_Won  THEN ACV END), 0) AS won_acv
        FROM {tbl}
        WHERE IsClosed = TRUE
          AND FiscalYear = 2027
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        GROUP BY BU
        ORDER BY lost_acv DESC
    """

    # ── Recent wins and losses (for context in prompts) ────────────────────────
    recent_sql = f"""
        SELECT
            Name            AS opp_name,
            Account_Name,
            BU,
            Sales_Motion,
            StageName       AS stage,
            Is_Won,
            Is_Lost,
            ACV,
            Loss_Reason,
            CloseDate
        FROM {tbl}
        WHERE IsClosed = TRUE
          AND FiscalYear = 2027
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        ORDER BY ACV DESC
        LIMIT 20
    """

    # ── Execute ───────────────────────────────────────────────────────────────
    motion_rows  = _query(by_motion_sql)
    reason_rows  = _query(loss_reason_sql)
    stage_rows   = _query(loss_by_stage_sql)
    bu_rows      = _query(loss_by_bu_sql)
    recent_rows  = _query(recent_sql)

    # ── Shape win rates ────────────────────────────────────────────────────────
    total_closed = sum(safe_int(r.get("closed_count")) for r in motion_rows)
    total_won    = sum(safe_int(r.get("won_count"))    for r in motion_rows)
    total_lost   = sum(safe_int(r.get("lost_count"))   for r in motion_rows)

    win_rates    = {}
    avg_deal     = {}
    for r in motion_rows:
        motion = r.get("Sales_Motion", "")
        closed = safe_int(r.get("closed_count"))
        won    = safe_int(r.get("won_count"))
        win_rates[motion] = round(won / closed * 100, 1) if closed > 0 else 0.0
        avg_deal[motion]  = safe_float(r.get("avg_deal_won"))

    # ── Shape loss reasons with pct ────────────────────────────────────────────
    loss_reasons = []
    for r in reason_rows:
        count  = safe_int(r.get("reason_count"))
        pct_of = round(count / total_lost * 100, 1) if total_lost > 0 else 0.0
        loss_reasons.append({
            "reason":         str(r.get("loss_reason", "Unknown")),
            "count":          count,
            "acv":            safe_float(r.get("reason_acv")),
            "pct_of_losses":  pct_of,
        })

    top_reason = loss_reasons[0]["reason"] if loss_reasons else ""
    top_reason_pct = loss_reasons[0]["pct_of_losses"] if loss_reasons else 0.0

    # ── Shape recent deals ────────────────────────────────────────────────────
    recent_wins   = [{"opp_name": r["opp_name"], "account_name": r["Account_Name"],
                       "bu": r["BU"], "motion": r["Sales_Motion"],
                       "acv": safe_float(r["ACV"]), "close_date": str(r.get("CloseDate",""))}
                      for r in recent_rows if r.get("Is_Won")][:5]
    recent_losses = [{"opp_name": r["opp_name"], "account_name": r["Account_Name"],
                       "bu": r["BU"], "motion": r["Sales_Motion"],
                       "acv": safe_float(r["ACV"]), "loss_reason": str(r.get("Loss_Reason","") or ""),
                       "stage": r["stage"], "close_date": str(r.get("CloseDate",""))}
                      for r in recent_rows if r.get("Is_Lost")][:5]

    return {
        "closed_won_count":    total_won,
        "closed_lost_count":   total_lost,
        "total_closed_count":  total_closed,
        "win_rates_by_motion": win_rates,
        "avg_deal_by_motion":  avg_deal,
        "top_loss_reasons":    loss_reasons,
        "top_loss_reason":     top_reason,
        "top_loss_reason_pct": top_reason_pct,
        "systemic_threshold":  30.0,
        "loss_by_stage":       [{"stage": r["stage"], "lost_count": safe_int(r["lost_count"]),
                                  "lost_acv": safe_float(r["lost_acv"])} for r in stage_rows],
        "loss_by_bu":          [{"bu": r["BU"], "lost_count": safe_int(r["lost_count"]),
                                  "won_count": safe_int(r["won_count"]),
                                  "lost_acv": safe_float(r["lost_acv"])} for r in bu_rows],
        "recent_wins":         recent_wins,
        "recent_losses":       recent_losses,
    }

"""
api/main.py — Revenue Intelligence Cloud Function
Reads from BigQuery views and returns JSON to the frontend.

All business logic lives in the SQL views.
This function only: filters by quarter, queries the views, shapes JSON.

To migrate to a different project:
    Change PROJECT and DATASET. Views must exist there first (run setup_views.py).
"""

import json
import functions_framework
from datetime import datetime, timezone
from google.cloud import bigquery

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"

bq = bigquery.Client(project=PROJECT)

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json",
}

def ref(view): return f"`{PROJECT}.{DATASET}.{view}`"

@functions_framework.http
def dashboard_api(request):
    if request.method == "OPTIONS":
        return ("", 204, CORS)

    q = request.args.get("q")
    fiscal_quarter = int(q) if q and q.isdigit() and 1 <= int(q) <= 4 else 0
    mode = request.args.get("mode", "")

    try:
        if mode == "signals":
            payload = build_signals_payload(fiscal_quarter)
        elif mode == "clear-cache":
            from shared import cache
            from teams.revenue_signals import memory as signals_memory
            from teams.icp import memory as icp_memory
            from google.cloud import storage as _gcs
            cache.clear()
            signals_memory.clear()
            icp_memory.clear()
            gcs_client = _gcs.Client()
            for blob_name in ("signals_output.json", "icp_output.json"):
                try:
                    gcs_client.bucket("forecast-dashboard-mvp-frontend").blob(blob_name).delete()
                except Exception:
                    pass  # non-fatal: file may not exist yet
            payload = {"status": "cleared", "timestamp": datetime.now(timezone.utc).isoformat()}
        elif mode == "run-agents":
            from teams.revenue_signals import orchestrator
            result = orchestrator.run(fiscal_quarter=fiscal_quarter, force_refresh=True)
            return (json.dumps(result, default=str), 200, CORS)
        elif mode == "icp":
            from teams.icp import orchestrator as icp_orchestrator
            result = icp_orchestrator.run(fiscal_quarter=fiscal_quarter, force_refresh=True)
            return (json.dumps(result, default=str), 200, CORS)
        elif mode == "digest":
            from shared.digest_utils import (
                get_hero_metrics as _get_hero,
                get_latest_signals as _get_signals,
                get_latest_icp as _get_icp,
                get_signals_headlines as _get_headlines,
                get_regional_breakdown as _get_regional,
                generate_digest as _generate_digest,
                send_to_slack as _send_to_slack,
                save_snapshot as _save_snapshot,
            )
            body        = request.get_json(silent=True) or {}
            webhook_url = body.get("webhook_url", "")
            do_snapshot = bool(body.get("save_snapshot", False))

            hero     = _get_hero(bq)
            signals  = _get_signals(bq)
            icp      = _get_icp(bq)
            headlines = _get_headlines(bq)
            regional = _get_regional(bq)
            digest_text, week_key = _generate_digest(hero, signals, icp, headlines, regional)

            slack_sent = False
            if webhook_url:
                slack_sent = _send_to_slack(webhook_url, digest_text, week_key)
            if do_snapshot:
                _save_snapshot(bq, digest_text, hero, week_key, slack_sent)

            return (json.dumps(
                {"digest_text": digest_text, "week_key": week_key, "slack_sent": slack_sent},
                default=str,
            ), 200, CORS)
        else:
            payload = build_payload(fiscal_quarter)
        return (json.dumps(payload, default=str), 200, CORS)
    except Exception as e:
        return (json.dumps({"error": str(e)}), 500, CORS)


def query(sql):
    return list(bq.query(sql).result())


def build_payload(fiscal_quarter):
    fq = fiscal_quarter  # 0 = full year

    # ── HERO METRICS ──────────────────────────────────────────────────────────
    hero_rows = query(f"""
        SELECT * FROM {ref('vw_hero_metrics')}
        WHERE fiscal_quarter = {fq}
    """)
    h = dict(hero_rows[0]) if hero_rows else {}

    # ── WATERFALL ─────────────────────────────────────────────────────────────
    wf_rows = query(f"""
        SELECT * FROM {ref('vw_waterfall')}
        WHERE fiscal_quarter = {fq}
    """)
    w = dict(wf_rows[0]) if wf_rows else {}

    # ── REVENUE DYNAMICS ──────────────────────────────────────────────────────
    dyn_rows = query(f"""
        SELECT * FROM {ref('vw_revenue_dynamics')}
        WHERE fiscal_quarter = {fq}
          AND fiscal_year = 2027
        ORDER BY bu
    """)

    # ── PIPELINE ──────────────────────────────────────────────────────────────
    pipe_rows = query(f"""
        SELECT * FROM {ref('vw_pipeline')}
        WHERE fiscal_quarter = {fq}
        ORDER BY record_type, dimension
    """)

    # ── ACCOUNT HEALTH (opps + jira tickets risk scoring) ─────────────────────
    ah_rows = query(f"""
        SELECT
            account_id, account_name, bu,
            renewal_atr, days_to_earliest_renewal, earliest_renewal_date,
            open_tickets, p1_open, p1_p2_open,
            escalated_open, stale_tickets_open, oldest_open_ticket_days,
            risk_score, risk_tier, has_ticket_data,
            renewals_won_hist, renewals_lost_hist
        FROM {ref('vw_account_health')}
        ORDER BY risk_score DESC
        LIMIT 50
    """)

    # ── LOST ANALYSIS ─────────────────────────────────────────────────────────
    lost_rows = query(f"""
        SELECT * FROM {ref('vw_lost_analysis')}
        WHERE fiscal_quarter = {fq}
        ORDER BY record_type, reason_rank NULLS LAST
    """)

    # ── SHAPE RESPONSE ────────────────────────────────────────────────────────
    return {
        "meta": {
            "fiscal_quarter": fq,
            "fiscal_year":    int(h.get("fiscal_year", 2027)),
            "total_opps":     int(h.get("total_opps", 0)),
            "won_opps":       int(h.get("won_opps", 0)),
            "lost_opps":      int(h.get("lost_opps", 0)),
            "open_opps":      int(h.get("open_opps", 0)),
        },

        "revenue_health": {
            "renewal_win_rate":    _f(h.get("renewal_win_rate_pct")),
            "coverage_solutions_direct": _f(h.get("sales_coverage_pct")),
            "coverage_net_new":    _f(h.get("net_new_coverage_pct")),
            "renewal_won_acv":     _f(h.get("renewal_won_acv")),
            "renewal_lost_acv":    _f(h.get("renewal_lost_acv")),
            "sales_won_acv":       _f(h.get("sales_won_acv")),        # Solutions Direct
            "net_new_won_acv":     _f(h.get("net_new_won_acv")),
            "expansion_won_acv":   _f(h.get("expansion_won_acv")),
            "migration_won_acv":   _f(h.get("migration_won_acv")),
            "channel_won_acv":     _f(h.get("channel_won_acv")),      # Solutions Channel
            "services_won_acv":    _f(h.get("services_won_acv")),     # Services (excluded)
            "total_won_acv":       _f(h.get("total_won_acv")),
            "total_lost_acv":      _f(h.get("total_lost_acv")),
            "total_open_acv":      _f(h.get("total_open_acv")),
        },

        "waterfall": {
            "starting_arr":       _f(w.get("starting_arr", 436700000)),
            "net_new":            _f(w.get("net_new")),
            "expansion":          _f(w.get("expansion")),
            "migration":          _f(w.get("migration")),
            "churn":              _f(w.get("churn")),          # already negative
            "renewal_won_acv":    _f(w.get("renewal_won_acv")), # reference only
            "renewal_won_count":  int(w.get("renewal_won_count") or 0),
            "renewal_lost_count": int(w.get("renewal_lost_count") or 0),
            "ending_arr":         _f(w.get("ending_arr")),
            "net_growth":         _f(w.get("net_growth")),
        },

        "by_bu":     _shape_by_bu(dyn_rows),
        "by_motion": _shape_by_motion(lost_rows),
        "pipeline":  _shape_pipeline(pipe_rows),
        "lost_analysis": _shape_lost(lost_rows),
        "account_health": _shape_account_health(ah_rows),
        "signals":        _read_signals_from_gcs(ah_rows),
    }


# ── SHAPERS ───────────────────────────────────────────────────────────────────

def _shape_by_bu(rows):
    """Revenue dynamics keyed by BU name."""
    result = {}
    for r in rows:
        row = dict(r)
        bu = row["bu"]
        result[bu] = {
            "win_rate":                    _f(row.get("renewal_win_rate_pct")),
            "renewal_won_acv":             _f(row.get("renewal_won_acv")),
            "renewal_lost_acv":            _f(row.get("renewal_lost_acv")),
            "sales_won_acv":               _f(row.get("sales_won_acv")),       # Solutions Direct
            "net_new_won_acv":             _f(row.get("net_new_won_acv")),
            "expansion_won_acv":           _f(row.get("expansion_won_acv")),
            "migration_won_acv":           _f(row.get("migration_won_acv")),
            "channel_won_acv":             _f(row.get("channel_won_acv")),     # Solutions Channel
            "services_won_acv":            _f(row.get("services_won_acv")),    # Services (excl.)
            "solutions_total_coverage_pct":_f(row.get("solutions_total_coverage_pct")),
            "sales_coverage_pct":          _f(row.get("sales_coverage_pct")),  # Direct only
            "open_acv":                    _f(row.get("sales_open_acv", 0)) + _f(row.get("renewal_open_acv", 0)),
            "renewal_open_acv":            _f(row.get("renewal_open_acv")),
            "sales_open_acv":              _f(row.get("sales_open_acv")),
            "renewal_won_count":           int(row.get("renewal_won_count") or 0),
            "renewal_lost_count":          int(row.get("renewal_lost_count") or 0),
        }
    return result


def _shape_by_motion(rows):
    """Won/lost/open by sales motion — from vw_lost_analysis by_motion rows."""
    result = {}
    for r in rows:
        row = dict(r)
        if row.get("record_type") != "by_motion":
            continue
        motion = row.get("motion")
        if not motion:
            continue
        result[motion] = {
            "won_acv":    _f(row.get("won_acv")),
            "lost_acv":   _f(row.get("lost_acv_motion")),
            "open_acv":   _f(row.get("open_acv")),
            "won_opps":   int(row.get("won_count") or 0),
            "lost_opps":  int(row.get("lost_count_motion") or 0),
            "open_opps":  int(row.get("open_count") or 0),
            "win_rate":   _f(row.get("win_rate_pct")),
            "avg_deal":   _f(row.get("avg_deal_won")),
        }
    return result


def _shape_pipeline(rows):
    """Pipeline by stage and by BU."""
    by_stage = {}
    by_bu    = {}

    for r in rows:
        row = dict(r)
        rt = row.get("record_type")

        if rt == "stage":
            stage = row.get("dimension")
            if stage:
                by_stage[stage] = {
                    "acv":   _f(row.get("open_acv")),
                    "count": int(row.get("count") or 0),
                    "group": row.get("dimension_group"),
                }

        elif rt == "bu":
            bu = row.get("dimension")
            if bu:
                by_bu[bu] = {
                    "open_acv":                  _f(row.get("open_acv")),
                    "open_count":                int(row.get("count") or 0),
                    "won_acv":                   _f(row.get("won_acv")),
                    "win_rate":                  _f(row.get("win_rate_pct")),
                    "avg_deal":                  _f(row.get("avg_deal_won")),
                    "open_net_new_acv":          _f(row.get("open_net_new_acv")),
                    "open_expansion_acv":        _f(row.get("open_expansion_acv")),
                    "open_migration_acv":        _f(row.get("open_migration_acv")),
                    "open_renewal_acv":          _f(row.get("open_renewal_acv")),
                    "q_score":                   _f(row.get("q_score")),
                    "q_trend":                   row.get("q_trend") or "",
                    "q_condition":               row.get("q_condition") or "",
                    "account_at_risk":           bool(row.get("account_at_risk") or False),
                    "target_account_status":     row.get("target_account_status") or "",
                    "whitespace_gross_potential":_f(row.get("whitespace_gross_potential")),
                }

    return {"by_stage": by_stage, "by_bu": by_bu}


def _shape_lost(rows):
    """Lost totals, by BU, top reasons."""
    totals   = {}
    by_bu    = {}
    reasons  = []

    for r in rows:
        row = dict(r)
        rt = row.get("record_type")

        if rt == "lost_total":
            totals = {
                "total_acv":   _f(row.get("total_lost_acv")),
                "total_count": int(row.get("total_lost_count") or 0),
                "avg_deal":    _f(row.get("avg_deal_lost")),
            }

        elif rt == "lost_by_bu":
            bu = row.get("bu")
            if bu:
                by_bu[bu] = {
                    "lost_acv":   _f(row.get("lost_acv_bu")),
                    "lost_count": int(row.get("lost_count_bu") or 0),
                }

        elif rt == "loss_reason":
            rank = row.get("reason_rank") or 99
            if int(rank) <= 5:
                reasons.append({
                    "reason": row.get("loss_reason", "Unknown"),
                    "count":  int(row.get("reason_count") or 0),
                    "acv":    _f(row.get("reason_acv")),
                    "rank":   int(rank),
                })

    reasons.sort(key=lambda x: x["rank"])
    top_reason = reasons[0] if reasons else {}

    return {
        **totals,
        "by_bu":       by_bu,
        "top_reasons": reasons,
        "peak_reason": top_reason.get("reason", "—"),
        "peak_count":  top_reason.get("count", 0),
    }


def _shape_account_health(rows):
    """Top at-risk accounts with ticket signals."""
    result = {"high": [], "medium": [], "low": [], "all": []}
    for r in rows:
        row = dict(r)
        account = {
            "account_id":               row.get("account_id", ""),
            "account_name":             row.get("account_name", ""),
            "bu":                       row.get("bu", ""),
            "renewal_atr":              _f(row.get("renewal_atr")),
            "days_to_renewal":          int(row.get("days_to_earliest_renewal") or 0),
            "earliest_renewal_date":    str(row.get("earliest_renewal_date") or ""),
            "open_tickets":             int(row.get("open_tickets") or 0),
            "p1_open":                  int(row.get("p1_open") or 0),
            "p1_p2_open":               int(row.get("p1_p2_open") or 0),
            "escalated_open":           int(row.get("escalated_open") or 0),
            "stale_tickets_open":       int(row.get("stale_tickets_open") or 0),
            "oldest_open_ticket_days":  int(row.get("oldest_open_ticket_days") or 0),
            "risk_score":               int(row.get("risk_score") or 0),
            "risk_tier":                row.get("risk_tier", "Low"),
            "has_ticket_data":          bool(row.get("has_ticket_data", False)),
            "renewals_lost_hist":       int(row.get("renewals_lost_hist") or 0),
        }
        tier = account["risk_tier"].lower()
        if tier in result:
            result[tier].append(account)
        result["all"].append(account)
    result["high_count"]   = len(result["high"])
    result["medium_count"] = len(result["medium"])
    result["low_count"]    = len(result["low"])
    result["total_atr_at_risk"] = sum(
        a["renewal_atr"] for a in result["high"] + result["medium"]
    )
    return result


def _read_signals_from_gcs(fallback_rows):
    """Read signals banner data from agents' signals_output.json on GCS.
    Falls back to _shape_signals(fallback_rows) on any error.

    warnings = number of top-risk deals surfaced by the pipeline agent
    critical = top-risk deals where the agent flagged no economic buyer
    week     = meta.week from the agents' last run
    """
    try:
        from google.cloud import storage as _gcs_mod
        raw  = (_gcs_mod.Client()
                .bucket("forecast-dashboard-mvp-frontend")
                .blob("signals_output.json")
                .download_as_text())
        data      = json.loads(raw)
        meta      = data.get("meta", {})
        top_risks = (data.get("pipeline") or {}).get("top_risks") or []
        warnings  = len(top_risks)
        critical  = sum(
            1 for d in top_risks
            if any("economic buyer" in str(f).lower() for f in (d.get("flags") or []))
        )
        return {
            "week":         meta.get("week", ""),
            "warnings":     warnings,
            "critical":     critical,
            "generated_at": meta.get("generated_at", ""),
            "source":       "agents",
        }
    except Exception:
        return _shape_signals(fallback_rows)


def _shape_signals(ah_rows):
    # fallback only — primary source is signals_output.json
    """
    Derives signal counts from account_health data already in memory.
    No extra BQ query needed.

    warnings  = High-risk accounts with renewal <= 90 days
    critical  = High-risk accounts with P1 open AND renewal <= 60 days
    """
    from datetime import datetime, timezone

    warnings  = 0
    critical  = 0

    for r in ah_rows:
        row      = dict(r)
        tier     = (row.get("risk_tier") or "").lower()
        days     = int(row.get("days_to_earliest_renewal") or 999)
        p1_open  = int(row.get("p1_open") or 0)

        if tier == "high":
            if days <= 90:
                warnings += 1
            if p1_open > 0 and days <= 60:
                critical += 1

    now = datetime.now(timezone.utc)
    # ISO week label: "May 4, 2026"
    week_label = f"{now.day} {now.strftime('%b %Y')}"

    return {
        "week":         week_label,
        "warnings":     warnings,
        "critical":     critical,
        "generated_at": now.isoformat(),
        "source":       "derived",
    }


# ── SIGNALS MODE ──────────────────────────────────────────────────────────────

def build_signals_payload(fiscal_quarter):
    """
    Payload for ?mode=signals — powers revenue-signals.html.
    Query 1: flagged deals (open opps with strong signal flags)
    Query 2: at-risk accounts (High tier from vw_account_health)

    Flag priority logic:
      STRONG flags (qualify deal for table): Pushed_5x, Overdue_Close,
                                             No_Activity_7d, Touch_Back_Overdue
      ADDITIVE flag (shown if present):      No_Next_Step
      EXCLUDED from table criteria:          Stagnant_Stage (Days_In_Stage=0 placeholder)
    """
    from datetime import datetime, timezone

    fq = fiscal_quarter
    tbl = f"`{PROJECT}.{DATASET}.opportunities`"

    # Quarter filter — same PCED-based logic as main dashboard
    # For signals we filter by FiscalQuarter column (already assigned in export)
    fq_filter = f"AND FiscalQuarter = {fq}" if fq > 0 else ""

    # ── FLAGGED DEALS ─────────────────────────────────────────────────────────
    # Only open deals with at least one STRONG flag
    # Ordered by ATR_Value desc (renewals first by risk $), then ACV desc
    deal_rows = query(f"""
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
            Touch_Back_Date,
            QAD_Status,
            Opp_Age_Days,
            Flag_Pushed_5x,
            Flag_No_Activity_7d,
            Flag_Overdue_Close,
            Flag_Touch_Back_Overdue,
            Flag_No_Next_Step,
            Flag_Stagnant_Stage
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND (
            Flag_Pushed_5x          = TRUE
            OR Flag_No_Activity_7d  = TRUE
            OR Flag_Overdue_Close   = TRUE
            OR Flag_Touch_Back_Overdue = TRUE
          )
          {fq_filter}
        ORDER BY
            CASE WHEN Sales_Motion = 'Renewal' THEN 0 ELSE 1 END,
            ATR_Value DESC,
            ACV DESC
        LIMIT 150
    """)

    # ── AT-RISK ACCOUNTS ──────────────────────────────────────────────────────
    # High tier only, ordered by risk_score desc
    ah_rows = query(f"""
        SELECT
            account_id,
            account_name,
            bu,
            renewal_atr,
            days_to_earliest_renewal,
            earliest_renewal_date,
            open_tickets,
            p1_open,
            p1_p2_open,
            escalated_open,
            stale_tickets_open,
            oldest_open_ticket_days,
            risk_score,
            risk_tier,
            has_ticket_data,
            renewals_won_hist,
            renewals_lost_hist
        FROM `{PROJECT}.{DATASET}.vw_account_health`
        WHERE risk_tier = 'High'
        ORDER BY risk_score DESC, renewal_atr DESC
        LIMIT 25
    """)

    deals        = _shape_flagged_deals(deal_rows)
    at_risk      = _shape_at_risk_accounts(ah_rows)
    now          = datetime.now(timezone.utc)
    week_label   = now.strftime("%-d %b %Y").lstrip("0")

    # Summary counts
    warnings = sum(1 for a in at_risk if a["days_to_renewal"] <= 90)
    critical = sum(1 for a in at_risk if a["p1_open"] > 0 and a["days_to_renewal"] <= 60)
    total_atr_at_risk = sum(a["renewal_atr"] for a in at_risk)

    return {
        "meta": {
            "mode":           "signals",
            "fiscal_quarter": fq,
            "week":           week_label,
            "generated_at":   now.isoformat(),
        },
        "summary": {
            "warnings":         warnings,
            "critical":         critical,
            "flagged_deals":    len(deals),
            "total_atr_at_risk": total_atr_at_risk,
        },
        "flagged_deals":    deals,
        "at_risk_accounts": at_risk,
    }


def _shape_flagged_deals(rows):
    """Shape flagged deal rows into frontend-ready list."""
    result = []
    for r in rows:
        row = dict(r)

        # Build flags list — strong flags first, additive after
        flags = []
        if row.get("Flag_Pushed_5x"):
            flags.append({"key": "pushed_5x",    "label": "Pushed 5+ qtrs", "severity": "critical"})
        if row.get("Flag_Overdue_Close"):
            flags.append({"key": "overdue_close", "label": "Close date past", "severity": "critical"})
        if row.get("Flag_No_Activity_7d"):
            flags.append({"key": "no_activity",   "label": "No activity 7d",  "severity": "warning"})
        if row.get("Flag_Touch_Back_Overdue"):
            flags.append({"key": "touch_back",    "label": "Follow-up overdue","severity": "warning"})
        if row.get("Flag_No_Next_Step"):
            flags.append({"key": "no_next_step",  "label": "No next step",     "severity": "info"})
        if row.get("Flag_Stagnant_Stage"):
            flags.append({"key": "stagnant",      "label": "Stagnant 30d",     "severity": "warning"})

        result.append({
            "opp_id":        row.get("opp_id", ""),
            "opp_name":      row.get("opp_name", ""),
            "account_name":  row.get("Account_Name", ""),
            "bu":            row.get("BU", ""),
            "stage":         row.get("stage", ""),
            "sales_motion":  row.get("Sales_Motion", ""),
            "acv":           _f(row.get("ACV")),
            "atr_value":     _f(row.get("ATR_Value")),
            "pced":          str(row.get("PCED") or ""),
            "close_date":    str(row.get("CloseDate") or ""),
            "owner_name":    row.get("Owner_Name", ""),
            "push_count":    int(row.get("Push_Count") or 0),
            "last_activity": str(row.get("Last_Activity_Date") or ""),
            "next_step":     row.get("Next_Step") or "",
            "opp_age_days":  int(row.get("Opp_Age_Days") or 0),
            "flags":         flags,
            "flag_count":    len(flags),
        })
    return result


def _shape_at_risk_accounts(rows):
    """Shape at-risk account rows."""
    result = []
    for r in rows:
        row = dict(r)
        result.append({
            "account_id":       row.get("account_id", ""),
            "account_name":     row.get("account_name", ""),
            "bu":               row.get("bu", ""),
            "renewal_atr":      _f(row.get("renewal_atr")),
            "days_to_renewal":  int(row.get("days_to_earliest_renewal") or 0),
            "renewal_date":     str(row.get("earliest_renewal_date") or ""),
            "open_tickets":     int(row.get("open_tickets") or 0),
            "p1_open":          int(row.get("p1_open") or 0),
            "p1_p2_open":       int(row.get("p1_p2_open") or 0),
            "escalated_open":   int(row.get("escalated_open") or 0),
            "stale_tickets":    int(row.get("stale_tickets_open") or 0),
            "oldest_ticket_days": int(row.get("oldest_open_ticket_days") or 0),
            "risk_score":       int(row.get("risk_score") or 0),
            "risk_tier":        row.get("risk_tier", "High"),
            "has_ticket_data":  bool(row.get("has_ticket_data", False)),
            "renewals_lost_hist": int(row.get("renewals_lost_hist") or 0),
        })
    return result


def _f(val):
    """Safe float conversion — returns 0.0 for None."""
    if val is None:
        return 0.0
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0

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
from google.cloud import bigquery

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"

bq = bigquery.Client(project=PROJECT)

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json",
}

def ref(view): return f"`{PROJECT}.{DATASET}.{view}`"

@functions_framework.http
def dashboard_api(request):
    if request.method == "OPTIONS":
        return ("", 204, CORS)

    # quarter param: 1-4 for specific quarter, absent = full year (fiscal_quarter = 0)
    q = request.args.get("q")
    fiscal_quarter = int(q) if q and q.isdigit() and 1 <= int(q) <= 4 else 0

    try:
        payload = build_payload(fiscal_quarter)
        return (json.dumps(payload), 200, CORS)
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
            "coverage_all_sales":  _f(h.get("sales_coverage_pct")),   # Solutions Direct only
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
                    "open_acv":          _f(row.get("open_acv")),
                    "open_count":        int(row.get("count") or 0),
                    "won_acv":           _f(row.get("won_acv")),
                    "win_rate":          _f(row.get("win_rate_pct")),
                    "avg_deal":          _f(row.get("avg_deal_won")),
                    "open_net_new_acv":  _f(row.get("open_net_new_acv")),
                    "open_expansion_acv":_f(row.get("open_expansion_acv")),
                    "open_migration_acv":_f(row.get("open_migration_acv")),
                    "open_renewal_acv":  _f(row.get("open_renewal_acv")),
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


def _f(val):
    """Safe float conversion — returns 0.0 for None."""
    if val is None:
        return 0.0
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0
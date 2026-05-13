"""
teams/icp/tools.py
Deterministic BigQuery tools for the ICP Analysis pipeline.

Design rules:
  - NO LLM calls — tools only fetch and normalize data.
  - Revenue bucketing is Python-computed for consistency across agents.
  - All monetary values are floats (USD).
  - NULL Primary_Vertical → kept as 'Unknown' in aggregation; coverage is documented.
  - BU values in BQ: 'ERP BU', 'Supply Chain BU', 'Redzone BU'.
"""

import os
from google.cloud import bigquery

from shared.utils import safe_float, safe_int

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"

_bq_client = None


def _bq() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


def _query(sql: str) -> list:
    rows = list(_bq().query(sql).result())
    return [dict(r) for r in rows]


def _tbl(name: str) -> str:
    return f"`{PROJECT}.{DATASET}.{name}`"


# ── REVENUE BUCKETING ─────────────────────────────────────────────────────────

def _revenue_bucket(annual_revenue) -> str:
    if annual_revenue is None:
        return "Unknown"
    try:
        rev = float(annual_revenue)
    except (TypeError, ValueError):
        return "Unknown"
    if rev < 40_000_000:
        return "<$40M"
    if rev < 500_000_000:
        return "$40M-$500M"
    if rev < 4_000_000_000:
        return "$500M-$4B"
    return ">$4B"


# ── AGGREGATION HELPER ────────────────────────────────────────────────────────

def _aggregate_by_bu(deals: list) -> dict:
    """
    Aggregates win/loss deal data into ICP-relevant summaries per BU.

    Returns:
        {bu: {by_vertical, by_revenue_range, by_region,
              customer_profile_dist, win_rate_pct, avg_deal_won,
              total, won, lost, won_acv, lost_acv}}
    """
    bus = {}

    for d in deals:
        bu = str(d.get("BU", "") or "")
        if not bu:
            continue
        if bu not in bus:
            bus[bu] = {
                "by_vertical":           {},
                "by_revenue_range":      {},
                "by_region":             {},
                "customer_profile_dist": {},
                "total":     0,
                "won":       0,
                "lost":      0,
                "won_acv":   0.0,
                "lost_acv":  0.0,
            }

        b        = bus[bu]
        is_won   = bool(d.get("Is_Won"))
        is_lost  = bool(d.get("Is_Lost"))
        acv      = safe_float(d.get("ACV"))
        vertical = str(d.get("Primary_Vertical") or "Unknown")
        rev_rng  = _revenue_bucket(d.get("AnnualRevenue"))
        country  = str(d.get("Country") or "Unknown")
        cp       = str(d.get("Customer_Profile") or "Unknown")

        b["total"] += 1
        if is_won:
            b["won"]     += 1
            b["won_acv"] += acv
        if is_lost:
            b["lost"]     += 1
            b["lost_acv"] += acv

        # By vertical
        bv = b["by_vertical"].setdefault(vertical, {"won": 0, "lost": 0, "won_acv": 0.0, "lost_acv": 0.0})
        if is_won:
            bv["won"]     += 1
            bv["won_acv"] += acv
        if is_lost:
            bv["lost"]     += 1
            bv["lost_acv"] += acv

        # By revenue range
        br = b["by_revenue_range"].setdefault(rev_rng, {"won": 0, "lost": 0, "won_acv": 0.0})
        if is_won:
            br["won"]     += 1
            br["won_acv"] += acv
        if is_lost:
            br["lost"] += 1

        # By region (country)
        bc = b["by_region"].setdefault(country, {"won": 0, "lost": 0, "won_acv": 0.0})
        if is_won:
            bc["won"]     += 1
            bc["won_acv"] += acv
        if is_lost:
            bc["lost"] += 1

        # Customer profile
        bcp = b["customer_profile_dist"].setdefault(cp, {"won": 0, "lost": 0})
        if is_won:
            bcp["won"] += 1
        if is_lost:
            bcp["lost"] += 1

    # Compute derived rates
    for bu, b in bus.items():
        closed = b["won"] + b["lost"]
        b["win_rate_pct"] = round(b["won"] / closed * 100, 1) if closed > 0 else 0.0
        b["avg_deal_won"] = round(b["won_acv"] / b["won"],  0) if b["won"]  > 0 else 0.0

        for s in b["by_vertical"].values():
            cl = s["won"] + s["lost"]
            s["win_rate_pct"] = round(s["won"] / cl * 100, 1) if cl > 0 else 0.0
            s["avg_acv_won"]  = round(s["won_acv"] / s["won"], 0) if s["won"] > 0 else 0.0

        for s in b["by_revenue_range"].values():
            cl = s["won"] + s["lost"]
            s["win_rate_pct"] = round(s["won"] / cl * 100, 1) if cl > 0 else 0.0

        for s in b["by_region"].values():
            cl = s["won"] + s["lost"]
            s["win_rate_pct"] = round(s["won"] / cl * 100, 1) if cl > 0 else 0.0

    return bus


# ── TOOL 1: get_won_lost_by_bu ────────────────────────────────────────────────

def get_won_lost_by_bu() -> dict:
    """
    Fetches all closed won/lost Sales deals for ICP discovery.

    No fiscal year filter — ICP is based on full historical patterns.
    Includes all Sales motions (Net New, Expansion, Migration).

    Returns:
        dict with keys:
            raw_deals           — list of deal dicts (used by validator)
            by_bu               — per-BU aggregated ICP metrics
            vertical_coverage   — float: % of deals with non-null Primary_Vertical
            total_deals         — int: total closed Sales deals
            with_vertical       — int: deals that have Primary_Vertical
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            BU,
            Is_Won,
            Is_Lost,
            Primary_Vertical,
            Account_Annual_Revenue  AS AnnualRevenue,
            Account_No_of_Employees AS No_of_Employees,
            Account_Type,
            Country,
            ACV,
            Loss_Reason,
            Customer_Profile,
            Sales_Motion,
            FiscalYear
        FROM {tbl}
        WHERE (Is_Won = TRUE OR Is_Lost = TRUE)
          AND Sales_Motion IN ('Net New', 'Expansion', 'Migration')
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        ORDER BY BU, Is_Won DESC
    """
    rows = _query(sql)

    total_deals    = len(rows)
    with_vertical  = sum(1 for r in rows if r.get("Primary_Vertical"))
    vertical_coverage = round(with_vertical / total_deals * 100, 1) if total_deals > 0 else 0.0

    by_bu = _aggregate_by_bu(rows)

    return {
        "raw_deals":         rows,
        "by_bu":             by_bu,
        "vertical_coverage": vertical_coverage,
        "total_deals":       total_deals,
        "with_vertical":     with_vertical,
    }


# ── TOOL 2: get_pipeline_by_bu ────────────────────────────────────────────────

def get_pipeline_by_bu() -> dict:
    """
    Fetches current open pipeline for ICP validation.

    Returns:
        dict with keys:
            deals       — list of deal dicts (revenue_bucket added in Python)
            by_bu       — {bu: {total_acv, deal_count}}
            total_deals — int
            total_acv   — float
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            Id,
            Name,
            BU,
            StageName,
            ACV,
            Account_Name,
            Primary_Vertical,
            Account_Annual_Revenue  AS AnnualRevenue,
            Account_No_of_Employees AS No_of_Employees,
            Account_Type,
            Country,
            Customer_Profile,
            Sales_Motion,
            Owner_Name,
            CloseDate
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        ORDER BY ACV DESC
    """
    deals = _query(sql)

    for d in deals:
        d["revenue_bucket"] = _revenue_bucket(d.get("AnnualRevenue"))

    by_bu: dict = {}
    for d in deals:
        bu = str(d.get("BU", "") or "")
        if bu not in by_bu:
            by_bu[bu] = {"total_acv": 0.0, "deal_count": 0}
        by_bu[bu]["total_acv"]   += safe_float(d.get("ACV"))
        by_bu[bu]["deal_count"]  += 1

    return {
        "deals":       deals,
        "by_bu":       by_bu,
        "total_deals": len(deals),
        "total_acv":   sum(safe_float(d.get("ACV")) for d in deals),
    }

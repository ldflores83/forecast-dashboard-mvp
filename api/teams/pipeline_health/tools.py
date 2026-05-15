"""
teams/pipeline_health/tools.py
Deterministic BigQuery tools for the Pipeline Health pipeline.

Design rules:
  - NO LLM calls — tools only fetch and normalize data.
  - All monetary values are floats (USD).
  - MEDDPICC rules are deterministic Python logic, no LLM.
  - Stage max-day thresholds from Crystal's methodology deck.
  - BDE/CSM role segmentation uses keyword matching on Owner_Role.
"""

import os
from datetime import date, datetime as _dt
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


# ── STAGE CONSTANTS ───────────────────────────────────────────────────────────

# Max days per stage from Crystal's methodology deck.
# None = no explicit cap defined (still tracked).
STAGE_MAX_DAYS: dict = {
    "Prospecting":         None,
    "Discovery":           30,
    "Scoping":             30,
    "Evaluation":          30,
    "Proposal":            60,
    "Contracts":           30,
    "Development":         90,   # BDE SLA: max before leakage
    "Sales Ready":          7,   # BDE SLA: max before AE must accept
    "Renewal Qualifying":  None,
    "Renewal Validation":  None,
    "Renewal Negotiation": None,
    "Renewal Pending":     None,
    "Renewal Confirmed":   None,
    "Pending Renewal":     None,
}

STAGE_GROUP: dict = {
    "Prospecting":         "Early",
    "Discovery":           "Early",
    "Scoping":             "Mid",
    "Evaluation":          "Mid",
    "Proposal":            "Late",
    "Contracts":           "Late",
    "Development":         "Legacy",
    "Sales Ready":         "Legacy",
    "Renewal Qualifying":  "Renewal",
    "Renewal Validation":  "Renewal",
    "Renewal Negotiation": "Renewal",
    "Renewal Pending":     "Renewal",
    "Renewal Confirmed":   "Renewal",
    "Pending Renewal":     "Renewal",
}

# Substage order within Discovery (for MEDDPICC M-criterion check)
_DISCOVERY_SUBSTAGE_ORDER = [
    "Customer Identifying Problem",
    "Customer Exploring Solutions",
    "Customer Building Requirements",
]

# Senior title keywords for MEDDPICC I-criterion (Prospecting)
_SENIOR_TITLE_KEYWORDS = (
    "champion", "director", "vp", "vice president",
    "ceo", "coo", "cfo", "cto", "chief", "president", "svp", "evp",
)

# Role segment keyword sets
_BDE_KEYWORDS = ("bde", "business development", "lde")
_CSM_KEYWORDS = ("customer success", "csm")

# MEDDPICC criteria checked per stage
_STAGE_CRITERIA: dict = {
    "Prospecting": ["I"],
    "Discovery":   ["M", "E"],
    "Evaluation":  ["E", "D", "C"],
    "Proposal":    ["E"],
    "Contracts":   ["E", "D"],
}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _role_segment(owner_role: str) -> str:
    r = (owner_role or "").lower()
    if any(k in r for k in _BDE_KEYWORDS):
        return "BDE"
    if any(k in r for k in _CSM_KEYWORDS):
        return "CSM"
    return "AE"


def _days_in_stage(row: dict, today: date) -> int:
    """Compute days in current stage; falls back to Last_Stage_Change_Date when Days_In_Stage=0."""
    days = safe_int(row.get("Days_In_Stage"))
    if days and days > 0:
        return days
    raw = row.get("Last_Stage_Change_Date")
    if raw is None:
        return 0
    try:
        d = raw.date() if hasattr(raw, "date") else _dt.fromisoformat(str(raw)[:10]).date()
        return max(0, (today - d).days)
    except Exception:
        return 0


def _bu_filter(bu: str | None) -> str:
    return f"AND BU = '{bu}'" if bu else ""


def _fq_filter(fq: int) -> str:
    return f"AND FiscalQuarter = {fq}" if fq > 0 else ""


def _check_meddpicc(
    criterion: str,
    stage: str,
    at_power: bool,
    fc_name: str,
    vp_fore: str,
    substage: str,
    contacts: list,
) -> bool:
    """Returns True if the MEDDPICC criterion is MET (not a gap) for this deal."""
    if criterion == "I":   # Identify Pain — Prospecting
        return any(
            any(kw in c.get("title", "").lower() for kw in _SENIOR_TITLE_KEYWORDS)
            for c in contacts
        )
    if criterion == "M":   # Metrics — Discovery: substage past first
        try:
            return _DISCOVERY_SUBSTAGE_ORDER.index(substage) > 0
        except ValueError:
            return False
    if criterion == "E":   # Economic Buyer
        if stage == "Proposal":
            return at_power or vp_fore in ("Best Case", "Forecast", "Commit")
        return at_power        # Evaluation, Contracts, Discovery
    if criterion == "D":   # Decision Criteria (Evaluation) / Decision Process (Contracts)
        if stage == "Evaluation":
            return bool(fc_name) and fc_name != "Omitted"
        if stage == "Contracts":
            return fc_name in ("Forecast", "Commit")
        return False
    if criterion == "C":   # Champion — Evaluation
        return len(contacts) > 0
    return False


# ── TOOL 1: get_stage_health ──────────────────────────────────────────────────

def get_stage_health(bu: str | None = None, fiscal_quarter: int = 0) -> dict:
    """
    Returns per-stage pipeline health metrics for open opportunities.

    Derives avg_days_in_stage and pct_over_max_days from Days_In_Stage
    (falling back to Last_Stage_Change_Date when field is 0).

    Returns:
        dict with keys:
            stages          — list of per-stage summaries sorted by total ACV desc
            total_open_acv  — float
            total_deal_count — int
            bu_filter       — str ('All' if no BU filter)
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            StageName,
            Substage,
            BU,
            Id,
            ACV,
            Days_In_Stage,
            Last_Stage_Change_Date,
            Owner_Name,
            Account_Name,
            Name AS opp_name,
            CloseDate
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          {_bu_filter(bu)}
          {_fq_filter(fiscal_quarter)}
        ORDER BY ACV DESC
    """
    rows  = _query(sql)
    today = date.today()

    stages: dict = {}
    for r in rows:
        stage = str(r.get("StageName") or "Unknown")
        acv   = safe_float(r.get("ACV"))
        days  = _days_in_stage(r, today)
        sub   = str(r.get("Substage") or "Unknown")
        max_d = STAGE_MAX_DAYS.get(stage)

        if stage not in stages:
            stages[stage] = {
                "stage_name":       stage,
                "stage_group":      STAGE_GROUP.get(stage, "Other"),
                "max_days_allowed": max_d,
                "deal_count":       0,
                "total_acv_usd":    0.0,
                "_days":            [],
                "_over_max":        0,
                "_substage_counts": {},
            }

        s = stages[stage]
        s["deal_count"]    += 1
        s["total_acv_usd"] += acv
        if days > 0:
            s["_days"].append(days)
        if max_d and days > max_d:
            s["_over_max"] += 1
        s["_substage_counts"][sub] = s["_substage_counts"].get(sub, 0) + 1

    result_stages = []
    for s in stages.values():
        cnt      = s["deal_count"]
        days_lst = s.pop("_days")
        over_max = s.pop("_over_max")
        subs     = s.pop("_substage_counts")
        s["avg_days_in_stage"] = round(sum(days_lst) / len(days_lst), 1) if days_lst else 0.0
        s["pct_over_max_days"] = round(over_max / cnt * 100, 1) if cnt > 0 else 0.0
        s["substage_breakdown"] = subs
        result_stages.append(s)

    result_stages.sort(key=lambda x: -x["total_acv_usd"])

    return {
        "stages":            result_stages,
        "total_open_acv":    sum(s["total_acv_usd"]  for s in result_stages),
        "total_deal_count":  sum(s["deal_count"]      for s in result_stages),
        "bu_filter":         bu or "All",
    }


# ── TOOL 2: get_meddpicc_gaps ─────────────────────────────────────────────────

def get_meddpicc_gaps(bu: str | None = None, fiscal_quarter: int = 0) -> dict:
    """
    Applies deterministic MEDDPICC gap analysis to open opportunities.

    Rules are hardcoded from Crystal's methodology — no LLM.
    Contact roles are fetched to evaluate Champion (C) and Identify Pain (I) criteria.
    Only stages with defined MEDDPICC criteria are analyzed.

    Returns:
        dict with keys:
            by_stage       — list of per-stage gap summaries with gap_pct per criterion
            top_offenders  — top 5 deals by ACV with the most gaps
            total_deals    — int (total open deals queried, across all stages)
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            o.Id,
            o.Name        AS opp_name,
            o.Account_Name,
            o.BU,
            o.StageName,
            o.Substage,
            o.ACV,
            o.At_Power,
            o.ForecastCategoryName,
            o.VP_Forecast,
            o.Owner_Name
        FROM {tbl} o
        WHERE o.Is_Open = TRUE
          AND o.BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          {_bu_filter(bu)}
          {_fq_filter(fiscal_quarter)}
        ORDER BY o.ACV DESC
    """
    rows = _query(sql)

    # Fetch all contact roles in batches
    opp_ids = [str(r["Id"]) for r in rows if r.get("Id")]
    contact_map: dict = {}
    if opp_ids:
        cr_tbl = _tbl("contact_roles")
        for i in range(0, len(opp_ids), 150):
            batch  = opp_ids[i:i + 150]
            ids_in = ", ".join(f"'{oid}'" for oid in batch)
            for cr in _query(f"""
                SELECT opportunityid, contact_title, role
                FROM {cr_tbl}
                WHERE opportunityid IN ({ids_in})
            """):
                oid = str(cr.get("opportunityid", ""))
                contact_map.setdefault(oid, []).append({
                    "title": str(cr.get("contact_title") or "").lower(),
                    "role":  str(cr.get("role") or ""),
                })

    stage_agg: dict = {}
    deal_gaps: list = []

    for r in rows:
        stage    = str(r.get("StageName") or "")
        criteria = _STAGE_CRITERIA.get(stage)
        if not criteria:
            continue

        opp_id   = str(r.get("Id", ""))
        contacts = contact_map.get(opp_id, [])
        acv      = safe_float(r.get("ACV"))
        at_power = bool(r.get("At_Power", False))
        fc_name  = str(r.get("ForecastCategoryName") or "")
        vp_fore  = str(r.get("VP_Forecast") or "")
        substage = str(r.get("Substage") or "")

        gaps = [
            c for c in criteria
            if not _check_meddpicc(c, stage, at_power, fc_name, vp_fore, substage, contacts)
        ]

        deal_gaps.append({
            "opp_id":       opp_id,
            "opp_name":     str(r.get("opp_name") or ""),
            "account_name": str(r.get("Account_Name") or ""),
            "bu":           str(r.get("BU") or ""),
            "stage":        stage,
            "acv":          acv,
            "gaps":         gaps,
            "gap_count":    len(gaps),
            "owner_name":   str(r.get("Owner_Name") or ""),
        })

        if stage not in stage_agg:
            stage_agg[stage] = {
                "stage_name":  stage,
                "deal_count":  0,
                "gap_tallies": {c: 0 for c in criteria},
            }
        stage_agg[stage]["deal_count"] += 1
        for g in gaps:
            stage_agg[stage]["gap_tallies"][g] = stage_agg[stage]["gap_tallies"].get(g, 0) + 1

    by_stage = []
    for agg in stage_agg.values():
        cnt = agg["deal_count"]
        by_stage.append({
            "stage_name":  agg["stage_name"],
            "deal_count":  cnt,
            "meddpicc_gaps": [
                {
                    "criterion":    crit,
                    "missing_count": count,
                    "gap_pct":      round(count / cnt * 100, 1) if cnt > 0 else 0.0,
                }
                for crit, count in agg["gap_tallies"].items()
            ],
        })

    top_offenders = sorted(deal_gaps, key=lambda d: (-d["gap_count"], -d["acv"]))[:5]

    return {
        "by_stage":      by_stage,
        "top_offenders": top_offenders,
        "total_deals":   len(rows),
    }


# ── TOOL 3: get_push_analysis ─────────────────────────────────────────────────

def get_push_analysis(bu: str | None = None, fiscal_quarter: int = 0) -> dict:
    """
    Returns push and zombie deal analysis for open opportunities.

    Zombie deals: Push_Count >= 3 AND days in stage exceeds stage max.

    Returns:
        dict with keys:
            zombie_deals        — list of zombie deal dicts
            pushed_5x_count     — int
            pushed_5x_acv       — float
            overdue_close_count — int
            overdue_close_acv   — float
            avg_push_by_stage   — list of {stage, avg_push_count, deal_count}
            top_10_worst        — top 10 deals by Push_Count
            total_open_deals    — int
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            Id,
            Name        AS opp_name,
            Account_Name,
            BU,
            StageName,
            ACV,
            Push_Count,
            Days_In_Stage,
            Last_Stage_Change_Date,
            CloseDate,
            Owner_Name,
            Flag_Pushed_5x,
            Flag_Overdue_Close
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          {_bu_filter(bu)}
          {_fq_filter(fiscal_quarter)}
        ORDER BY Push_Count DESC, ACV DESC
    """
    rows  = _query(sql)
    today = date.today()

    zombie_deals  = []
    pushed_5x_cnt = 0
    pushed_5x_acv = 0.0
    overdue_cnt   = 0
    overdue_acv   = 0.0
    stage_pushes: dict = {}
    top_10: list = []

    for r in rows:
        stage    = str(r.get("StageName") or "")
        acv      = safe_float(r.get("ACV"))
        push_cnt = safe_int(r.get("Push_Count"))
        days     = _days_in_stage(r, today)
        max_d    = STAGE_MAX_DAYS.get(stage)

        if r.get("Flag_Pushed_5x"):
            pushed_5x_cnt += 1
            pushed_5x_acv += acv
        if r.get("Flag_Overdue_Close"):
            overdue_cnt += 1
            overdue_acv += acv

        if push_cnt >= 3 and max_d and days > max_d:
            zombie_deals.append({
                "opp_id":        str(r.get("Id", "")),
                "opp_name":      str(r.get("opp_name") or ""),
                "account_name":  str(r.get("Account_Name") or ""),
                "bu":            str(r.get("BU") or ""),
                "stage":         stage,
                "acv":           acv,
                "push_count":    push_cnt,
                "days_in_stage": days,
                "max_days":      max_d,
                "owner_name":    str(r.get("Owner_Name") or ""),
                "close_date":    str(r.get("CloseDate") or ""),
            })

        if stage not in stage_pushes:
            stage_pushes[stage] = {"total": 0, "count": 0}
        stage_pushes[stage]["total"] += push_cnt
        stage_pushes[stage]["count"] += 1

        if len(top_10) < 10:
            top_10.append({
                "opp_name":   str(r.get("opp_name") or ""),
                "owner_name": str(r.get("Owner_Name") or ""),
                "stage":      stage,
                "acv":        acv,
                "push_count": push_cnt,
            })

    avg_push_by_stage = sorted(
        [
            {
                "stage":          stg,
                "avg_push_count": round(v["total"] / v["count"], 1) if v["count"] > 0 else 0.0,
                "deal_count":     v["count"],
            }
            for stg, v in stage_pushes.items()
        ],
        key=lambda x: -x["avg_push_count"],
    )

    return {
        "zombie_deals":        zombie_deals,
        "pushed_5x_count":     pushed_5x_cnt,
        "pushed_5x_acv":       pushed_5x_acv,
        "overdue_close_count": overdue_cnt,
        "overdue_close_acv":   overdue_acv,
        "avg_push_by_stage":   avg_push_by_stage,
        "top_10_worst":        top_10,
        "total_open_deals":    len(rows),
    }


# ── TOOL 4: get_bde_cadence ───────────────────────────────────────────────────

def get_bde_cadence(bu: str | None = None) -> dict:
    """
    Returns BDE pipeline health: handoff velocity, leakage, and source breakdown.

    Queries Development, Sales Ready, and Prospecting deals created >= 2025-01-01.
    Segments by Owner_Role into BDE, CSM-sourced, and AE-self.

    Leakage = Development deals open for > 90 days that have not progressed.
    Conversion = Development deals that closed (won/lost/moved on).

    Returns:
        dict with keys:
            source_breakdown        — {BDE|CSM|AE: {deal_count, total_acv, avg_days_in_stage}}
            avg_days_in_development — float
            avg_days_to_accept      — float (avg days spent in Sales Ready)
            leakage_rate            — float (% Dev deals > 90d still open)
            handoff_count           — int (current Prospecting deals from BDE pipeline)
            conversion_by_source    — {BDE|CSM|AE: conversion_rate_pct}
            bu_filter               — str
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            Id,
            BU,
            StageName,
            Owner_Name,
            Owner_Role,
            ACV,
            Days_In_Stage,
            Last_Stage_Change_Date,
            CreatedDate,
            Is_Open
        FROM {tbl}
        WHERE StageName IN ('Development', 'Sales Ready', 'Prospecting')
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          AND DATE(CreatedDate) >= '2025-01-01'
          {_bu_filter(bu)}
        ORDER BY CreatedDate DESC
    """
    rows  = _query(sql)
    today = date.today()

    source_data: dict = {
        seg: {"count": 0, "acv": 0.0, "days_list": [], "converted": 0}
        for seg in ("BDE", "CSM", "AE")
    }
    dev_by_seg: dict = {"BDE": 0, "CSM": 0, "AE": 0}
    dev_days:   list = []
    sr_days:    list = []
    leakage_candidates = 0
    leakage_over_90    = 0
    handoff_count      = 0

    for r in rows:
        stage   = str(r.get("StageName") or "")
        role    = str(r.get("Owner_Role") or "")
        seg     = _role_segment(role)
        acv     = safe_float(r.get("ACV"))
        days    = _days_in_stage(r, today)
        is_open = bool(r.get("Is_Open", True))

        sd = source_data[seg]
        sd["count"] += 1
        sd["acv"]   += acv
        if days > 0:
            sd["days_list"].append(days)

        if stage == "Development":
            dev_by_seg[seg]    += 1
            leakage_candidates += 1
            if days > 0:
                dev_days.append(days)
            if is_open and days > 90:
                leakage_over_90 += 1
            if not is_open:
                sd["converted"] += 1

        elif stage == "Sales Ready":
            if days > 0:
                sr_days.append(days)

        elif stage == "Prospecting":
            handoff_count += 1

    leakage_rate = (
        round(leakage_over_90 / leakage_candidates * 100, 1)
        if leakage_candidates > 0 else 0.0
    )

    source_breakdown = {
        seg: {
            "deal_count":        sd["count"],
            "total_acv":         sd["acv"],
            "avg_days_in_stage": round(sum(sd["days_list"]) / len(sd["days_list"]), 1)
                                 if sd["days_list"] else 0.0,
        }
        for seg, sd in source_data.items()
    }

    conversion_by_source = {
        seg: round(source_data[seg]["converted"] / dev_by_seg[seg] * 100, 1)
             if dev_by_seg[seg] > 0 else 0.0
        for seg in ("BDE", "CSM", "AE")
    }

    return {
        "source_breakdown":         source_breakdown,
        "avg_days_in_development":  round(sum(dev_days) / len(dev_days), 1) if dev_days else 0.0,
        "avg_days_to_accept":       round(sum(sr_days)  / len(sr_days),  1) if sr_days  else 0.0,
        "leakage_rate":             leakage_rate,
        "handoff_count":            handoff_count,
        "conversion_by_source":     conversion_by_source,
        "bu_filter":                bu or "All",
    }


# ── TOOL 5: get_pipeline_by_owner ─────────────────────────────────────────────

def get_pipeline_by_owner(bu: str | None = None, fiscal_quarter: int = 0) -> dict:
    """
    Returns open pipeline grouped by owner with MEDDPICC compliance score.

    meddpicc_score = avg % of applicable criteria met per deal, for stages
    where criteria are defined. Computed without contact-role lookups
    (C and I criteria will be 0 for all owners at this aggregate level).

    Returns:
        dict with keys:
            owners       — list of per-owner summaries sorted by total ACV desc
            total_owners — int
            bu_filter    — str
    """
    tbl = _tbl("opportunities")
    sql = f"""
        SELECT
            Owner_Name,
            Owner_Role,
            BU,
            StageName,
            ACV,
            Days_In_Stage,
            Last_Stage_Change_Date,
            Flag_Pushed_5x,
            Flag_No_Activity_7d,
            Flag_Stagnant_Stage,
            At_Power,
            ForecastCategoryName,
            VP_Forecast,
            Substage
        FROM {tbl}
        WHERE Is_Open = TRUE
          AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
          {_bu_filter(bu)}
          {_fq_filter(fiscal_quarter)}
        ORDER BY ACV DESC
    """
    rows  = _query(sql)
    today = date.today()

    owners: dict = {}
    for r in rows:
        owner = str(r.get("Owner_Name") or "Unknown")
        role  = str(r.get("Owner_Role") or "")
        acv   = safe_float(r.get("ACV"))
        days  = _days_in_stage(r, today)
        stage = str(r.get("StageName") or "")

        if owner not in owners:
            owners[owner] = {
                "owner_name":        owner,
                "owner_role":        role,
                "deal_count":        0,
                "total_acv":         0.0,
                "_days":             [],
                "_meddpicc_scores":  [],
                "flag_counts":       {"pushed": 0, "stagnant": 0, "no_activity": 0},
            }

        o = owners[owner]
        o["deal_count"] += 1
        o["total_acv"]  += acv
        if days > 0:
            o["_days"].append(days)
        if r.get("Flag_Pushed_5x"):      o["flag_counts"]["pushed"]      += 1
        if r.get("Flag_Stagnant_Stage"): o["flag_counts"]["stagnant"]    += 1
        if r.get("Flag_No_Activity_7d"): o["flag_counts"]["no_activity"] += 1

        criteria = _STAGE_CRITERIA.get(stage)
        if criteria:
            at_power = bool(r.get("At_Power", False))
            fc_name  = str(r.get("ForecastCategoryName") or "")
            vp_fore  = str(r.get("VP_Forecast") or "")
            substage = str(r.get("Substage") or "")
            met = sum(
                1 for c in criteria
                if _check_meddpicc(c, stage, at_power, fc_name, vp_fore, substage, [])
            )
            o["_meddpicc_scores"].append(round(met / len(criteria) * 100, 1))

    result_owners = []
    for o in owners.values():
        dl    = o.pop("_days")
        medds = o.pop("_meddpicc_scores")
        o["avg_days_in_stage"] = round(sum(dl) / len(dl), 1) if dl else 0.0
        o["meddpicc_score"]    = round(sum(medds) / len(medds), 1) if medds else None
        result_owners.append(o)

    result_owners.sort(key=lambda x: -x["total_acv"])

    return {
        "owners":       result_owners,
        "total_owners": len(result_owners),
        "bu_filter":    bu or "All",
    }


def get_regional_breakdown(bu: str | None = None) -> list:
    """Pipeline and closed-deal metrics grouped by Opp_Owner_Region for FY2027."""
    bu_clause = _bu_filter(bu)
    _region_filter = "AND COALESCE(Opp_Owner_Region, 'Global') IN ('NAmer', 'LAmer', 'EMEA', 'Asia/Pac', 'Global')"
    _fy_filter     = "AND fiscal_year = 2027"
    _stage_filter  = "AND (IsClosed = FALSE OR StageName IN ('Closed-Won', 'Closed-Lost'))"

    sql = f"""
        WITH base AS (
            SELECT
                Opp_Owner_Region,
                BU,
                StageName,
                COALESCE(ACV, 0) AS ACV,
                COALESCE(IsClosed, FALSE) AS IsClosed,
                COALESCE(Is_Won, FALSE) AS Is_Won,
                FiscalYear AS fiscal_year
            FROM {_tbl('opportunities')}
            WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        )
        SELECT
            COALESCE(Opp_Owner_Region, 'Global') AS region,
            COUNTIF(IsClosed = FALSE)                                           AS pipeline_deals,
            COALESCE(SUM(CASE WHEN IsClosed = FALSE THEN ACV ELSE 0 END), 0)    AS pipeline_acv,
            COUNTIF(IsClosed = TRUE AND Is_Won = TRUE)                          AS won_deals,
            COALESCE(SUM(CASE WHEN IsClosed = TRUE AND Is_Won = TRUE THEN ACV ELSE 0 END), 0) AS won_acv,
            COUNTIF(IsClosed = TRUE AND Is_Won = FALSE)                         AS lost_deals
        FROM base
        WHERE TRUE {_fy_filter} {_stage_filter} {_region_filter} {bu_clause}
        GROUP BY 1
        ORDER BY pipeline_acv DESC
    """

    bu_sql = f"""
        WITH base AS (
            SELECT
                Opp_Owner_Region,
                BU,
                StageName,
                COALESCE(ACV, 0) AS ACV,
                COALESCE(IsClosed, FALSE) AS IsClosed,
                COALESCE(Is_Won, FALSE) AS Is_Won,
                FiscalYear AS fiscal_year
            FROM {_tbl('opportunities')}
            WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
        )
        SELECT
            COALESCE(Opp_Owner_Region, 'Global') AS region,
            BU                                   AS bu,
            COUNTIF(IsClosed = FALSE)                                           AS pipeline_deals,
            COALESCE(SUM(CASE WHEN IsClosed = FALSE THEN ACV ELSE 0 END), 0)    AS pipeline_acv,
            COUNTIF(IsClosed = TRUE AND Is_Won = TRUE)                          AS won_deals,
            COALESCE(SUM(CASE WHEN IsClosed = TRUE AND Is_Won = TRUE THEN ACV ELSE 0 END), 0) AS won_acv,
            COUNTIF(IsClosed = TRUE AND Is_Won = FALSE)                         AS lost_deals
        FROM base
        WHERE TRUE {_fy_filter} {_stage_filter} {_region_filter}
          {bu_clause}
        GROUP BY 1, 2
        ORDER BY region, pipeline_acv DESC
    """

    rows    = _query(sql)
    bu_rows = _query(bu_sql)

    bu_by_region: dict = {}
    for r in bu_rows:
        won   = int(r.get("won_deals", 0) or 0)
        lost  = int(r.get("lost_deals", 0) or 0)
        denom = won + lost
        bu_by_region.setdefault(r["region"], []).append({
            "bu":           r["bu"],
            "pipeline_acv": safe_float(r.get("pipeline_acv", 0)),
            "won_acv":      safe_float(r.get("won_acv", 0)),
            "won_deals":    won,
            "lost_deals":   lost,
            "win_rate":     round(won / denom * 100, 1) if denom else 0.0,
        })

    result = []
    for r in rows:
        won    = int(r.get("won_deals", 0) or 0)
        lost   = int(r.get("lost_deals", 0) or 0)
        denom  = won + lost
        region = r["region"]
        result.append({
            "region":         region,
            "pipeline_acv":   safe_float(r.get("pipeline_acv", 0)),
            "pipeline_deals": int(r.get("pipeline_deals", 0) or 0),
            "won_acv":        safe_float(r.get("won_acv", 0)),
            "won_deals":      won,
            "lost_deals":     lost,
            "win_rate":       round(won / denom * 100, 1) if denom else 0.0,
            "bu_breakdown":   bu_by_region.get(region, []),
        })
    return result

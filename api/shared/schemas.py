"""
shared/schemas.py
Defines the expected JSON output shape for each agent and the reviewer.

Design rules:
  - Schemas are plain dicts (no Pydantic for v1 — keep dependencies minimal).
  - Each schema is a dict of {field_name: (type, description)}.
  - validate_output() checks required fields and basic types before the
    orchestrator writes to GCS or cache.
  - On validation failure, the field is replaced with a safe fallback,
    not raised as an exception — the system should never crash on bad output.
"""

from typing import Any


# ── FLAGGED DEAL FIELDS (tool output shape — not agent output) ────────────────
# Documents the fields in each dict within pipeline_data["flagged_deals"].
# These are inputs to the Pipeline Sentinel prompt, not validated by schemas.py.
FLAGGED_DEAL_FIELDS = {
    # Identity
    "opp_id":               str,
    "opp_name":             str,
    "account_name":         str,
    "bu":                   str,
    "stage":                str,
    "sales_motion":         str,
    "owner_name":           str,
    # Financials
    "acv":                  float,
    "atr_value":            float,
    # Timeline
    "close_date":           str,
    "pced":                 str,
    "last_activity":        str,
    "opp_age_days":           int,
    "days_in_stage":          int,   # SF field (always 0 — kept as passive context)
    "stage_entered_date":     str,   # ISO date from opportunity_history (nullable)
    "days_in_current_stage":  int,   # today - stage_entered_date (nullable; preferred over days_in_stage)
    # Engagement — opp-level (passive context only; always 1 in current data)
    "gong_count":           int,
    # Engagement — account-level from gong_conversations table
    "gong_call_count":               int,   # total Gong calls for this account (None if table missing)
    "gong_last_call":                str,   # ISO date of most recent call (nullable)
    "gong_days_since_last_call":     int,   # days since last call (nullable)
    "gong_latest_key_points":        str,   # HTML-stripped key points from most recent call (nullable)
    "gong_latest_next_steps":        str,   # HTML-stripped next steps from most recent call (nullable)
    "gong_latest_call_title":        str,   # title of most recent call (nullable)
    "next_step":            str,
    "push_count":           int,
    # Forecast alignment
    "vp_forecast":          str,   # VP_Forecast__c: Commit/Upside/Best Case/Omit
    "forecast_category":    str,   # AE ForecastCategoryName
    # MEDDPICC signal
    "at_power":             bool,  # whether AE has access to economic buyer
    "customer_profile":     str,   # ICP/ACP/UCP tier
    # Contact roles (joined from contact_roles table)
    "has_economic_buyer":   bool,
    "has_decision_maker":   bool,
    "contact_roles_summary": list, # [{name, title, role}]
    # Existing BQ flags (from Salesforce)
    "Flag_Pushed_5x":           bool,
    "Flag_No_Activity_7d":      bool,
    "Flag_Overdue_Close":       bool,
    "Flag_Touch_Back_Overdue":  bool,
    "Flag_No_Next_Step":        bool,
    # Python-computed flags (deterministic business rules)
    "Flag_Stagnant_Stage":      bool,  # Days_In_Stage > stage threshold
    "Flag_No_Economic_Buyer":   bool,  # At_Power=False AND late stage
    # Summary
    "flags":        list,  # [{key, label, severity}]
    "flag_count":   int,
}


# ── AGENT SCHEMAS ─────────────────────────────────────────────────────────────

PIPELINE_SCHEMA = {
    # Agent 1: Pipeline Sentinel
    # Focuses on open Sales pipeline (Net New, Expansion, Migration).
    # Input deals include: Flag_Stagnant_Stage, Flag_No_Economic_Buyer,
    # Flag_No_Gong_Activity, customer_profile, days_in_stage, gong_count,
    # vp_forecast, forecast_category (context only), has_economic_buyer.
    "headline":         (str,  "1 sentence, max 150 chars — what is the key pipeline signal this week"),
    "top_risks":        (list, "List of up to 5 dicts: {deal_name, account_name, bu, acv, flags, reason}"),
    "pattern":          (str,  "Pattern observed across flagged deals — BU concentration, stage stall, etc."),
    "coverage_signal":  (str,  "Pipeline coverage context vs ARR base and churn"),
    "recommendation":   (str,  "1 actionable recommendation for Sales leadership"),
}

RENEWAL_SCHEMA = {
    # Agent 2: Renewal Pulse
    # Focuses on ARR base health — renewals as a revenue signal for Sales.
    "headline":           (str,  "1 sentence — overall renewal health this week"),
    "arr_health":         (str,  "Status of the ARR base: protected, at risk, or deteriorating"),
    "bu_pulse":           (dict, "Per-BU summary: {ERP BU: {win_rate_signal, churn_signal, coverage_signal}, ...}"),
    "concentration_risk": (str,  "Whether renewal risk is concentrated in few large accounts"),
    "coverage_gap":       (str,  "Whether open Sales pipeline covers projected churn"),
    "recommendation":     (str,  "1 actionable recommendation"),
}

WINLOSS_SCHEMA = {
    # Agent 3: Win/Loss Intelligence
    # Focuses on patterns in closed deals last 90 days.
    "headline":       (str,  "1 sentence — what the win/loss data shows this period"),
    "win_profile":    (str,  "What type of deal we are winning: motion, BU, size, stage velocity"),
    "loss_patterns":  (str,  "Where and why we are losing deals"),
    "systemic_flag":  (bool, "True if a single loss reason accounts for >30% of lost deals"),
    "recommendation": (str,  "1 actionable recommendation for Sales"),
}

REVIEWER_SCHEMA = {
    # Reviewer output wraps the validated agent outputs.
    "status":      (str,  "'passed' or 'flagged'"),
    "notes":       (list, "List of strings — corrections or observations made by reviewer"),
    "corrections": (list, "List of strings — specific claims that were removed or rewritten"),
    "pipeline":    (dict, "Validated pipeline agent output"),
    "renewal":     (dict, "Validated renewal agent output"),
    "winloss":     (dict, "Validated winloss agent output"),
}


# ── FALLBACK VALUES ───────────────────────────────────────────────────────────
# Used when an agent returns a field of the wrong type or misses a field.

PIPELINE_FALLBACKS = {
    "headline":        "Pipeline analysis unavailable for this cycle.",
    "top_risks":       [],
    "pattern":         "Pattern analysis unavailable.",
    "coverage_signal": "Coverage data unavailable.",
    "recommendation":  "Review pipeline manually in Salesforce.",
}

RENEWAL_FALLBACKS = {
    "headline":           "Renewal health analysis unavailable for this cycle.",
    "arr_health":         "ARR health analysis unavailable.",
    "bu_pulse":           {},
    "concentration_risk": "Concentration analysis unavailable.",
    "coverage_gap":       "Coverage gap analysis unavailable.",
    "recommendation":     "Review renewal pipeline manually.",
}

WINLOSS_FALLBACKS = {
    "headline":       "Win/loss analysis unavailable for this cycle.",
    "win_profile":    "Win pattern analysis unavailable.",
    "loss_patterns":  "Loss pattern analysis unavailable.",
    "systemic_flag":  False,
    "recommendation": "Review closed deals manually in Salesforce.",
}

REVIEWER_FALLBACKS = {
    "status":      "error",
    "notes":       ["Reviewer output was malformed — raw agent outputs used."],
    "corrections": [],
    "pipeline":    PIPELINE_FALLBACKS,
    "renewal":     RENEWAL_FALLBACKS,
    "winloss":     WINLOSS_FALLBACKS,
}


# ── VALIDATION ────────────────────────────────────────────────────────────────

def validate_output(output: dict, schema: dict, fallbacks: dict) -> dict:
    """
    Validates an agent output dict against its schema.

    For each required field:
      - If missing: fills with fallback value and logs warning.
      - If wrong type: fills with fallback value and logs warning.
      - If correct: keeps as-is.

    Returns the validated (possibly patched) output dict.
    Never raises — always returns a usable dict.
    """
    if not isinstance(output, dict):
        print(f"[schemas] WARNING: output is not a dict ({type(output)}), using full fallback")
        return dict(fallbacks)

    result = {}
    for field_name, (expected_type, description) in schema.items():
        value = output.get(field_name)

        if value is None:
            print(f"[schemas] WARNING: missing field '{field_name}' — using fallback")
            result[field_name] = fallbacks.get(field_name)
        elif not isinstance(value, expected_type):
            print(f"[schemas] WARNING: field '{field_name}' expected {expected_type.__name__}, "
                  f"got {type(value).__name__} — using fallback")
            result[field_name] = fallbacks.get(field_name)
        else:
            result[field_name] = value

    return result


def validate_pipeline(output: dict) -> dict:
    return validate_output(output, PIPELINE_SCHEMA, PIPELINE_FALLBACKS)


def validate_renewal(output: dict) -> dict:
    return validate_output(output, RENEWAL_SCHEMA, RENEWAL_FALLBACKS)


def validate_winloss(output: dict) -> dict:
    return validate_output(output, WINLOSS_SCHEMA, WINLOSS_FALLBACKS)


def validate_reviewer(output: dict) -> dict:
    return validate_output(output, REVIEWER_SCHEMA, REVIEWER_FALLBACKS)

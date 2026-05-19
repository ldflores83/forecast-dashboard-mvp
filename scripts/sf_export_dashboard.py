"""
scripts/sf_export_dashboard.py
Salesforce → BigQuery export for Revenue Intelligence dashboard.

Auth:    SID-based (refresh from browser cookies ~every 2hrs)
Output:  dashboard_export.csv  +  BQ table opportunities_fy2027

How to get SID:
    F12 → Application → Cookies → copy "sid" value (starts with 00D...)

Changes v2 (Apr 2026):
    Added signal fields from Needs Attention Dashboard audit:
    Push_Count_FQ__c, STAGE_DURATION (Days_In_Stage), LastActivityDate,
    LastStageChangeDate, Touch_Back_Date__c, QAD_Status__c,
    Reason_LQ_Q__c,
    Reason_LQ_Q_Description__c,
    Cancellation_Reason__c,

    NextStep, Description — enables Revenue Signals agentic analysis
"""

import os
from pathlib import Path
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from simple_salesforce import Salesforce
from google.cloud import bigquery

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / '.env')
_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
if _creds and not Path(_creds).is_absolute():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_ROOT / _creds)

# -- AUTH ----------------------------------------------------------------------
# Set SALESFORCE_SESSION_ID in .env (F12 → Application → Cookies → sid)
SESSION_ID   = os.environ.get("SALESFORCE_SESSION_ID", "")
INSTANCE     = "qad.my.salesforce.com"

# -- CONFIG --------------------------------------------------------------------
FISCAL_YEARS = (2026, 2027)
OUTPUT_FILE  = "dashboard_export.csv"
GCP_PROJECT  = "forecast-dashboard-mvp"
BQ_DATASET   = "forecast_data"
BQ_TABLE               = "opportunities"
BQ_TABLE_HISTORY       = "opportunity_history"
BQ_TABLE_CONTACT_ROLES = "contact_roles"
BQ_TABLE_GONG          = "gong_conversations"
BQ_TABLE_SPLITS        = "opportunity_splits"

# Substages and name patterns to exclude (inflate lost renewals otherwise)
EXCL_SUBSTAGE = ['Combined', 'Credited', 'Closed-Duplicate', 'Junk']
EXCL_NAME     = ['Amendment', 'zzz']

# Sales motion mapping — QAD-specific Type values → normalized motion
MOTION_MAP = {
    # Renewals
    'Renewal':               'Renewal',
    'Sub Renewal':           'Renewal',
    # Net New
    'New Customer':          'Net New',
    'New Users':             'Net New',
    'New Site':              'Migration',
    # Expansion
    'New Modules':           'Expansion',
    'Upgrade':               'Expansion',
    'Expansion':             'Expansion',
    # Migration
    'Conversion':            'Migration',
    'Rollout':               'Migration',
    'Migration':             'Migration',
    # Channel
    'Channel':               'Channel',
    'Solutions Channel':     'Channel',
    # Services
    'Services':              'Services',
    'Professional Services': 'Services',
}

# Stage groupings for pipeline funnel
STAGE_GROUP = {
    'Prospecting':          'Early',
    'Discovery':            'Early',
    'Scoping':              'Mid',
    'Evaluation':           'Mid',
    'Proposal':             'Late',
    'Contracts':            'Late',
    'Qualifying':           'Early',
    'Solution Exploration': 'Mid',
    'Evaluation & Alignment': 'Mid',
    'Proposal & Negotiation': 'Late',
    'Awaiting Signature':   'Late',
    'Development':          'Early',
    'Sales Ready':          'Early',
    'Stalled':              'Mid',
    'Renewal Qualifying':   'Renewal',
    'Renewal Validation':   'Renewal',
    'Renewal Negotiation':  'Renewal',
    'Renewal Pending':      'Renewal',
    'Renewal Confirmed':    'Renewal',
    'Pending Renewal':      'Renewal',
    'Closed-Won':           'Closed',
    'Closed-Lost':          'Closed',
    'Deal Lost':            'Closed',
}


# -- SOQL ----------------------------------------------------------------------
# Quarter assignment uses Prior_Contract_End_Date__c (PCED) for renewals,
# CloseDate for sales opps. FY label = end year (Feb-Apr 2026 = FY2027).
#
# Signal fields added v2 (from Needs Attention Dashboard audit):
#   Push_Count_FQ__c       — times pushed to next quarter (zombie deals)
#   LastActivityDate       — last logged activity (stale pipeline detection)
#   LastStageChangeDate    — when stage last moved (stagnation detection)
#   Touch_Back_Date__c     — scheduled follow-up date (overdue = no follow-through)
#   QAD_Status__c          — internal QAD pipeline status
#   S_Date_Aging__c        — days in funnel since Sales Ready date
#   NextStep               — next action text (empty = at-risk flag)
#   Description            — opportunity description (empty = data hygiene issue)
#   Days_In_Stage__c       — days in current stage (stagnant stage detection)
#                            Note: SF standard STAGE_DURATION report field maps
#                            to Age formula — using Days_In_Stage__c custom field
FISCAL_YEAR_FILTER = ", ".join(str(year) for year in FISCAL_YEARS)

OPPORTUNITY_FIELDS = [
    "Id",
    "CurrencyIsoCode",
    "Name",
    "AccountId",
    "StageName",
    "Type",
    "CloseDate",
    "FiscalYear",
    "FiscalQuarter",
    "IsClosed",
    "IsWon",
    "Probability",
    "ForecastCategoryName",
    "LeadSource",
    "CreatedDate",
    "LastStageChangeInDays",
    "HasOverdueTask",
    "Prior_Contract_End_Date__c",
    "ATR_Value__c",
    "Total_Bookings_Net__c",
    "Solutions_Rev_ACV_Net__c",
    "Category__c",
    "Substage__c",
    "Primary_Opp_Value_Stream__c",
    "Reason_LQ_Q__c",
    "Reason_LQ_Q_Description__c",
    "Cancellation_Reason__c",
    "Customer_Profile__c",
    "Gong__Gong_Count__c",
    "q_Score__c",
    "q_Trend__c",
    "q_Meetings_Booked__c",
    "Push_Count_FQ__c",
    "VP_Forecast__c",
    "At_Power__c",
    "Escalation__c",
    "EO_Meeting_Date__c",
    "First_Meeting__c",
    "Meeting_With__c",
    "Accord_Url__c",
    "Accord_Execution_Score__c",
    "Accord_Customer_Accepted__c",
    "NextStep",
    "Description",
    "LastActivityDate",
    "LastStageChangeDate",
    "Touch_Back_Date__c",
    "QAD_Status__c",
    "OwnerId",
    "Opp_Owner_Region__c",
    "Account_Region__c",
]

ACCOUNT_FIELDS = [
    "Name",
    "BillingCountry",
    "Type",
    "ERP_Customer_Base__c",
    "SC_Customer_Base__c",
    "Global_HQ_18_ID__c",
    "Site_Type__c",
    "Primary_Vertical__c",
    "Primary_Sub_Vertical__c",
    "AnnualRevenue",
    "No_of_Employees__c",
    "Account_Region__c",
]

OWNER_FIELDS = [
    "Name",
    "Business_Unit__c",
]

OPPORTUNITY_HISTORY_SOQL = f"""
SELECT OpportunityId, StageName, Amount, CloseDate, CreatedDate, SystemModstamp
FROM OpportunityHistory
WHERE Opportunity.FiscalYear IN ({FISCAL_YEAR_FILTER})
ORDER BY OpportunityId, CreatedDate ASC
"""

CONTACT_ROLES_SOQL = f"""
SELECT OpportunityId, ContactId, Contact.Name, Contact.Title, Role, IsPrimary
FROM OpportunityContactRole
WHERE Opportunity.FiscalYear IN ({FISCAL_YEAR_FILTER})
"""

SPLITS_SOQL = """
SELECT
    OpportunityId,
    SplitOwnerId,
    SplitOwner.Name,
    SplitType.MasterLabel,
    SplitPercentage,
    SplitAmount,
    Split_Solutions_Rev_ACV_Net__c,
    Opportunity.Total_Bookings_Net__c,
    Opportunity.Solutions_Rev_ACV_Net__c
FROM OpportunitySplit
WHERE Opportunity.FiscalYear >= 2026
  AND SplitType.MasterLabel = 'Solutions Revenue'
"""

HISTORY_SCHEMA = [
    bigquery.SchemaField("opportunityid", "STRING"),
    bigquery.SchemaField("stagename", "STRING"),
    bigquery.SchemaField("amount", "FLOAT"),
    bigquery.SchemaField("closedate", "DATE"),
    bigquery.SchemaField("createddate", "TIMESTAMP"),
    bigquery.SchemaField("systemmodstamp", "TIMESTAMP"),
]

CONTACT_ROLE_SCHEMA = [
    bigquery.SchemaField("opportunityid", "STRING"),
    bigquery.SchemaField("contactid", "STRING"),
    bigquery.SchemaField("contact_name", "STRING"),
    bigquery.SchemaField("contact_title", "STRING"),
    bigquery.SchemaField("role", "STRING"),
    bigquery.SchemaField("isprimary", "BOOLEAN"),
]

# Gong conversations — date filter keeps volume manageable
GONG_DATE_FILTER = "2025-01-01T00:00:00Z"

GONG_FIELDS = [
    "Id",
    "Gong__Title__c",
    "Gong__Primary_Account__c",
    "Gong__Primary_Opportunity__c",
    "Gong__Call_Start__c",
    "Gong__Call_Duration__c",
    "Gong__Call_Outcome__c",
    "Gong__Call_Key_Points__c",
    "Gong__Call_Highlights_Next_Steps__c",
    "Gong__Opp_Stage_Time_Of_Call__c",
    "Gong__View_call__c",
    "Gong__Talk_Time_Them__c",
    "Gong__Talk_Time_Us__c",
]

GONG_RENAME = {
    "Id":                                    "gong_call_id",
    "Gong__Title__c":                        "title",
    "Gong__Primary_Account__c":              "account_id",
    "Gong__Primary_Opportunity__c":          "opportunity_id",
    "Gong__Call_Start__c":                   "call_start",
    "Gong__Call_Duration__c":                "duration_min",
    "Gong__Call_Outcome__c":                 "call_outcome",
    "Gong__Call_Key_Points__c":              "key_points",
    "Gong__Call_Highlights_Next_Steps__c":   "next_steps",
    "Gong__Opp_Stage_Time_Of_Call__c":       "stage_at_call",
    "Gong__View_call__c":                    "call_url",
    "Gong__Talk_Time_Them__c":               "talk_time_them",
    "Gong__Talk_Time_Us__c":                 "talk_time_us",
}

GONG_SCHEMA = [
    bigquery.SchemaField("gong_call_id",   "STRING"),
    bigquery.SchemaField("title",          "STRING"),
    bigquery.SchemaField("account_id",     "STRING"),
    bigquery.SchemaField("opportunity_id", "STRING"),
    bigquery.SchemaField("call_start",     "TIMESTAMP"),
    bigquery.SchemaField("duration_min",   "FLOAT"),
    bigquery.SchemaField("call_outcome",   "STRING"),
    bigquery.SchemaField("key_points",     "STRING"),
    bigquery.SchemaField("next_steps",     "STRING"),
    bigquery.SchemaField("stage_at_call",  "STRING"),
    bigquery.SchemaField("call_url",       "STRING"),
    bigquery.SchemaField("talk_time_them", "FLOAT"),
    bigquery.SchemaField("talk_time_us",   "FLOAT"),
]

SPLITS_SCHEMA = [
    bigquery.SchemaField("opportunity_id",              "STRING"),
    bigquery.SchemaField("split_owner_id",              "STRING"),
    bigquery.SchemaField("split_owner_name",            "STRING"),
    bigquery.SchemaField("split_type",                  "STRING"),
    bigquery.SchemaField("split_pct",                   "FLOAT"),
    bigquery.SchemaField("total_bookings_net",          "FLOAT"),
    bigquery.SchemaField("split_solutions_rev_acv_net", "FLOAT"),
    bigquery.SchemaField("split_solutions_acv",         "FLOAT"),
    bigquery.SchemaField("fiscal_year",                 "INTEGER"),
    bigquery.SchemaField("close_date",                  "STRING"),
]


# -- HELPERS -------------------------------------------------------------------
def safe_float(val):
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def assign_quarter(row):
    """
    Assign fiscal quarter using PCED for renewals, CloseDate for sales.
    QAD fiscal calendar: Q1=Feb-Apr, Q2=May-Jul, Q3=Aug-Oct, Q4=Nov-Jan
    FY label = calendar year + 1 (e.g. Feb-Apr 2026 = Q1 FY2027)
    """
    motion = row.get("Sales_Motion", "")
    if motion == "Renewal":
        date_str = row.get("PCED") or row.get("CloseDate")
    else:
        date_str = row.get("CloseDate")

    if not date_str:
        return 0

    try:
        d = pd.to_datetime(date_str)
    except Exception:
        return 0

    month = d.month
    if month in (2, 3, 4):   return 1
    if month in (5, 6, 7):   return 2
    if month in (8, 9, 10):  return 3
    if month in (11, 12, 1): return 4
    return 0


def flatten_record(rec):
    """Flatten nested SF record dict (Account.Name etc.)."""
    flat = {}
    for k, v in rec.items():
        if isinstance(v, dict) and "attributes" in v:
            for nk, nv in v.items():
                if nk != "attributes":
                    flat[f"{k}_{nk}"] = nv
        elif k != "attributes":
            flat[k] = v
    return flat


def object_fields(sf, object_name):
    """Return the set of field API names available on a Salesforce object."""
    desc = getattr(sf, object_name).describe()
    return {field["name"] for field in desc["fields"]}


def keep_available_fields(requested, available, label):
    fields = [field for field in requested if field in available]
    missing = [field for field in requested if field not in available]
    if missing:
        print(f"  Skipping unavailable {label} fields: {', '.join(missing)}")
    return fields


def build_opportunity_soql(sf):
    opp_available = object_fields(sf, "Opportunity")
    account_available = object_fields(sf, "Account")
    user_available = object_fields(sf, "User")

    opp_fields = keep_available_fields(OPPORTUNITY_FIELDS, opp_available, "Opportunity")
    account_fields = keep_available_fields(ACCOUNT_FIELDS, account_available, "Account")
    owner_fields = keep_available_fields(OWNER_FIELDS, user_available, "Owner")

    select_fields = (
        opp_fields +
        [f"Account.{field}" for field in account_fields] +
        [f"Owner.{field}" for field in owner_fields] +
        ["Owner.UserRole.Name"]
    )

    return f"""
SELECT
    {", ".join(select_fields)}
FROM Opportunity
WHERE FiscalYear IN ({FISCAL_YEAR_FILTER})
ORDER BY CloseDate ASC
"""


def ensure_columns(df, defaults):
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default
    return df


# -- CONNECT -------------------------------------------------------------------
def connect_sf():
    if not SESSION_ID:
        raise RuntimeError("SALESFORCE_SESSION_ID is not set. Add it to .env or export it.")
    print("  Connecting to Salesforce...")
    sf = Salesforce(instance=INSTANCE, session_id=SESSION_ID)
    print(f"  Connected: {sf.sf_instance}")
    return sf


# -- FETCH ---------------------------------------------------------------------
def fetch_opportunities(sf):
    print("  Running SOQL query...")
    result  = sf.query_all(build_opportunity_soql(sf))
    records = [flatten_record(r) for r in result["records"]]
    df = pd.DataFrame(records)
    print(f"  Fetched {len(df)} opportunities")
    return df


def fetch_opportunity_history(sf):
    print("  Running OpportunityHistory SOQL query...")
    result = sf.query_all(OPPORTUNITY_HISTORY_SOQL)
    records = [flatten_record(r) for r in result["records"]]
    df = pd.DataFrame(records)
    df = df.rename(columns={
        "OpportunityId": "opportunityid",
        "StageName": "stagename",
        "Amount": "amount",
        "CloseDate": "closedate",
        "CreatedDate": "createddate",
        "SystemModstamp": "systemmodstamp",
    })
    df = ensure_columns(df, {
        "opportunityid": None,
        "stagename": None,
        "amount": None,
        "closedate": None,
        "createddate": None,
        "systemmodstamp": None,
    })
    df = df[["opportunityid", "stagename", "amount", "closedate", "createddate", "systemmodstamp"]]
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["closedate"] = pd.to_datetime(df["closedate"], errors="coerce").dt.date
    df["createddate"] = pd.to_datetime(df["createddate"], errors="coerce", utc=True)
    df["systemmodstamp"] = pd.to_datetime(df["systemmodstamp"], errors="coerce", utc=True)
    print(f"  Fetched {len(df)} opportunity history rows")
    return df


def fetch_contact_roles(sf):
    print("  Running OpportunityContactRole SOQL query...")
    result = sf.query_all(CONTACT_ROLES_SOQL)
    records = [flatten_record(r) for r in result["records"]]
    df = pd.DataFrame(records)
    df = df.rename(columns={
        "OpportunityId": "opportunityid",
        "ContactId": "contactid",
        "Contact_Name": "contact_name",
        "Contact_Title": "contact_title",
        "Role": "role",
        "IsPrimary": "isprimary",
    })
    df = ensure_columns(df, {
        "opportunityid": None,
        "contactid": None,
        "contact_name": None,
        "contact_title": None,
        "role": None,
        "isprimary": None,
    })
    df = df[["opportunityid", "contactid", "contact_name", "contact_title", "role", "isprimary"]]
    df["isprimary"] = df["isprimary"].fillna(False).astype(bool)
    print(f"  Fetched {len(df)} contact role rows")
    return df


def fetch_splits(sf):
    print("  Running OpportunitySplit SOQL query...")
    result = sf.query_all(SPLITS_SOQL)
    records = [flatten_record(r) for r in result["records"]]
    if not records:
        print("  No split records found")
        return pd.DataFrame(columns=["opportunity_id", "split_owner_id", "split_owner_name",
                                     "split_type", "split_pct", "total_bookings_net",
                                     "split_solutions_rev_acv_net", "split_solutions_acv"])
    df = pd.DataFrame(records)
    df = df.rename(columns={
        "OpportunityId":                       "opportunity_id",
        "SplitOwnerId":                        "split_owner_id",
        "SplitOwner_Name":                     "split_owner_name",
        "SplitType_MasterLabel":               "split_type",
        "SplitPercentage":                     "split_pct",
        "Split_Solutions_Rev_ACV_Net__c":      "split_solutions_rev_acv_net",
        "Opportunity_Total_Bookings_Net__c":   "total_bookings_net",
        "Opportunity_Solutions_Rev_ACV_Net__c":"opp_solutions_rev_acv_net",
    })
    df = ensure_columns(df, {
        "opportunity_id":             None,
        "split_owner_id":             None,
        "split_owner_name":           None,
        "split_type":                 None,
        "split_pct":                  None,
        "total_bookings_net":         None,
        "split_solutions_rev_acv_net": None,
    })
    df = df[["opportunity_id", "split_owner_id", "split_owner_name", "split_type",
             "split_pct", "total_bookings_net", "split_solutions_rev_acv_net"]]
    df["split_pct"]                  = pd.to_numeric(df["split_pct"],                  errors="coerce")
    df["total_bookings_net"]         = pd.to_numeric(df["total_bookings_net"],         errors="coerce")
    df["split_solutions_rev_acv_net"]= pd.to_numeric(df["split_solutions_rev_acv_net"],errors="coerce")
    df["split_solutions_acv"] = df["split_solutions_rev_acv_net"].fillna(0)
    print(f"  Fetched {len(df)} split records")
    return df


def fetch_gong_conversations(sf):
    """
    Exports Gong call records from Gong__Gong_Call__c.
    Returns an empty DataFrame (not None) if the object is inaccessible or has no data.
    Field availability is checked at runtime — any missing fields are skipped gracefully.
    """
    print("  Checking Gong__Gong_Call__c availability...")
    try:
        gong_available = object_fields(sf, "Gong__Gong_Call__c")
    except Exception as e:
        print(f"  Gong__Gong_Call__c not accessible ({type(e).__name__}) — skipping")
        return pd.DataFrame()

    available = keep_available_fields(GONG_FIELDS, gong_available, "Gong__Gong_Call__c")
    if not available:
        print("  No Gong fields available — skipping")
        return pd.DataFrame()

    soql = f"""
        SELECT {", ".join(available)}
        FROM Gong__Gong_Call__c
        WHERE Gong__Call_Start__c >= {GONG_DATE_FILTER}
        AND IsDeleted = false
        ORDER BY Gong__Call_Start__c DESC
    """

    print("  Running Gong SOQL query...")
    result  = sf.query_all(soql)
    records = [flatten_record(r) for r in result["records"]]
    if not records:
        print("  No Gong records found")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.rename(columns={k: v for k, v in GONG_RENAME.items() if k in df.columns})

    # Ensure all schema columns present (fill missing fields with None)
    for col in [f.name for f in GONG_SCHEMA]:
        if col not in df.columns:
            df[col] = None

    df = df[[f.name for f in GONG_SCHEMA]]

    # Type coercion
    df["call_start"]     = pd.to_datetime(df["call_start"],     errors="coerce", utc=True)
    df["duration_min"]   = pd.to_numeric(df["duration_min"],    errors="coerce")
    df["talk_time_them"] = pd.to_numeric(df["talk_time_them"],  errors="coerce")
    df["talk_time_us"]   = pd.to_numeric(df["talk_time_us"],    errors="coerce")

    print(f"  Fetched {len(df)} Gong conversations")
    return df


def fetch_fx_rates(sf) -> dict:
    """Returns {IsoCode: ConversionRate} for all active currencies. USD=1.0 always present."""
    print("  Fetching FX rates...")
    result = sf.query_all("SELECT IsoCode, ConversionRate FROM CurrencyType WHERE IsActive = true")
    fx = {r["IsoCode"]: float(r["ConversionRate"]) for r in result["records"]}
    fx.setdefault("USD", 1.0)
    print(f"  FX rates: {len(fx)} currencies — {', '.join(sorted(fx))}")
    return fx


# -- TRANSFORM -----------------------------------------------------------------
def transform(df, fx_rates=None):
    print("  Transforming...")

    # Owner sub-object arrives two levels deep; flatten_record only goes one level.
    # Case 1: simple_salesforce returns Owner as a single nested dict column.
    if "Owner" in df.columns and df["Owner"].apply(lambda x: isinstance(x, dict)).any():
        df["Owner_Name"] = df["Owner"].apply(lambda x: x.get("Name") if isinstance(x, dict) else None)
        df["Owner_Role"] = df["Owner"].apply(lambda x: x.get("UserRole", {}).get("Name") if isinstance(x, dict) else None)
    # Case 2: dotted column names (Owner.Name / Owner.UserRole.Name) passed through as literals.
    if "Owner.Name" in df.columns:
        df["Owner_Name"] = df["Owner.Name"]
    if "Owner.UserRole.Name" in df.columns:
        df["Owner_Role"] = df["Owner.UserRole.Name"]
    # Case 3: flatten_record produced Owner_UserRole as a dict (two-level nesting).
    if "Owner_UserRole" in df.columns and df["Owner_UserRole"].apply(lambda x: isinstance(x, dict)).any():
        df["Owner_Role"] = df["Owner_UserRole"].apply(lambda x: x.get("Name") if isinstance(x, dict) else None)

    # Rename for clarity
    df = df.rename(columns={
        # Account nested fields (flattened by flatten_record)
        "Account_Name":                "Account_Name",
        "Account_BillingCountry":      "Country",
        "Account_Type":                "Account_Type",
        "Account_ERP_Customer_Base__c":"Account_ERP",
        "Account_SC_Customer_Base__c": "Account_SC",
        "Account_Global_HQ_18_ID__c":  "HQ_ID",
        "Account_Site_Type__c":        "SiteType",
        "Account_Primary_Vertical__c": "Primary_Vertical",
        "Account_Primary_Sub_Vertical__c": "Primary_Sub_Vertical",
        "Account_AnnualRevenue":       "Account_Annual_Revenue",
        "Account_No_of_Employees__c":  "Account_No_of_Employees",
        "Account_Account_Region__c":   "Account_Region",
        "Owner.Name":                  "Owner_Name",
        "Owner.UserRole.Name":         "Owner_Role",
        "Owner_Business_Unit__c":      "Owner_BU",
        # Custom fields — strip __c suffix for cleaner names
        "Prior_Contract_End_Date__c":  "PCED",
        "ATR_Value__c":                "ATR_Value",
        "Total_Bookings_Net__c":       "ACV",
        "Solutions_Rev_ACV_Net__c":    "Solutions_ACV",
        "Category__c":                 "Category",
        "Substage__c":                 "Substage",
        "Primary_Opp_Value_Stream__c": "BU",
        "Reason_LQ_Q__c":              "Loss_Reason",
        "Reason_LQ_Q_Description__c":  "Loss_Details",
        "Cancellation_Reason__c":      "Cancellation_Reason",
        "Customer_Profile__c":         "Customer_Profile",
        "Gong__Gong_Count__c":         "Gong_Count",
        "q_Score__c":                  "Q_Score",
        "q_Trend__c":                  "Q_Trend",
        "q_Meetings_Booked__c":        "Q_Meetings_Booked",
        "VP_Forecast__c":              "VP_Forecast",
        "At_Power__c":                 "At_Power",
        "Escalation__c":               "Escalation",
        "EO_Meeting_Date__c":          "EO_Meeting_Date",
        "First_Meeting__c":            "First_Meeting",
        "Meeting_With__c":             "Meeting_With",
        "Accord_Url__c":               "Accord_Url",
        "Accord_Execution_Score__c":   "Accord_Execution_Score",
        "Accord_Customer_Accepted__c": "Accord_Customer_Accepted",
        # Signal fields (new)
        "NextStep":                    "Next_Step",
        "LastActivityDate":            "Last_Activity_Date",
        "LastStageChangeDate":         "Last_Stage_Change_Date",
        "LastStageChangeInDays":       "Last_Stage_Change_Days",
        "Push_Count_FQ__c":            "Push_Count",
        "Touch_Back_Date__c":          "Touch_Back_Date",
        "QAD_Status__c":               "QAD_Status",
        "Opp_Owner_Region__c":         "Opp_Owner_Region",
        "Account_Region__c":           "Account_Region",
        # Standard SF fields kept as-is (views reference these names):
        # Id, Name, AccountId, StageName, Type, FiscalYear, FiscalQuarter,
        # IsClosed, IsWon, CloseDate, Probability, LeadSource, CreatedDate, OwnerId
    })

    df = ensure_columns(df, {
        "IsWon": False,
        "IsClosed": False,
        "Type": None,
        "StageName": None,
        "ATR_Value": 0,
        "ACV": 0,
        "Solutions_ACV": 0,
        "CurrencyIsoCode": None,
        "CloseDate": None,
        "CreatedDate": None,
        "LeadSource": None,
        "Owner_Name": None,
        "Owner_Role": None,
        "Lead_Source": None,
        "Category": None,
        "Substage": None,
        "Name": "",
        "Last_Activity_Date": None,
        "Touch_Back_Date": None,
        "Push_Count": 0,
        "Next_Step": "",
        "Description": "",
        "Opp_Owner_Region": None,
        "Account_Region": None,
    })

    # Derived flags
    df["Is_Won"]  = df["IsWon"].fillna(False).astype(bool)
    df["Is_Lost"] = (df["IsClosed"].fillna(False).astype(bool)) & (~df["Is_Won"])
    df["Is_Open"] = (~df["IsClosed"].fillna(False).astype(bool))

    # Sales motion
    df["Sales_Motion"] = df["Type"].map(MOTION_MAP).fillna("Other")

    # Stage group
    df["Stage_Group"] = df["StageName"].map(STAGE_GROUP).fillna("Other")

    # ACV — smart fallback by motion
    # Renewals: ACV → ATR_Value (never Amount/TCV)
    # Sales:    ACV → Solutions_ACV → 0
    df["ATR_Value"]    = df["ATR_Value"].apply(safe_float)
    df["ACV"]          = df["ACV"].apply(safe_float)
    df["Solutions_ACV"]= df["Solutions_ACV"].apply(safe_float)

    def smart_acv(row):
        acv = row["ACV"]
        if acv != 0:
            return acv
        if row["Sales_Motion"] == "Renewal":
            return row["ATR_Value"]
        return row["Solutions_ACV"]

    df["ACV_Final"] = df.apply(smart_acv, axis=1)

    # FX conversion — ACV_USD and ATR_Value_USD in USD
    _fx = fx_rates or {}
    def _rate(iso):
        return _fx.get(str(iso).strip().upper(), 1.0) if iso and str(iso).strip() else 1.0

    df["FX_Rate"]      = df["CurrencyIsoCode"].apply(_rate)
    # ConversionRate in SF = units of currency per 1 USD (e.g. INR=83, EUR~0.9)
    # Divide to convert native amount to USD
    safe_fx = df["FX_Rate"].replace(0, 1.0)
    df["ACV_USD"]      = (df["ACV_Final"]   / safe_fx).round(2)
    df["ATR_Value_USD"]= (df["ATR_Value"]   / safe_fx).round(2)

    # Fiscal quarter (PCED-based for renewals)
    df["FiscalQuarter"] = df.apply(assign_quarter, axis=1)

    # BU — normalize to match view filter values ('ERP BU', 'Supply Chain BU')
    BU_SUFFIX_MAP = {
        'ERP':           'ERP BU',
        'Supply Chain':  'Supply Chain BU',
        'Redzone':       'Redzone BU',
    }
    df["BU"] = df["BU"].map(BU_SUFFIX_MAP).fillna(df["BU"])

    df["Is_Channel"] = df["Type"].isin(["Channel", "Solutions Channel"])

    # CS-sourced flag
    df["Is_CS_Sourced"] = df["LeadSource"].str.lower().str.contains(
        "cs|customer success|csm", na=False
    )

    # Signal fields — ensure numeric types
    df["Push_Count"]    = pd.to_numeric(df.get("Push_Count"),    errors="coerce").fillna(0).astype(int)
    df["Opp_Age_Days"]  = (pd.Timestamp.now() - pd.to_datetime(df["CreatedDate"], errors="coerce", utc=True).dt.tz_localize(None)).dt.days.fillna(0).astype(int)
    # S_Date_Aging and Days_In_Stage added once API names confirmed
    df["S_Date_Aging"]  = 0
    df["Days_In_Stage"] = df["Last_Stage_Change_Days"].fillna(0).astype(int)

    # Signal flags — derived booleans for easy filtering in BQ views
    df["Flag_No_Activity_7d"]  = (
        df["Is_Open"] &
        df["Last_Activity_Date"].notna() &
        (pd.to_datetime(df["Last_Activity_Date"], errors="coerce") <
         pd.Timestamp.now() - pd.Timedelta(days=7))
    )
    df["Flag_Stagnant_Stage"]  = df["Is_Open"] & (df["Days_In_Stage"] >= 30)
    df["Flag_Pushed_5x"]       = df["Push_Count"] >= 5
    df["Flag_No_Next_Step"]    = df["Is_Open"] & df["Next_Step"].fillna("").str.strip().eq("")
    df["Flag_No_Description"]  = df["Is_Open"] & df["Description"].fillna("").str.strip().eq("")
    df["Flag_Overdue_Close"]   = (
        df["Is_Open"] &
        (pd.to_datetime(df["CloseDate"], errors="coerce") < pd.Timestamp.now())
    )
    df["Flag_Touch_Back_Overdue"] = (
        df["Is_Open"] &
        df["Touch_Back_Date"].notna() &
        (pd.to_datetime(df["Touch_Back_Date"], errors="coerce") < pd.Timestamp.now())
    )

    return df


# -- FILTER --------------------------------------------------------------------
def apply_filters(df):
    print("  Applying exclusion filters...")
    before = len(df)

    # Exclude bad substages
    df = df[~df["Substage"].isin(EXCL_SUBSTAGE)]

    # Exclude Amendment and zzz opps
    for pattern in EXCL_NAME:
        df = df[~df["Name"].str.contains(pattern, case=False, na=False)]

    # Only exclude negative ACV on Closed-Won deals.
    # Open and Lost deals can legitimately have ACV = 0 or null.
    negative_won_mask = (df["Is_Won"] == True) & (df["ACV_USD"] < 0)
    excluded = df[negative_won_mask]
    df = df[~negative_won_mask]
    print(f"  Excluded {len(excluded)} Closed-Won deals with negative ACV_USD")

    after = len(df)
    print(f"  Filtered {before - after} rows -> {after} remaining")
    return df.copy()


# -- PREVIEW -------------------------------------------------------------------
def print_preview(df):
    print()
    print(f"  {'-'*50}")
    print(f"  Total opps     : {len(df)}")
    won  = df[df["Is_Won"]]
    lost = df[df["Is_Lost"]]
    open_ = df[df["Is_Open"]]
    def _usd(sub): return sub["ACV_USD"].sum() if "ACV_USD" in sub.columns else sub["ACV_Final"].sum()
    print(f"  Closed-Won     : {len(won):>4}  ACV native: ${won['ACV_Final'].sum()/1e6:.1f}M  ACV_USD: ${_usd(won)/1e6:.1f}M")
    print(f"  Closed-Lost    : {len(lost):>4}  ACV native: ${lost['ACV_Final'].sum()/1e6:.1f}M  ACV_USD: ${_usd(lost)/1e6:.1f}M")
    print(f"  Open pipeline  : {len(open_):>4}  ACV native: ${open_['ACV_Final'].sum()/1e6:.1f}M  ACV_USD: ${_usd(open_)/1e6:.1f}M")
    print()
    print(f"  Sales motion (Won):")
    for motion, acv in won.groupby("Sales_Motion")["ACV_Final"].sum().sort_values(ascending=False).items():
        print(f"    {motion:<22}: ${acv/1e6:.1f}M")
    print()
    print(f"  Signal flags (open pipeline):")
    print(f"    No activity 7d     : {df['Flag_No_Activity_7d'].sum()}")
    print(f"    Stagnant stage 30d : {df['Flag_Stagnant_Stage'].sum()}")
    print(f"    Pushed 5+ quarters : {df['Flag_Pushed_5x'].sum()}")
    print(f"    No next step       : {df['Flag_No_Next_Step'].sum()}")
    print(f"    Overdue close date : {df['Flag_Overdue_Close'].sum()}")
    print(f"    Touch-back overdue : {df['Flag_Touch_Back_Overdue'].sum()}")
    print(f"  {'-'*50}")


# -- SAVE CSV ------------------------------------------------------------------
def save_csv(df):
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"  Saved: {OUTPUT_FILE} ({len(df)} rows, {len(df.columns)} cols)")


# -- UPLOAD TO BIGQUERY --------------------------------------------------------
def upload_bq(df, table_name=BQ_TABLE, schema=None):
    print(f"  Uploading {table_name} to BigQuery...")
    client    = bigquery.Client(project=GCP_PROJECT)
    table_ref = f"{GCP_PROJECT}.{BQ_DATASET}.{table_name}"

    job_config = bigquery.LoadJobConfig(
        write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE,
        autodetect        = schema is None,
        schema            = schema,
    )

    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()

    table = client.get_table(table_ref)
    print(f"  Uploaded: {table_ref} ({table.num_rows} rows)")
    return table.num_rows


# -- MAIN ----------------------------------------------------------------------
def main():
    print("=" * 60)
    print(f"  Revenue Intelligence Export — FY{', FY'.join(str(year) for year in FISCAL_YEARS)}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n[1/10] Connecting to Salesforce...")
    sf = connect_sf()

    print("\n[2/10] Fetching FX rates...")
    fx_rates = fetch_fx_rates(sf)

    print("\n[3/10] Fetching opportunities...")
    df_raw = fetch_opportunities(sf)

    print("\n[4/10] Transforming...")
    df = transform(df_raw, fx_rates=fx_rates)

    print("\n[5/10] Filtering opportunities...")
    df = apply_filters(df)
    print_preview(df)

    print("\n[6/10] Fetching OpportunitySplits (Solutions Revenue)...")
    splits_df     = fetch_splits(sf)
    splits_upload = pd.DataFrame()
    if not splits_df.empty:
        # Enrich splits with FiscalYear and CloseDate from the main DataFrame
        opp_lookup = df[["Id", "FiscalYear", "CloseDate"]].rename(columns={"Id": "opportunity_id"})
        splits_enriched = splits_df.merge(opp_lookup, on="opportunity_id", how="left")

        # Build upload DataFrame — all records, no deduplication (vw_opportunity_splits handles it)
        splits_upload = splits_enriched[[
            "opportunity_id", "split_owner_id", "split_owner_name",
            "split_type", "split_pct", "total_bookings_net",
            "split_solutions_rev_acv_net", "split_solutions_acv",
        ]].copy()
        splits_upload["fiscal_year"] = splits_enriched["FiscalYear"].fillna(0).astype(int)
        splits_upload["close_date"]  = pd.to_datetime(
            splits_enriched["CloseDate"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")

        unique_opps = splits_df["opportunity_id"].nunique()
        nonzero     = int((splits_df["split_solutions_acv"] > 0).sum())
        print(f"  Splits: {len(splits_df)} raw records across {unique_opps} unique opps")
        print(f"  split_solutions_acv (from Split_Solutions_Rev_ACV_Net__c): {nonzero} non-zero records")
    else:
        print("  No split records found — skipping opportunity_splits upload")

    print("\n[7/10] Fetching related objects...")
    history_df       = fetch_opportunity_history(sf)
    contact_roles_df = fetch_contact_roles(sf)
    gong_df          = fetch_gong_conversations(sf)

    print("\n[8/10] Saving opportunity CSV...")
    save_csv(df)

    print("\n[9/10] Uploading to BigQuery...")
    row_counts = {
        BQ_TABLE:               upload_bq(df, BQ_TABLE),
        BQ_TABLE_HISTORY:       upload_bq(history_df, BQ_TABLE_HISTORY, HISTORY_SCHEMA),
        BQ_TABLE_CONTACT_ROLES: upload_bq(contact_roles_df, BQ_TABLE_CONTACT_ROLES, CONTACT_ROLE_SCHEMA),
    }
    if not splits_upload.empty:
        row_counts[BQ_TABLE_SPLITS] = upload_bq(splits_upload, BQ_TABLE_SPLITS, SPLITS_SCHEMA)
    else:
        print(f"  Skipping {BQ_TABLE_SPLITS} — no split data fetched")

    print("\n[10/10] Uploading Gong conversations to BigQuery...")
    if not gong_df.empty:
        row_counts[BQ_TABLE_GONG] = upload_bq(gong_df, BQ_TABLE_GONG, GONG_SCHEMA)
    else:
        print(f"  Skipping {BQ_TABLE_GONG} — no data fetched")

    print("\nBigQuery row counts:")
    for table_name, rows in row_counts.items():
        table_ref = f"{GCP_PROJECT}.{BQ_DATASET}.{table_name}"
        print(f"  {table_ref}: {rows}")

    print(f"\nExport complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    main()

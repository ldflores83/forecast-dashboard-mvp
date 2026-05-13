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
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from simple_salesforce import Salesforce
from google.cloud import bigquery

load_dotenv()

# ── AUTH ──────────────────────────────────────────────────────────────────────
# Set SALESFORCE_SESSION_ID in .env (F12 → Application → Cookies → sid)
SESSION_ID   = os.environ.get("SALESFORCE_SESSION_ID", "")
INSTANCE     = "qad.my.salesforce.com"

# ── CONFIG ────────────────────────────────────────────────────────────────────
FISCAL_YEAR  = 2027
OUTPUT_FILE  = "dashboard_export.csv"
GCP_PROJECT  = "forecast-dashboard-mvp"
BQ_DATASET   = "forecast_data"
BQ_TABLE     = "opportunities_fy2027"
CREDENTIALS  = os.path.join(
    os.path.dirname(__file__),
    "../credentials/forecast-dashboard-mvp-724e09b0b17a.json"
)

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
    'New Site':              'Net New',
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
    'Qualifying':              'Early',
    'Evaluation & Alignment':  'Early',
    'Solution Exploration':    'Mid',
    'Proposal & Negotiation':  'Mid',
    'Verbal Selection':        'Late',
    'Finalizing Contracts':    'Late',
    'Sales Ready':             'Renewal',
    'Dev Assigned':            'Renewal',
    'Negotiation':             'Renewal',
    'Closed-Won':              'Closed',
    'Closed-Lost':             'Closed',
}


# ── SOQL ──────────────────────────────────────────────────────────────────────
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
SOQL = f"""
SELECT
    Id,
    Name,
    AccountId,
    Account.Name,
    Account.BillingCountry,
    Account.Type,
    Account.ERP_Customer_Base__c,
    Account.SC_Customer_Base__c,
    Account.Global_HQ_18_ID__c,
    Account.Site_Type__c,

    StageName,
    Type,
    CloseDate,
    FiscalYear,
    FiscalQuarter,
    IsClosed,
    IsWon,
    Probability,
    LeadSource,
    CreatedDate,

    Prior_Contract_End_Date__c,
    ATR_Value__c,
    Total_Bookings_Net__c,
    Solutions_Rev_ACV_Net__c,
    Substage__c,
    Primary_Opp_Value_Stream__c,
    Reason_LQ_Q__c,
    Reason_LQ_Q_Description__c,
    Cancellation_Reason__c,

    NextStep,
    Description,
    LastActivityDate,
    LastStageChangeDate,
    Push_Count_FQ__c,
    Touch_Back_Date__c,
    QAD_Status__c,
    OwnerId,
    Owner.Name,
    Owner.Business_Unit__c

FROM Opportunity
WHERE FiscalYear = {FISCAL_YEAR}
AND Primary_Opp_Value_Stream__c != 'Redzone'
ORDER BY CloseDate ASC
"""


# ── HELPERS ───────────────────────────────────────────────────────────────────
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


# ── CONNECT ───────────────────────────────────────────────────────────────────
def connect_sf():
    if not SESSION_ID:
        raise RuntimeError("SALESFORCE_SESSION_ID is not set. Add it to .env or export it.")
    print("  Connecting to Salesforce...")
    sf = Salesforce(instance=INSTANCE, session_id=SESSION_ID)
    print(f"  Connected → {sf.sf_instance}")
    return sf


# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_opportunities(sf):
    print("  Running SOQL query...")
    result  = sf.query_all(SOQL)
    records = [flatten_record(r) for r in result["records"]]
    df = pd.DataFrame(records)
    print(f"  Fetched {len(df)} opportunities")
    return df


# ── TRANSFORM ─────────────────────────────────────────────────────────────────
def transform(df):
    print("  Transforming...")

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
        "Owner_Name":                  "Owner_Name",
        "Owner_Business_Unit__c":      "Owner_BU",
        # Custom fields — strip __c suffix for cleaner names
        "Prior_Contract_End_Date__c":  "PCED",
        "ATR_Value__c":                "ATR_Value",
        "Total_Bookings_Net__c":       "ACV",
        "Solutions_Rev_ACV_Net__c":    "Solutions_ACV",
        "Substage__c":                 "Substage",
        "Primary_Opp_Value_Stream__c": "BU",
        "Reason_LQ_Q__c":              "Loss_Reason",
        "Reason_LQ_Q_Description__c":  "Loss_Details",
        "Cancellation_Reason__c":      "Cancellation_Reason",
        # Signal fields (new)
        "NextStep":                    "Next_Step",
        "LastActivityDate":            "Last_Activity_Date",
        "LastStageChangeDate":         "Last_Stage_Change_Date",
        "Push_Count_FQ__c":            "Push_Count",
        "Touch_Back_Date__c":          "Touch_Back_Date",
        "QAD_Status__c":               "QAD_Status",
        # Standard SF fields kept as-is (views reference these names):
        # Id, Name, AccountId, StageName, Type, FiscalYear, FiscalQuarter,
        # IsClosed, IsWon, CloseDate, Probability, LeadSource, CreatedDate, OwnerId
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

    # Fiscal quarter (PCED-based for renewals)
    df["FiscalQuarter"] = df.apply(assign_quarter, axis=1)

    # BU — normalize to match view filter values ('ERP BU', 'Supply Chain BU')
    BU_SUFFIX_MAP = {
        'ERP':           'ERP BU',
        'Supply Chain':  'Supply Chain BU',
        'Redzone':       'Redzone BU',
    }
    df["BU"] = df["BU"].map(BU_SUFFIX_MAP).fillna(df["BU"])

    # Category — Solutions vs Services vs Other (mirrors SF reporting logic)
    def get_category(opp_type):
        if pd.isna(opp_type):
            return "Other"
        t = str(opp_type).strip()
        if t in ("Services", "Professional Services"):
            return "Services"
        if t in ("Channel", "Solutions Channel"):
            return "Channel"
        return "Solutions"

    df["Category"]   = df["Type"].apply(get_category)
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
    df["Days_In_Stage"] = 0

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


# ── FILTER ────────────────────────────────────────────────────────────────────
def apply_filters(df):
    print("  Applying exclusion filters...")
    before = len(df)

    # Exclude bad substages
    df = df[~df["Substage"].isin(EXCL_SUBSTAGE)]

    # Exclude Amendment and zzz opps
    for pattern in EXCL_NAME:
        df = df[~df["Name"].str.contains(pattern, case=False, na=False)]

    after = len(df)
    print(f"  Filtered {before - after} rows → {after} remaining")
    return df.copy()


# ── PREVIEW ───────────────────────────────────────────────────────────────────
def print_preview(df):
    print()
    print(f"  {'─'*50}")
    print(f"  Total opps     : {len(df)}")
    won  = df[df["Is_Won"]]
    lost = df[df["Is_Lost"]]
    open_ = df[df["Is_Open"]]
    print(f"  Closed-Won     : {len(won):>4}  ACV: ${won['ACV_Final'].sum()/1e6:.1f}M")
    print(f"  Closed-Lost    : {len(lost):>4}  ACV: ${lost['ACV_Final'].sum()/1e6:.1f}M")
    print(f"  Open pipeline  : {len(open_):>4}  ACV: ${open_['ACV_Final'].sum()/1e6:.1f}M")
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
    print(f"  {'─'*50}")


# ── SAVE CSV ──────────────────────────────────────────────────────────────────
def save_csv(df):
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"  Saved → {OUTPUT_FILE} ({len(df)} rows, {len(df.columns)} cols)")


# ── UPLOAD TO BIGQUERY ────────────────────────────────────────────────────────
def upload_bq(df):
    print("  Uploading to BigQuery...")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS
    client    = bigquery.Client(project=GCP_PROJECT)
    table_ref = f"{GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

    job_config = bigquery.LoadJobConfig(
        write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE,
        autodetect        = True,
    )

    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()

    table = client.get_table(table_ref)
    print(f"  Uploaded → {table_ref} ({table.num_rows} rows)")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"  Revenue Intelligence Export — FY{FISCAL_YEAR} (full year)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n[1/5] Connecting to Salesforce...")
    sf = connect_sf()

    print("\n[2/5] Fetching opportunities...")
    df_raw = fetch_opportunities(sf)

    print("\n[3/5] Transforming...")
    df = transform(df_raw)

    print("\n[4/5] Filtering...")
    df = apply_filters(df)
    print_preview(df)

    print("\n[5/5] Saving...")
    save_csv(df)
    upload_bq(df)

    print(f"\nExport complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
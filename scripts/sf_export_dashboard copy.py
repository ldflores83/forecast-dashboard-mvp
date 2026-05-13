"""
Salesforce CSV Export — Revenue Intelligence Dashboard
Auth: SID-based (refresh browser cookie every ~2hrs)
Output: Single combined CSV ready for dashboard upload
  - dashboard_export.csv (opportunities + account fields joined)
"""

from simple_salesforce import Salesforce
import pandas as pd
from datetime import datetime
from google.cloud import bigquery
import os

# ── AUTH ──────────────────────────────────────────────────────────────────────
SESSION_ID = "REMOVED_SF_SESSION_ID"  # F12 → Application → Cookies → copy "sid" value
sf = Salesforce(instance="qad.my.salesforce.com", session_id=SESSION_ID)

# ── CONFIG ────────────────────────────────────────────────────────────────────
FISCAL_YEAR    = 2027
OUTPUT_FILE    = "dashboard_export.csv"
GCP_PROJECT    = "forecast-dashboard-mvp"
BQ_DATASET     = "forecast_data"
BQ_TABLE       = "opportunities_fy2027"
CREDENTIALS    = os.path.join(os.path.dirname(__file__), 
                 "../credentials/forecast-dashboard-mvp-724e09b0b17a.json")

print(f"Revenue Intelligence Export — FY{FISCAL_YEAR} (full year)")
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ── HELPER ────────────────────────────────────────────────────────────────────
def flatten_record(r):
    flat = {}
    for k, v in r.items():
        if k == "attributes":
            continue
        if isinstance(v, dict) and "attributes" in v:
            for sub_k, sub_v in v.items():
                if sub_k != "attributes":
                    flat[f"{k}_{sub_k}"] = sub_v
        else:
            flat[k] = v
    return flat

# ── OPPORTUNITIES ─────────────────────────────────────────────────────────────
print("\n[1/4] Fetching opportunities...")

OPP_FIELDS = ",".join([
    "Id", "Name", "AccountId", "Account.Name", "Account.BillingCountry",
    "Account.Type", "Account.Status__c", "Account.ERP_Customer_Base__c",
    "Account.SC_Customer_Base__c", "Account.Global_HQ_18_ID__c",
    "OwnerId", "Owner.Name",
    "StageName", "Substage__c",
    "ForecastCategory", "ForecastCategoryName",
    "IsClosed", "IsWon",
    "CloseDate", "FiscalYear", "FiscalQuarter", "Fiscal",
    "Type", "LeadSource", "Probability",
    "Primary_Opp_Value_Stream__c",
    "Opportunity_Owner_Business_Group__c",
    "Category__c",
    "Opp_Owner_Region__c",
    "Channel_Deal__c",
    "Amount",
    "Wt_Total_Bookings_Net__c",
    "Solutions_Rev_ACV_Net__c",
    "Wt_Solutions_Rev_ACV_Net__c",
    "ATR_Value__c",
    "Prior_Contract_End_Date__c",
    "Churn_Value__c",
    "Pending_Cancellation__c",
    "Reason_LQ_Q__c",
    "Reason_LQ_Q_Description__c",
    "Competitor__c",
    "Cancellation_Reason__c",
    "CreatedDate",
    "LastModifiedDate",
])

opp_soql = f"""
    SELECT {OPP_FIELDS}
    FROM Opportunity
    WHERE FiscalYear = {FISCAL_YEAR}
    ORDER BY CloseDate ASC
""".strip()

try:
    result      = sf.query_all(opp_soql)
    opp_records = result["records"]
    print(f"  Found: {len(opp_records)} opportunities")
except Exception as e:
    print(f"  ERROR: {e}")
    opp_records = []

df = pd.DataFrame([flatten_record(r) for r in opp_records])

# ── DERIVED COLUMNS ───────────────────────────────────────────────────────────
print("\n[2/4] Computing derived columns...")

# BU from Primary_Opp_Value_Stream__c
df["BU"] = df["Primary_Opp_Value_Stream__c"].map({
    "ERP":          "ERP BU",
    "Supply Chain": "Supply Chain BU",
    "MO":           "ERP BU",       # deprecated — map to ERP
    "Redzone":      "Redzone BU",
}).fillna("Other")

# Sales motion from Type — mapped to QAD actual values
motion_map = {
    "New Customer":         "Net New",
    "New Division":         "Net New",
    "New Site":             "Net New",
    "New Users":            "Net New",
    "New Modules":          "Expansion",
    "Upgrade":              "Expansion",
    "ESS":                  "Expansion",
    "Sub Renewal":          "Renewal",
    "Maint Renewal":        "Renewal",
    "Renewal":              "Renewal",
    "Conversion":           "Migration",
    "Rollout":              "Migration",
    "Ad Hoc":               "Other",
    "Services Only":        "Other",
    "Admin $0":             "Other",
    "Concession":           "Other",
    "Mid-Term Cancellation":"Churn",
}
df["Sales_Motion"] = df["Type"].map(motion_map).fillna("Other")

# Stage group — mapped to QAD actual stage names
def stage_group(stage):
    if stage in {"Development", "Sales Ready", "Qualifying", "Stalled"}:
        return "Early"
    if stage in {"Solution Exploration", "Evaluation & Alignment",
                 "Proposal & Negotiation", "Awaiting Signature"}:
        return "Active"
    if stage in {"Renewal Pending", "Renewal Validation",
                 "Renewal Negotiation", "Renewal Confirmed",
                 "Pending Renewal"}:
        return "Renewal"
    if stage == "Closed-Won":  return "Closed Won"
    if stage == "Closed-Lost": return "Closed Lost"
    return "Other"

df["Stage_Group"] = df["StageName"].apply(stage_group)

# ACV fallback strategy — confirmed via multiyear dashboard analysis:
# - ACV (Wt_Solutions_Rev_ACV_Net__c) is the primary field for all motions
# - For Sales opps (Net New, Expansion, Migration): fallback to Amount
# - For Renewal opps: fallback to Total_Bookings_Net (new ACV, 98% populated on won)
#   Never use ATR_Value or Amount for renewals — those are TCV (multi-year total)
#   ATR_Value is reserved as "previous annual value at risk" — baseline only

def compute_acv(row):
    acv = pd.to_numeric(row.get("Wt_Solutions_Rev_ACV_Net__c"), errors="coerce")
    if pd.notna(acv) and acv != 0:
        return acv
    motion    = row.get("Sales_Motion", "")
    is_closed = row.get("IsClosed", False)
    is_won    = row.get("IsWon", False)
    if motion == "Renewal":
        # Won renewals: use Total_Bookings_Net (new ACV, 98% populated)
        # Lost renewals: use ATR_Value as churn proxy (annual value that was at risk)
        if is_closed and not is_won:
            atr = pd.to_numeric(row.get("ATR_Value__c"), errors="coerce")
            return atr if pd.notna(atr) else 0
        fallback = pd.to_numeric(row.get("Wt_Total_Bookings_Net__c"), errors="coerce")
        return fallback if pd.notna(fallback) else 0
    else:
        # Sales opps: use Amount as fallback (no TCV issue for non-renewal)
        fallback = pd.to_numeric(row.get("Amount"), errors="coerce")
        return fallback if pd.notna(fallback) else 0

df["ACV"] = df.apply(compute_acv, axis=1)

# ATR_Annual — previous annual value at risk (renewals baseline), computed before rename
df["ATR_Annual"] = pd.to_numeric(df["ATR_Value__c"], errors="coerce").fillna(0)

# Status flags
df["Is_Won"]  = df["IsWon"] == True
df["Is_Lost"] = (df["IsClosed"] == True) & (df["IsWon"] == False)
df["Is_Open"] = df["IsClosed"] == False

# Channel flag — separate CS-sourced from Sales-sourced
df["Is_CS_Sourced"] = df["Opportunity_Owner_Business_Group__c"] == "Customer Success"
# Is_Channel now comes directly from Channel_Deal__c field (renamed above)

# ── CLEAN UP COLUMNS ──────────────────────────────────────────────────────────
# Rename nested account fields for clarity
rename_map = {
    "Account_Name":                "Account_Name",
    "Account_BillingCountry":      "Country",
    "Account_Type":                "Account_Type",
    "Account_Status__c":           "Account_Status",
    "Account_ERP_Customer_Base__c":"Account_ERP",
    "Account_SC_Customer_Base__c": "Account_SC",
    "Account_Global_HQ_18_ID__c":  "HQ_ID",
    "Owner_Name":                  "Owner_Name",
    "Wt_Solutions_Rev_ACV_Net__c": "ACV_Weighted",
    "Solutions_Rev_ACV_Net__c":    "ACV_Gross",
    "Wt_Total_Bookings_Net__c":    "Total_Bookings_Net",
    "Category__c":                 "Category",
    "Opp_Owner_Region__c":         "Opp_Owner_Region",
    "Channel_Deal__c":             "Is_Channel",
    "Reason_LQ_Q__c":              "Loss_Reason",
    "Reason_LQ_Q_Description__c":  "Loss_Details",
    "Opportunity_Owner_Business_Group__c": "Business_Group",
    "Primary_Opp_Value_Stream__c": "Value_Stream",
    "Prior_Contract_End_Date__c":  "PCED",
    "ATR_Value__c":                "ATR_Value",
    "Churn_Value__c":              "Churn_Value",
    "Pending_Cancellation__c":     "Pending_Cancellation",
    "Cancellation_Reason__c":      "Cancellation_Reason",
    "ForecastCategoryName":        "Forecast_Category",
    "Substage__c":                 "Substage",
}
df = df.rename(columns=rename_map)

# Final column order — clean for dashboard consumption
FINAL_COLS = [
    # Identity
    "Id", "Name", "AccountId", "Account_Name", "Country",
    "HQ_ID", "OwnerId", "Owner_Name",
    # Classification
    "BU", "Value_Stream", "Business_Group", "Sales_Motion",
    "Category", "Opp_Owner_Region", "Is_Channel",
    "Is_CS_Sourced",
    # Stage & forecast
    "StageName", "Stage_Group", "Substage",
    "Forecast_Category", "ForecastCategory",
    "IsClosed", "Is_Won", "Is_Lost", "Is_Open",
    "Probability",
    # Dates
    "CloseDate", "PCED", "FiscalYear", "FiscalQuarter", "Fiscal",
    "CreatedDate", "LastModifiedDate",
    # Financials
    "ACV", "ACV_Weighted", "ACV_Gross", "Total_Bookings_Net",
    "Amount", "ATR_Value", "ATR_Annual", "Churn_Value",
    "Pending_Cancellation",
    # Deal attributes
    "Type", "LeadSource",
    # Loss/churn
    "Loss_Reason", "Loss_Details", "Cancellation_Reason",
    "Competitor__c",
    # Account flags
    "Account_Type", "Account_Status", "Account_ERP", "Account_SC",
]

# Only keep columns that exist
final_cols = [c for c in FINAL_COLS if c in df.columns]
df_final   = df[final_cols]

# ── PREVIEW ───────────────────────────────────────────────────────────────────
print(f"  Total opps    : {len(df_final)}")
print(f"  Closed-Won    : {df_final['Is_Won'].sum()}  |  ACV: ${df_final[df_final['Is_Won']]['ACV'].sum():,.0f}")
print(f"  Closed-Lost   : {df_final['Is_Lost'].sum()}")
print(f"  Open pipeline : {df_final['Is_Open'].sum()}  |  ACV: ${df_final[df_final['Is_Open']]['ACV'].sum():,.0f}")
print(f"\n  BU split (open pipeline):")
bu_open = df_final[df_final["Is_Open"]].groupby("BU")["ACV"].agg(["sum","count"])
for bu, row in bu_open.iterrows():
    print(f"    {bu:<25}: ${row['sum']:>12,.0f}  ({int(row['count'])} opps)")
print(f"\n  Sales motion (Closed-Won):")
motion_won = df_final[df_final["Is_Won"]].groupby("Sales_Motion")["ACV"].sum().sort_values(ascending=False)
for motion, val in motion_won.items():
    print(f"    {motion:<20}: ${val:>12,.0f}")
print(f"\n  CS-sourced opps: {df_final['Is_CS_Sourced'].sum()} ({df_final['Is_CS_Sourced'].mean()*100:.1f}% of total)")

# ── SUBSTAGE FILTER ──────────────────────────────────────────────────────────
# Exclude noise opps matching SF renewal dashboard filter logic
# Matches: Substage NOT IN (Combined, Credited, Closed-Duplicate, Junk)
#          AND Name NOT LIKE Amendment / zzz
EXCL_SUBSTAGE = ['Combined', 'Credited', 'Closed-Duplicate', 'Junk']
before_filter = len(df_final)
df_final = df_final[
    ~df_final["Substage"].isin(EXCL_SUBSTAGE) &
    ~df_final["Name"].str.contains("Amendment", case=False, na=False) &
    ~df_final["Name"].str.contains("zzz", case=False, na=False)
].copy()
removed = before_filter - len(df_final)
print(f"\n[3.5/5] Substage filter applied:")
print(f"  Before: {before_filter:,} opps")
print(f"  After:  {len(df_final):,} opps")
print(f"  Removed: {removed:,} opps (Combined/Credited/Duplicate/Junk/Amendment)")

# ── SAVE CSV ──────────────────────────────────────────────────────────────────
print(f"\n[4/5] Saving CSV...")
df_final.to_csv(OUTPUT_FILE, index=False)
print(f"  Saved -> {OUTPUT_FILE} ({len(df_final)} rows, {len(df_final.columns)} cols)")

# ── UPLOAD TO BIGQUERY ────────────────────────────────────────────────────────
print(f"\n[5/5] Uploading to BigQuery...")
try:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS
    bq_client  = bigquery.Client(project=GCP_PROJECT)
    table_ref  = f"{GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

    # Ensure boolean columns are proper bool type for BigQuery
    bool_cols = ["IsClosed","IsWon","Is_Won","Is_Lost","Is_Open",
                 "Is_CS_Sourced","Is_Channel","Account_ERP","Account_SC",
                 "Pending_Cancellation"]
    for col in bool_cols:
        if col in df_final.columns:
            df_final[col] = df_final[col].map(
                lambda x: True if x in [True,"True","true","1",1] else False
            )

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    job = bq_client.load_table_from_dataframe(df_final, table_ref, job_config=job_config)
    job.result()  # Wait for completion
    table = bq_client.get_table(table_ref)
    print(f"  Uploaded -> {table_ref}")
    print(f"  Rows in BigQuery: {table.num_rows:,}")
except Exception as e:
    print(f"  BigQuery upload failed: {e}")
    print(f"  CSV still available locally at {OUTPUT_FILE}")

print(f"\nExport complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
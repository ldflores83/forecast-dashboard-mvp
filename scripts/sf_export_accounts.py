"""
scripts/sf_export_accounts.py
Salesforce Account → BigQuery export for Revenue Intelligence.

Auth:    SID-based (refresh from browser cookies ~every 2hrs)
Output:  BQ table forecast-dashboard-mvp.forecast_data.accounts

Universe:
  customer_base    — Accounts where ERP_Customer_Base = TRUE or SC_Customer_Base = TRUE
  active_pipeline  — Non-customer-base accounts with at least one open opportunity

Deduplication: an account in both sets keeps universe_reason = 'customer_base'.
Write mode: WRITE_TRUNCATE (full refresh every run).
"""

import os
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
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
SESSION_ID = os.environ.get("SALESFORCE_SESSION_ID", "")
INSTANCE   = "qad.my.salesforce.com"

# -- CONFIG --------------------------------------------------------------------
GCP_PROJECT = "forecast-dashboard-mvp"
BQ_DATASET  = "forecast_data"
BQ_TABLE    = "accounts"

# -- FIELD LIST ----------------------------------------------------------------
ACCOUNT_FIELDS = [
    "Id",
    "Name",
    "Type",
    "Site_Type__c",
    "Status__c",
    "Region__c",
    "Account_Owner_Region_txt__c",
    "ERP_Customer_Base__c",
    "SC_Customer_Base__c",
    "Customer_Tier__c",
    "Primary_Vertical__c",
    "Primary_Sub_Vertical__c",
    "AnnualRevenue",
    "Global_HQ_Name__c",
    "Global_HQ_18_ID__c",
    "q_Score__c",
    "q_Trend__c",
    "q_Meetings_Booked__c",
    "q_Condition__c",
    "q_Visitor_Count__c",
    "Days_Since_Last_Activity_Qualified__c",
    "At_Risk__c",
    "Recurring_Rev_Customer_Base__c",
    "Has_Open_Opportunity__c",
    "Open_Opportunities_Count__c",
    "Target_Account_Status__c",
    "BillingLatitude",
    "BillingLongitude",
    "Whitespace_Gross_Potential__c",
]

# SF API name → BQ column name
FIELD_RENAME = {
    "Id":                                    "account_id",
    "Name":                                  "name",
    "Type":                                  "type",
    "Site_Type__c":                          "site_type",
    "Status__c":                             "status",
    "Region__c":                             "region",
    "Account_Owner_Region_txt__c":           "owner_region",
    "ERP_Customer_Base__c":                  "erp_customer_base",
    "SC_Customer_Base__c":                   "sc_customer_base",
    "Customer_Tier__c":                      "erp_customer_tier",
    "Primary_Vertical__c":                   "primary_vertical",
    "Primary_Sub_Vertical__c":               "primary_sub_vertical",
    "AnnualRevenue":                         "annual_revenue",
    "Global_HQ_Name__c":                     "global_hq_name",
    "Global_HQ_18_ID__c":                    "global_hq_id",
    "q_Score__c":                            "q_score",
    "q_Trend__c":                            "q_trend",
    "q_Meetings_Booked__c":                  "q_meetings_booked",
    "q_Condition__c":                        "q_condition",
    "q_Visitor_Count__c":                    "q_visitor_count",
    "Days_Since_Last_Activity_Qualified__c": "days_since_last_activity_qualified",
    "At_Risk__c":                            "at_risk",
    "Recurring_Rev_Customer_Base__c":        "recurring_rev_customer_base",
    "Has_Open_Opportunity__c":               "has_open_opportunity",
    "Open_Opportunities_Count__c":           "open_opportunities_count",
    "Target_Account_Status__c":              "target_account_status",
    "BillingLatitude":                       "billing_latitude",
    "BillingLongitude":                      "billing_longitude",
    "Whitespace_Gross_Potential__c":         "whitespace_gross_potential",
}

ACCOUNTS_SCHEMA = [
    bigquery.SchemaField("account_id",                          "STRING"),
    bigquery.SchemaField("name",                                "STRING"),
    bigquery.SchemaField("type",                                "STRING"),
    bigquery.SchemaField("site_type",                           "STRING"),
    bigquery.SchemaField("status",                              "STRING"),
    bigquery.SchemaField("region",                              "STRING"),
    bigquery.SchemaField("owner_region",                        "STRING"),
    bigquery.SchemaField("erp_customer_base",                   "BOOL"),
    bigquery.SchemaField("sc_customer_base",                    "BOOL"),
    bigquery.SchemaField("erp_customer_tier",                   "STRING"),
    bigquery.SchemaField("primary_vertical",                    "STRING"),
    bigquery.SchemaField("primary_sub_vertical",                "STRING"),
    bigquery.SchemaField("annual_revenue",                      "FLOAT"),
    bigquery.SchemaField("global_hq_name",                      "STRING"),
    bigquery.SchemaField("global_hq_id",                        "STRING"),
    bigquery.SchemaField("q_score",                             "FLOAT"),
    bigquery.SchemaField("q_trend",                             "STRING"),
    bigquery.SchemaField("q_meetings_booked",                   "FLOAT"),
    bigquery.SchemaField("q_condition",                         "STRING"),
    bigquery.SchemaField("q_visitor_count",                     "FLOAT"),
    bigquery.SchemaField("days_since_last_activity_qualified",  "FLOAT"),
    bigquery.SchemaField("at_risk",                             "BOOL"),
    bigquery.SchemaField("recurring_rev_customer_base",         "BOOL"),
    bigquery.SchemaField("has_open_opportunity",                "BOOL"),
    bigquery.SchemaField("open_opportunities_count",            "FLOAT"),
    bigquery.SchemaField("target_account_status",               "STRING"),
    bigquery.SchemaField("billing_latitude",                    "FLOAT"),
    bigquery.SchemaField("billing_longitude",                   "FLOAT"),
    bigquery.SchemaField("whitespace_gross_potential",          "FLOAT"),
    bigquery.SchemaField("universe_reason",                     "STRING"),
    bigquery.SchemaField("exported_at",                         "TIMESTAMP"),
]

BQ_COLUMNS = [f.name for f in ACCOUNTS_SCHEMA]


# -- HELPERS -------------------------------------------------------------------
def object_fields(sf, object_name):
    """Return the set of field API names available on a Salesforce object."""
    desc = getattr(sf, object_name).describe()
    return {field["name"] for field in desc["fields"]}


def keep_available_fields(requested, available, label):
    fields  = [f for f in requested if f in available]
    missing = [f for f in requested if f not in available]
    if missing:
        print(f"  Skipping unavailable {label} fields: {', '.join(missing)}")
    return fields


def flatten_record(rec):
    """Flatten nested SF record dict (handles Account.Name etc.)."""
    flat = {}
    for k, v in rec.items():
        if isinstance(v, dict) and "attributes" in v:
            for nk, nv in v.items():
                if nk != "attributes":
                    flat[f"{k}_{nk}"] = nv
        elif k != "attributes":
            flat[k] = v
    return flat


# -- CONNECT -------------------------------------------------------------------
def connect_sf():
    if not SESSION_ID:
        raise RuntimeError("SALESFORCE_SESSION_ID is not set. Add it to .env or export it.")
    print("  Connecting to Salesforce...")
    sf = Salesforce(instance=INSTANCE, session_id=SESSION_ID)
    print(f"  Connected: {sf.sf_instance}")
    return sf


# -- FETCH ---------------------------------------------------------------------
def fetch_accounts(sf):
    print("  Inspecting Account object fields...")
    available     = object_fields(sf, "Account")
    fields        = keep_available_fields(ACCOUNT_FIELDS, available, "Account")
    select_clause = ", ".join(fields)

    # Query 1 — customer base
    print("  Running SOQL query 1 — customer base (ERP or SC)...")
    soql_base = f"""
        SELECT {select_clause}
        FROM Account
        WHERE ERP_Customer_Base__c = TRUE OR SC_Customer_Base__c = TRUE
    """
    result_base    = sf.query_all(soql_base)
    records_base   = [flatten_record(r) for r in result_base["records"]]
    for r in records_base:
        r["universe_reason"] = "customer_base"
    print(f"  Fetched {len(records_base)} customer base accounts")

    # Query 2 — active pipeline (non-customer-base with open opps)
    print("  Running SOQL query 2 — active pipeline (non-customer-base with open opps)...")
    soql_pipeline = f"""
        SELECT {select_clause}
        FROM Account
        WHERE ERP_Customer_Base__c = FALSE
          AND SC_Customer_Base__c = FALSE
          AND Id IN (SELECT AccountId FROM Opportunity WHERE IsClosed = FALSE)
    """
    result_pipeline  = sf.query_all(soql_pipeline)
    records_pipeline = [flatten_record(r) for r in result_pipeline["records"]]
    for r in records_pipeline:
        r["universe_reason"] = "active_pipeline"
    print(f"  Fetched {len(records_pipeline)} active pipeline accounts")

    return records_base, records_pipeline


# -- TRANSFORM -----------------------------------------------------------------
def transform(records_base, records_pipeline):
    print("  Building combined DataFrame...")
    df_base     = pd.DataFrame(records_base)
    df_pipeline = pd.DataFrame(records_pipeline)
    df_all      = pd.concat([df_base, df_pipeline], ignore_index=True)
    print(f"  Combined: {len(df_base)} + {len(df_pipeline)} = {len(df_all)} rows before dedup")

    # Deduplication — customer_base is first in concat so drop_duplicates keeps it
    before_dedup = len(df_all)
    df_all = df_all.drop_duplicates(subset="Id", keep="first")
    dupes  = before_dedup - len(df_all)
    if dupes:
        print(f"  Deduped {dupes} accounts that appeared in both sets (kept customer_base)")

    # Rename SF API names → BQ column names
    df_all = df_all.rename(columns=FIELD_RENAME)

    # Ensure every BQ schema column exists
    for col in BQ_COLUMNS:
        if col not in df_all.columns:
            df_all[col] = None

    # Bool coercion
    for col in ["erp_customer_base", "sc_customer_base", "at_risk",
                "recurring_rev_customer_base", "has_open_opportunity"]:
        df_all[col] = df_all[col].fillna(False).astype(bool)

    # Float coercion
    for col in ["annual_revenue", "q_score", "q_meetings_booked", "q_visitor_count",
                "days_since_last_activity_qualified", "open_opportunities_count",
                "billing_latitude", "billing_longitude", "whitespace_gross_potential"]:
        df_all[col] = pd.to_numeric(df_all[col], errors="coerce")

    # Add exported_at timestamp
    df_all["exported_at"] = datetime.now(timezone.utc)

    # Return only schema columns in declared order
    df_all = df_all[BQ_COLUMNS]
    print(f"  Transformed: {len(df_all)} rows, {len(df_all.columns)} columns")
    return df_all


# -- PREVIEW -------------------------------------------------------------------
def print_preview(df):
    print()
    print(f"  {'-'*50}")
    print(f"  Total accounts : {len(df)}")
    for reason, grp in df.groupby("universe_reason"):
        print(f"    {reason:<22}: {len(grp)}")
    print(f"  ERP customer base  : {int(df['erp_customer_base'].sum())}")
    print(f"  SC customer base   : {int(df['sc_customer_base'].sum())}")
    top_verticals = df["primary_vertical"].dropna().value_counts().head(5)
    if not top_verticals.empty:
        print(f"  Top primary verticals:")
        for v, c in top_verticals.items():
            print(f"    {str(v):<30}: {c}")
    print(f"  {'-'*50}")


# -- UPLOAD TO BIGQUERY --------------------------------------------------------
def upload_bq(df):
    print(f"  Uploading to BigQuery...")
    client    = bigquery.Client(project=GCP_PROJECT)
    table_ref = f"{GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=ACCOUNTS_SCHEMA,
    )

    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()

    table = client.get_table(table_ref)
    print(f"  Uploaded: {table_ref} ({table.num_rows} rows)")
    return table.num_rows


# -- MAIN ----------------------------------------------------------------------
def main():
    print("=" * 60)
    print(f"  Accounts Export — Revenue Intelligence")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n[1/5] Connecting to Salesforce...")
    sf = connect_sf()

    print("\n[2/5] Fetching accounts...")
    records_base, records_pipeline = fetch_accounts(sf)

    print("\n[3/5] Transforming...")
    df = transform(records_base, records_pipeline)
    print_preview(df)

    print("\n[4/5] Uploading to BigQuery...")
    row_count = upload_bq(df)

    print(f"\n[5/5] Done.")
    print(f"  {GCP_PROJECT}.{BQ_DATASET}.{BQ_TABLE}: {row_count} rows written")
    print(f"  Export complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    main()

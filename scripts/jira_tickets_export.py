"""
jira_tickets_export.py
Exports Jira support tickets from QAD BigQuery for accounts
that have open opportunities in FY2027.

Auth: uses luis.flores@qad.com gcloud profile
      gcloud config set account luis.flores@qad.com
      gcloud auth application-default login
"""

import os
import pandas as pd
from google.cloud import bigquery
from datetime import datetime

QAD_PROJECT  = "qad-edp-customersuccess"
QAD_DATASET  = "ds_Jira_Reporting"
QAD_VIEW     = "vw_planhat_support_tickets"
OPP_CSV      = "dashboard_export.csv"
OUTPUT_FILE  = "jira_tickets_export.csv"
DATE_FILTER  = "2025-01-01"

print("Jira Tickets Export")
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

print("\n[1/3] Loading AccountIds from dashboard_export.csv...")
if not os.path.exists(OPP_CSV):
    print(f"  ERROR: {OPP_CSV} not found. Run sf_export_dashboard.py first.")
    exit(1)

df_opps     = pd.read_csv(OPP_CSV)
account_ids = df_opps['AccountId'].dropna().unique().tolist()
print(f"  Unique accounts: {len(account_ids):,}")
ids_quoted  = ", ".join([f"'{aid}'" for aid in account_ids])

print(f"\n[2/3] Querying QAD BigQuery ({QAD_PROJECT})...")
print(f"  View: {QAD_DATASET}.{QAD_VIEW}")
print(f"  Date filter: created >= {DATE_FILTER}")
print(f"  Note: standard read query - no external project references.")

try:
    bq_client = bigquery.Client(project=QAD_PROJECT)

    sql = f"""
        SELECT
            issue_id,
            issue_key,
            issue_type_name,
            summary,
            priority_name,
            issue_status_name,
            resolution_name,
            created,
            updated,
            resolution_date,
            assignee_name,
            escalated_10182                          AS is_escalated,
            escalation_type_21900                   AS escalation_type,
            customer_service_delivery_manager_20008 AS csdm,
            reporter_region,
            environment_type_21700                  AS environment_type,
            customer_type,
            salesforce_account_id,
            product_category
        FROM `{QAD_PROJECT}.{QAD_DATASET}.{QAD_VIEW}`
        WHERE salesforce_account_id IN ({ids_quoted})
          AND created >= '{DATE_FILTER}'
        ORDER BY created DESC
    """

    print("\n  Running query...")
    df_tickets = bq_client.query(sql).result().to_dataframe()
    print(f"  Tickets found: {len(df_tickets):,}")

    if len(df_tickets) == 0:
        print("\n  WARNING: No tickets returned.")
        print(f"  Sample AccountId from opps: {account_ids[0]}")
        print("  Check if ID formats match between opps and Jira view.")
        exit(1)

    accts = df_tickets['salesforce_account_id'].nunique()
    print(f"  Accounts with tickets: {accts:,} of {len(account_ids):,} ({accts/len(account_ids)*100:.1f}%)")

    print(f"\n  Status distribution (top 8):")
    for s, c in df_tickets['issue_status_name'].value_counts().head(8).items():
        print(f"    {str(s):<35}: {c:>6,}")

    print(f"\n  Priority distribution:")
    for p, c in df_tickets['priority_name'].value_counts().head(5).items():
        print(f"    {str(p):<35}: {c:>6,}")

    escalated = df_tickets[df_tickets['is_escalated'].notna() & (df_tickets['is_escalated'] != '')]
    print(f"\n  Escalated: {len(escalated):,} ({len(escalated)/len(df_tickets)*100:.1f}%)")
    print(f"  Date range: {df_tickets['created'].min()} to {df_tickets['created'].max()}")

except Exception as e:
    print(f"\n  ERROR: {e}")
    print("\n  Authenticate with: gcloud config set account luis.flores@qad.com")
    print("  Then: gcloud auth application-default login")
    exit(1)

print(f"\n[3/3] Saving to {OUTPUT_FILE}...")
df_tickets.to_csv(OUTPUT_FILE, index=False)
print(f"  Saved: {len(df_tickets):,} rows")
print(f"  File:  {os.path.abspath(OUTPUT_FILE)}")
print(f"\nExport complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"\nNext step: python scripts/jira_tickets_upload.py")
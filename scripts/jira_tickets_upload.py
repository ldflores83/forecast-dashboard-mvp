"""
jira_tickets_upload.py
Uploads jira_tickets_export.csv to forecast-dashboard-mvp BigQuery.

Run AFTER jira_tickets_export.py has generated the CSV.
Uses personal GCP credentials (not QAD).

Usage:
    python scripts/jira_tickets_upload.py
"""

import os
import pandas as pd
from google.cloud import bigquery
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
PROJECT     = "forecast-dashboard-mvp"
DATASET     = "forecast_data"
TABLE       = "jira_tickets"
INPUT_FILE  = "jira_tickets_export.csv"
CREDENTIALS = os.path.join(os.path.dirname(__file__),
              "../credentials/forecast-dashboard-mvp-724e09b0b17a.json")
# ─────────────────────────────────────────────────────────────────────────────

print("Jira Tickets Upload → forecast-dashboard-mvp BQ")
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ── Load CSV ──────────────────────────────────────────────────────────────────
print(f"\n[1/2] Loading {INPUT_FILE}...")

if not os.path.exists(INPUT_FILE):
    print(f"  ERROR: {INPUT_FILE} not found.")
    print(f"  Run jira_tickets_export.py first.")
    exit(1)

df = pd.read_csv(INPUT_FILE)
print(f"  Rows: {len(df):,}")
print(f"  Columns: {list(df.columns)}")

# Parse date columns
date_cols = ['created','updated','resolution_date']
for col in date_cols:
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

print(f"  Date range: {df['created'].min()} → {df['created'].max()}")
print(f"  Unique accounts: {df['salesforce_account_id'].nunique():,}")

# ── Upload to BQ ──────────────────────────────────────────────────────────────
print(f"\n[2/2] Uploading to {PROJECT}.{DATASET}.{TABLE}...")

try:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS
    bq_client  = bigquery.Client(project=PROJECT)
    table_ref  = f"{PROJECT}.{DATASET}.{TABLE}"

    job_config = bigquery.LoadJobConfig(
        write_disposition = "WRITE_TRUNCATE",
        autodetect        = True,
    )

    job = bq_client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()

    table = bq_client.get_table(table_ref)
    print(f"  Uploaded → {table_ref}")
    print(f"  Rows in BQ: {table.num_rows:,}")

except Exception as e:
    print(f"  ERROR: {e}")
    exit(1)

print(f"\nUpload complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()
print("Next steps:")
print("  1. Run setup_views.py to create vw_account_health view")
print("  2. Re-deploy Cloud Function to expose ticket data via API")
"""
setup_digest.py
Creates the digest_snapshots table in forecast-dashboard-mvp.forecast_data.
Run once before using digest.py.

Usage:
    python scripts/setup_digest.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import bigquery

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / '.env')
_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
if _creds and not Path(_creds).is_absolute():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_ROOT / _creds)

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"
TABLE   = "digest_snapshots"

client = bigquery.Client(project=PROJECT)

schema = [
    bigquery.SchemaField("week_key",      "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("digest_text",   "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("hero_json",     "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("slack_sent",    "BOOL",      mode="NULLABLE"),
    bigquery.SchemaField("generated_at",  "TIMESTAMP", mode="REQUIRED"),
]

table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
table_obj = bigquery.Table(table_ref, schema=schema)
table_obj.time_partitioning = bigquery.TimePartitioning(
    type_=bigquery.TimePartitioningType.DAY,
    field="generated_at",
)

print(f"Creating table {table_ref} ...")
table_obj = client.create_table(table_obj, exists_ok=True)
print(f"  OK    {table_obj.full_table_id}")
print(f"  Partition field: generated_at (DAY)")
print(f"\nDone. Validate in BQ console:")
print(f"  https://console.cloud.google.com/bigquery?project={PROJECT}&ws=!1m5!1m4!4m3!1s{PROJECT}!2s{DATASET}!3s{TABLE}")

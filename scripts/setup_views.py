"""
setup_views.py
Creates all Revenue Intelligence BQ views in one shot.
Run this whenever the view definitions change.

Usage:
    python setup_views.py

To point to a different project (e.g. QAD production):
    Edit PROJECT and DATASET below, then re-run.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import bigquery

# Load .env from project root, then resolve credentials path to absolute
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / '.env')
_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
if _creds and not Path(_creds).is_absolute():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_ROOT / _creds)

# ── CONFIG ────────────────────────────────────────────────────────────────────
PROJECT   = "forecast-dashboard-mvp"
DATASET   = "forecast_data"
SQL_DIR   = os.path.join(os.path.dirname(__file__), "../sql")

VIEWS = [
    "vw_hero_metrics",
    "vw_opportunity_splits",
    "vw_waterfall",
    "vw_revenue_dynamics",
    "vw_pipeline",
    "vw_lost_analysis",
    "vw_account_health",   # requires jira_tickets table to exist first
]
# ─────────────────────────────────────────────────────────────────────────────

client = bigquery.Client(project=PROJECT)

print(f"Creating views in {PROJECT}.{DATASET}")
print("=" * 60)

for view_name in VIEWS:
    sql_path = os.path.join(SQL_DIR, f"{view_name}.sql")
    if not os.path.exists(sql_path):
        print(f"  SKIP  {view_name} — SQL file not found at {sql_path}")
        continue

    with open(sql_path) as f:
        sql = f.read()

    try:
        client.query(sql).result()
        print(f"  OK    {view_name}")
    except Exception as e:
        print(f"  FAIL  {view_name}: {e}")

print("=" * 60)
print("Done. Validate in BQ console:")
print(f"  https://console.cloud.google.com/bigquery?project={PROJECT}")
"""
digest.py
Weekly Executive Digest — CLI wrapper around shared/digest_utils.py.

Usage:
    python scripts/digest.py                          # generate + send Slack
    python scripts/digest.py --dry-run                # generate, skip Slack
    python scripts/digest.py --webhook <url>          # override SLACK_WEBHOOK_URL
    python scripts/digest.py --dry-run --save-snapshot

Weekly workflow:
    python scripts/sf_export_dashboard.py   # 1. Refresh Salesforce data
    python scripts/run_agents.py            # 2. Run signals/ICP agents
    python scripts/digest.py               # 3. Generate + send digest
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows (digest contains emoji)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv

# ── Path setup (same pattern as run_agents.py) ────────────────────────────────
_script_dir   = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
_api_dir      = os.path.join(_project_root, "api")

if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / '.env')
_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
if _creds and not Path(_creds).is_absolute():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(_ROOT / _creds)

from google.cloud import bigquery
from shared.digest_utils import (
    get_hero_metrics,
    get_latest_signals,
    get_latest_icp,
    get_signals_headlines,
    get_regional_breakdown,
    generate_digest,
    send_to_slack,
    save_snapshot,
)


def main():
    parser = argparse.ArgumentParser(description="Generate and send weekly executive digest.")
    parser.add_argument("--dry-run",       action="store_true", help="Generate but skip Slack send")
    parser.add_argument("--webhook",       type=str, default=None, help="Override SLACK_WEBHOOK_URL")
    parser.add_argument("--save-snapshot", action="store_true", help="Save digest to BQ after generating")
    args = parser.parse_args()

    webhook_url = args.webhook or os.environ.get("SLACK_WEBHOOK_URL", "")
    bq = bigquery.Client(project="forecast-dashboard-mvp")

    # ── [1/4] Fetch ───────────────────────────────────────────────────────────
    print("[1/4] Fetching data from BigQuery...")
    hero     = get_hero_metrics(bq)
    signals  = get_latest_signals(bq)
    icp      = get_latest_icp(bq)
    headlines = get_signals_headlines(bq)
    regional = get_regional_breakdown(bq)
    print(f"  Hero metrics:  {'OK' if hero else 'empty'}")
    print(f"  Signals cache: {'OK — ' + signals.get('_week_key', '') if signals else 'empty'}")
    print(f"  ICP profiles:  {len(icp)} BUs")
    print(f"  Headlines:     {'OK — ' + headlines.get('week_key', '') if headlines else 'empty'}")
    print(f"  Regional:      {len(regional)} regions")

    # ── [2/4] Generate ────────────────────────────────────────────────────────
    print("\n[2/4] Generating digest via Claude...")
    digest_text, week_key = generate_digest(hero, signals, icp, headlines, regional)
    print(f"  Week: {week_key}")
    print(f"  Length: {len(digest_text)} chars\n")
    print("-" * 60)
    print(digest_text)
    print("-" * 60)

    # ── [3/4] Send ────────────────────────────────────────────────────────────
    slack_sent = False
    if args.dry_run:
        print("\n[3/4] Skipping Slack send (--dry-run)")
    elif not webhook_url:
        print("\n[3/4] Skipping Slack send (no SLACK_WEBHOOK_URL configured)")
    else:
        print("\n[3/4] Sending to Slack...")
        slack_sent = send_to_slack(webhook_url, digest_text, week_key)
        print(f"  {'OK — message sent' if slack_sent else 'FAIL — check webhook URL'}")

    # ── [4/4] Snapshot ────────────────────────────────────────────────────────
    if args.save_snapshot:
        print("\n[4/4] Saving snapshot to BigQuery...")
        ok = save_snapshot(bq, digest_text, hero, week_key, slack_sent)
        print(f"  {'OK' if ok else 'FAIL — check BQ errors above'}")
    else:
        print("\n[4/4] Skipping snapshot (pass --save-snapshot to persist)")


if __name__ == "__main__":
    main()

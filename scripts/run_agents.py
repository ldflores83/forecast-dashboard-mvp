"""
scripts/run_agents.py
Standalone script entrypoint for the agentic pipeline.

Usage:
    python scripts/run_agents.py                        # full year, use cache
    python scripts/run_agents.py --q 1                  # Q1 only
    python scripts/run_agents.py --force                # bypass cache, re-run agents
    python scripts/run_agents.py --debug                # include timing in output
    python scripts/run_agents.py --dry-run              # run tools only, skip LLM
    python scripts/run_agents.py --team signals         # explicit team (default)

Weekly workflow:
    python scripts/sf_export_dashboard.py       # 1. Export Salesforce → BQ
    python scripts/run_agents.py                # 2. Run agents → write GCS

Prerequisites:
    1. .env file with:
         ANTHROPIC_API_KEY=sk-ant-...
         GOOGLE_APPLICATION_CREDENTIALS=credentials/forecast-dashboard-mvp-xxxxx.json
    2. BQ table opportunities populated (sf_export_dashboard.py)
    3. BQ views vw_account_health and vw_revenue_dynamics exist (setup_views.py)
"""

import argparse
import json
import os
import sys

# ── Ensure the api/ package is on the path when running from project root ──────
script_dir   = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
api_dir      = os.path.join(project_root, "api")

if api_dir not in sys.path:
    sys.path.insert(0, api_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ── Team registry — maps --team flag to orchestrator module path ───────────────
TEAM_REGISTRY = {
    "signals": "teams.revenue_signals.orchestrator",
}


def main():
    parser = argparse.ArgumentParser(
        description="Run an agentic pipeline team."
    )
    parser.add_argument(
        "--team", type=str, default="signals",
        choices=list(TEAM_REGISTRY.keys()),
        help="Which team pipeline to run (default: signals)"
    )
    parser.add_argument(
        "--q", type=int, default=0, choices=[0, 1, 2, 3, 4],
        help="Fiscal quarter filter (0 = full year, default: 0)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass cache and re-run all agents even if data is unchanged"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Include timing and intermediate counts in output"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run tools only (Phase 1) — validates BQ connectivity without LLM calls"
    )
    parser.add_argument(
        "--print-output", action="store_true",
        help="Print the final JSON output to console"
    )

    args = parser.parse_args()

    # ── Dry run: tools only ────────────────────────────────────────────────────
    if args.dry_run:
        print("=" * 60)
        print(f"DRY RUN — Tools only (no LLM calls) — team: {args.team}")
        print("=" * 60)
        from teams.revenue_signals.tools import get_flagged_deals, get_renewal_health, get_winloss_data
        from shared.utils  import build_context, source_hash
        from shared.state  import SharedState

        state = SharedState()
        state.context       = build_context(args.q)
        state.pipeline_data = get_flagged_deals(args.q)
        state.renewal_data  = get_renewal_health(args.q)
        state.winloss_data  = get_winloss_data(args.q)

        s_hash = source_hash(state)

        print(f"\nContext:          {state.context['week']} — Q{args.q or 'FY'}")
        print(f"Source hash:      {s_hash}")
        print(f"Flagged deals:    {len(state.pipeline_data.get('flagged_deals', []))}")
        print(f"Pushed 5x:        {state.pipeline_data.get('pushed_5x_count', 0)}")
        print(f"Overdue close:    {state.pipeline_data.get('overdue_close_count', 0)}")
        print(f"High risk accts:  {len(state.renewal_data.get('high_risk_accounts', []))}")
        print(f"ATR at risk:      ${state.renewal_data.get('total_atr_at_risk', 0)/1e6:.1f}M")
        print(f"Closed FY2027:    {state.winloss_data.get('total_closed_count', 0)} deals")
        print(f"Top loss reason:  {state.winloss_data.get('top_loss_reason', '—')}")
        print(f"\n[OK] Dry run complete - BQ connectivity confirmed")
        return

    # ── Full run ───────────────────────────────────────────────────────────────
    import importlib
    module_path = TEAM_REGISTRY[args.team]
    orchestrator = importlib.import_module(module_path)

    try:
        result = orchestrator.run(
            fiscal_quarter=args.q,
            force_refresh=args.force,
            debug=args.debug,
        )

        print(f"\n[OK] Pipeline complete — team: {args.team}")
        print(f"  Week:         {result['meta']['week']}")
        print(f"  Reviewer:     {result['review']['status']}")
        print(f"  Corrections:  {len(result['review'].get('corrections', []))}")
        print(f"  Cache hit:    {result['meta']['cache_hit']}")
        print(f"  Source hash:  {result['meta']['source_hash']}")
        print(f"\n  Output written to GCS:")
        print(f"  https://storage.googleapis.com/forecast-dashboard-mvp-frontend/signals_output.json")

        if result["review"].get("corrections"):
            print(f"\n  Reviewer corrections:")
            for c in result["review"]["corrections"]:
                print(f"    - {c}")

        if args.print_output:
            print("\n" + "=" * 60)
            print("OUTPUT JSON:")
            print("=" * 60)
            print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        print(f"\n[FAIL] Pipeline failed: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

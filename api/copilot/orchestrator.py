"""
copilot/orchestrator.py
Main orchestrator for the Weekly Revenue Signals agentic pipeline.

Workflow:
    1. Initialize SharedState
    2. Run tools — populate state with BQ data (no LLM)
    3. Compute source_hash for cache lookup
    4. Check cache — return cached result if data unchanged
    5. Run 3 agents — all read from SharedState independently
    6. Run reviewer — validates all outputs against SharedState
    7. Build final output dict
    8. Write to cache (BQ)
    9. Write signals_output.json to GCS
    10. Return result

Design rules:
  - Orchestrator is the only place that calls agents, reviewer, cache, and GCS.
  - Tools are called before any LLM work.
  - No LLM logic lives in this file.
  - GCS write happens LAST — after cache write — so a GCS failure doesn't
    prevent caching.
"""

import json
import os
from datetime import datetime, timezone

from google.cloud import storage

from .state          import SharedState
from .tool_registry  import TOOLS
from .llm_adapter    import run_tool_phase
from .agents         import pipeline_sentinel, renewal_pulse, winloss_intel
from .reviewer       import validate as reviewer_validate
from .cache          import get as cache_get, set as cache_set
from .utils          import build_context, source_hash
from .memory         import load_prior_week as memory_load, save_week as memory_save

GCS_BUCKET  = "forecast-dashboard-mvp-frontend"
GCS_FILE    = "signals_output.json"

_gcs_client = None


def _gcs() -> storage.Client:
    """Lazy-initialized GCS client."""
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = storage.Client()
    return _gcs_client


def _write_gcs(result: dict) -> None:
    """
    Writes the final result JSON to GCS.

    Uses no-cache headers so the frontend always fetches the latest version.
    """
    try:
        bucket = _gcs().bucket(GCS_BUCKET)
        blob   = bucket.blob(GCS_FILE)
        blob.upload_from_string(
            data=json.dumps(result, indent=2, default=str),
            content_type="application/json",
        )
        blob.cache_control = "no-cache, no-store, max-age=0"
        blob.patch()
        print(f"[orchestrator] Written to gs://{GCS_BUCKET}/{GCS_FILE}")
    except Exception as e:
        print(f"[orchestrator] GCS WRITE ERROR: {e}")
        raise


def run(fiscal_quarter: int = 0, force_refresh: bool = False, debug: bool = False) -> dict:
    """
    Runs the full agentic pipeline and returns the signals output.

    Args:
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.
        force_refresh:  If True, bypasses cache and re-runs all agents.
        debug:          If True, includes timing and intermediate data in output.

    Returns:
        Final signals output dict — same shape as signals_output.json.
    """
    start_time = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"[orchestrator] Revenue Signals — {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"[orchestrator] fiscal_quarter={fiscal_quarter}, force_refresh={force_refresh}")
    print(f"{'='*60}")

    # ── STEP 1: Initialize state ───────────────────────────────────────────────
    state = SharedState()
    state.context = build_context(fiscal_quarter)
    print(f"[orchestrator] Week: {state.context['week']}")

    # ── STEP 2: Run tools (via Claude tool use, with direct fallback) ──────────
    print("\n[orchestrator] Phase 1: Running tools via tool_phase...")
    t0 = datetime.now(timezone.utc)

    run_tool_phase(TOOLS, fiscal_quarter, state)

    tools_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)

    if not state.is_data_ready():
        raise RuntimeError("SharedState is incomplete after tools ran — cannot proceed.")

    print(f"[orchestrator] Tools complete in {tools_ms}ms — "
          f"flagged_deals={len(state.pipeline_data.get('flagged_deals', []))}, "
          f"high_risk_accounts={len(state.renewal_data.get('high_risk_accounts', []))}, "
          f"closed_deals={state.winloss_data.get('total_closed_count', 0)}")

    # ── Load prior week context into state for prompt injection ────────────────
    prior_week = memory_load(fiscal_quarter)
    if prior_week:
        state.context["prior_week"] = prior_week

    # ── STEP 3: Compute source_hash ────────────────────────────────────────────
    s_hash = source_hash(state)
    print(f"[orchestrator] source_hash={s_hash}")

    # ── STEP 4: Check cache ────────────────────────────────────────────────────
    if not force_refresh:
        cached = cache_get(s_hash, fiscal_quarter)
        if cached:
            print("[orchestrator] Cache HIT — returning cached result")
            _write_gcs(cached)
            return cached

    print("[orchestrator] Cache MISS — running agents")

    # ── STEP 5: Run agents (all read from SharedState independently) ───────────
    print("\n[orchestrator] Phase 2: Running agents...")
    t0 = datetime.now(timezone.utc)

    out_pipeline = pipeline_sentinel(state)
    out_renewal  = renewal_pulse(state)
    out_winloss  = winloss_intel(state)

    agents_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Agents complete in {agents_ms}ms")

    # ── STEP 6: Run reviewer ───────────────────────────────────────────────────
    print("\n[orchestrator] Phase 3: Running reviewer...")
    t0 = datetime.now(timezone.utc)

    reviewed = reviewer_validate(
        state=state,
        agent_outputs={
            "pipeline": out_pipeline,
            "renewal":  out_renewal,
            "winloss":  out_winloss,
        },
    )

    reviewer_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Reviewer complete in {reviewer_ms}ms — "
          f"status={reviewed.get('status')}")

    # ── STEP 7: Build final output ─────────────────────────────────────────────
    total_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    result = {
        "meta": {
            "week":             state.context["week"],
            "fiscal_quarter":   fiscal_quarter,
            "fiscal_year":      state.context["fiscal_year"],
            "generated_at":     start_time.isoformat(),
            "source_hash":      s_hash,
            "cache_hit":        False,
            "reviewer_status":  reviewed.get("status", "unknown"),
        },
        "pipeline": reviewed["pipeline"],
        "renewal":  reviewed["renewal"],
        "winloss":  reviewed["winloss"],
        "review": {
            "status":      reviewed.get("status", "unknown"),
            "notes":       reviewed.get("notes", []),
            "corrections": reviewed.get("corrections", []),
        },
    }

    # ── Debug mode: add timing and intermediate data ───────────────────────────
    if debug:
        result["debug"] = {
            "latency_ms": {
                "total":   total_ms,
                "tools":   tools_ms,
                "agents":  agents_ms,
                "reviewer":reviewer_ms,
            },
            "raw_counts": {
                "flagged_deals":     len(state.pipeline_data.get("flagged_deals", [])),
                "high_risk_accounts":len(state.renewal_data.get("high_risk_accounts", [])),
                "closed_deals":      state.winloss_data.get("total_closed_count", 0),
                "pushed_5x":         state.pipeline_data.get("pushed_5x_count", 0),
                "overdue_close":     state.pipeline_data.get("overdue_close_count", 0),
            },
        }

    # ── STEP 8: Write to cache ─────────────────────────────────────────────────
    cache_set(s_hash, fiscal_quarter, result)

    # ── STEP 8b: Save to memory (signals_history) ──────────────────────────────
    memory_save(result, fiscal_quarter)

    # ── STEP 9: Write to GCS ──────────────────────────────────────────────────
    _write_gcs(result)

    print(f"\n[orchestrator] Complete in {total_ms}ms — "
          f"reviewer={result['review']['status']}, "
          f"corrections={len(result['review']['corrections'])}")
    print(f"{'='*60}\n")

    return result

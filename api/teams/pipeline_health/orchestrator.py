"""
teams/pipeline_health/orchestrator.py
Main orchestrator for the Pipeline Health agentic pipeline.

Workflow:
    1. Run all 5 tools in parallel (concurrent.futures.ThreadPoolExecutor)
    2. Compute source_hash for cache lookup
    3. Check cache — return cached result if data unchanged
    4. Run 3 agents in parallel (can run concurrently — no cross-dependencies)
    5. Run synthesizer — depends on all 3 agent outputs
    6. Run reviewer — validates synthesizer output against ground-truth tool data
    7. Build final output dict
    8. Write to cache (BQ)
    9. Write pipeline_health_output.json to GCS
    10. Return result

Design rules:
  - Orchestrator is the only place that calls agents, reviewer, cache, and GCS.
  - Tools are called before any LLM work.
  - No LLM logic lives in this file.
  - bu=None means all BUs; individual BU runs are NOT cached separately.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from google.cloud import storage

from shared.cache import get as cache_get, set as cache_set
from shared.utils import build_context

from .tools import (
    get_stage_health,
    get_meddpicc_gaps,
    get_push_analysis,
    get_bde_cadence,
    get_pipeline_by_owner,
    get_regional_breakdown,
)
from .agents import (
    pipeline_risk_agent,
    meddpicc_qualification_agent,
    bde_cadence_agent,
    synthesizer,
    reviewer,
)

GCS_BUCKET = "forecast-dashboard-mvp-frontend"
GCS_FILE   = "pipeline_health_output.json"

_gcs_client = None


def _gcs() -> storage.Client:
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = storage.Client()
    return _gcs_client


def _write_gcs(result: dict) -> None:
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


def _source_hash(
    stage_health:      dict,
    meddpicc_gaps:     dict,
    push_analysis:     dict,
    bde_cadence:       dict,
    pipeline_by_owner: dict,
    bu:                str | None,
    fiscal_quarter:    int,
) -> str:
    key = {
        "total_deal_count":   stage_health.get("total_deal_count", 0),
        "total_open_acv":     round(stage_health.get("total_open_acv", 0.0), -3),
        "meddpicc_deals":     meddpicc_gaps.get("total_deals", 0),
        "zombie_count":       len(push_analysis.get("zombie_deals", [])),
        "pushed_5x_count":    push_analysis.get("pushed_5x_count", 0),
        "bde_handoff_count":  bde_cadence.get("handoff_count", 0),
        "total_owners":       pipeline_by_owner.get("total_owners", 0),
        "bu":                 bu or "All",
        "fiscal_quarter":     fiscal_quarter,
    }
    return hashlib.md5(json.dumps(key, sort_keys=True).encode()).hexdigest()[:8]


def run_pipeline_health(
    fiscal_quarter: int = 0,
    bu: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """
    Runs the full Pipeline Health pipeline and returns the output.

    Args:
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.
        bu:             Optional BU filter ('ERP BU', 'Supply Chain BU', 'Redzone BU').
                        None = all BUs.
        force_refresh:  If True, bypasses cache and re-runs all agents.

    Returns:
        Pipeline health output dict.
    """
    start_time = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"[orchestrator] Pipeline Health — {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"[orchestrator] fiscal_quarter={fiscal_quarter}, bu={bu or 'All'}, force_refresh={force_refresh}")
    print(f"{'='*60}")

    context = build_context(fiscal_quarter)

    # ── STEP 1: Run all 5 tools in parallel ───────────────────────────────────
    print("\n[orchestrator] Phase 1: Running tools in parallel...")
    t0 = datetime.now(timezone.utc)

    tool_results: dict = {}
    tool_errors:  dict = {}

    def _run_tool(name, fn, kwargs):
        try:
            return name, fn(**kwargs), None
        except Exception as e:
            return name, None, str(e)

    tool_specs = [
        ("stage_health",        get_stage_health,        {"bu": bu, "fiscal_quarter": fiscal_quarter}),
        ("meddpicc_gaps",       get_meddpicc_gaps,       {"bu": bu, "fiscal_quarter": fiscal_quarter}),
        ("push_analysis",       get_push_analysis,       {"bu": bu, "fiscal_quarter": fiscal_quarter}),
        ("bde_cadence",         get_bde_cadence,         {"bu": bu}),
        ("pipeline_by_owner",   get_pipeline_by_owner,   {"bu": bu, "fiscal_quarter": fiscal_quarter}),
        ("regional_breakdown",  get_regional_breakdown,  {"bu": bu}),
    ]

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_run_tool, name, fn, kw): name for name, fn, kw in tool_specs}
        for future in as_completed(futures):
            name, data, err = future.result()
            if err:
                print(f"[orchestrator] TOOL ERROR ({name}): {err}")
                tool_errors[name] = err
                tool_results[name] = {}
            else:
                tool_results[name] = data

    tools_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)

    stage_health_data       = tool_results.get("stage_health", {})
    meddpicc_gaps_data      = tool_results.get("meddpicc_gaps", {})
    push_analysis_data      = tool_results.get("push_analysis", {})
    bde_cadence_data        = tool_results.get("bde_cadence", {})
    pipeline_by_owner_data  = tool_results.get("pipeline_by_owner", {})
    regional_breakdown_data = tool_results.get("regional_breakdown", [])

    print(
        f"[orchestrator] Tools complete in {tools_ms}ms — "
        f"stages={len(stage_health_data.get('stages', []))}, "
        f"open_deals={stage_health_data.get('total_deal_count', 0)}, "
        f"zombie_deals={len(push_analysis_data.get('zombie_deals', []))}, "
        f"owners={pipeline_by_owner_data.get('total_owners', 0)}"
    )

    # ── STEP 2: Compute source_hash ───────────────────────────────────────────
    s_hash = _source_hash(
        stage_health_data, meddpicc_gaps_data, push_analysis_data,
        bde_cadence_data, pipeline_by_owner_data, bu, fiscal_quarter,
    )
    print(f"[orchestrator] source_hash={s_hash}")

    # ── STEP 3: Check cache ───────────────────────────────────────────────────
    if not force_refresh:
        cached = cache_get(s_hash, fiscal_quarter)
        if cached:
            print("[orchestrator] Cache HIT — returning cached result")
            _write_gcs(cached)
            return cached

    print("[orchestrator] Cache MISS — running agents")

    # ── STEP 4: Run 3 agents in parallel ──────────────────────────────────────
    print("\n[orchestrator] Phase 2: Running agents in parallel...")
    t0 = datetime.now(timezone.utc)

    out_risk          = {}
    out_qualification = {}
    out_cadence       = {}

    def _run_risk():
        return pipeline_risk_agent(stage_health_data, push_analysis_data, context)

    def _run_qualification():
        return meddpicc_qualification_agent(meddpicc_gaps_data, context)

    def _run_cadence():
        return bde_cadence_agent(bde_cadence_data, pipeline_by_owner_data, context)

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_risk  = pool.submit(_run_risk)
        f_qual  = pool.submit(_run_qualification)
        f_cad   = pool.submit(_run_cadence)
        out_risk          = f_risk.result()
        out_qualification = f_qual.result()
        out_cadence       = f_cad.result()

    agents_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Agents complete in {agents_ms}ms")

    # ── STEP 5: Synthesizer ───────────────────────────────────────────────────
    print("\n[orchestrator] Phase 3: Running synthesizer...")
    t0 = datetime.now(timezone.utc)

    out_synthesizer = synthesizer(out_risk, out_qualification, out_cadence, context)

    synth_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Synthesizer complete in {synth_ms}ms")

    # ── STEP 6: Reviewer ──────────────────────────────────────────────────────
    print("\n[orchestrator] Phase 4: Running reviewer...")
    t0 = datetime.now(timezone.utc)

    reviewed = reviewer(
        synthesizer_output=out_synthesizer,
        stage_health_data=stage_health_data,
        meddpicc_gaps_data=meddpicc_gaps_data,
        push_analysis_data=push_analysis_data,
        bde_cadence_data=bde_cadence_data,
        pipeline_by_owner_data=pipeline_by_owner_data,
    )

    reviewer_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Reviewer complete in {reviewer_ms}ms — status={reviewed.get('status')}")

    # ── STEP 7: Build final output ────────────────────────────────────────────
    total_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    result = {
        "meta": {
            "week":            context["week"],
            "fiscal_quarter":  fiscal_quarter,
            "fiscal_year":     context["fiscal_year"],
            "bu_filter":       bu or "All",
            "generated_at":    start_time.isoformat(),
            "source_hash":     s_hash,
            "cache_hit":       False,
            "reviewer_status": reviewed.get("status", "unknown"),
            "latency_ms": {
                "total":      total_ms,
                "tools":      tools_ms,
                "agents":     agents_ms,
                "synthesizer": synth_ms,
                "reviewer":   reviewer_ms,
            },
        },
        "narrative":      reviewed.get("final_narrative", out_synthesizer.get("final_narrative", "")),
        "bu_scorecard":   out_synthesizer.get("bu_scorecard", {}),
        "stage_health":   stage_health_data,
        "meddpicc_gaps":  meddpicc_gaps_data,
        "push_analysis":  push_analysis_data,
        "bde_cadence":    bde_cadence_data,
        "pipeline_by_owner":   pipeline_by_owner_data,
        "regional_breakdown":  regional_breakdown_data,
        "agent_outputs": {
            "stage_risk":      out_risk,
            "qualification":   out_qualification,
            "cadence":         out_cadence,
        },
        "review": {
            "status":      reviewed.get("status", "unknown"),
            "notes":       reviewed.get("notes", []),
            "corrections": reviewed.get("corrections", []),
        },
    }

    if tool_errors:
        result["meta"]["tool_errors"] = tool_errors

    # ── STEP 8: Write to cache ────────────────────────────────────────────────
    cache_set(s_hash, fiscal_quarter, result)

    # ── STEP 9: Write to GCS ─────────────────────────────────────────────────
    _write_gcs(result)

    print(
        f"\n[orchestrator] Complete in {total_ms}ms — "
        f"reviewer={result['review']['status']}, "
        f"corrections={len(result['review']['corrections'])}"
    )
    print(f"{'='*60}\n")

    return result

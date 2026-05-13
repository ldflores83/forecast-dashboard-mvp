"""
teams/icp/orchestrator.py
Main orchestrator for the ICP Analysis agentic pipeline.

Workflow:
    1. Run tools — fetch win/loss history + open pipeline (independent, sequential)
    2. Compute source_hash for cache lookup
    3. Check cache — return cached result if data unchanged
    4. Load prior week context from memory
    5. Run ICP Discovery agent (win/loss → ICP profile per BU)
    6. Run ICP Validator agent (ICP profile + pipeline → gap analysis) [depends on 5]
    7. Run Reviewer (validates both outputs against source data)
    8. Build final output dict
    9. Write to cache
    10. Save to memory (icp_profiles)
    11. Write icp_output.json to GCS
    12. Return result

Design rules:
  - Orchestrator is the only place that calls agents, reviewer, cache, and GCS.
  - Tools are called before any LLM work.
  - Discovery must complete before Validator runs (validator depends on its output).
  - No LLM logic lives in this file.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

from google.cloud import storage

from shared.cache   import get as cache_get, set as cache_set
from shared.utils   import build_context
from .tools         import get_won_lost_by_bu, get_pipeline_by_bu
from .agents        import icp_discovery, icp_validator
from .reviewer      import validate as reviewer_validate
from .memory        import load_prior_week as memory_load, save_week as memory_save

GCS_BUCKET = "forecast-dashboard-mvp-frontend"
GCS_FILE   = "icp_output.json"

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


def _source_hash(won_lost_data: dict, pipeline_data: dict) -> str:
    """
    Computes a deterministic hash from ICP input data counts.

    Hashes deal counts and total ACV — stable across minor row ordering changes.
    Returns an 8-char hex string.
    """
    key_data = {
        "total_won_lost":      won_lost_data.get("total_deals", 0),
        "with_vertical":       won_lost_data.get("with_vertical", 0),
        "pipeline_total":      pipeline_data.get("total_deals", 0),
        "pipeline_total_acv":  round(pipeline_data.get("total_acv", 0.0), -3),  # round to nearest $1K
    }
    serialized = json.dumps(key_data, sort_keys=True)
    return hashlib.md5(serialized.encode()).hexdigest()[:8]


def run(fiscal_quarter: int = 0, force_refresh: bool = False, debug: bool = False) -> dict:
    """
    Runs the full ICP Analysis pipeline and returns the output.

    Args:
        fiscal_quarter: Accepted for interface compatibility — not used in ICP queries.
                        ICP discovery uses full historical data; pipeline uses current open.
        force_refresh:  If True, bypasses cache and re-runs all agents.
        debug:          If True, includes timing in the output.

    Returns:
        Final ICP output dict — same shape as icp_output.json.
    """
    start_time = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"[orchestrator] ICP Analysis — {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"[orchestrator] force_refresh={force_refresh}")
    print(f"{'='*60}")

    context = build_context(fiscal_quarter)
    print(f"[orchestrator] Week: {context['week']}")

    # ── STEP 1: Run tools ─────────────────────────────────────────────────────
    print("\n[orchestrator] Phase 1: Running tools...")
    t0 = datetime.now(timezone.utc)

    won_lost_data = get_won_lost_by_bu()
    pipeline_data = get_pipeline_by_bu()

    tools_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Tools complete in {tools_ms}ms — "
          f"won_lost={won_lost_data['total_deals']}, "
          f"pipeline={pipeline_data['total_deals']}")

    # ── STEP 2: Compute source_hash ───────────────────────────────────────────
    s_hash = _source_hash(won_lost_data, pipeline_data)
    print(f"[orchestrator] source_hash={s_hash}")

    # ── STEP 3: Check cache ───────────────────────────────────────────────────
    if not force_refresh:
        cached = cache_get(s_hash, fiscal_quarter)
        if cached:
            print("[orchestrator] Cache HIT — returning cached result")
            _write_gcs(cached)
            return cached

    print("[orchestrator] Cache MISS — running agents")

    # ── STEP 4: Load prior week context ───────────────────────────────────────
    prior_week = memory_load(fiscal_quarter)

    # ── STEP 5: ICP Discovery ─────────────────────────────────────────────────
    print("\n[orchestrator] Phase 2: Running ICP Discovery...")
    t0 = datetime.now(timezone.utc)

    out_discovery = icp_discovery(
        won_lost_data=won_lost_data,
        prior_week_context=prior_week,
    )

    discovery_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Discovery complete in {discovery_ms}ms")

    # ── STEP 6: ICP Validator (depends on discovery) ──────────────────────────
    print("\n[orchestrator] Phase 3: Running ICP Validator...")
    t0 = datetime.now(timezone.utc)

    out_validation = icp_validator(
        discovery_output=out_discovery,
        pipeline_data=pipeline_data,
        prior_week_context=prior_week,
    )

    validation_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Validation complete in {validation_ms}ms")

    # ── STEP 7: Reviewer ──────────────────────────────────────────────────────
    print("\n[orchestrator] Phase 4: Running reviewer...")
    t0 = datetime.now(timezone.utc)

    reviewed = reviewer_validate(
        won_lost_data=won_lost_data,
        pipeline_data=pipeline_data,
        icp_profile=out_discovery,
        validation=out_validation,
    )

    reviewer_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[orchestrator] Reviewer complete in {reviewer_ms}ms — "
          f"status={reviewed.get('status')}")

    # ── STEP 8: Build final output ────────────────────────────────────────────
    total_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    result = {
        "meta": {
            "week":            context["week"],
            "fiscal_year":     context["fiscal_year"],
            "generated_at":    start_time.isoformat(),
            "source_hash":     s_hash,
            "cache_hit":       False,
            "reviewer_status": reviewed.get("status", "unknown"),
            "total_deals_analyzed":  won_lost_data.get("total_deals", 0),
            "vertical_coverage_pct": won_lost_data.get("vertical_coverage", 0),
            "open_pipeline_deals":   pipeline_data.get("total_deals", 0),
            "open_pipeline_acv":     pipeline_data.get("total_acv", 0.0),
        },
        "icp_profile": reviewed["icp_profile"],
        "validation":  reviewed["validation"],
        "review": {
            "status":      reviewed.get("status", "unknown"),
            "notes":       reviewed.get("notes", []),
            "corrections": reviewed.get("corrections", []),
        },
    }

    if debug:
        result["debug"] = {
            "latency_ms": {
                "total":      total_ms,
                "tools":      tools_ms,
                "discovery":  discovery_ms,
                "validation": validation_ms,
                "reviewer":   reviewer_ms,
            },
        }

    # ── STEP 9: Write to cache ────────────────────────────────────────────────
    cache_set(s_hash, fiscal_quarter, result)

    # ── STEP 10: Save to memory ───────────────────────────────────────────────
    memory_save(result, fiscal_quarter)

    # ── STEP 11: Write to GCS ─────────────────────────────────────────────────
    _write_gcs(result)

    print(f"\n[orchestrator] Complete in {total_ms}ms — "
          f"reviewer={result['review']['status']}, "
          f"corrections={len(result['review']['corrections'])}")
    print(f"{'='*60}\n")

    return result

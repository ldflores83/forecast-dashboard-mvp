"""
shared/cache.py
BigQuery-based cache for the agentic pipeline.

Cache key: source_hash + fiscal_quarter
If the underlying BQ data produces the same source_hash as the previous run,
the cached result is returned without re-running the agents.

BQ table: forecast-dashboard-mvp.forecast_data.signals_cache
Schema:
    week_key        STRING    — e.g. "2026-W18"
    fiscal_quarter  INT64     — 0 = full year, 1-4 = specific quarter
    source_hash     STRING    — 8-char MD5 from utils.source_hash()
    result_json     STRING    — JSON-serialized final output
    generated_at    TIMESTAMP — when the result was generated

Table is created automatically on first use if it does not exist.
"""

import json
from datetime import datetime, timezone

from google.cloud import bigquery

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"
TABLE   = "signals_cache"
FULL_TABLE = f"{PROJECT}.{DATASET}.{TABLE}"

_bq_client = None


def _bq() -> bigquery.Client:
    """Lazy-initialized BigQuery client."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


def _ensure_table() -> None:
    """Creates the signals_cache table if it does not already exist."""
    schema = [
        bigquery.SchemaField("week_key",       "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("fiscal_quarter", "INT64",     mode="REQUIRED"),
        bigquery.SchemaField("source_hash",    "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("result_json",    "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("generated_at",   "TIMESTAMP", mode="REQUIRED"),
    ]
    table_ref = bigquery.Table(FULL_TABLE, schema=schema)
    try:
        _bq().create_table(table_ref)
        print(f"[cache] Created table {FULL_TABLE}")
    except Exception:
        # Table already exists — expected on every run after the first
        pass


def _week_key() -> str:
    """Returns the current ISO week key, e.g. '2026-W18'."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-W%V")


def get(source_hash: str, fiscal_quarter: int) -> dict | None:
    """
    Looks up a cached result by source_hash and fiscal_quarter.

    Args:
        source_hash:    8-char hash from utils.source_hash().
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.

    Returns:
        Parsed result dict if cache hit, None if miss.
    """
    sql = f"""
        SELECT result_json, generated_at
        FROM `{FULL_TABLE}`
        WHERE source_hash = @source_hash
          AND fiscal_quarter = @fiscal_quarter
        ORDER BY generated_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("source_hash",    "STRING", source_hash),
            bigquery.ScalarQueryParameter("fiscal_quarter", "INT64",  fiscal_quarter),
        ]
    )
    try:
        rows = list(_bq().query(sql, job_config=job_config).result())
        if not rows:
            print(f"[cache] MISS — hash={source_hash}, q={fiscal_quarter}")
            return None

        result = json.loads(rows[0]["result_json"])
        # Mark as cache hit
        if "meta" in result:
            result["meta"]["cache_hit"] = True
        print(f"[cache] HIT — hash={source_hash}, q={fiscal_quarter}, "
              f"generated_at={rows[0]['generated_at']}")
        return result

    except Exception as e:
        print(f"[cache] GET ERROR: {e} — treating as cache miss")
        return None


def set(source_hash: str, fiscal_quarter: int, result: dict) -> None:
    """
    Writes a result to the cache.

    Args:
        source_hash:    8-char hash from utils.source_hash().
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.
        result:         Final output dict from the orchestrator.
    """
    _ensure_table()

    rows = [{
        "week_key":       _week_key(),
        "fiscal_quarter": fiscal_quarter,
        "source_hash":    source_hash,
        "result_json":    json.dumps(result, default=str),
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }]

    try:
        errors = _bq().insert_rows_json(FULL_TABLE, rows)
        if errors:
            print(f"[cache] SET ERROR: {errors}")
        else:
            print(f"[cache] SET — hash={source_hash}, q={fiscal_quarter}")
    except Exception as e:
        print(f"[cache] SET ERROR: {e} — result not cached (non-fatal)")


def clear(fiscal_quarter: int = None) -> None:
    """
    Clears cached entries. Used for testing or forced refresh.

    Args:
        fiscal_quarter: If provided, clears only entries for that quarter.
                        If None, clears ALL cache entries.
    """
    if fiscal_quarter is not None:
        sql = f"DELETE FROM `{FULL_TABLE}` WHERE fiscal_quarter = {fiscal_quarter}"
    else:
        sql = f"DELETE FROM `{FULL_TABLE}` WHERE TRUE"

    try:
        _bq().query(sql).result()
        scope = f"q={fiscal_quarter}" if fiscal_quarter is not None else "all"
        print(f"[cache] CLEARED — scope: {scope}")
    except Exception as e:
        print(f"[cache] CLEAR ERROR: {e}")

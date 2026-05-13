"""
teams/revenue_signals/memory.py
BQ-backed memory layer for cross-week context in the Revenue Signals pipeline.

Saves a compact summary of each run's agent headlines to BigQuery so that
next week's agents can reference what was flagged previously — enabling
trend detection and preventing repetition of stale signals.

BQ table: forecast-dashboard-mvp.forecast_data.signals_history
Schema:
    week_key           STRING    — e.g. "2026-W18"
    fiscal_quarter     INT64     — 0 = full year, 1-4 = specific quarter
    pipeline_headline  STRING    — Agent 1 headline for this week
    renewal_headline   STRING    — Agent 2 headline for this week
    winloss_headline   STRING    — Agent 3 headline for this week
    review_status      STRING    — 'passed', 'flagged', or 'error'
    source_hash        STRING    — 8-char MD5 from utils.source_hash()
    generated_at       TIMESTAMP — when the result was generated
"""

from datetime import datetime, timezone

from google.cloud import bigquery

from shared.base_memory import BaseMemory


class SignalsMemory(BaseMemory):
    """BQ memory for the Revenue Signals pipeline."""

    @property
    def table_name(self) -> str:
        return "signals_history"

    def _ensure_table(self) -> None:
        schema = [
            bigquery.SchemaField("week_key",          "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("fiscal_quarter",    "INT64",     mode="REQUIRED"),
            bigquery.SchemaField("pipeline_headline", "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("renewal_headline",  "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("winloss_headline",  "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("review_status",     "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("source_hash",       "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("generated_at",      "TIMESTAMP", mode="REQUIRED"),
        ]
        table_ref = bigquery.Table(self.full_table, schema=schema)
        try:
            self._bq().create_table(table_ref)
            print(f"[memory] Created table {self.full_table}")
        except Exception:
            pass

    def load_prior_week(self, fiscal_quarter: int) -> dict | None:
        """
        Returns the most recent prior-week signals summary for context injection.

        Excludes the current week so a force-refresh does not reference itself.
        Returns None if no prior data exists (non-fatal — agents run without it).

        Args:
            fiscal_quarter: 0 = full year, 1-4 = specific quarter.

        Returns:
            Dict with week, pipeline_headline, renewal_headline, winloss_headline,
            review_status — or None if no prior record exists.
        """
        current_week = self._week_key()
        sql = f"""
            SELECT pipeline_headline, renewal_headline, winloss_headline,
                   review_status, source_hash, week_key
            FROM `{self.full_table}`
            WHERE fiscal_quarter = @fiscal_quarter
              AND week_key != @current_week
            ORDER BY generated_at DESC
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("fiscal_quarter", "INT64",  fiscal_quarter),
                bigquery.ScalarQueryParameter("current_week",   "STRING", current_week),
            ]
        )
        try:
            rows = self._run_query(sql, job_config=job_config)
            if not rows:
                print(f"[memory] No prior week data for q={fiscal_quarter}")
                return None
            row = rows[0]
            print(f"[memory] Loaded prior week context: {row['week_key']}")
            return {
                "week":              row["week_key"],
                "pipeline_headline": row["pipeline_headline"],
                "renewal_headline":  row["renewal_headline"],
                "winloss_headline":  row["winloss_headline"],
                "review_status":     row["review_status"],
            }
        except Exception as e:
            print(f"[memory] LOAD ERROR: {e} — continuing without prior context")
            return None

    def save_week(self, result: dict, fiscal_quarter: int) -> None:
        """
        Saves this week's agent headlines to signals_history.

        Called after cache write so a cache failure doesn't prevent memory from saving.
        Non-fatal — a save error does not interrupt the pipeline.

        Args:
            result:         Final output dict from the orchestrator.
            fiscal_quarter: 0 = full year, 1-4 = specific quarter.
        """
        self._ensure_table()

        rows = [{
            "week_key":          self._week_key(),
            "fiscal_quarter":    fiscal_quarter,
            "pipeline_headline": result.get("pipeline", {}).get("headline", ""),
            "renewal_headline":  result.get("renewal",  {}).get("headline", ""),
            "winloss_headline":  result.get("winloss",  {}).get("headline", ""),
            "review_status":     result.get("review",   {}).get("status",   "unknown"),
            "source_hash":       result.get("meta",     {}).get("source_hash", ""),
            "generated_at":      datetime.now(timezone.utc).isoformat(),
        }]

        try:
            self._insert_rows(rows)
            print(f"[memory] Saved week {rows[0]['week_key']} to signals_history (q={fiscal_quarter})")
        except Exception as e:
            print(f"[memory] SAVE ERROR: {e} — non-fatal")


# ── Module-level singleton + convenience functions ────────────────────────────
# Callers (orchestrator, main.py) use these directly, matching the original API.

_memory = SignalsMemory()


def load_prior_week(fiscal_quarter: int) -> dict | None:
    return _memory.load_prior_week(fiscal_quarter)


def save_week(result: dict, fiscal_quarter: int) -> None:
    _memory.save_week(result, fiscal_quarter)


def clear(fiscal_quarter: int = None) -> None:
    _memory.clear(fiscal_quarter)

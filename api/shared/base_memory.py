"""
shared/base_memory.py
Abstract base class for BQ-backed team memory.

Provides generic BigQuery read/write infrastructure. Each team subclasses
BaseMemory, declares its table_name, and implements the three abstract methods.
The clear() method is provided as a concrete default.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from google.cloud import bigquery

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"

_bq_client = None


class BaseMemory(ABC):
    """Abstract BQ-backed memory for cross-week context."""

    # ── Subclass contract ─────────────────────────────────────────────────────

    @property
    @abstractmethod
    def table_name(self) -> str:
        """BQ table name (without project/dataset prefix)."""
        ...

    @abstractmethod
    def _ensure_table(self) -> None:
        """Create the BQ table if it does not exist."""
        ...

    @abstractmethod
    def load_prior_week(self, fiscal_quarter: int) -> dict | None:
        """
        Returns the most recent prior-week context for prompt injection.
        Returns None if no prior data exists (non-fatal).
        """
        ...

    @abstractmethod
    def save_week(self, result: dict, fiscal_quarter: int) -> None:
        """Persist this week's agent headlines to the memory table."""
        ...

    # ── Shared infrastructure ─────────────────────────────────────────────────

    @property
    def full_table(self) -> str:
        return f"{PROJECT}.{DATASET}.{self.table_name}"

    def _bq(self) -> bigquery.Client:
        """Lazy-initialized, module-level BigQuery client."""
        global _bq_client
        if _bq_client is None:
            _bq_client = bigquery.Client(project=PROJECT)
        return _bq_client

    def _week_key(self) -> str:
        """Returns the current ISO week key, e.g. '2026-W18'."""
        return datetime.now(timezone.utc).strftime("%Y-W%V")

    def _run_query(self, sql: str, job_config=None) -> list:
        """Execute a BQ query and return rows as a list of dicts."""
        if job_config:
            rows = self._bq().query(sql, job_config=job_config).result()
        else:
            rows = self._bq().query(sql).result()
        return [dict(r) for r in rows]

    def _insert_rows(self, rows: list) -> None:
        """Insert rows via streaming insert. Raises on error."""
        errors = self._bq().insert_rows_json(self.full_table, rows)
        if errors:
            raise RuntimeError(str(errors))

    def clear(self, fiscal_quarter: int = None) -> None:
        """
        Deletes memory entries.
        If fiscal_quarter is None, clears all entries across all quarters.
        """
        if fiscal_quarter is not None:
            sql = f"DELETE FROM `{self.full_table}` WHERE fiscal_quarter = {fiscal_quarter}"
            scope = f"q={fiscal_quarter}"
        else:
            sql = f"DELETE FROM `{self.full_table}` WHERE TRUE"
            scope = "all"
        try:
            self._bq().query(sql).result()
            print(f"[memory] CLEARED — scope: {scope}")
        except Exception as e:
            print(f"[memory] CLEAR ERROR: {e}")

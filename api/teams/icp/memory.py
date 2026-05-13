"""
teams/icp/memory.py
BQ-backed memory layer for cross-week ICP context.

Saves the per-BU ICP profile each run so that next week's agents can compare
and detect trend changes (improving ICP alignment, shifting win patterns, etc.).

BQ table: forecast-dashboard-mvp.forecast_data.icp_profiles
Schema:
    week_key           STRING    — e.g. "2026-W18"
    bu                 STRING    — "ERP BU", "Supply Chain BU", "Redzone BU"
    icp_profile_json   STRING    — JSON of the per-BU icp_profile dict
    top_verticals      STRING    — comma-joined top_verticals for quick scan
    win_rate           FLOAT64   — win_rate from icp_profile
    sample_size        INT64     — sample_size for this BU
    review_status      STRING    — 'passed', 'flagged', or 'error'
    created_at         TIMESTAMP — when the result was generated
"""

import json
from datetime import datetime, timezone

from google.cloud import bigquery

from shared.base_memory import BaseMemory


class ICPMemory(BaseMemory):
    """BQ memory for the ICP Analysis pipeline."""

    @property
    def table_name(self) -> str:
        return "icp_profiles"

    def _ensure_table(self) -> None:
        schema = [
            bigquery.SchemaField("week_key",         "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("bu",               "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("icp_profile_json", "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("top_verticals",    "STRING",    mode="NULLABLE"),
            bigquery.SchemaField("win_rate",         "FLOAT64",   mode="NULLABLE"),
            bigquery.SchemaField("sample_size",      "INT64",     mode="NULLABLE"),
            bigquery.SchemaField("review_status",    "STRING",    mode="REQUIRED"),
            bigquery.SchemaField("created_at",       "TIMESTAMP", mode="REQUIRED"),
        ]
        table_ref = bigquery.Table(self.full_table, schema=schema)
        try:
            self._bq().create_table(table_ref)
            print(f"[memory] Created table {self.full_table}")
        except Exception:
            pass

    def load_prior_week(self, fiscal_quarter: int = 0) -> dict | None:
        """
        Returns per-BU ICP profiles from the most recent prior run.

        The fiscal_quarter arg is accepted for interface compatibility but not
        used in the query — ICP profiles are not quarter-specific.

        Returns:
            {bu: {icp_profile_json (parsed), top_verticals, win_rate, week}} per BU,
            or None if no prior data exists.
        """
        current_week = self._week_key()
        sql = f"""
            SELECT bu, icp_profile_json, top_verticals, win_rate, week_key
            FROM `{self.full_table}`
            WHERE week_key != @current_week
            ORDER BY created_at DESC
            LIMIT 10
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("current_week", "STRING", current_week),
            ]
        )
        try:
            rows = self._run_query(sql, job_config=job_config)
            if not rows:
                print("[memory] No prior ICP data found")
                return None

            # One row per BU — take the most recent per BU (rows already desc by created_at)
            seen = set()
            result = {}
            for row in rows:
                bu = row.get("bu", "")
                if bu in seen:
                    continue
                seen.add(bu)
                try:
                    profile = json.loads(row.get("icp_profile_json") or "{}")
                except Exception:
                    profile = {}
                result[bu] = {
                    "icp_profile":   profile,
                    "top_verticals": row.get("top_verticals", ""),
                    "win_rate":      row.get("win_rate"),
                    "week":          row.get("week_key", ""),
                }

            if result:
                print(f"[memory] Loaded prior ICP context from week {rows[0].get('week_key', '?')}")
            return result or None

        except Exception as e:
            print(f"[memory] LOAD ERROR: {e} — continuing without prior context")
            return None

    def save_week(self, result: dict, fiscal_quarter: int = 0) -> None:
        """
        Saves per-BU ICP profiles from this run to icp_profiles.

        Writes one row per BU. Non-fatal — a save error does not interrupt the pipeline.

        Args:
            result:         Final output dict from the ICP orchestrator.
            fiscal_quarter: Unused; accepted for interface compatibility.
        """
        self._ensure_table()

        icp_profile = result.get("icp_profile") or {}
        review_status = result.get("review", {}).get("status", "unknown")
        week_key = self._week_key()
        now_iso  = datetime.now(timezone.utc).isoformat()

        rows = []
        for bu, bu_data in icp_profile.items():
            if not isinstance(bu_data, dict):
                continue
            profile = bu_data.get("icp_profile") or {}
            top_v   = profile.get("top_verticals") or []
            rows.append({
                "week_key":         week_key,
                "bu":               bu,
                "icp_profile_json": json.dumps(bu_data, default=str),
                "top_verticals":    ", ".join(str(v) for v in top_v) if top_v else None,
                "win_rate":         float(profile.get("win_rate") or 0.0),
                "sample_size":      int(bu_data.get("sample_size") or 0),
                "review_status":    review_status,
                "created_at":       now_iso,
            })

        if not rows:
            print("[memory] No ICP profiles to save")
            return

        try:
            self._insert_rows(rows)
            print(f"[memory] Saved ICP profiles for week {week_key} — {len(rows)} BUs")
        except Exception as e:
            print(f"[memory] SAVE ERROR: {e} — non-fatal")


# ── Module-level singleton + convenience functions ────────────────────────────

_memory = ICPMemory()


def load_prior_week(fiscal_quarter: int = 0) -> dict | None:
    return _memory.load_prior_week(fiscal_quarter)


def save_week(result: dict, fiscal_quarter: int = 0) -> None:
    _memory.save_week(result, fiscal_quarter)


def clear(fiscal_quarter: int = None) -> None:
    _memory.clear(fiscal_quarter)

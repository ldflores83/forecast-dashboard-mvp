"""
shared/utils.py
Shared utility functions for the agentic pipeline.

Functions:
    build_context()   — builds the weekly context dict for SharedState
    source_hash()     — computes a deterministic hash from state data
    fmt_currency()    — formats a float as $X.XM / $XK
    safe_float()      — safe float conversion (returns 0.0 on None/error)
    safe_int()        — safe int conversion (returns 0 on None/error)
"""

import hashlib
import json
from datetime import datetime, timezone, date


def build_context(fiscal_quarter: int = 0) -> dict:
    """
    Builds the weekly context dict injected into SharedState.context.

    Args:
        fiscal_quarter: 0 = full year, 1-4 = specific quarter.

    Returns:
        dict with week, fiscal_quarter, snapshot_date, and fiscal_year.
    """
    now = datetime.now(timezone.utc)

    # Week label: "May 4, 2026"
    day = now.day
    week_label = now.strftime(f"{day} %b %Y")

    return {
        "week":            week_label,
        "fiscal_quarter":  fiscal_quarter,
        "fiscal_year":     2027,
        "snapshot_date":   now.strftime("%Y-%m-%d"),
        "generated_at":    now.isoformat(),
        # Starting ARR — QAD Tableau snapshot Feb 2026
        "starting_arr":    436_700_000.0,
    }


def source_hash(state) -> str:
    """
    Computes a deterministic MD5 hash from key state data fields.

    The hash captures the most signal-rich metrics from each tool output.
    If the underlying BQ data has not changed since the last run, the hash
    will be identical and the orchestrator will use the cached result.

    Only hashes counts and totals, not full deal lists — keeps it stable
    across minor data ordering changes.

    Args:
        state: SharedState instance with pipeline_data, renewal_data,
               winloss_data populated.

    Returns:
        8-character hex string (first 8 chars of MD5).
    """
    pd = state.pipeline_data or {}
    rd = state.renewal_data  or {}
    wd = state.winloss_data  or {}

    key_data = {
        # Pipeline signals
        "flagged_deal_count":    len(pd.get("flagged_deals", [])),
        "total_open_acv":        safe_float(pd.get("total_open_sales_acv")),
        "pushed_5x_count":       safe_int(pd.get("pushed_5x_count")),
        "no_activity_count":     safe_int(pd.get("no_activity_count")),
        "overdue_close_count":   safe_int(pd.get("overdue_close_count")),

        # Renewal signals
        "high_risk_count":       len(rd.get("high_risk_accounts", [])),
        "total_atr_at_risk":     safe_float(rd.get("total_atr_at_risk")),
        "renewal_win_rate":      safe_float(rd.get("overall_renewal_win_rate")),
        "total_churn_acv":       safe_float(rd.get("total_churn_acv")),

        # Win/loss signals
        "closed_won_count":      safe_int(wd.get("closed_won_count")),
        "closed_lost_count":     safe_int(wd.get("closed_lost_count")),
        "top_loss_reason":       wd.get("top_loss_reason", ""),

        # Quarter
        "fiscal_quarter":        state.context.get("fiscal_quarter", 0),
    }

    serialized = json.dumps(key_data, sort_keys=True)
    return hashlib.md5(serialized.encode()).hexdigest()[:8]


def fmt_currency(value, decimals: int = 1) -> str:
    """
    Formats a numeric value as a compact currency string.

    Examples:
        4_200_000  → "$4.2M"
        340_000    → "$340K"
        0          → "$0"
        None       → "$—"
    """
    if value is None:
        return "$—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "$—"

    if v == 0:
        return "$0"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.{decimals}f}M"
    if abs(v) >= 1_000:
        return f"${round(v / 1_000)}K"
    return f"${round(v)}"


def safe_float(value, default: float = 0.0) -> float:
    """Safe float conversion — returns default on None or error."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default: int = 0) -> int:
    """Safe int conversion — returns default on None or error."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pct(value, default: str = "—") -> str:
    """Formats a float (0-100 scale) as a percentage string."""
    if value is None:
        return default
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return default


def days_ago_label(date_str: str) -> str:
    """
    Returns a human-readable 'X days ago' label from a date string.
    Returns '—' if date_str is None or unparseable.
    """
    if not date_str:
        return "—"
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        delta = (date.today() - d).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "1 day ago"
        return f"{delta} days ago"
    except (ValueError, TypeError):
        return "—"

"""
copilot/tool_registry.py
MCP-style tool registry for the agentic pipeline's data collection phase.

Each entry has:
    definition  — Anthropic tool schema (name, description, input_schema)
    fn          — Python callable to invoke when Claude calls this tool
    state_field — SharedState attribute to populate with the result

Used by llm_adapter.run_tool_phase() to execute Phase 1 through Claude.
"""

from .tools import get_flagged_deals, get_renewal_health, get_winloss_data

TOOLS = [
    {
        "definition": {
            "name": "get_flagged_deals",
            "description": (
                "Fetches open Sales pipeline deals with active risk signal flags. "
                "Returns flagged deals (top 25 by ACV), pipeline breakdowns by BU and stage, "
                "flag counts (pushed 5x, overdue close, no activity, touch-back overdue), "
                "and pipeline coverage metrics. Required for this week's pipeline risk analysis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fiscal_quarter": {
                        "type": "integer",
                        "description": "Fiscal quarter filter: 0 = full year, 1 = Q1, 2 = Q2, 3 = Q3, 4 = Q4",
                    }
                },
                "required": ["fiscal_quarter"],
            },
        },
        "fn":          get_flagged_deals,
        "state_field": "pipeline_data",
    },
    {
        "definition": {
            "name": "get_renewal_health",
            "description": (
                "Fetches ARR base health signals from renewal data. "
                "Returns BU-level renewal dynamics (win rates, churn, coverage), "
                "high-risk accounts, recent closures in the last 28 days, "
                "and overall churn vs. new-sales coverage metrics. Required for renewal analysis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fiscal_quarter": {
                        "type": "integer",
                        "description": "Fiscal quarter filter: 0 = full year, 1-4 = specific quarter",
                    }
                },
                "required": ["fiscal_quarter"],
            },
        },
        "fn":          get_renewal_health,
        "state_field": "renewal_data",
    },
    {
        "definition": {
            "name": "get_winloss_data",
            "description": (
                "FY2027 closed deals (won and lost), including future-dated "
                "closures which may represent pipeline cleanup decisions. "
                "Returns win rates by motion, loss reason breakdown with systemic threshold check, "
                "deal velocity by motion, and recent wins/losses. Required for win/loss analysis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fiscal_quarter": {
                        "type": "integer",
                        "description": "Fiscal quarter filter: 0 = full year, 1-4 = specific quarter",
                    }
                },
                "required": ["fiscal_quarter"],
            },
        },
        "fn":          get_winloss_data,
        "state_field": "winloss_data",
    },
]

# Fast lookup by tool name
TOOL_MAP = {t["definition"]["name"]: t for t in TOOLS}

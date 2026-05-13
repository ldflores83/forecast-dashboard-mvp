"""
copilot/state.py
Defines SharedState — the single source of truth for the agentic pipeline.

Design rules:
  - Tools WRITE to state (populate data fields).
  - Agents READ from state (never write to data fields).
  - Agents WRITE ONLY to agent_outputs.
  - Reviewer reads state + agent_outputs; writes to review.
  - No LLM-generated content ever enters the data fields.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharedState:
    """
    Central state object passed through the entire agentic pipeline.

    Fields populated by tools (Phase 1):
        context        — week label, fiscal quarter, snapshot date
        pipeline_data  — flagged deals, pipeline by BU/stage, coverage
        renewal_data   — BU dynamics, high-risk accounts, recent closures
        winloss_data   — closed deals last 90 days, loss reasons, win rates

    Fields populated by agents and reviewer (Phase 2):
        agent_outputs  — one key per agent; set after each agent runs
        review         — set by reviewer after all agents complete

    Fields populated by orchestrator (Phase 3):
        meta           — source_hash, generated_at, cache_hit, reviewer_status
    """

    # ── DATA FIELDS (populated by tools — no LLM content) ────────────────────
    context:       dict = field(default_factory=dict)
    pipeline_data: dict = field(default_factory=dict)
    renewal_data:  dict = field(default_factory=dict)
    winloss_data:  dict = field(default_factory=dict)

    # ── OUTPUT FIELDS (populated by agents and reviewer) ──────────────────────
    agent_outputs: dict = field(default_factory=lambda: {
        "pipeline": None,
        "renewal":  None,
        "winloss":  None,
    })
    review: dict = field(default_factory=dict)

    # ── META FIELDS (populated by orchestrator) ───────────────────────────────
    meta: dict = field(default_factory=dict)

    def is_data_ready(self) -> bool:
        """Returns True if all three tool outputs have been populated."""
        return all([
            bool(self.context),
            bool(self.pipeline_data),
            bool(self.renewal_data),
            bool(self.winloss_data),
        ])

    def to_context_summary(self) -> dict:
        """
        Returns a compact summary of state data fields.
        Used by the reviewer to validate agent outputs against grounded data.
        Excludes agent_outputs to prevent circular reference.
        """
        return {
            "context":       self.context,
            "pipeline_data": self.pipeline_data,
            "renewal_data":  self.renewal_data,
            "winloss_data":  self.winloss_data,
        }

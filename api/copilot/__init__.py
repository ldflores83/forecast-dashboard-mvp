"""
copilot/
Agentic AI layer for Revenue Intelligence.

Architecture:
    orchestrator.py  — coordinates the full workflow
    state.py         — SharedState: single source of truth
    tools.py         — deterministic BigQuery tools (no LLM)
    agents.py        — 3 specialist agents (call Claude)
    reviewer.py      — validates agent outputs vs SharedState
    prompts.py       — all prompt templates (centralized)
    llm_adapter.py   — single Claude API adapter
    schemas.py       — output shape definitions
    cache.py         — BigQuery cache (keyed by source_hash)
    utils.py         — shared helpers
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env")

_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if _creds and not Path(_creds).is_absolute():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_ROOT / _creds)

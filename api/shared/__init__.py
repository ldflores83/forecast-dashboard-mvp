"""
shared/
Shared infrastructure for the multi-team agentic platform.

Loads .env and resolves GOOGLE_APPLICATION_CREDENTIALS relative to the project
root so all teams pick up credentials automatically on import.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env")

_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if _creds and not Path(_creds).is_absolute():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_ROOT / _creds)

"""Shared pytest fixtures and setup.

Loads `.testenv` over `.env` so test runs use test-specific configuration
(e.g. a separate log directory).
"""

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".testenv", override=True)

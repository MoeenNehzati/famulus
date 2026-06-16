"""Centralized access to configuration loaded from .env / .testenv.

On import, loads variables from `.env` (project root) into the process
environment via python-dotenv, without overriding variables already set in
the environment. Test code (see tests/conftest.py) loads `.testenv` on top
with override=True so test-only values take precedence during a test run.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


def get(key: str, default: str | None = None) -> str | None:
    """Return the configuration value for `key`, or `default` if unset.

    Args:
        key: Environment variable name.
        default: Value to return if `key` is not set.

    Returns:
        The string value of the environment variable, or `default`.
    """
    return os.environ.get(key, default)

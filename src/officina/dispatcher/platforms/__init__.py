"""Host platform normalization for dispatcher metadata checks."""

from __future__ import annotations

import sys


def current_platform_name() -> str:
    """Return the canonical blueprint platform key for the current host."""

    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


__all__ = ["current_platform_name"]

"""Legacy compatibility surface for the shared dispatcher API."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_officina_on_path() -> None:
    repo_src = Path(__file__).resolve().parents[3] / "src"
    repo_src_str = str(repo_src)
    if repo_src.is_dir() and repo_src_str not in sys.path:
        sys.path.insert(0, repo_src_str)


_ensure_officina_on_path()

from officina.dispatcher import InvocationError, ResolvedInvocation, dispatch, resolve_dispatch

__all__ = [
    "InvocationError",
    "ResolvedInvocation",
    "dispatch",
    "resolve_dispatch",
]

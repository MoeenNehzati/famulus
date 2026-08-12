"""Thin process boundary from CI-debug to the repository-owned check runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence


class RunnerInvocationError(RuntimeError):
    """The canonical repository-check runner cannot be invoked safely."""


def invoke_runner(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    timeout_seconds: int,
) -> int:
    """Invoke the public runner unchanged and return its exit status.

    The runner owns selection, GitHub transport, diagnostics, and JSON output.
    This adapter deliberately adds no second interpretation layer.
    """

    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise RunnerInvocationError("repository root is unavailable") from exc
    runner = root / "repo_checks.py"
    if not root.is_dir() or not runner.is_file() or runner.is_symlink():
        raise RunnerInvocationError("repository-check runner is unavailable")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise RunnerInvocationError("timeout must be a positive integer")
    try:
        completed = subprocess.run(
            (sys.executable, str(runner), *arguments),
            cwd=root,
            check=False,
            shell=False,
            timeout=timeout_seconds + 30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerInvocationError("repository-check runner invocation failed") from exc
    return completed.returncode

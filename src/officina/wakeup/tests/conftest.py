"""Shared protections for the wakeup test package.

These tests exercise code whose job is to notify a human. Left unguarded, they
do it -- on the desktop of whoever runs the suite.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def never_notify_the_real_desktop(monkeypatch):
    """Fail loudly rather than raising a notification on someone's screen.

    monitor_usage() falls back to _default_notifier when no notifier is passed,
    and that runs notify-send. A test that omitted the argument therefore
    popped a real desktop notification about whatever session the developer was
    in -- once per suite run, which during an afternoon of commits meant dozens
    of them, indistinguishable from the real warnings they were meant to be.

    This lives in conftest so it covers every module in the package. As a
    module-level fixture it protected only the file it was written in, and the
    next test file added would have had to remember it independently.
    """
    import officina.wakeup.claude_codex_monitor as monitor

    def _refuse(message: str) -> None:
        pytest.fail(
            "a test reached the real desktop notifier; pass notifier= "
            f"explicitly. message: {message}"
        )

    monkeypatch.setattr(monitor, "_default_notifier", _refuse)

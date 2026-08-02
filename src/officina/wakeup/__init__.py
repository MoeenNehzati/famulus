"""Guarded continuation scheduling for rate-limited LLM sessions.

The package is deterministic infrastructure. It parses provider timeout
messages, stores persistent jobs, detects transcript progress, and resumes
unchanged Claude or Codex sessions after their usage limits reset.

The public CLI has two scheduling routes: ``schedule`` accepts an explicit
provider, session, and deadline; ``infer`` derives those values from supplied
timeout text or the newest structured rate-limit event in local provider logs.
Both routes persist a transcript-state snapshot. Delivery is suppressed when
that snapshot no longer matches, which means the user has already resumed the
conversation independently.

The package deliberately has no database or third-party runtime dependency.
Jobs are stored as an atomically replaced JSON list and execution events are
written to standard output for capture by systemd-journald.
"""

from __future__ import annotations


DEFAULT_MESSAGE = (
    "Your usage limit has reset. Resume the previous task, wake any subagents, "
    "and ensure they are progressing rather than stale."
)


class WakeupError(Exception):
    """Expected operational failure suitable for concise CLI presentation.

    The CLI converts this exception to exit status 2 without a traceback.
    Unexpected programming errors are intentionally not wrapped.
    """


from .claude_codex_cli import main as cli_main


__all__ = ["DEFAULT_MESSAGE", "WakeupError", "cli_main"]

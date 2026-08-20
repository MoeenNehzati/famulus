"""Codex transcript, timeout, executable, and resume adapter."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..deadlines import parse_deadline
from .base import Cutoff, RateLimit


# Seconds. A live refusal writes its line within a second of its own
# ``completed_at``; a replayed one is off by the age of the fork.
FORK_REPLAY_TOLERANCE = 10.0


class CodexAdapter:
    """Interpret Codex JSONL records, including numeric rate-limit windows."""

    name = "codex"

    def transcript_root(self) -> Path:
        """Return Codex's local session transcript directory."""

        return Path(
            os.environ.get("LLM_WAKEUP_CODEX_DIR", "~/.codex/sessions")
        ).expanduser()

    def include_log(self, path: Path) -> bool:
        """Accept Codex JSONL rollout transcripts and reject other files."""

        return True

    def session_id(self, event: dict) -> str | None:
        """Extract the Codex thread identifier from session metadata."""

        if event.get("type") != "session_meta":
            return None
        payload = event.get("payload", {})
        value = payload.get("id") if isinstance(payload, dict) else None
        return value if isinstance(value, str) else None

    def alias(self, event: dict) -> str | None:
        """Return Codex's optional session alias when present."""

        return None

    def indexed_sessions(self, alias: str) -> list[str]:
        """Resolve an alias through Codex's local session index, if available."""

        path = Path(
            os.environ.get("LLM_WAKEUP_CODEX_INDEX", "~/.codex/session_index.jsonl")
        ).expanduser()
        if not path.is_file():
            return []
        matches: list[str] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(entry.get("thread_name", "")).casefold() == alias.casefold():
                value = entry.get("id")
                if isinstance(value, str):
                    matches.append(value)
        return matches

    def rate_limit(self, event: dict) -> RateLimit | None:
        """Report an enforced Codex usage limit with a recoverable reset time.

        This deliberately does not key on ``used_percent >= 100``. In a survey
        of 2308 local rollouts (2026-08-19, a one-time measurement rather than
        a guarantee), that condition appeared in 162 files of which 136 were
        never refused a turn, and in all 38 refusals in that corpus both
        windows read ``null`` at the moment of refusal -- so the percentage
        both missed real refusals and invented ones that never happened.
        """

        cut = self.cutoff(event)
        if cut is None or cut.reset_at is None:
            return None
        return RateLimit(cut.reset_at, cut.observed_at, cut.context)

    def cutoff(self, event: dict) -> Cutoff | None:
        """Identify the Codex record proving a turn was refused for quota.

        The record is an ordinary ``task_complete`` carrying an ``error``
        object, and ``codex_error_info`` is what distinguishes it from a
        normal completion. Observed refusals also have a null
        ``last_agent_message``; that is not required here, because the error
        code alone was unambiguous across every local record. A forked or
        resumed rollout replays its parent's history under fresh wall-clock
        timestamps, so a copied record is rejected by comparing the line
        timestamp with the payload's own ``completed_at``.
        """

        if event.get("type") != "event_msg":
            return None
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "task_complete":
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        if error.get("codex_error_info") != "usage_limit_exceeded":
            return None
        observed = self._observed_at(event)
        completed = payload.get("completed_at")
        if isinstance(completed, (int, float)):
            if abs(observed.timestamp() - float(completed)) > FORK_REPLAY_TOLERANCE:
                return None
        context = str(error.get("message", ""))
        try:
            reset_at: datetime | None = parse_deadline(
                context, now=observed, embedded=True
            )
        except Exception:
            reset_at = None
        return Cutoff(reset_at=reset_at, observed_at=observed, context=context)

    def self_continuation(self, event: dict) -> bool | None:
        """Codex has no self-resume mechanism, so it never claims one."""

        return None

    def _observed_at(self, event: dict) -> datetime:
        """Return the event timestamp, falling back to the current instant."""

        raw = str(event.get("timestamp", "")).replace("Z", "+00:00")
        try:
            observed = datetime.fromisoformat(raw)
        except ValueError:
            observed = datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return observed.astimezone(timezone.utc)

    def meaningful(self, event: dict) -> bool:
        """Identify Codex events that prove post-scheduling progress."""

        if event.get("type") != "response_item":
            return False
        payload = event.get("payload", {})
        return isinstance(payload, dict) and payload.get("type") == "message" and payload.get(
            "role"
        ) in {"user", "assistant"}

    def cwd(self, event: dict) -> Path | None:
        """Extract the working directory from Codex session metadata."""

        if event.get("type") != "session_meta":
            return None
        payload = event.get("payload", {})
        value = payload.get("cwd") if isinstance(payload, dict) else None
        return Path(value).expanduser() if isinstance(value, str) else None

    def executable_override(self) -> str | None:
        """Return the documented or legacy Codex executable override."""

        return os.environ.get("CODEX_EXECUTABLE") or os.environ.get(
            "LLM_WAKEUP_CODEX_BIN"
        )

    def executable_candidates(self) -> tuple[Path, ...]:
        """Return common Codex installation paths."""

        return (
            Path.home() / ".npm-global/bin/codex",
            Path.home() / ".local/bin/codex",
        )

    def resume_command(self, executable: str, session_id: str, message: str) -> list[str]:
        """Build a noninteractive Codex resume with workspace and network access."""

        return [
            executable,
            "--ask-for-approval",
            "never",
            "--sandbox",
            "workspace-write",
            "--config",
            "sandbox_workspace_write.network_access=true",
            "--search",
            "exec",
            "resume",
            session_id,
            message,
        ]

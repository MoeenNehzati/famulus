"""Claude transcript, timeout, executable, and resume adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..deadlines import parse_deadline
from .base import RateLimit, string_leaves


class ClaudeAdapter:
    """Interpret Claude JSONL records without mutating provider-owned state."""

    name = "claude"

    def transcript_root(self) -> Path:
        """Return Claude Code's project transcript directory."""

        return Path(
            os.environ.get("LLM_WAKEUP_CLAUDE_DIR", "~/.claude/projects")
        ).expanduser()

    def include_log(self, path: Path) -> bool:
        """Accept Claude JSONL session transcripts and reject other files."""

        return "subagents" not in path.parts

    def session_id(self, event: dict) -> str | None:
        """Extract Claude's ``sessionId`` field."""

        value = event.get("sessionId")
        return value if isinstance(value, str) else None

    def alias(self, event: dict) -> str | None:
        """Extract Claude's optional human-readable session slug."""

        value = event.get("customTitle")
        return value if isinstance(value, str) else None

    def indexed_sessions(self, alias: str) -> list[str]:
        """Resolve a Claude resume slug through Claude's local index."""

        return []

    def rate_limit(self, event: dict) -> RateLimit | None:
        """Normalize Claude's rate-limit transcript event."""

        if event.get("error") != "rate_limit" and not event.get("isApiErrorMessage"):
            return None
        context = "\n".join(string_leaves(event))
        lowered = context.casefold()
        if "limit" not in lowered or "reset" not in lowered:
            return None
        raw = str(event.get("timestamp", "")).replace("Z", "+00:00")
        try:
            observed = datetime.fromisoformat(raw)
        except ValueError:
            observed = datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed = observed.astimezone(timezone.utc)
        return RateLimit(
            reset_at=parse_deadline(context, now=observed, embedded=True),
            observed_at=observed,
            context=context,
        )

    def meaningful(self, event: dict) -> bool:
        """Identify Claude events that prove post-scheduling progress."""

        role = event.get("type")
        message = event.get("message")
        if isinstance(message, dict):
            role = message.get("role", role)
        return role in {"user", "assistant"}

    def cwd(self, event: dict) -> Path | None:
        """Extract Claude's recorded working directory."""

        value = event.get("cwd")
        return Path(value).expanduser() if isinstance(value, str) else None

    def executable_override(self) -> str | None:
        """Return the documented or legacy Claude executable override."""

        return os.environ.get("CLAUDE_EXECUTABLE") or os.environ.get(
            "LLM_WAKEUP_CLAUDE_BIN"
        )

    def executable_candidates(self) -> tuple[Path, ...]:
        """Return common Claude installation paths."""

        return (Path.home() / ".local/bin/claude",)

    def resume_command(self, executable: str, session_id: str, message: str) -> list[str]:
        """Build a noninteractive Claude resume with local and web permissions."""

        return [
            executable,
            "--print",
            "--permission-mode",
            "auto",
            "--allowedTools",
            "WebFetch,WebSearch",
            "--resume",
            session_id,
            message,
        ]

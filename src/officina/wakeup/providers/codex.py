"""Codex transcript, timeout, executable, and resume adapter."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .base import RateLimit


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
        """Normalize Codex token-count rate-limit windows."""

        if event.get("type") != "event_msg":
            return None
        payload = event.get("payload", {})
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            return None
        limits = payload.get("rate_limits", {})
        if not isinstance(limits, dict):
            return None
        exhausted: list[int] = []
        for key in ("primary", "secondary"):
            window = limits.get(key)
            if not isinstance(window, dict):
                continue
            if float(window.get("used_percent", 0)) < 100:
                continue
            reset = window.get("resets_at")
            if isinstance(reset, (int, float)):
                exhausted.append(int(reset))
        if not exhausted:
            return None
        reset_at = datetime.fromtimestamp(max(exhausted), timezone.utc)
        raw = str(event.get("timestamp", "")).replace("Z", "+00:00")
        try:
            observed = datetime.fromisoformat(raw)
        except ValueError:
            observed = datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        context = f"structured rate limit resets {reset_at.isoformat()}"
        return RateLimit(reset_at, observed.astimezone(timezone.utc), context)

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
        """Return ``CODEX_EXECUTABLE`` when configured."""

        return os.environ.get("LLM_WAKEUP_CODEX_BIN")

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

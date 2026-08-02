"""Claude and Codex transcript discovery, inference, and progress detection.

Claude stores project-scoped JSONL transcripts and title aliases; Codex stores
dated JSONL transcripts plus a session-name index. This module normalizes both
formats into provider/session/transcript tuples without modifying provider
state. Subagent transcripts are excluded from Claude discovery so an automatic
wakeup cannot accidentally target a child instead of the requested chat.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from . import WakeupError


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def claude_dir() -> Path:
    """Return the Claude transcript root, honoring ``LLM_WAKEUP_CLAUDE_DIR``."""
    return Path(os.environ.get("LLM_WAKEUP_CLAUDE_DIR", "~/.claude/projects")).expanduser()


def codex_dir() -> Path:
    """Return the Codex transcript root, honoring ``LLM_WAKEUP_CODEX_DIR``."""
    return Path(os.environ.get("LLM_WAKEUP_CODEX_DIR", "~/.codex/sessions")).expanduser()


def codex_index() -> Path:
    """Return the Codex alias index, honoring ``LLM_WAKEUP_CODEX_INDEX``."""
    return Path(
        os.environ.get("LLM_WAKEUP_CODEX_INDEX", "~/.codex/session_index.jsonl")
    ).expanduser()


def json_lines(path: Path, *, strict: bool = False) -> Iterator[dict]:
    """Yield mapping-valued JSONL records while tolerating malformed lines.

    Provider transcripts can contain a partially written final line while a
    chat is active. Such lines are ignored. ``strict`` controls only file I/O:
    unreadable or missing transcripts become ``WakeupError`` during guarded
    delivery, while discovery treats them as absent candidates.
    """
    try:
        with path.open(errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError as error:
        if strict:
            raise WakeupError(f"could not read transcript {path}: {error}") from error


def session_logs(provider: str) -> list[Path]:
    """List provider session transcripts, excluding Claude subagent logs."""
    root = claude_dir() if provider == "claude" else codex_dir()
    if not root.is_dir():
        return []
    logs = root.rglob("*.jsonl")
    if provider == "claude":
        return [path for path in logs if "subagents" not in path.parts]
    return list(logs)


def session_id_from_log(provider: str, path: Path) -> str | None:
    """Extract a normalized session UUID from a filename or metadata record."""
    filename_id = UUID_RE.search(path.name)
    if filename_id:
        return filename_id.group(0).lower()
    for event in json_lines(path):
        if provider == "claude":
            candidate = event.get("sessionId")
        else:
            payload = event.get("payload", {})
            candidate = payload.get("id") if event.get("type") == "session_meta" else None
        if isinstance(candidate, str) and UUID_RE.fullmatch(candidate):
            return candidate.lower()
    return None


def find_session_log(provider: str, session_id: str) -> Path | None:
    """Find the transcript belonging to an exact normalized session UUID."""
    target = session_id.lower()
    for path in session_logs(provider):
        if target in path.name.lower() or session_id_from_log(provider, path) == target:
            return path
    return None


def _resolve_alias(provider: str, alias: str, context: str) -> str | None:
    """Resolve a provider title alias, rejecting unsafe ambiguity.

    Claude titles may legitimately repeat across worktrees. When timeout text
    includes ``--worktree``, its token is used only to disambiguate otherwise
    matching aliases; unresolved duplicates remain a hard error.
    """
    candidates: list[tuple[str, Path]] = []
    if provider == "codex" and codex_index().is_file():
        for entry in json_lines(codex_index()):
            if str(entry.get("thread_name", "")).casefold() != alias.casefold():
                continue
            candidate = str(entry.get("id", ""))
            path = find_session_log(provider, candidate)
            if UUID_RE.fullmatch(candidate) and path:
                candidates.append((candidate.lower(), path))

    if provider == "claude":
        for path in session_logs(provider):
            for event in json_lines(path):
                if str(event.get("customTitle", "")).casefold() != alias.casefold():
                    continue
                candidate = str(event.get("sessionId", ""))
                if UUID_RE.fullmatch(candidate):
                    candidates.append((candidate.lower(), path))
                break

    unique = list({session_id: path for session_id, path in candidates}.items())
    if len(unique) == 1:
        return unique[0][0]
    if len(unique) > 1 and provider == "claude":
        worktree = re.search(
            r"--worktree(?:=|\s+)[\"']?([^\s\"']+)",
            context,
            re.IGNORECASE,
        )
        if worktree:
            token = worktree.group(1).casefold()
            matches = [
                session_id
                for session_id, path in unique
                if token in str(path).casefold()
            ]
            if len(matches) == 1:
                return matches[0]
    if len(unique) > 1:
        raise WakeupError(f'{provider} session alias "{alias}" is ambiguous')
    return None


def resolve_session(provider: str, value: str, context: str = "") -> tuple[str, Path]:
    """Resolve a UUID or provider alias to an exact session and transcript."""
    identifier = value.strip().strip("\"'")
    session_id = (
        identifier.lower()
        if UUID_RE.fullmatch(identifier)
        else _resolve_alias(provider, identifier, context)
    )
    if not session_id:
        raise WakeupError(f'could not resolve {provider} session "{identifier}"')
    path = find_session_log(provider, session_id)
    if not path:
        raise WakeupError(f"could not find transcript for {provider} session {session_id}")
    return session_id, path


def latest_session(provider: str) -> tuple[str, Path]:
    """Return the newest unambiguous provider session.

    Sessions modified within 30 seconds of each other are treated as ambiguous
    because concurrent agents commonly update several transcripts together.
    """
    candidates = sorted(
        session_logs(provider),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    resolved = [(session_id_from_log(provider, path), path) for path in candidates[:10]]
    resolved = [(session_id, path) for session_id, path in resolved if session_id]
    if not resolved:
        raise WakeupError(f"could not find a recent {provider} session")
    if len(resolved) > 1:
        newest = resolved[0][1].stat().st_mtime
        second = resolved[1][1].stat().st_mtime
        if newest - second < 30:
            raise WakeupError(
                f"recent {provider} session is ambiguous; include its resume command"
            )
    return resolved[0]


def infer_provider(text: str) -> str:
    """Infer exactly one provider from timeout or resume-command text."""
    lowered = text.lower()
    has_claude = bool(re.search(r"\bclaude\b", lowered))
    has_codex = bool(re.search(r"\bcodex\b", lowered))
    if has_claude != has_codex:
        return "claude" if has_claude else "codex"
    if "session limit" in lowered and not has_codex:
        return "claude"
    raise WakeupError("could not infer provider; include the Claude or Codex resume command")


def infer_session_token(provider: str, text: str) -> str | None:
    """Extract a resume alias or sole UUID from provider output, if present."""
    if provider == "claude":
        match = re.search(
            r"\bclaude\b[^\n]*?--resume(?:=|\s+)[\"']?([^\s\"']+)",
            text,
            re.IGNORECASE,
        )
    else:
        match = re.search(
            r"\bcodex\b[^\n]*?\bresume\s+[\"']?([^\s\"']+)",
            text,
            re.IGNORECASE,
        )
    if match:
        return match.group(1)
    identifiers = UUID_RE.findall(text)
    return identifiers[0] if len(identifiers) == 1 else None


def _event_text(value: object) -> Iterator[str]:
    """Yield string leaves without flattening an entire transcript into memory."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _event_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from _event_text(child)


def _is_rate_limit_event(provider: str, event: dict, text: str) -> bool:
    """Recognize structured provider error records, not quoted user prose."""
    lowered = text.casefold()
    mentions_limit = "limit" in lowered and (
        "reset" in lowered or "try again" in lowered
    )
    if not mentions_limit:
        return False
    if provider == "claude":
        return event.get("error") == "rate_limit" or bool(
            event.get("isApiErrorMessage")
        )
    payload = event.get("payload", {})
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    return event.get("type") in {"event_msg", "turn_aborted"} and payload_type in {
        "error",
        "turn_aborted",
        "warning",
    }


def latest_rate_limit() -> tuple[str, str, str]:
    """Return provider, session UUID, and text for the newest rate-limit event.

    Only the 20 most recently modified transcripts per provider are scanned.
    This bounds no-input inference while covering the expected workflow where
    the user invokes the helper immediately after leaving a limited session.

    Raises:
        WakeupError: If no structured rate-limit event is available.
    """

    matches: list[tuple[datetime, str, str, str]] = []
    for provider in ("claude", "codex"):
        paths = sorted(
            session_logs(provider),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )[:20]
        for path in paths:
            session_id = session_id_from_log(provider, path)
            if not session_id:
                continue
            for event in json_lines(path):
                text = "\n".join(_event_text(event))
                if not _is_rate_limit_event(provider, event, text):
                    continue
                raw_timestamp = event.get("timestamp")
                try:
                    timestamp = datetime.fromisoformat(
                        str(raw_timestamp).replace("Z", "+00:00")
                    )
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                matches.append((timestamp, provider, session_id, text))
    if not matches:
        raise WakeupError("could not find a recent structured rate-limit event")
    _, provider, session_id, text = max(matches, key=lambda item: item[0])
    return provider, session_id, text


def _meaningful_event(provider: str, event: dict) -> bool:
    """Identify user/assistant turns that constitute conversation progress."""
    if provider == "claude":
        role = event.get("type")
        message = event.get("message")
        if isinstance(message, dict):
            role = message.get("role", role)
        return role in {"user", "assistant"}
    if event.get("type") != "response_item":
        return False
    payload = event.get("payload", {})
    return payload.get("type") == "message" and payload.get("role") in {
        "user",
        "assistant",
    }


def transcript_state(provider: str, path: Path) -> str:
    """Hash the latest meaningful user or assistant event in a transcript.

    The hash is a lightweight progress token, not an integrity guarantee. A
    changed token means either the user or provider advanced the conversation,
    so a scheduled wakeup must be suppressed.
    """
    latest = ""
    for event in json_lines(path, strict=True):
        if _meaningful_event(provider, event):
            encoded = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
            latest = hashlib.sha256(encoded).hexdigest()
    return latest


def session_cwd(provider: str, path: Path) -> Path | None:
    """Return the latest existing working directory recorded by a session.

    Provider resume commands are project-scoped. Running them from systemd's
    default home directory can make a valid UUID appear missing, so delivery
    reuses this directory when available.
    """

    latest: Path | None = None
    for event in json_lines(path, strict=True):
        candidate = event.get("cwd")
        if provider == "codex" and event.get("type") == "session_meta":
            payload = event.get("payload", {})
            if isinstance(payload, dict):
                candidate = payload.get("cwd", candidate)
        if isinstance(candidate, str):
            resolved = Path(candidate).expanduser()
            if resolved.is_dir():
                latest = resolved
    return latest


__all__ = [
    "infer_provider",
    "infer_session_token",
    "latest_rate_limit",
    "latest_session",
    "resolve_session",
    "session_cwd",
    "transcript_state",
]

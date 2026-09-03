"""Acquire and normalize local Claude Code and Codex quota observations.

Claude Code pushes status-line JSON through :func:`capture_claude_status`.
Codex usage is read from the newest structured ``token_count`` event in a
transcript tail. Both paths return the same immutable ``UsageSnapshot`` model
and never contact a provider API or invoke an LLM.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from . import WakeupError
from ._claude_codex_sessions import (
    find_session_log,
    latest_session,
    transcript_tail_lines,
)
from ._wakeup_policies import auto_scheduled_sessions
from ._wakeup_providers import provider_for
from ._wakeup_store import data_dir


@dataclass(frozen=True)
class UsageSnapshot:
    """One provider quota window observed for one resumable session."""

    provider: Literal["claude", "codex"]
    session_id: str
    window: str
    used_percentage: float
    resets_at: int
    transcript_path: str
    observed_at: str


def _snapshot_path(snapshot: UsageSnapshot) -> Path:
    """Return the stable per-provider/session/window snapshot path."""

    key = f"{snapshot.provider}:{snapshot.session_id}:{snapshot.window}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return data_dir() / "usage-snapshots" / f"{digest}.json"


def _save_snapshot(snapshot: UsageSnapshot) -> None:
    """Atomically replace one independently keyed quota-window snapshot."""

    path = _snapshot_path(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(snapshot), sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def capture_claude_status(
    payload: dict, *, observed_at: datetime | None = None
) -> list[UsageSnapshot]:
    """Persist all valid quota windows in one Claude status-line payload.

    Claude may omit rate limits before its first successful API response.
    Missing session metadata or quota data therefore returns an empty list
    rather than treating normal startup as an error.
    """

    session_id = str(payload.get("session_id") or "").strip()
    transcript_path = str(payload.get("transcript_path") or "").strip()
    limits = payload.get("rate_limits")
    if not session_id or not transcript_path or not isinstance(limits, dict):
        return []
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    ).isoformat()
    snapshots: list[UsageSnapshot] = []
    for window in ("five_hour", "seven_day"):
        record = limits.get(window)
        if not isinstance(record, dict):
            continue
        try:
            percentage = float(record["used_percentage"])
            resets_at = int(record["resets_at"])
        except (KeyError, TypeError, ValueError):
            continue
        snapshot = UsageSnapshot(
            provider="claude",
            session_id=session_id,
            window=window,
            used_percentage=percentage,
            resets_at=resets_at,
            transcript_path=transcript_path,
            observed_at=observed,
        )
        _save_snapshot(snapshot)
        snapshots.append(snapshot)
    return snapshots


def read_codex_usage(path: Path, session_id: str) -> list[UsageSnapshot]:
    """Normalize the newest valid Codex quota record in one transcript."""

    rate_limits: dict | None = None
    for line in reversed(transcript_tail_lines(path)):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        if (
            isinstance(event, dict)
            and event.get("type") == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "token_count"
            and isinstance(payload.get("rate_limits"), dict)
        ):
            rate_limits = payload["rate_limits"]
            break
    if rate_limits is None:
        return []
    observed = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    snapshots: list[UsageSnapshot] = []
    for window in ("primary", "secondary"):
        record = rate_limits.get(window)
        if not isinstance(record, dict):
            continue
        try:
            percentage = float(record["used_percent"])
            resets_at = int(record["resets_at"])
        except (KeyError, TypeError, ValueError):
            continue
        snapshots.append(
            UsageSnapshot(
                provider="codex",
                session_id=session_id,
                window=window,
                used_percentage=percentage,
                resets_at=resets_at,
                transcript_path=str(path),
                observed_at=observed,
            )
        )
    return snapshots


def read_claude_exhaustion(
    path: Path, session_id: str
) -> UsageSnapshot | None:
    """Normalize the newest exhausted-limit event in a Claude transcript tail."""

    adapter = provider_for("claude")
    for line in reversed(transcript_tail_lines(path)):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        limit = adapter.rate_limit(event)
        if limit is None:
            continue
        return UsageSnapshot(
            provider="claude",
            session_id=session_id,
            window="exhausted",
            used_percentage=100.0,
            resets_at=int(limit.reset_at.timestamp()),
            transcript_path=str(path),
            observed_at=limit.observed_at.astimezone(timezone.utc).isoformat(),
        )
    return None


def _saved_claude_snapshots() -> list[UsageSnapshot]:
    """Load valid Claude snapshots while ignoring corrupt independent records."""

    directory = data_dir() / "usage-snapshots"
    paths = directory.glob("*.json") if directory.exists() else ()
    snapshots: list[UsageSnapshot] = []
    for path in paths:
        try:
            snapshot = UsageSnapshot(**json.loads(path.read_text()))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if snapshot.provider == "claude":
            snapshots.append(snapshot)
    return snapshots


def _observable_codex_snapshots() -> list[UsageSnapshot]:
    """Read the latest Codex session plus every auto-enabled Codex session."""

    targets = set(auto_scheduled_sessions("codex"))
    try:
        targets.add(latest_session("codex")[0])
    except (OSError, WakeupError):
        pass
    snapshots: list[UsageSnapshot] = []
    for session_id in targets:
        transcript = find_session_log("codex", session_id)
        if transcript is not None:
            snapshots.extend(read_codex_usage(transcript, session_id))
    return snapshots


def _observable_claude_exhaustions() -> list[UsageSnapshot]:
    """Read exact exhaustion events for the latest and auto-enabled sessions."""

    targets = set(auto_scheduled_sessions("claude"))
    try:
        targets.add(latest_session("claude")[0])
    except (OSError, WakeupError):
        pass
    snapshots: list[UsageSnapshot] = []
    for session_id in targets:
        transcript = find_session_log("claude", session_id)
        if transcript is None:
            continue
        snapshot = read_claude_exhaustion(transcript, session_id)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def observable_usage_snapshots() -> list[UsageSnapshot]:
    """Return every locally available snapshot relevant to this monitor pass."""

    return [
        *_saved_claude_snapshots(),
        *_observable_claude_exhaustions(),
        *_observable_codex_snapshots(),
    ]


__all__ = [
    "UsageSnapshot",
    "capture_claude_status",
    "observable_usage_snapshots",
    "read_claude_exhaustion",
    "read_codex_usage",
]

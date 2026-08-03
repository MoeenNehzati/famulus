"""Apply wakeup policy to normalized local quota observations.

This module owns decisions and side effects, not provider parsing. Each pass
groups current quota windows by session, suppresses observations below the
near-limit threshold, and performs at most one deduplicated reminder or
schedule action for the constraining reset time.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal

from .claude_codex_service import schedule
from .policies import auto_schedule_enabled
from .store import data_dir
from .claude_codex_usage import UsageSnapshot, observable_usage_snapshots


NEAR_LIMIT_PERCENT = 90.0
DEFAULT_WAKEUP_DELAY = timedelta(minutes=1)


@dataclass(frozen=True)
class MonitorAction:
    """One externally visible decision made by a monitor pass."""

    kind: Literal["scheduled", "reminded"]
    provider: str
    session_id: str
    resets_at: int
    used_percentage: float


def _utc_now() -> datetime:
    """Return an aware UTC instant; isolated to make monitor tests deterministic."""

    return datetime.now(timezone.utc)


def _event_key(snapshots: list[UsageSnapshot], kind: str) -> str:
    """Identify one policy outcome across repeated minute-level checks."""

    windows = ",".join(sorted(item.window for item in snapshots))
    reset = max(item.resets_at for item in snapshots)
    first = snapshots[0]
    return f"{kind}:{first.provider}:{first.session_id}:{windows}:{reset}"


def _event_marker_path(key: str) -> Path:
    """Map a semantic monitor event key to its persistent deduplication marker."""

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return data_dir() / "monitor-events" / f"{digest}.json"


def _claim_event(key: str) -> Path | None:
    """Atomically claim one outcome, returning None when already handled."""

    path = _event_marker_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"key": key, "created_at": _utc_now().isoformat()}, stream)
        stream.write("\n")
    return path


def _default_notifier(message: str) -> None:
    """Emit journald-friendly output and a best-effort desktop notification."""

    print(json.dumps({"event": "usage-near-limit", "message": message}))
    executable = shutil.which("notify-send")
    if executable is not None:
        subprocess.run(
            [executable, "LLM usage nearing limit", message],
            check=False,
            timeout=5,
        )


def _near_limit_groups(
    snapshots: Iterable[UsageSnapshot], now: datetime
) -> list[list[UsageSnapshot]]:
    """Group actionable, unexpired quota windows by provider session."""

    grouped: dict[tuple[str, str], list[UsageSnapshot]] = {}
    now_epoch = int(now.timestamp())
    for snapshot in snapshots:
        if (
            snapshot.used_percentage < NEAR_LIMIT_PERCENT
            or snapshot.resets_at <= now_epoch
        ):
            continue
        grouped.setdefault((snapshot.provider, snapshot.session_id), []).append(
            snapshot
        )
    return list(grouped.values())


def _reminder(group: list[UsageSnapshot], percentage: float) -> str:
    """Build the actionable manual-policy reminder for one session."""

    first = group[0]
    return (
        f"{first.provider} session {first.session_id} is at {percentage:g}% usage; "
        f"run lw auto on {first.provider} {first.session_id} to schedule its wakeup."
    )


def monitor_usage(
    *,
    now: datetime | None = None,
    notifier: Callable[[str], None] | None = None,
) -> list[MonitorAction]:
    """Evaluate all local observations and perform each new outcome once.

    Scheduling uses the latest reset among all near-limit windows for a session,
    then adds the standard one-minute delay. If a side effect fails, its marker
    is removed so the next systemd pass can retry.
    """

    current = (now or _utc_now()).astimezone(timezone.utc)
    notify = notifier or _default_notifier
    actions: list[MonitorAction] = []
    for group in _near_limit_groups(observable_usage_snapshots(), current):
        first = group[0]
        reset = max(item.resets_at for item in group)
        percentage = max(item.used_percentage for item in group)
        automatic = auto_schedule_enabled(first.provider, first.session_id)
        kind = "scheduled" if automatic else "reminded"
        marker = _claim_event(_event_key(group, kind))
        if marker is None:
            continue
        try:
            if automatic:
                schedule(
                    first.provider,
                    first.session_id,
                    datetime.fromtimestamp(reset, tz=timezone.utc)
                    + DEFAULT_WAKEUP_DELAY,
                    None,
                    transcript_path=Path(first.transcript_path),
                )
            else:
                notify(_reminder(group, percentage))
        except Exception:
            marker.unlink(missing_ok=True)
            raise
        actions.append(
            MonitorAction(
                kind=kind,
                provider=first.provider,
                session_id=first.session_id,
                resets_at=reset,
                used_percentage=percentage,
            )
        )
    return actions


__all__ = [
    "DEFAULT_WAKEUP_DELAY",
    "MonitorAction",
    "NEAR_LIMIT_PERCENT",
    "monitor_usage",
]

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

from ._claude_codex_cutoff import Cutoff, detect_cutoff
from ._claude_codex_service import schedule
from ._claude_codex_sessions import find_session_log
from ._wakeup_policies import FORCE, INTERRUPTED, auto_schedule_level, auto_scheduled_sessions
from ._wakeup_store import data_dir
from ._claude_codex_usage import UsageSnapshot, observable_usage_snapshots


NEAR_LIMIT_PERCENT = 90.0
DEFAULT_WAKEUP_DELAY = timedelta(minutes=1)


@dataclass(frozen=True)
class MonitorAction:
    """One externally visible decision made by a monitor pass.

    ``used_percentage`` is ``None`` for a cut-off-driven action: nothing was
    measured, a refusal was observed. Codex reports no percentage at all at
    the moment it refuses a turn, and Claude's can sit well below 100.
    """

    kind: Literal["scheduled", "reminded"]
    provider: str
    session_id: str
    resets_at: int
    used_percentage: float | None = None


def _utc_now() -> datetime:
    """Return an aware UTC instant; isolated to make monitor tests deterministic."""

    return datetime.now(timezone.utc)


def _event_key(snapshots: list[UsageSnapshot], kind: str) -> str:
    """Identify one policy outcome across repeated minute-level checks."""

    # Deliberately excludes the window set. It used to be part of the key, so a
    # session already notified for `five_hour` was notified again the minute it
    # also crossed `exhausted` -- one condition, two popups, a minute apart.
    # What the user needs to hear once is "this session is near its limit until
    # <reset>", which is exactly (session, reset).
    reset = max(item.resets_at for item in snapshots)
    first = snapshots[0]
    return f"{kind}:{first.provider}:{first.session_id}:{reset}"


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
        f"run lw auto on {first.provider} {first.session_id} to wake it if the "
        f"limit stops it, or lw auto force {first.provider} {first.session_id} to "
        "wake it at reset either way."
    )


def observable_cutoffs() -> list[tuple[Cutoff, str]]:
    """Return the newest quota refusal for each enabled session, with its level.

    Both levels are scanned. Refusal evidence is the stronger signal of the
    two, and a rejection can arrive while reported utilization is well below
    the near-limit threshold, so restricting this to the conditional level
    would leave a forced session unwoken in exactly the case it most wants.
    """

    found: list[tuple[Cutoff, str]] = []
    for provider in ("claude", "codex"):
        for level in (INTERRUPTED, FORCE):
            for session_id in auto_scheduled_sessions(provider, level):
                transcript = find_session_log(provider, session_id)
                if transcript is None:
                    continue
                cut = detect_cutoff(provider, transcript, session_id)
                if cut is not None:
                    found.append((cut, level))
    return found


def _schedule_cutoffs(current: datetime) -> tuple[list[MonitorAction], set[tuple[str, str]]]:
    """Schedule one wakeup per session whose refusal is still unanswered.

    Also returns the sessions handled here, so the percentage route does not
    queue a second job for the same session in the same pass.
    """

    actions: list[MonitorAction] = []
    handled: set[tuple[str, str]] = set()
    for cut, level in observable_cutoffs():
        if not cut.wakeable or cut.reset_at <= current:
            continue
        reset = int(cut.reset_at.timestamp())
        marker = _claim_event(f"cutoff:{cut.provider}:{cut.session_id}:{reset}")
        if marker is None:
            handled.add((cut.provider, cut.session_id))
            continue
        try:
            schedule(
                cut.provider,
                cut.session_id,
                cut.reset_at + DEFAULT_WAKEUP_DELAY,
                None,
                transcript_path=cut.transcript_path,
                level=level,
            )
        except Exception:
            marker.unlink(missing_ok=True)
            raise
        handled.add((cut.provider, cut.session_id))
        actions.append(
            MonitorAction(
                kind="scheduled",
                provider=cut.provider,
                session_id=cut.session_id,
                resets_at=reset,
            )
        )
    return actions, handled


def monitor_usage(
    *,
    now: datetime | None = None,
    notifier: Callable[[str], None] | None = None,
) -> list[MonitorAction]:
    """Evaluate all local observations and perform each new outcome once.

    Two routes reach a wakeup. Refusal evidence is checked first, for every
    enabled session: the wakeup is scheduled only once the provider has
    actually refused a turn and nothing has happened since, and its reset comes
    from the refusal itself. A session enabled at ``force`` then also takes the
    older near-limit-percentage route, which fires whether or not the session
    was stopped; a session enabled at ``interrupted`` does not. Sessions with no
    policy are only ever reminded.

    If a side effect fails, its marker is removed so the next systemd pass can
    retry.
    """

    current = (now or _utc_now()).astimezone(timezone.utc)
    notify = notifier or _default_notifier
    actions, handled = _schedule_cutoffs(current)
    for group in _near_limit_groups(observable_usage_snapshots(), current):
        first = group[0]
        reset = max(item.resets_at for item in group)
        percentage = max(item.used_percentage for item in group)
        level = auto_schedule_level(first.provider, first.session_id)
        if level == INTERRUPTED or (first.provider, first.session_id) in handled:
            # Decided above, on evidence rather than on a percentage.
            continue
        kind = "scheduled" if level == FORCE else "reminded"
        marker = _claim_event(_event_key(group, kind))
        if marker is None:
            continue
        try:
            if level == FORCE:
                schedule(
                    first.provider,
                    first.session_id,
                    datetime.fromtimestamp(reset, tz=timezone.utc)
                    + DEFAULT_WAKEUP_DELAY,
                    None,
                    transcript_path=Path(first.transcript_path),
                    level=FORCE,
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
    "observable_cutoffs",
    "MonitorAction",
    "NEAR_LIMIT_PERCENT",
    "monitor_usage",
]

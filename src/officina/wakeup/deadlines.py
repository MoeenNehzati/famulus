"""Deadline parsing for explicit and provider-reported wakeup times.

All returned datetimes are timezone-aware UTC values. Explicit input and text
embedded in provider output differ deliberately when a clock has passed: an
explicit clock rolls to tomorrow, while an expired provider reset is due now.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import WakeupError


DURATION_RE = re.compile(
    r"(?P<number>\d+)\s*(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b",
    re.IGNORECASE,
)


def utc_now() -> datetime:
    """Return the current UTC time, honoring the deterministic test override."""
    override = os.environ.get("LLM_WAKEUP_NOW")
    value = datetime.fromisoformat(override) if override else datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def _duration(match: re.Match[str]) -> timedelta:
    """Convert a matched human duration into a ``timedelta``."""
    number = int(match.group("number"))
    unit = match.group("unit").lower()
    if unit.startswith(("s", "sec")):
        return timedelta(seconds=number)
    if unit.startswith(("m", "min")):
        return timedelta(minutes=number)
    if unit.startswith(("h", "hr")):
        return timedelta(hours=number)
    return timedelta(days=number)


def parse_deadline(
    value: str,
    now: datetime | None = None,
    *,
    embedded: bool = False,
) -> datetime:
    """Parse a duration, ISO timestamp, or clock time and return UTC.

    Explicit past clock times mean the next occurrence. A reset clock embedded
    in provider output means "now" when it has already passed, because the
    restriction is already lifted and rolling to tomorrow would be unsafe.

    Args:
        value: Explicit deadline text or provider output containing a deadline.
        now: Optional timezone-aware reference instant, primarily for tests.
        embedded: Search provider prose rather than requiring a full match.

    Raises:
        WakeupError: If no supported deadline or timezone can be resolved.
    """
    now = now or utc_now()
    text = value.strip()

    duration = DURATION_RE.search(text) if embedded else DURATION_RE.fullmatch(text)
    if duration and (
        not embedded
        or re.search(
            r"\b(?:in|after)\s+~?\s*" + re.escape(duration.group(0)),
            text,
            re.IGNORECASE,
        )
    ):
        return now + _duration(duration)

    if not embedded:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=now.astimezone().tzinfo)
            return parsed.astimezone(timezone.utc)

    reset_clock = re.search(
        r"(?:resets?(?:\s+at)?|until)\s+"
        r"(?P<clock>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
        r"(?:\s*\((?P<zone>[^)]+)\))?",
        text,
        re.IGNORECASE,
    )
    plain_clock = re.fullmatch(
        r"(?P<clock>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
        r"(?:\s*\((?P<zone>[^)]+)\))?",
        text,
        re.IGNORECASE,
    )
    clock_match = reset_clock or plain_clock
    if not clock_match:
        raise WakeupError(f"could not parse deadline: {value}")

    zone_name = clock_match.group("zone")
    try:
        zone = ZoneInfo(zone_name) if zone_name else now.astimezone().tzinfo
    except ZoneInfoNotFoundError as error:
        raise WakeupError(f"unknown timezone: {zone_name}") from error
    local_now = now.astimezone(zone)
    clock_text = clock_match.group("clock").replace(" ", "")
    clock_format = "%I:%M%p" if ":" in clock_text else "%I%p"
    clock = datetime.strptime(clock_text, clock_format)
    candidate = datetime.combine(local_now.date(), clock.time(), tzinfo=zone)
    if candidate <= local_now:
        if embedded:
            return now
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


__all__ = ["parse_deadline", "utc_now"]

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
DEFAULT_DELAY = timedelta(minutes=1)
CLOCK_PATTERN = r"\d{1,2}(?::\d{2})?\s*(?:am|pm)"
CALENDAR_RESET_RE = re.compile(
    r"(?:resets?(?:\s+at)?|until)\s+"
    r"(?:(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2}),?\s*|"
    r"(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day|sday|nesday|rsday|day|urday)?\s+)"
    rf"(?P<clock>{CLOCK_PATTERN})"
    r"(?:\s*\((?P<zone>[^)]+)\))?",
    re.IGNORECASE,
)
WEEKDAYS = {name: index for index, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}


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


def parse_delay(value: str) -> timedelta:
    """Parse a non-negative full-match duration used after a reset time."""

    match = DURATION_RE.fullmatch(value.strip())
    if not match:
        raise WakeupError(f"could not parse delay: {value}")
    return _duration(match)


def _zone_for(name: str | None, now: datetime):
    """Resolve a provider timezone, falling back to the caller's local zone."""

    try:
        return ZoneInfo(name) if name else now.astimezone().tzinfo
    except ZoneInfoNotFoundError as error:
        raise WakeupError(f"unknown timezone: {name}") from error


def _clock_time(value: str):
    """Parse a compact 12-hour provider clock into a naive time value."""

    compact = value.replace(" ", "")
    clock_format = "%I:%M%p" if ":" in compact else "%I%p"
    return datetime.strptime(compact, clock_format).time()


def _calendar_reset(text: str, now: datetime) -> datetime | None:
    """Parse dated or weekday reset prose and return an exact UTC instant."""

    match = CALENDAR_RESET_RE.search(text)
    if match is None:
        return None
    zone = _zone_for(match.group("zone"), now)
    local_now = now.astimezone(zone)
    clock = _clock_time(match.group("clock"))
    if match.group("month"):
        month_text = match.group("month")
        month = None
        for month_format in ("%b", "%B"):
            try:
                month = datetime.strptime(month_text, month_format).month
                break
            except ValueError:
                continue
        if month is None:
            raise WakeupError(f"could not parse reset month: {month_text}")
        candidate = datetime(
            local_now.year,
            month,
            int(match.group("day")),
            clock.hour,
            clock.minute,
            tzinfo=zone,
        )
        if candidate <= local_now:
            candidate = candidate.replace(year=candidate.year + 1)
    else:
        target = WEEKDAYS[match.group("weekday")[:3].lower()]
        days_ahead = (target - local_now.weekday()) % 7
        candidate = datetime.combine(
            local_now.date() + timedelta(days=days_ahead),
            clock,
            tzinfo=zone,
        )
        if candidate <= local_now:
            candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


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

    calendar_reset = _calendar_reset(text, now)
    if calendar_reset is not None:
        return calendar_reset

    reset_clock = re.search(
        r"(?:resets?(?:\s+at)?|until)\s+"
        rf"(?P<clock>{CLOCK_PATTERN})"
        r"(?:\s*\((?P<zone>[^)]+)\))?",
        text,
        re.IGNORECASE,
    )
    plain_clock = re.fullmatch(
        rf"(?P<clock>{CLOCK_PATTERN})"
        r"(?:\s*\((?P<zone>[^)]+)\))?",
        text,
        re.IGNORECASE,
    )
    clock_match = reset_clock or plain_clock
    if not clock_match:
        raise WakeupError(f"could not parse deadline: {value}")

    zone = _zone_for(clock_match.group("zone"), now)
    local_now = now.astimezone(zone)
    clock = _clock_time(clock_match.group("clock"))
    candidate = datetime.combine(local_now.date(), clock, tzinfo=zone)
    if candidate <= local_now:
        if embedded:
            return now
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


__all__ = ["DEFAULT_DELAY", "parse_deadline", "parse_delay", "utc_now"]

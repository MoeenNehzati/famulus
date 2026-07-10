"""Project-owned date and time IO formatting helpers.

Keep reusable storage/display date formats here instead of relying on
platform-specific ``strftime`` padding behavior in individual skills.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_DATE_KEY_PATTERN = re.compile(r"^(?P<month>\d{1,2})-(?P<day>\d{1,2})-(?P<year>\d{2})$")


def format_date_key(date_value: date | datetime) -> str:
    """Format a date as the repo's compact storage key: M-D-YY.

    Month and day are intentionally unpadded; year is always two digits.
    Keep this explicit instead of using platform-specific ``strftime``
    padding modifiers.
    """
    return f"{date_value.month}-{date_value.day}-{date_value.year % 100:02d}"


def parse_date_key(value: str) -> date:
    """Parse the repo's compact M-D-YY date key.

    The legacy key stores a two-digit year; this parser maps it into the
    2000s, matching the current daily-plan storage horizon.
    """
    match = _DATE_KEY_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid date key: {value!r}")
    return date(
        2000 + int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
    )


def get_today_date_key() -> str:
    """Return today's date in the repo's compact M-D-YY storage-key format."""
    return format_date_key(date.today())

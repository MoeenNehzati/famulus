#!/usr/bin/env python3
"""
Fetch himalaya envelopes received since the last triage watermark,
filtering by exact datetime entirely outside the LLM.

Usage:
  fetch-envelopes.py -a <account>

himalaya only supports date-level `after` filtering, so this script:
  1. reads the watermark datetime
  2. calls himalaya with `after <watermark_date - 1>` (one extra day for safety)
  3. parses the envelope table and drops rows older than the watermark datetime
  4. prints only the rows the model needs to see

Filtering in this script keeps old emails out of the LLM's context entirely.
"""
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WATERMARK = Path("~/.claude/skills/email-triage/last_run").expanduser()


def load_cutoff():
    """Return (cutoff_datetime, warning_message|None)."""
    if not WATERMARK.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        return cutoff, "WARNING: No watermark found — defaulting to 24h lookback."
    raw = WATERMARK.read_text().strip()
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt, None
    except ValueError:
        # Legacy date-only watermark — treat as midnight UTC on that date
        from datetime import date
        d = date.fromisoformat(raw)
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return dt, None


def parse_envelope_date(date_str):
    """Parse himalaya date column: '2026-06-19 14:46-04:00'"""
    m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})([+-]\d{2}:\d{2})", date_str.strip())
    if not m:
        return None
    date_part, time_part, tz_part = m.groups()
    sign = 1 if tz_part[0] == "+" else -1
    h, mn = int(tz_part[1:3]), int(tz_part[4:6])
    tz = timezone(timedelta(hours=sign * h, minutes=sign * mn))
    return datetime.strptime(f"{date_part}T{time_part}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=tz)


def filter_table(output, cutoff_dt):
    """Keep only table rows whose DATE column is strictly after cutoff_dt."""
    result = []
    in_data = False
    for line in output.splitlines():
        # Non-table lines (himalaya warnings, empty lines)
        if not line.startswith("|"):
            if line.strip():
                result.append(line)
            continue
        parts = [p.strip() for p in line.split("|")]
        inner = parts[1:-1]
        # Separator row (all dashes) — marks end of header
        if all(re.fullmatch(r"-+", p) for p in inner if p):
            result.append(line)
            in_data = True
            continue
        if not in_data:
            result.append(line)  # header row
            continue
        # Data row — filter by date
        if len(inner) >= 5:
            email_dt = parse_envelope_date(inner[-1])
            if email_dt is None or email_dt > cutoff_dt:
                result.append(line)
    return "\n".join(result)


def main():
    if "-a" not in sys.argv:
        print("Usage: fetch-envelopes.py -a <account>", file=sys.stderr)
        sys.exit(1)
    account = sys.argv[sys.argv.index("-a") + 1]

    cutoff_dt, warning = load_cutoff()
    if warning:
        print(warning, file=sys.stderr)

    after_date = (cutoff_dt.date() - timedelta(days=1)).isoformat()

    result = subprocess.run(
        ["himalaya", "envelope", "list", "-a", account, f"after {after_date}"],
        capture_output=True, text=True
    )

    filtered = filter_table(result.stdout, cutoff_dt)
    if filtered.strip():
        print(filtered)
    else:
        print(f"(no new emails for {account} since {cutoff_dt.strftime('%Y-%m-%d %H:%M %Z')})")

    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


if __name__ == "__main__":
    main()

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
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# State lives next to this script (SKILL_DIR/state), matching get-cutoff.py and
# update-watermark.py, so it stays portable across machines regardless of
# $HOME layout or the caller's cwd.
SKILL_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = SKILL_DIR / "state"
WATERMARK = STATE_DIR / "last_run"
STATUS_FILE = STATE_DIR / "status.json"


def record_warning(message: str) -> None:
    """Surface a problem to the recurring-tasks healthcheck via status.json."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({"result": "warning", "message": message}, indent=2))


def load_cutoff():
    """Return (cutoff_datetime, warning_message|None)."""
    if not WATERMARK.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        msg = "No watermark found — defaulting to 24h lookback."
        record_warning(msg)
        return cutoff, f"WARNING: {msg}"
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


def clear_stale_error():
    """Reset a leftover result=error from a previously-failed, already-reported
    run. Runs at the start of every triage cycle so a stale failure can't block
    update-watermark.py forever once the underlying problem is fixed — this
    run's own mark-failure.py call (if any) will set it again before Step 7."""
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            status = {}
        if status.get("result") == "error":
            STATUS_FILE.write_text(json.dumps({"result": "ok", "message": "reset at start of new run"}, indent=2))


def main():
    if "-a" not in sys.argv:
        print("Usage: fetch-envelopes.py -a <account>", file=sys.stderr)
        sys.exit(1)
    account = sys.argv[sys.argv.index("-a") + 1]

    clear_stale_error()
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

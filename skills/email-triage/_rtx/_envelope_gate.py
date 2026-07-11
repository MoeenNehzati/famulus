#!/usr/bin/env python3
"""
Filter JSON envelopes (from email-client's mail-list) to those strictly after
the triage watermark, dropping old emails before the model ever sees them.

Usage:
  filter_envelopes.py -a <account>   < envelopes.json

email-client's mail-list only supports date-level `--after` filtering (an
IMAP SINCE limitation), so the caller is expected to:
  1. get the coarse cutoff date from scripts-get-cutoff
  2. call email-client's mail-list with --after <that date>
  3. pipe the resulting JSON into this script on stdin

This script then drops anything at or before the exact watermark datetime
(sub-day precision, which mail-list's --after can't express) and prints only
the envelopes the model needs to see, as JSON.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

# State lives next to this script (SKILL_DIR/state), matching get_cutoff.py and
# update_watermark.py, so it stays portable across machines regardless of
# $HOME layout or the caller's cwd.
SKILL_DIR = Path(__file__).resolve().parent.parent
# Overridable via env var so tests can point at a tmp_path instead of the
# real state/ directory.
STATE_DIR = Path(os.environ["EMAIL_TRIAGE_STATE_DIR"]) if os.environ.get("EMAIL_TRIAGE_STATE_DIR") else SKILL_DIR / "state"
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


def clear_stale_error():
    """Reset a leftover result=error from a previously-failed, already-reported
    run. Runs at the start of every triage cycle so a stale failure can't block
    update_watermark.py forever once the underlying problem is fixed — this
    run's own mark_failure.py call (if any) will set it again before Step 7."""
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            status = {}
        if status.get("result") == "error":
            STATUS_FILE.write_text(json.dumps({"result": "ok", "message": "reset at start of new run"}, indent=2))


class Interface(PythonArgvMachineInterface):
    prog = "filter_envelopes.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-a" not in argv:
        print("Usage: filter_envelopes.py -a <account>   < envelopes.json", file=sys.stderr)
        return 1
    account = argv[argv.index("-a") + 1]

    clear_stale_error()
    cutoff_dt, warning = load_cutoff()
    if warning:
        print(warning, file=sys.stderr)

    raw = sys.stdin.read()
    try:
        envelopes = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        print(f"error: could not parse envelope JSON on stdin: {exc}", file=sys.stderr)
        sys.exit(1)

    kept = []
    for env in envelopes:
        date_str = env.get("date")
        if not date_str:
            kept.append(env)  # can't judge age; err on the side of showing it
            continue
        try:
            email_dt = datetime.fromisoformat(date_str)
        except ValueError:
            kept.append(env)
            continue
        if email_dt > cutoff_dt:
            kept.append(env)

    if kept:
        print(json.dumps(kept, indent=2))
    else:
        print(f"(no new emails for {account} since {cutoff_dt.strftime('%Y-%m-%d %H:%M %Z')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

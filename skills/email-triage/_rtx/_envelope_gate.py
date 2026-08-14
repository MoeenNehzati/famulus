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

SKILL_DIR = Path(__file__).resolve().parent.parent


def default_state_dir(*, home: Path | None = None) -> Path:
    """Resolve the mutable state root for email-triage.

    Defaults to the shared Famulus state root (not SKILL_DIR/state, which may
    be a read-only installed/plugin tree). Overridable via
    EMAIL_TRIAGE_STATE_DIR so tests and CI can point at a tmp_path instead of
    the real state directory.
    """
    override = os.environ.get("EMAIL_TRIAGE_STATE_DIR")
    if override:
        return Path(override)
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(platform=sys.platform, home=home or Path.home()).email_triage_state_root


WATERMARK = default_state_dir() / "last_run"
STATUS_FILE = default_state_dir() / "status.json"


def record_warning(message: str) -> None:
    """Surface a problem to the recurring-tasks healthcheck via status.json."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({"result": "warning", "message": message}, indent=2))


def _parse_cutoff_value(raw: str) -> datetime:
    """Parse a watermark-shaped string (ISO datetime, or legacy date-only)
    into a cutoff datetime. Shared by the file-backed watermark and by an
    explicit `--rescan-after` override so both accept the same formats."""
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt
    except ValueError:
        # Legacy date-only watermark — treat as midnight UTC on that date
        from datetime import date
        d = date.fromisoformat(raw)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def load_cutoff(*, override: str | None = None):
    """Return (cutoff_datetime, warning_message|None).

    `override`, when given, is used in place of the stored watermark file --
    this is how a manual historical rescan (`--rescan-after <value>`) fetches
    from an arbitrary earlier point instead of the normal watermark, without
    ever touching or advancing the real watermark file.
    """
    if override:
        return _parse_cutoff_value(override), None
    if not WATERMARK.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        msg = "No watermark found — defaulting to 24h lookback."
        record_warning(msg)
        return cutoff, f"WARNING: {msg}"
    raw = WATERMARK.read_text().strip()
    return _parse_cutoff_value(raw), None


def clear_stale_error():
    """Reset a leftover result=error from a previously-failed, already-reported
    run. Runs at the start of every triage cycle so a stale failure can't block
    update_watermark.py forever once the underlying problem is fixed — this
    run's own mark_failure.py call (if any) will set it again before Step 6."""
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            status = {}
        if status.get("result") == "error":
            # "pending", not "ok": this only unlatches the previous run's error
            # so update_watermark can run again -- it says nothing about how
            # THIS run goes. Writing "ok" here made the reset indistinguishable
            # from a finished run, and the scheduler's require_inner_status: ok
            # contract reads whatever status.json holds after the run starts.
            # A run that reset the latch and then stalled before Step 6 scored
            # a full success while its watermark never moved, which is exactly
            # how a stuck triage stayed invisible for days. Only
            # update_watermark.py writes "ok", and only after it advances.
            STATUS_FILE.write_text(
                json.dumps({"result": "pending", "message": "reset at start of new run"}, indent=2)
            )


def filter_envelopes(envelopes, cutoff_dt):
    """Return envelopes strictly after cutoff, retaining undatable entries."""
    kept = []
    for env in envelopes:
        date_str = env.get("date")
        if not date_str:
            kept.append(env)
            continue
        try:
            email_dt = datetime.fromisoformat(date_str)
        except ValueError:
            kept.append(env)
            continue
        if email_dt > cutoff_dt:
            kept.append(env)
    return kept


def render_filtered_envelopes(envelopes, account, cutoff_dt):
    """Render filtered envelopes or the established no-new-email message."""
    if envelopes:
        return json.dumps(envelopes, indent=2)
    return f"(no new emails for {account} since {cutoff_dt.strftime('%Y-%m-%d %H:%M %Z')})"


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

    kept = filter_envelopes(envelopes, cutoff_dt)
    print(render_filtered_envelopes(kept, account, cutoff_dt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

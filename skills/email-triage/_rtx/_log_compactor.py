#!/usr/bin/env python3
"""
Prune triage.log entries older than 30 days.

Usage: prune_log.py
Reads and rewrites triage.log in place.
Prints a one-line summary: "Pruned N entries older than 30 days (M kept)."
Lines that cannot be parsed are kept.

Log line format:
  [ISO-TIMESTAMP] [ACCOUNT] [ID:N] FROM | SUBJECT → DECISION: reason
"""

import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

CUTOFF_DAYS = 30
LEGACY_LOG_FILE = Path(__file__).resolve().parent / "triage.log"


def _migrate_legacy_log(
    destination: Path, *, legacy_file: Path | None = None
) -> None:
    """Copy the legacy log only for the canonical managed destination."""
    if os.environ.get("EMAIL_TRIAGE_STATE_DIR"):
        return
    source = LEGACY_LOG_FILE if legacy_file is None else legacy_file
    if destination.exists() or not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def triage_log_path(*, home: Path | None = None) -> Path:
    """Return the managed email-triage log location without process overrides."""
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(
        platform=sys.platform, home=home or Path.home(), environ=os.environ
    ).email_triage_state_root / "triage.log"


def unmanaged_triage_log_path(*, home: Path | None = None) -> Path:
    """Return the explicit standalone/test log path, honoring its override."""
    override = os.environ.get("EMAIL_TRIAGE_STATE_DIR")
    if override:
        return Path(override) / "triage.log"
    return triage_log_path() if home is None else triage_log_path(home=home)


def parse_timestamp(line: str) -> datetime | None:
    """Extract and parse the leading [ISO-TIMESTAMP] from a log line."""
    if not line.startswith("["):
        return None
    end = line.find("]")
    if end == -1:
        return None
    try:
        return datetime.fromisoformat(line[1:end])
    except ValueError:
        return None


class Interface(PythonArgvMachineInterface):
    prog = "prune_log.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(_argv: list[str] | None = None) -> int:
    logfile = unmanaged_triage_log_path()
    _migrate_legacy_log(logfile, legacy_file=LEGACY_LOG_FILE)
    if not logfile.exists():
        print("Pruned 0 entries older than 30 days (0 kept).")
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CUTOFF_DAYS)

    lines = logfile.read_text().splitlines(keepends=True)
    kept = []
    pruned = 0

    for line in lines:
        ts = parse_timestamp(line)
        if ts is None:
            # Unparseable — keep it (blank lines, headers, etc.)
            kept.append(line)
            continue
        # Ensure timezone-aware comparison
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            kept.append(line)
        else:
            pruned += 1

    logfile.write_text("".join(kept))
    print(f"Pruned {pruned} entries older than {CUTOFF_DAYS} days ({len(kept)} kept).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

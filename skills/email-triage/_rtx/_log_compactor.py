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

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

LOGFILE = Path(__file__).resolve().parent.parent / "triage.log"
CUTOFF_DAYS = 30


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
    if not LOGFILE.exists():
        print("Pruned 0 entries older than 30 days (0 kept).")
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CUTOFF_DAYS)

    lines = LOGFILE.read_text().splitlines(keepends=True)
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

    LOGFILE.write_text("".join(kept))
    print(f"Pruned {pruned} entries older than {CUTOFF_DAYS} days ({len(kept)} kept).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Print the himalaya `after` cutoff date for email triage.

Usage:
  get-cutoff.py            — print watermark date (or 1-day default with warning if none exists)
  get-cutoff.py --days N   — compute cutoff for N days back (ignores watermark)

The printed date is meant to be passed directly to `himalaya envelope list after <date>`.
himalaya's `after` is strictly after, so the cutoff is already offset by 1 day.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# State lives next to this script (SKILL_DIR/state), matching update-watermark.py,
# so it stays portable across machines regardless of $HOME layout or caller cwd.
SKILL_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = SKILL_DIR / "state"
WATERMARK = STATE_DIR / "last_run"
STATUS_FILE = STATE_DIR / "status.json"


def record_warning(message: str) -> None:
    """Surface a problem to the recurring-tasks healthcheck via status.json.

    healthcheck.sh (in the recurring-tasks skill) reads SKILLS_ROOT/<job>/state/status.json
    for each enabled job and notifies the user if result != "ok". This is the
    same channel that already pops up desktop notifications for job failures.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({"result": "warning", "message": message}, indent=2))


if "--days" in sys.argv:
    idx = sys.argv.index("--days")
    n = int(sys.argv[idx + 1])
    cutoff = date.today() - timedelta(days=n + 1)
    print(cutoff.isoformat())
elif WATERMARK.exists():
    with WATERMARK.open() as f:
        watermark_date = date.fromisoformat(f.read().strip()[:10])
    cutoff = watermark_date - timedelta(days=1)
    print(cutoff.isoformat())
else:
    cutoff = date.today() - timedelta(days=2)
    msg = "No watermark found — defaulting to 2-day lookback."
    print(f"WARNING: {msg}", file=sys.stderr)
    record_warning(msg)
    print(cutoff.isoformat())

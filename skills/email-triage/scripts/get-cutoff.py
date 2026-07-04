#!/usr/bin/env python3
"""
Print the himalaya `after` cutoff date for email triage.

Usage:
  get-cutoff.py            — print watermark date (or 1-day default with warning if none exists)
  get-cutoff.py --days N   — compute cutoff for N days back (ignores watermark)

The printed date is meant to be passed directly to `himalaya envelope list after <date>`.
himalaya's `after` is strictly after, so the cutoff is already offset by 1 day.
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

WATERMARK = Path(os.path.expanduser("~/.local/share/email-triage/last_run"))

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
    print("WARNING: No watermark found — defaulting to 1 day lookback.", file=sys.stderr)
    print(cutoff.isoformat())

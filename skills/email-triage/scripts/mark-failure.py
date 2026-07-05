#!/usr/bin/env python3
"""Record that this triage run failed, so update-watermark.py refuses to advance.

Call this as soon as a list-manager add/update fails (Step 5). Do not call
scripts-update-watermark afterward in the same run — this file is the guard
that makes that mistake safe even if the instruction is skipped.

Usage: mark-failure.py "<reason>"
"""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
# Overridable via env var so tests can point at a tmp_path instead of the
# real state/ directory.
STATE_DIR = Path(os.environ["EMAIL_TRIAGE_STATE_DIR"]) if os.environ.get("EMAIL_TRIAGE_STATE_DIR") else SKILL_DIR / "state"
STATUS_FILE = STATE_DIR / "status.json"

reason = sys.argv[1] if len(sys.argv) > 1 else "triage run failed (no reason given)"

STATE_DIR.mkdir(parents=True, exist_ok=True)
STATUS_FILE.write_text(json.dumps({"result": "error", "message": reason}, indent=2))
print(f"Triage marked as failed: {reason}", file=sys.stderr)

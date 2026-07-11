#!/usr/bin/env python3
"""Advance the triage watermark — but only if the run wasn't marked as failed.

State lives in a directory next to this script (SKILL_DIR/state), so it stays
portable across machines regardless of $HOME layout or the caller's cwd.

Safety: if _rtx/_failure_sentinel.py was called earlier in this run, a
status.json with result="error" will be present. In that case this script
refuses to advance the watermark (exit 1) so no emails are silently skipped
on the next run. On success it resets status.json to result="ok".
"""
import json
import sys
from datetime import datetime
from pathlib import Path
import os

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).resolve().parent.parent
# Overridable via env var so tests can point at a tmp_path instead of the
# real state/ directory.
STATE_DIR = Path(os.environ["EMAIL_TRIAGE_STATE_DIR"]) if os.environ.get("EMAIL_TRIAGE_STATE_DIR") else SKILL_DIR / "state"
WATERMARK = STATE_DIR / "last_run"
STATUS_FILE = STATE_DIR / "status.json"

class Interface(PythonArgvMachineInterface):
    prog = "update_watermark.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(_argv: list[str] | None = None) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            status = {}
        if status.get("result") == "error":
            print(
                "error: last triage run was marked failed "
                f"({status.get('message', 'no reason given')}). "
                "Watermark NOT advanced to avoid skipping emails.",
                file=sys.stderr,
            )
            return 1

    now = datetime.now().astimezone()
    WATERMARK.write_text(now.isoformat())
    STATUS_FILE.write_text(json.dumps({"result": "ok", "message": f"watermark advanced {now.isoformat()}"}, indent=2))
    print(f"Watermark updated: {now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

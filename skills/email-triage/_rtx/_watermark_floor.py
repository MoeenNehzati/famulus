#!/usr/bin/env python3
"""
Print the coarse `--after` cutoff date for email triage.

Usage:
  get_cutoff.py            — print watermark date (or 2-day default with warning if none exists)
  get_cutoff.py --days N   — compute cutoff for N days back (ignores watermark)

The printed date is meant to be passed directly to email-client's
`mail-list --after <date>`. IMAP's SINCE (which --after maps to) is a
day-level filter, so the cutoff here is offset by 1 day for safety; the
precise sub-day cutoff is applied afterward by filter_envelopes.py.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path
import os

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

# State lives next to this script (SKILL_DIR/state), matching update_watermark.py,
# so it stays portable across machines regardless of $HOME layout or caller cwd.
SKILL_DIR = Path(__file__).resolve().parent.parent
# Overridable via env var so tests can point at a tmp_path instead of the
# real state/ directory.
STATE_DIR = Path(os.environ["EMAIL_TRIAGE_STATE_DIR"]) if os.environ.get("EMAIL_TRIAGE_STATE_DIR") else SKILL_DIR / "state"
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


class Interface(PythonArgvMachineInterface):
    prog = "get_cutoff.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--days" in argv:
        idx = argv.index("--days")
        n = int(argv[idx + 1])
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write metrics from a triage run to status.json for visibility.

Usage:
  _write_metrics.py --total-scanned N --added-todo N --added-triage N --skipped N [--deduped N] [--accounts account1,account2]

The metrics are written to state/status.json and will be preserved when
the watermark is advanced, providing a complete picture of what happened
in this triage run.
"""
import json
import sys
import argparse
from datetime import datetime
from pathlib import Path
import os

try:
    from officina.runtime.python_machine_interface import PythonArgvMachineInterface
    HAS_OFFICINA = True
except ImportError:
    HAS_OFFICINA = False

SKILL_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ["EMAIL_TRIAGE_STATE_DIR"]) if os.environ.get("EMAIL_TRIAGE_STATE_DIR") else SKILL_DIR / "state"
STATUS_FILE = STATE_DIR / "status.json"


if HAS_OFFICINA:
    class Interface(PythonArgvMachineInterface):
        prog = "write_metrics.py"

        def run(self, argv: list[str]) -> int:
            return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write triage run metrics to status.json")
    parser.add_argument("--total-scanned", type=int, required=True, help="Total emails scanned")
    parser.add_argument("--added-todo", type=int, required=True, help="Items added to todo")
    parser.add_argument("--added-triage", type=int, required=True, help="Items added to triage")
    parser.add_argument("--skipped", type=int, required=True, help="Emails skipped")
    parser.add_argument("--deduped", type=int, default=0, help="Deduped items (already exist)")
    parser.add_argument("--accounts", type=str, default="", help="Comma-separated list of accounts triaged")

    args = parser.parse_args(argv)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing status.json if it exists, to preserve any error state
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            status = {}

    # Add metrics
    status["metrics"] = {
        "total_scanned": args.total_scanned,
        "added_todo": args.added_todo,
        "added_triage": args.added_triage,
        "skipped": args.skipped,
        "deduped": args.deduped,
    }
    status["accounts_triaged"] = [a.strip() for a in args.accounts.split(",") if a.strip()]
    status["metrics_timestamp"] = datetime.now().astimezone().isoformat()

    STATUS_FILE.write_text(json.dumps(status, indent=2))

    print(
        f"Metrics written: scanned={args.total_scanned} todo={args.added_todo} "
        f"triage={args.added_triage} skipped={args.skipped} deduped={args.deduped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

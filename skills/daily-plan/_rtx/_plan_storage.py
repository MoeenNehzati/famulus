"""Cloud-backed daily-plan storage operations."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from officina.runtime.python_machine_interface import PythonMachineInterface


REMOTE_PREFIX = "GDrive:assistant/plans"


def _remote_path(date_key: str) -> str:
    return f"{REMOTE_PREFIX}/{date_key}.md"


# Self-reported outcome, in the shape recurring-tasks' run records already
# consume (state/status.json with {"result": "ok"|"error"|...}, read by
# _run_record.read_inner_status). Persisting a plan is this skill's actual
# deliverable, so it is the only event that may claim success.
#
# Without this the scheduled daily-plan job had no success signal beyond the
# agent CLI's exit code, which is 0 even when the agent accomplishes nothing
# -- so runs that never reached this interface at all were recorded as
# successes for days while no plan was produced.
STATE_DIR = Path(__file__).resolve().parents[1] / "state"


def _record_status_ok(date_key: str) -> None:
    """Record that a plan was actually persisted for ``date_key``."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "status.json").write_text(
            json.dumps(
                {
                    "result": "ok",
                    "date_key": date_key,
                    "recorded_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        # Never fail a successfully written plan because bookkeeping failed;
        # an absent status is already interpreted as "this run did not report
        # success", which is the safe direction.
        pass


class Interface(PythonMachineInterface):
    prog = "plan-storage"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("operation", choices=("read", "write", "exists", "delete"))
        parser.add_argument("date_key")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        path = _remote_path(args.date_key)
        if args.operation == "read":
            result = subprocess.run(
                ["rclone", "cat", path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=False,
            )
            if result.returncode == 0:
                print(result.stdout, end="")
            return 0
        if args.operation == "write":
            content = sys.stdin.read()
            subprocess.run(
                ["rclone", "rcat", path],
                input=f"{content}\n",
                text=True,
                encoding="utf-8",
                errors="strict",
                check=True,
            )
            _record_status_ok(args.date_key)
            return 0
        if args.operation == "exists":
            result = subprocess.run(
                ["rclone", "lsf", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                print("exists")
                return 0
            print("not found")
            return 1
        if args.operation == "delete":
            subprocess.run(
                ["rclone", "deletefile", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return 0
        raise AssertionError(args.operation)


def main(argv: list[str] | None = None) -> int:
    interface = Interface()
    parser = interface.build_parser()
    return interface.run(parser.parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())

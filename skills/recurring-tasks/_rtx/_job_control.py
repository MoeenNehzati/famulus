"""Manage recurring jobs through recurring-owned control."""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from ._managed_control import run as run_managed_control
else:
    from _managed_control import run as run_managed_control  # noqa: E402


class Interface(PythonArgvMachineInterface):
    prog = "job_control.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("enable", help="Enable a job").add_argument("name")
    subparsers.add_parser("disable", help="Disable a job").add_argument("name")
    subparsers.add_parser("test", help="Test a job").add_argument("name")
    view_logs_parser = subparsers.add_parser("view-logs", help="View job logs")
    view_logs_parser.add_argument("name")
    view_logs_parser.add_argument("--lines", type=int, default=50)
    subparsers.add_parser("status", help="Show timer status")
    subparsers.add_parser("sync", help="Sync units")
    subparsers.add_parser(
        "remove-context", help="Remove only this context's native scheduler state"
    )

    args = parser.parse_args(argv)
    forwarded: list[str] = []
    if getattr(args, "name", None):
        forwarded.append(args.name)
    if args.command == "view-logs" and args.lines != 50:
        forwarded.extend(["--lines", str(args.lines)])
    return run_managed_control(args.command, forwarded)


if __name__ == "__main__":
    raise SystemExit(main())

"""Cloud-backed daily-plan storage operations."""
from __future__ import annotations

import argparse
import subprocess
import sys

from officina.runtime.python_machine_interface import PythonMachineInterface


REMOTE_PREFIX = "GDrive:assistant/plans"


def _remote_path(date_key: str) -> str:
    return f"{REMOTE_PREFIX}/{date_key}.md"


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

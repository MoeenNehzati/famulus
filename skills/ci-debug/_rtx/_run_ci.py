"""Run the complete remote CI matrix through the repository-owned runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from . import RunnerInvocationError, invoke_runner
except ImportError:
    from __init__ import RunnerInvocationError, invoke_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-ci")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-sha", required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir")
    destination.add_argument("--context")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args(argv)
    try:
        destination_args = (
            ("--context", args.context)
            if args.context
            else ("--output-dir", args.output_dir)
        )
        return invoke_runner(
            Path(args.repo_root),
            (
                "remote", "matrix", "--ref", args.ref,
                "--expected-sha", args.expected_sha,
                *destination_args,
                "--timeout", str(args.timeout),
            ),
            timeout_seconds=args.timeout,
        )
    except RunnerInvocationError as exc:
        print(json.dumps({"schema_version": 1, "error": "runner_interface_unavailable"}))
        print(str(exc), file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Expose full CI as one process-bound interface."""

    prog = "run-ci"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

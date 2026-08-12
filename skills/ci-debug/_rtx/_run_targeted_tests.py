"""Run one matrix element or failure selection through the canonical runner."""

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
    parser = argparse.ArgumentParser(prog="run-targeted-tests")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--os", required=True)
    parser.add_argument("--task", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--selector")
    selection.add_argument("--from-report")
    selection.add_argument("--whole-element", action="store_true")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--profile")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    runner_args = [
        "remote", "probe", "--ref", args.ref,
        "--expected-sha", args.expected_sha,
        "--os", args.os, "--task", args.task,
        "--output-dir", args.output_dir,
        "--timeout", str(args.timeout),
    ]
    if args.selector:
        runner_args.extend(("--selector", args.selector))
    if args.from_report:
        runner_args.extend(("--from-report", args.from_report))
    if args.whole_element:
        runner_args.append("--whole-element")
    if args.jobs is not None:
        runner_args.extend(("--jobs", str(args.jobs)))
    if args.profile:
        runner_args.extend(("--profile", args.profile))
    try:
        return invoke_runner(
            Path(args.repo_root), runner_args, timeout_seconds=args.timeout
        )
    except RunnerInvocationError as exc:
        print(json.dumps({"schema_version": 1, "error": "runner_interface_unavailable"}))
        print(str(exc), file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Expose one targeted CI run as a process-bound interface."""

    prog = "run-targeted-tests"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

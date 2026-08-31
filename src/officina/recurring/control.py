from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml

from officina.common.atomic_files import atomic_replace_bytes
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from .healthcheck import run as run_healthcheck
from .native import load_jobs, remove_context, status, sync, trigger
from .jobs import confined_child, validate_jobs_payload, validate_job_name
from .records import read_record
from .runtime import ManagedSchedule, load_managed_schedule
from .state import cleanup_legacy_agent_environment, prepare_context_state


def _write_jobs(schedule: ManagedSchedule, jobs: list[dict[str, object]]) -> None:
    jobs = validate_jobs_payload({"jobs": jobs})
    raw = yaml.safe_dump({"jobs": jobs}, sort_keys=False).encode("utf-8")
    schedule.jobs_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_replace_bytes(schedule.jobs_file, raw, allowed_root=schedule.jobs_file.parent, mode=0o600)


def _set_enabled(schedule: ManagedSchedule, name: str, enabled: bool) -> None:
    name = validate_job_name(name)
    jobs = load_jobs(schedule)
    for job in jobs:
        if job.get("name") == name:
            job["enabled"] = enabled
            _write_jobs(schedule, jobs)
            sync(schedule)
            print(("Enabled" if enabled else "Disabled") + f": {name}")
            return
    raise ValueError(f"Job not found: {name}")


def _test(schedule: ManagedSchedule, name: str) -> bool:
    name = validate_job_name(name)
    previous = read_record(log_root=schedule.log_root, job_name=name)
    previous_id = previous.get("run_id") if previous else None
    if not trigger(schedule, name):
        return False
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        current = read_record(log_root=schedule.log_root, job_name=name)
        if current and current.get("run_id") != previous_id:
            return current.get("success") is True
        time.sleep(0.5)
    return False


def _view_logs(schedule: ManagedSchedule, name: str, lines: int) -> None:
    name = validate_job_name(name)
    path = confined_child(schedule.log_root, name) / "run.log"
    if not path.exists():
        print(f"No logs for: {name}")
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
        print(line)


def _run_operation_unlocked(schedule: ManagedSchedule, *, operation: str, name: str | None, lines: int) -> int:
    if operation == "setup":
        prepare_context_state(schedule)
        sync(schedule)
        cleanup_legacy_agent_environment(schedule)
    elif operation == "sync":
        prepare_context_state(schedule)
        sync(schedule)
    elif operation == "enable":
        if not name: raise ValueError("enable requires a job name")
        _set_enabled(schedule, name, True)
    elif operation == "disable":
        if not name: raise ValueError("disable requires a job name")
        _set_enabled(schedule, name, False)
    elif operation == "status":
        print(status(schedule))
    elif operation == "test":
        if not name: raise ValueError("test requires a job name")
        return 0 if _test(schedule, name) else 1
    elif operation == "view-logs":
        if not name: raise ValueError("view-logs requires a job name")
        _view_logs(schedule, name, lines)
    elif operation == "healthcheck":
        return run_healthcheck(schedule, cron=False)
    elif operation == "remove-context":
        remove_context(schedule)
    return 0


def run_operation(schedule: ManagedSchedule, *, operation: str, name: str | None, lines: int) -> int:
    return _run_operation_unlocked(schedule, operation=operation, name=name, lines=lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("operation", choices=("setup", "sync", "enable", "disable", "status", "test", "healthcheck", "view-logs", "remove-context"))
    parser.add_argument("name", nargs="?")
    parser.add_argument("--lines", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        schedule = load_managed_schedule(descriptor_path=args.descriptor)
        if args.plugin_root.resolve() != schedule.plugin_root: raise ValueError("plugin root does not match descriptor")
        return run_operation(schedule, operation=args.operation, name=args.name, lines=args.lines)
    except Exception as exc:
        print(f"recurring control: {exc}; rerun recurring-tasks setup", file=sys.stderr)
        return 1


class Interface(PythonArgvMachineInterface):
    prog = "famulus-recurring-control"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Interface", "main", "run_operation"]

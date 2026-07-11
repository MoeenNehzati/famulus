#!/usr/bin/env python3
"""
Manage recurring jobs: enable, disable, test, view logs, and check status.

Usage:
  python3 manage_job.py enable <name>          # Enable a job (sets enabled: true, syncs units)
  python3 manage_job.py disable <name>         # Disable a job (sets enabled: false, syncs units)
  python3 manage_job.py enable <name> --jobs-file FILE --no-sync   # test/dry-run against a different jobs.yaml
  python3 manage_job.py test <name>            # Run a job immediately, show output
  python3 manage_job.py view-logs <name>       # Tail job logs (default 50 lines)
  python3 manage_job.py view-logs <name> --lines 100
  python3 manage_job.py status                 # Show all timers and next fire times
  python3 manage_job.py sync                   # Regenerate scheduler entries from jobs.yaml

All operations sync scheduler entries after modifying jobs.yaml.
"""
import sys
from argparse import ArgumentParser
from pathlib import Path

import yaml

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent.parent
RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _schedule_backend import (  # noqa: E402
    ScheduleContext,
    platform_schedule_backend,
    schedule_jobs_from_mappings,
)

JOBS_FILE = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"


def schedule_context(jobs_file: Path = JOBS_FILE) -> ScheduleContext:
    return ScheduleContext(skill_dir=SKILL_DIR, jobs_file=jobs_file, log_dir=LOG_DIR)


def load_jobs(jobs_file: Path = JOBS_FILE) -> list:
    """Load jobs from YAML."""
    with open(jobs_file) as f:
        return (yaml.safe_load(f) or {}).get("jobs", [])


def save_jobs(jobs: list, jobs_file: Path = JOBS_FILE) -> None:
    """Save jobs to YAML."""
    with open(jobs_file, "w") as f:
        yaml.safe_dump({"jobs": jobs}, f, sort_keys=False)


def sync_units(jobs_file: Path | None = None) -> None:
    """Regenerate scheduler entries."""
    selected_jobs_file = jobs_file or JOBS_FILE
    context = schedule_context(selected_jobs_file)
    platform_schedule_backend().sync(
        schedule_jobs_from_mappings(load_jobs(selected_jobs_file)),
        context,
    )


def enable_job(name: str, jobs_file: Path = JOBS_FILE, sync: bool = True) -> None:
    """Enable a job."""
    jobs = load_jobs(jobs_file)
    for job in jobs:
        if job["name"] == name:
            job["enabled"] = True
            save_jobs(jobs, jobs_file)
            if sync:
                sync_units(jobs_file if jobs_file != JOBS_FILE else None)
            print(f"Enabled: {name}")
            return
    raise ValueError(f"Job not found: {name}")


def disable_job(name: str, jobs_file: Path = JOBS_FILE, sync: bool = True) -> None:
    """Disable a job."""
    jobs = load_jobs(jobs_file)
    for job in jobs:
        if job["name"] == name:
            job["enabled"] = False
            save_jobs(jobs, jobs_file)
            if sync:
                sync_units(jobs_file if jobs_file != JOBS_FILE else None)
            print(f"Disabled: {name}")
            return
    raise ValueError(f"Job not found: {name}")


def test_job(name: str) -> bool:
    """Test a job immediately."""
    if platform_schedule_backend().test(name, schedule_context()):
        print(f"OK: Test passed: {name}")
        return True
    print(f"FAIL: Test failed: {name}")
    return False


def view_logs(name: str, lines: int = 50) -> None:
    """View job logs."""
    log_file = LOG_DIR / name / "run.log"
    if not log_file.exists():
        print(f"No logs for: {name}")
        return

    content = log_file.read_text()
    log_lines = content.splitlines()
    for line in log_lines[-lines:]:
        print(line)


def status() -> None:
    """Show status of all scheduled recurring jobs."""
    print(platform_schedule_backend().status(schedule_context()))


class Interface(PythonArgvMachineInterface):
    prog = "job_control.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    p = ArgumentParser()
    subparsers = p.add_subparsers(dest="command", required=True)

    enable_parser = subparsers.add_parser("enable", help="Enable a job")
    enable_parser.add_argument("name")
    enable_parser.add_argument("--jobs-file", type=Path, default=JOBS_FILE,
                                help="jobs.yaml to modify (default: this skill's jobs.yaml)")
    enable_parser.add_argument("--no-sync", action="store_true",
                                help="Skip regenerating scheduler entries after modifying jobs.yaml")

    disable_parser = subparsers.add_parser("disable", help="Disable a job")
    disable_parser.add_argument("name")
    disable_parser.add_argument("--jobs-file", type=Path, default=JOBS_FILE,
                                 help="jobs.yaml to modify (default: this skill's jobs.yaml)")
    disable_parser.add_argument("--no-sync", action="store_true",
                                 help="Skip regenerating scheduler entries after modifying jobs.yaml")

    subparsers.add_parser("test", help="Test a job").add_argument("name")
    view_logs_parser = subparsers.add_parser("view-logs", help="View job logs")
    view_logs_parser.add_argument("name")
    view_logs_parser.add_argument("--lines", type=int, default=50)
    subparsers.add_parser("status", help="Show timer status")
    subparsers.add_parser("sync", help="Sync units")

    args = p.parse_args(argv)

    try:
        if args.command == "enable":
            enable_job(args.name, jobs_file=args.jobs_file, sync=not args.no_sync)
        elif args.command == "disable":
            disable_job(args.name, jobs_file=args.jobs_file, sync=not args.no_sync)
        elif args.command == "test":
            test_job(args.name)
        elif args.command == "view-logs":
            view_logs(args.name, args.lines)
        elif args.command == "status":
            status()
        elif args.command == "sync":
            sync_units()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

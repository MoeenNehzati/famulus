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
import time
from argparse import ArgumentParser
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent
RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from ._jobs_config import load_jobs as _load_jobs, write_jobs as _write_jobs
    from ._schedule_backend import ScheduleContext, platform_schedule_backend, schedule_jobs_from_mappings
else:
    from _jobs_config import load_jobs as _load_jobs, write_jobs as _write_jobs  # noqa: E402
    from _schedule_backend import (  # noqa: E402
    ScheduleContext,
    platform_schedule_backend,
    schedule_jobs_from_mappings,
)
if __package__:
    from . import _unit_writer
else:
    import _unit_writer  # noqa: E402
if __package__:
    from ._run_record import read_latest_run_record
else:
    from _run_record import read_latest_run_record  # noqa: E402

JOBS_FILE = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"

# Bounded wait for a job's run record after triggering it via the OS
# scheduler (feedback item 14). Some backends' `test()` blocks until the job
# finishes (systemd's `start --wait`); others fire-and-forget (launchd's
# `kickstart`, Windows' `schtasks /Run`), so backend.test() returning True
# only means "the scheduler accepted the trigger," never "the job
# succeeded." We poll for a fresh JobRunRecord instead of trusting that
# return value, capped so a wedged job can't hang this call forever.
TEST_JOB_TIMEOUT_SECONDS = 60.0
TEST_JOB_POLL_INTERVAL_SECONDS = 0.5


def schedule_context(jobs_file: Path = JOBS_FILE) -> ScheduleContext:
    return ScheduleContext(skill_dir=SKILL_DIR, jobs_file=jobs_file, log_dir=LOG_DIR)


def load_jobs(jobs_file: Path = JOBS_FILE) -> list:
    """Load jobs from YAML."""
    return _load_jobs(jobs_file)


def save_jobs(jobs: list, jobs_file: Path = JOBS_FILE) -> None:
    """Save jobs to YAML."""
    _write_jobs(jobs_file, jobs)


def sync_units(jobs_file: Path | None = None) -> None:
    """Regenerate scheduler entries.

    Delegates so there is one implementation of the sync itself; this wrapper
    only supplies the defaults the interactive commands use.
    """
    selected_jobs_file = jobs_file or JOBS_FILE
    _unit_writer.sync_units(
        load_jobs(selected_jobs_file),
        None,  # unit_dir: let the platform backend choose its own location
        LOG_DIR,
        jobs_file=selected_jobs_file,
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


def test_job(
    name: str,
    *,
    timeout_seconds: float = TEST_JOB_TIMEOUT_SECONDS,
    poll_interval: float = TEST_JOB_POLL_INTERVAL_SECONDS,
) -> bool:
    """Test a job immediately and report whether it actually succeeded.

    Triggering the job through the host scheduler only tells us the
    scheduler *accepted the trigger* -- not that the job's own task
    succeeded (see the module docstring above `TEST_JOB_TIMEOUT_SECONDS`).
    So after triggering, this waits (bounded by `timeout_seconds`) for a
    fresh JobRunRecord to appear in logs/<name>/latest.json and reports
    pass/fail from its `success` field instead.
    """
    baseline = read_latest_run_record(log_dir=LOG_DIR, job_name=name)
    # Compare by run_id (a fresh uuid4 per run), not finished_at: finished_at
    # has only second resolution, so a fast run (e.g. an instant
    # spawn-failure) can share a timestamp with the baseline record and get
    # mistaken for "no new run yet," which would make this poll out its
    # full timeout on a run that actually completed immediately.
    baseline_run_id = baseline.get("run_id") if baseline else None

    if not platform_schedule_backend().test(name, schedule_context()):
        print(f"FAIL: Test failed: {name} (scheduler did not accept the trigger)")
        return False

    deadline = time.monotonic() + timeout_seconds
    record = None
    while time.monotonic() < deadline:
        candidate = read_latest_run_record(log_dir=LOG_DIR, job_name=name)
        if candidate is not None and candidate.get("run_id") != baseline_run_id:
            record = candidate
            break
        time.sleep(poll_interval)

    if record is None:
        print(
            f"FAIL: Test failed: {name} "
            f"(timed out after {timeout_seconds:.0f}s waiting for a run record)"
        )
        return False

    if record.get("success"):
        print(f"OK: Test passed: {name}")
        return True
    print(f"FAIL: Test failed: {name} ({record.get('reason') or 'run did not succeed'})")
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
            if not test_job(args.name):
                return 1
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

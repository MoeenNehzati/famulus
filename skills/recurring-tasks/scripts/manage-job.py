#!/usr/bin/env python3
"""
Manage recurring jobs: enable, disable, test, view logs, and check status.

Usage:
  python3 manage-job.py enable <name>          # Enable a job (sets enabled: true, syncs units)
  python3 manage-job.py disable <name>         # Disable a job (sets enabled: false, syncs units)
  python3 manage-job.py test <name>            # Run a job immediately, show output
  python3 manage-job.py view-logs <name>       # Tail job logs (default 50 lines)
  python3 manage-job.py view-logs <name> --lines 100
  python3 manage-job.py status                 # Show all timers and next fire times
  python3 manage-job.py sync                   # Regenerate systemd units from jobs.yaml

All operations sync systemd units after modifying jobs.yaml.
"""
import json
import subprocess
import sys
import time
from argparse import ArgumentParser
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).parent.parent
JOBS_FILE = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"


def load_jobs(jobs_file: Path = JOBS_FILE) -> list:
    """Load jobs from YAML."""
    with open(jobs_file) as f:
        return (yaml.safe_load(f) or {}).get("jobs", [])


def save_jobs(jobs: list, jobs_file: Path = JOBS_FILE) -> None:
    """Save jobs to YAML."""
    with open(jobs_file, "w") as f:
        yaml.safe_dump({"jobs": jobs}, f, sort_keys=False)


def sync_units() -> None:
    """Regenerate systemd units."""
    subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "sync-units.py")],
        check=True,
    )


def enable_job(name: str) -> None:
    """Enable a job."""
    jobs = load_jobs()
    for job in jobs:
        if job["name"] == name:
            job["enabled"] = True
            save_jobs(jobs)
            sync_units()
            print(f"Enabled: {name}")
            return
    raise ValueError(f"Job not found: {name}")


def disable_job(name: str) -> None:
    """Disable a job."""
    jobs = load_jobs()
    for job in jobs:
        if job["name"] == name:
            job["enabled"] = False
            save_jobs(jobs)
            sync_units()
            print(f"Disabled: {name}")
            return
    raise ValueError(f"Job not found: {name}")


def test_job(name: str) -> bool:
    """Test a job immediately."""
    result = subprocess.run(
        ["systemctl", "--user", "start", "--wait", f"ai-{name}.service"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"✓ Test passed: {name}")
        return True
    else:
        print(f"✗ Test failed: {name}")
        print("stderr:", result.stderr)
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
    """Show status of all timers."""
    result = subprocess.run(
        ["systemctl", "--user", "list-timers", "ai-*.timer", "--no-pager"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)


def main() -> None:
    p = ArgumentParser()
    subparsers = p.add_subparsers(dest="command", required=True)

    subparsers.add_parser("enable", help="Enable a job").add_argument("name")
    subparsers.add_parser("disable", help="Disable a job").add_argument("name")
    subparsers.add_parser("test", help="Test a job").add_argument("name")
    view_logs_parser = subparsers.add_parser("view-logs", help="View job logs")
    view_logs_parser.add_argument("name")
    view_logs_parser.add_argument("--lines", type=int, default=50)
    subparsers.add_parser("status", help="Show timer status")
    subparsers.add_parser("sync", help="Sync units")

    args = p.parse_args()

    try:
        if args.command == "enable":
            enable_job(args.name)
        elif args.command == "disable":
            disable_job(args.name)
        elif args.command == "test":
            test_job(args.name)
        elif args.command == "view-logs":
            view_logs(args.name, args.lines)
        elif args.command == "status":
            status()
        elif args.command == "sync":
            sync_units()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

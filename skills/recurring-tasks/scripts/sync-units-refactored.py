#!/usr/bin/env python3
"""
Regenerate systemd user timer/service units from jobs.yaml.

Simplified architecture:
- One service template per job (embedded command)
- No per-job runner scripts (command runs via bash -c)
- Direct invocation: invoke-skill <name>
"""
import re
import subprocess
import sys
import yaml
from pathlib import Path
from argparse import ArgumentParser

SKILL_DIR = Path(__file__).parent.parent
DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"
DEFAULT_UNIT_DIR = Path.home() / ".config/systemd/user"
PREFIX = "ai-"


def cron_to_systemd_calendar(cron: str) -> str:
    """Convert 5-field cron to systemd OnCalendar format."""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron: {cron!r}")
    minute, hour, dom, month, dow = parts
    if dom != '*' or month != '*':
        raise ValueError(f"dom and month must be '*': {cron!r}")

    DOW_NAMES = {
        '0': 'Sun', '7': 'Sun', '1': 'Mon', '2': 'Tue',
        '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat',
    }

    def field(v: str, pad: bool = False, step_base: str | None = None) -> str:
        if v == '*':
            return '*'
        if re.fullmatch(r'\*/\d+', v):
            step = v[2:]
            return f"{step_base}/{step}" if step_base else v
        if re.fullmatch(r'\d+', v):
            return v.zfill(2) if pad else v
        raise ValueError(f"Unsupported field: {v!r}")

    dow_prefix = ''
    if dow != '*':
        if dow not in DOW_NAMES:
            raise ValueError(f"Invalid day of week: {dow!r}")
        dow_prefix = DOW_NAMES[dow] + ' '

    return f'{dow_prefix}*-*-* {field(hour, pad=True)}:{field(minute, pad=True, step_base="00")}:00'


def service_content(job_name: str, description: str, command: str, log_file: Path) -> str:
    """Generate systemd service unit for a job.

    Command is executed via bash -c to allow shell features and variable expansion.
    Environment is inherited from systemd user session (including AI_AGENT_COMMAND_TEMPLATE).
    """
    return (
        "[Unit]\n"
        f"Description=AI job: {description}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/bin/bash -c '{command}'\n"
        f"StandardOutput=append:{log_file}\n"
        "StandardError=append\n"
    )


def timer_content(description: str, calendar: str, service_name: str) -> str:
    """Generate systemd timer unit for a job."""
    return (
        "[Unit]\n"
        f"Description=Timer for AI job: {description}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={calendar}\n"
        "Persistent=true\n"
        f"Unit={service_name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def sync_units(jobs: list, unit_dir: Path, log_dir: Path, live: bool = True) -> None:
    """Generate or update systemd units to match jobs.yaml."""
    unit_dir.mkdir(parents=True, exist_ok=True)
    enabled_names: set[str] = set()

    for job in jobs:
        if not job.get('enabled', False):
            continue

        name = job['name']
        enabled_names.add(name)
        log_file = log_dir / name / 'run.log'
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # Command: substitute {skill_dir} and escape shell special chars
        command = job["command"].replace("{skill_dir}", str(SKILL_DIR))
        # Escape single quotes in command for bash -c
        command = command.replace("'", "'\\''")

        calendar = cron_to_systemd_calendar(job['schedule'])
        svc_name = f"{PREFIX}{name}.service"

        (unit_dir / svc_name).write_text(
            service_content(name, job['description'], command, log_file)
        )
        (unit_dir / f"{PREFIX}{name}.timer").write_text(
            timer_content(job['description'], calendar, svc_name)
        )
        print(f"Synced '{name}' (OnCalendar={calendar})")

    # Remove disabled jobs' units
    for tmr in sorted(unit_dir.glob(f"{PREFIX}*.timer")):
        n = tmr.stem[len(PREFIX):]
        if n not in enabled_names:
            if live:
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", tmr.name],
                    capture_output=True,
                )
            tmr.unlink(missing_ok=True)
            (unit_dir / f"{PREFIX}{n}.service").unlink(missing_ok=True)
            print(f"Removed disabled job: '{n}'")

    if live:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        for name in sorted(enabled_names):
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", f"{PREFIX}{name}.timer"],
                check=True,
            )
            print(f"Enabled {PREFIX}{name}.timer")


def main() -> None:
    p = ArgumentParser(description=__doc__)
    p.add_argument("--unit-dir", default=None, help="Override unit dir (testing; skips systemctl)")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS), help="Override jobs.yaml location")
    args = p.parse_args()

    live = args.unit_dir is None
    unit_dir = Path(args.unit_dir) if args.unit_dir else DEFAULT_UNIT_DIR

    with open(args.jobs_file) as f:
        jobs = (yaml.safe_load(f) or {}).get("jobs", [])

    sync_units(jobs, unit_dir, LOG_DIR, live=live)
    if live:
        print("Done.")


if __name__ == "__main__":
    main()

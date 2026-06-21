#!/usr/bin/env python3
"""
Regenerate systemd user timer/service units from jobs.yaml.
Usage:
  sync-units.py                     # live system
  sync-units.py --unit-dir PATH     # override unit dir (testing; skips systemctl)
  sync-units.py --jobs-file PATH    # override jobs.yaml location
  sync-units.py --migrate-cron      # also remove old crontab block
"""
import re, subprocess, sys, yaml
from pathlib import Path
from argparse import ArgumentParser

SKILL_DIR        = Path(__file__).parent.parent
DEFAULT_JOBS     = SKILL_DIR / "jobs.yaml"
LOG_DIR          = SKILL_DIR / "logs"
RUNNER_DIR       = SKILL_DIR / "scripts" / "runners"
DEFAULT_UNIT_DIR = Path.home() / ".config/systemd/user"
PREFIX           = "claude-"
CRON_BEGIN = "# --- claude-recurring BEGIN (managed by recurring-tasks skill — do not edit manually) ---"
CRON_END   = "# --- claude-recurring END ---"


def cron_to_systemd_calendar(cron: str) -> str:
    """Convert a 5-field cron expression to systemd OnCalendar= format.

    Supports exact values, * wildcards, and */N step syntax.
    dom and month must be *.
    """
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got: {cron!r}")
    minute, hour, dom, month, dow = parts
    if dom != '*' or month != '*':
        raise ValueError(f"dom and month must be '*' (got: {cron!r})")

    DOW_NAMES = {
        '0': 'Sun', '7': 'Sun', '1': 'Mon', '2': 'Tue',
        '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat',
    }

    def field(v: str, pad: bool = False) -> str:
        if v == '*':
            return '*'
        if re.fullmatch(r'\*/\d+', v):
            return v
        if re.fullmatch(r'\d+', v):
            return v.zfill(2) if pad else v
        raise ValueError(f"Unsupported cron field: {v!r}")

    if dow == '*':
        dow_prefix = ''
    elif re.fullmatch(r'[0-9]', dow):
        if dow not in DOW_NAMES:
            raise ValueError(f"Unknown day of week: {dow!r}")
        dow_prefix = DOW_NAMES[dow] + ' '
    else:
        raise ValueError(f"Unsupported dow pattern: {dow!r}")

    return f'{dow_prefix}*-*-* {field(hour, pad=True)}:{field(minute, pad=True)}:00'


def write_runner(job: dict, log: Path, runner_dir: Path) -> Path:
    runner_dir.mkdir(parents=True, exist_ok=True)
    path = runner_dir / f"{job['name']}.sh"
    path.write_text(f"#!/bin/bash\n{job['command']} >> {log} 2>&1\n")
    path.chmod(0o755)
    return path


def service_content(description: str, runner: Path) -> str:
    return (
        "[Unit]\n"
        f"Description=Claude recurring job: {description}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/bin/bash {runner}\n"
    )


def timer_content(description: str, calendar: str, service_name: str) -> str:
    return (
        "[Unit]\n"
        f"Description=Timer for Claude recurring job: {description}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={calendar}\n"
        "Persistent=true\n"
        f"Unit={service_name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def sync_units(
    jobs: list,
    unit_dir: Path,
    log_dir: Path,
    runner_dir: Path,
    live: bool = True,
) -> None:
    unit_dir.mkdir(parents=True, exist_ok=True)
    enabled_names: set[str] = set()

    for job in jobs:
        if not job.get('enabled', False):
            continue
        name = job['name']
        enabled_names.add(name)
        log = log_dir / name / 'run.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        calendar   = cron_to_systemd_calendar(job['schedule'])
        runner     = write_runner(job, log, runner_dir)
        svc_name   = f"{PREFIX}{name}.service"
        (unit_dir / svc_name).write_text(service_content(job['description'], runner))
        (unit_dir / f"{PREFIX}{name}.timer").write_text(timer_content(job['description'], calendar, svc_name))
        print(f"Wrote unit files for '{name}' (OnCalendar={calendar})")

    for tmr in sorted(unit_dir.glob(f"{PREFIX}*.timer")):
        n = tmr.stem[len(PREFIX):]
        if n not in enabled_names:
            if live:
                r = subprocess.run(
                    ["systemctl", "--user", "disable", "--now", tmr.name],
                    capture_output=True,
                )
                if r.returncode != 0:
                    print(f"Warning: failed to disable {tmr.name} (already disabled?)")
            tmr.unlink(missing_ok=True)
            (unit_dir / f"{PREFIX}{n}.service").unlink(missing_ok=True)
            (runner_dir / f"{n}.sh").unlink(missing_ok=True)
            print(f"Removed units for disabled job: '{n}'")

    if live:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        for name in sorted(enabled_names):
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", f"{PREFIX}{name}.timer"],
                check=True,
            )
            print(f"Enabled timer: {PREFIX}{name}.timer")


def remove_cron_block() -> None:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        print("No crontab found; nothing to migrate.")
        return
    lines = r.stdout.splitlines(keepends=True)
    try:
        i = next(n for n, l in enumerate(lines) if l.rstrip() == CRON_BEGIN)
        j = next(n for n, l in enumerate(lines) if l.rstrip() == CRON_END)
        if j <= i:
            print("Warning: crontab block markers are in wrong order — skipping removal.")
            return
        new = "".join(lines[:i] + lines[j + 1:])
        subprocess.run(["crontab", "-"], input=new, text=True, check=True)
        print("Removed old crontab block.")
    except StopIteration:
        print("No claude-recurring crontab block found; nothing to migrate.")


def main() -> None:
    p = ArgumentParser()
    p.add_argument("--unit-dir", default=None,
                   help="Override unit dir (testing; skips systemctl)")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS))
    p.add_argument("--migrate-cron", action="store_true",
                   help="Remove old claude-recurring crontab block before syncing")
    args = p.parse_args()

    if args.migrate_cron:
        remove_cron_block()

    live       = args.unit_dir is None
    unit_dir   = Path(args.unit_dir) if args.unit_dir else DEFAULT_UNIT_DIR
    runner_dir = RUNNER_DIR if live else unit_dir / "runners"

    with open(args.jobs_file) as f:
        jobs = (yaml.safe_load(f) or {}).get("jobs", [])

    sync_units(jobs, unit_dir, LOG_DIR, runner_dir, live=live)
    if live:
        print("Done.")


if __name__ == "__main__":
    main()

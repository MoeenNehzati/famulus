#!/usr/bin/env python3
"""
Healthcheck for recurring tasks. Verifies jobs are healthy.
Run periodically via cron (not systemd, so it works even if systemd breaks).
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
JOBS_FILE = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"
HEALTHCHECK_LOG = LOG_DIR / "healthcheck" / "run.log"


def log(msg: str) -> None:
    """Log to healthcheck log file."""
    HEALTHCHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    HEALTHCHECK_LOG.append_text(f"[{timestamp}] {msg}\n")
    print(msg)


def check_systemd_manager() -> bool:
    """Verify systemd user manager is running."""
    result = subprocess.run(
        ["systemctl", "--user", "is-system-running"],
        capture_output=True,
        text=True,
    )
    state = result.stdout.strip()
    if state in ("running", "degraded"):
        log("✓ systemd user manager: OK")
        return True
    log(f"✗ systemd user manager: {state}")
    return False


def check_environment() -> bool:
    """Verify AI_AGENT_COMMAND_TEMPLATE is set and resolves."""
    result = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        capture_output=True,
        text=True,
    )
    template = None
    for line in result.stdout.splitlines():
        if line.startswith("AI_AGENT_COMMAND_TEMPLATE="):
            template = line.split("=", 1)[1]
            break

    if not template:
        log("✗ AI_AGENT_COMMAND_TEMPLATE: not set")
        return False

    # Extract command name (first token)
    cmd = template.split()[0] if template else ""
    if not shutil.which(cmd):
        log(f"✗ AI_AGENT_COMMAND_TEMPLATE: command not found: {cmd}")
        return False

    log(f"✓ AI_AGENT_COMMAND_TEMPLATE: {template}")
    return True


def check_job(job: dict) -> bool:
    """Check if a job ran recently and succeeded."""
    name = job["name"]
    log_file = LOG_DIR / name / "run.log"

    if not log_file.exists():
        log(f"  ✗ {name}: no log file")
        return False

    # Check if log is fresh (within 2x scheduled interval)
    interval_mins = parse_schedule_interval(job["schedule"])
    stale_threshold = timedelta(minutes=interval_mins * 2)
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        log_file.stat().st_mtime, tz=timezone.utc
    )

    if age > stale_threshold:
        log(f"  ⚠ {name}: log stale ({age.total_seconds() / 60:.0f}m old)")
        return False

    # Check systemd timer status
    result = subprocess.run(
        ["systemctl", "--user", "is-active", f"ai-{name}.timer"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"  ✗ {name}: timer not active")
        return False

    log(f"  ✓ {name}: OK")
    return True


def parse_schedule_interval(schedule: str) -> int:
    """Estimate interval in minutes from cron expression."""
    parts = schedule.split()
    minute, hour = parts[0], parts[1]

    if minute.startswith("*/"):
        return int(minute[2:])
    elif hour.startswith("*/"):
        return int(hour[2:]) * 60
    elif hour == "*" and minute != "*":
        return 60
    elif hour != "*":
        return 1440  # daily
    return 60


def main() -> None:
    log("=== healthcheck start ===")

    import yaml

    # Load jobs
    try:
        with open(JOBS_FILE) as f:
            jobs = (yaml.safe_load(f) or {}).get("jobs", [])
    except Exception as e:
        log(f"✗ Failed to load jobs.yaml: {e}")
        return

    problems = 0

    # Pre-flight checks
    if not check_systemd_manager():
        problems += 1
    if not check_environment():
        problems += 1

    # Per-job checks
    log("Per-job checks:")
    for job in jobs:
        if not job.get("enabled", False):
            continue
        if not check_job(job):
            problems += 1

    # Report
    if problems == 0:
        log("✓ All checks passed")
    else:
        log(f"✗ {problems} problem(s) found")
        # Send desktop notification if available
        try:
            subprocess.run(
                ["notify-send", "-u", "critical", "Recurring Tasks", f"{problems} health check problem(s)"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass  # notify-send not available or failed

    log("=== healthcheck done ===\n")


if __name__ == "__main__":
    main()

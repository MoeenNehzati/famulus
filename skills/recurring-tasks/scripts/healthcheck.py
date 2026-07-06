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


NOTIFY_SCRIPT = Path(__file__).parent / "assistant_desktop_notify.py"


def notify_desktop(title: str, body: str, urgency: str = "normal") -> None:
    """Send a cross-platform desktop notification via the sibling
    assistant_desktop_notify.py script. Best-effort: never raises, and logs
    a note if the tool isn't available."""
    if not NOTIFY_SCRIPT.is_file():
        log(f"  (desktop notification skipped: {NOTIFY_SCRIPT} not found)")
        return
    try:
        subprocess.run(
            [str(NOTIFY_SCRIPT), "--title", title, "--body", body, "--urgency", urgency],
            capture_output=True,
            timeout=10,
        )
    except Exception as e:
        log(f"  (desktop notification failed: {e})")


def log(msg: str) -> None:
    """Log to healthcheck log file."""
    HEALTHCHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with open(HEALTHCHECK_LOG, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(msg)


def check_systemd_manager() -> str | None:
    """Verify systemd user manager is running. Returns a failure reason, or None if OK."""
    result = subprocess.run(
        ["systemctl", "--user", "is-system-running"],
        capture_output=True,
        text=True,
    )
    state = result.stdout.strip()
    if state in ("running", "degraded"):
        log("✓ systemd user manager: OK")
        return None
    reason = f"systemd user manager: {state or 'unresponsive'}"
    log(f"✗ {reason}")
    return reason


def check_environment() -> str | None:
    """Verify AI_AGENT_COMMAND_TEMPLATE is set and resolves. Returns a failure reason, or None if OK."""
    result = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        capture_output=True,
        text=True,
    )
    template = None
    for line in result.stdout.splitlines():
        if line.startswith("AI_AGENT_COMMAND_TEMPLATE="):
            template = line.split("=", 1)[1]
            # Strip bash quoting ($'...')
            if template.startswith("$'") and template.endswith("'"):
                template = template[2:-1]
            break

    if not template:
        reason = "AI_AGENT_COMMAND_TEMPLATE: not set"
        log(f"✗ {reason}")
        return reason

    # Extract command name (first token)
    cmd = template.split()[0] if template else ""
    if not shutil.which(cmd):
        reason = f"AI_AGENT_COMMAND_TEMPLATE: command not found: {cmd}"
        log(f"✗ {reason}")
        return reason

    log(f"✓ AI_AGENT_COMMAND_TEMPLATE: {template}")
    return None


def check_job(job: dict) -> str | None:
    """Check if a job ran recently and succeeded. Returns a failure reason, or None if OK."""
    name = job["name"]
    log_file = LOG_DIR / name / "run.log"

    if not log_file.exists():
        reason = f"{name}: no log file"
        log(f"  ✗ {reason}")
        return reason

    # Check if log is fresh (within 2x scheduled interval)
    interval_mins = parse_schedule_interval(job["schedule"])
    stale_threshold = timedelta(minutes=interval_mins * 2)
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        log_file.stat().st_mtime, tz=timezone.utc
    )

    if age > stale_threshold:
        reason = f"{name}: log stale ({age.total_seconds() / 60:.0f}m old)"
        log(f"  ⚠ {reason}")
        return reason

    # Check systemd timer status
    result = subprocess.run(
        ["systemctl", "--user", "is-active", f"ai-{name}.timer"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        reason = f"{name}: timer not active"
        log(f"  ✗ {reason}")
        return reason

    log(f"  ✓ {name}: OK")
    return None


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

    failures: list[str] = []

    # Pre-flight checks
    reason = check_systemd_manager()
    if reason:
        failures.append(reason)
    reason = check_environment()
    if reason:
        failures.append(reason)

    # Per-job checks
    log("Per-job checks:")
    for job in jobs:
        if not job.get("enabled", False):
            continue
        reason = check_job(job)
        if reason:
            failures.append(reason)

    problems = len(failures)

    # Report
    if problems == 0:
        log("✓ All checks passed")
        notify_desktop("Recurring Tasks", "All checks passed", urgency="low")
    else:
        log(f"✗ {problems} problem(s) found")
        MAX_LISTED = 5
        listed = failures[:MAX_LISTED]
        body = f"{problems} health check problem(s):\n" + "\n".join(f"- {f}" for f in listed)
        if problems > MAX_LISTED:
            body += f"\n(+{problems - MAX_LISTED} more — see healthcheck log)"
        notify_desktop("Recurring Tasks", body, urgency="critical")

    log("=== healthcheck done ===\n")


if __name__ == "__main__":
    main()

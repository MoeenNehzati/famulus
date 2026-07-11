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

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent.parent
RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _schedule_backend import ScheduleBackendUnsupported, platform_schedule_backend  # noqa: E402

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
    """Verify the host scheduler manager. Returns a failure reason, or None if OK."""
    try:
        reason = platform_schedule_backend().check_manager()
    except ScheduleBackendUnsupported as e:
        reason = str(e)
    if reason:
        log(f"FAIL: {reason}")
        return reason
    log("OK: scheduler manager")
    return None


def check_environment() -> str | None:
    """Verify AI_AGENT_COMMAND_TEMPLATE is set and resolves. Returns a failure reason, or None if OK."""
    try:
        template = platform_schedule_backend().get_agent_command_template()
    except ScheduleBackendUnsupported as e:
        reason = str(e)
        log(f"FAIL: {reason}")
        return reason

    if not template:
        reason = "AI_AGENT_COMMAND_TEMPLATE: not set"
        log(f"FAIL: {reason}")
        return reason

    # Extract command name (first token)
    cmd = template.split()[0] if template else ""
    if not shutil.which(cmd):
        reason = f"AI_AGENT_COMMAND_TEMPLATE: command not found: {cmd}"
        log(f"FAIL: {reason}")
        return reason

    log(f"OK: AI_AGENT_COMMAND_TEMPLATE: {template}")
    return None


def check_job(job: dict) -> str | None:
    """Check if a job ran recently and succeeded. Returns a failure reason, or None if OK."""
    name = job["name"]
    log_file = LOG_DIR / name / "run.log"

    if not log_file.exists():
        reason = f"{name}: no log file"
        log(f"  FAIL: {reason}")
        return reason

    # Check if log is fresh (within 2x scheduled interval)
    interval_mins = parse_schedule_interval(job["schedule"])
    stale_threshold = timedelta(minutes=interval_mins * 2)
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        log_file.stat().st_mtime, tz=timezone.utc
    )

    if age > stale_threshold:
        reason = f"{name}: log stale ({age.total_seconds() / 60:.0f}m old)"
        log(f"  WARN: {reason}")
        return reason

    try:
        is_active = platform_schedule_backend().check_job_active(name)
    except ScheduleBackendUnsupported as e:
        reason = str(e)
        log(f"  FAIL: {reason}")
        return reason

    if not is_active:
        reason = f"{name}: timer not active"
        log(f"  FAIL: {reason}")
        return reason

    log(f"  OK: {name}")
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


class Interface(PythonArgvMachineInterface):
    prog = "healthcheck_probe.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(f"error: unexpected arguments: {' '.join(argv)}", file=sys.stderr)
        return 2

    log("=== healthcheck start ===")

    import yaml

    # Load jobs
    try:
        with open(JOBS_FILE) as f:
            jobs = (yaml.safe_load(f) or {}).get("jobs", [])
    except Exception as e:
        log(f"FAIL: Failed to load jobs.yaml: {e}")
        return 0

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
        log("OK: All checks passed")
        notify_desktop("Recurring Tasks", "All checks passed", urgency="low")
    else:
        log(f"FAIL: {problems} problem(s) found")
        MAX_LISTED = 5
        listed = failures[:MAX_LISTED]
        body = f"{problems} health check problem(s):\n" + "\n".join(f"- {f}" for f in listed)
        if problems > MAX_LISTED:
            body += f"\n(+{problems - MAX_LISTED} more - see healthcheck log)"
        notify_desktop("Recurring Tasks", body, urgency="critical")

    log("=== healthcheck done ===\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

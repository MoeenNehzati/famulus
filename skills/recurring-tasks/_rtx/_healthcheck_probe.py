#!/usr/bin/env python3
"""
Healthcheck for recurring tasks. Verifies jobs are healthy.
Run periodically via cron (not systemd, so it works even if systemd breaks).
"""
import os
import shutil
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).parent
REPO_ROOT = SKILL_DIR.parents[2]
# Deliberately no repository-src insert: officina is on site-packages in the
# managed runtime that both the cron line and the gateway execute under. This
# insert was unconditional, so it fired under the gateway too -- which snapshots
# sys.path around every module exec and raises ImportError on any change -- and
# it also silently preferred the working tree's officina over the pinned
# release the rest of the system runs. The RTX_DIR insert below stays: it is
# guarded on __package__, so it applies only to the standalone cron mode.
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_ROOT = SKILL_DIR.parent
RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from ._jobs_config import load_jobs
    from ._run_record import read_latest_run_record
    from ._schedule_backend._linux_registration_check import check_job_configuration
    from ._schedule_backend import (
        ScheduleBackendUnsupported,
        ScheduleContext,
        ScheduleJob,
        platform_schedule_backend,
    )
else:
    from _jobs_config import load_jobs  # noqa: E402
    from _run_record import read_latest_run_record  # noqa: E402
    from _schedule_backend._linux_registration_check import (  # noqa: E402
        check_job_configuration,
    )
    from _schedule_backend import (  # noqa: E402
        ScheduleBackendUnsupported,
        ScheduleContext,
        ScheduleJob,
        platform_schedule_backend,
    )

JOBS_FILE = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"
HEALTHCHECK_LOG = SKILL_ROOT / "logs" / "healthcheck" / "run.log"
# A run may legitimately be in flight when the check fires. Beyond the
# executor's own timeout (plus slack) it cannot be: it was killed without
# getting to write a record.
INCOMPLETE_RUN_GRACE_SECONDS = 3600 + 600


# The sentinel that raises the desktop popup runs from cron, in a separate
# failure domain, and so cannot see this process's findings. This file is the
# one channel between them: the checker writes why it failed, the sentinel
# reads it into the notification body. Kept short on purpose -- a popup that
# quotes a stack trace is a popup nobody reads.
HEALTHCHECK_SUMMARY = HEALTHCHECK_LOG.parent / "last-failure.txt"
_SUMMARY_MAX_REASONS = 3
_SUMMARY_REASON_LIMIT = 160


def write_failure_summary(failures: list[str]) -> None:
    """Record why this check failed, for the sentinel to show.

    Carries a timestamp because the sentinel also fires when the checker
    could not start at all, in which case what it reads is the previous
    run's file; the time is what makes that visible rather than misleading.
    """
    HEALTHCHECK_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    shown = failures[:_SUMMARY_MAX_REASONS]
    lines = [f"{len(failures)} problem(s) found at {when}:"]
    for reason in shown:
        if len(reason) > _SUMMARY_REASON_LIMIT:
            reason = reason[: _SUMMARY_REASON_LIMIT - 1].rstrip() + "…"
        lines.append(f"- {reason}")
    remaining = len(failures) - len(shown)
    if remaining > 0:
        lines.append(f"- (+{remaining} more; see the health-check log)")
    HEALTHCHECK_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clear_failure_summary() -> None:
    """Drop the stale reason once every check passes."""
    HEALTHCHECK_SUMMARY.unlink(missing_ok=True)


def log(msg: str) -> None:
    """Write one health-check report line.

    A single logging path avoids duplicate cron lines without losing durable
    output from direct invocations.
    """
    if not os.environ.get("RECURRING_TASKS_HEALTHCHECK_CRON"):
        HEALTHCHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(HEALTHCHECK_LOG, "a", encoding="utf-8") as report:
            report.write(f"[{timestamp}] {msg}\n")
    print(msg)


def check_systemd_manager() -> str | None:
    """Return a scheduler-manager failure reason when unavailable.

    Per-job evidence is unreliable when the owning scheduler manager is
    unavailable.
    """
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
    """Return an agent-command environment failure reason when invalid.

    A healthy timer cannot run useful AI work when its configured agent
    launcher is absent or unresolved. Resolving against this process's own PATH
    would answer a different question: cron's PATH lacks the launcher
    directory, and an operator's interactive PATH may contain one the scheduler
    never sees.
    """
    try:
        backend = platform_schedule_backend()
        template = backend.get_agent_command_template()
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
    # Ask the backend which directories its jobs actually resolve from, so the
    # question matches what the scheduler will do. A backend returning None
    # does not pin a job PATH, so its jobs inherit the ambient one.
    try:
        search_dirs = backend.job_search_dirs()
    except Exception as exc:  # an unresolvable install layout is a real failure
        reason = f"AI_AGENT_COMMAND_TEMPLATE: cannot resolve scheduler search path: {exc}"
        log(f"FAIL: {reason}")
        return reason
    search_path = (
        os.pathsep.join(str(part) for part in search_dirs)
        if search_dirs is not None
        else os.environ.get("PATH", "")
    )
    if not shutil.which(cmd, path=search_path):
        reason = f"AI_AGENT_COMMAND_TEMPLATE: command not found: {cmd}"
        log(f"FAIL: {reason}")
        return reason

    log(f"OK: AI_AGENT_COMMAND_TEMPLATE: {template}")
    return None


def check_job(job: dict) -> str | None:
    """Return the first registration or execution failure for one job.

    Fresh logs alone can mask stale units or failed runs, so health requires
    configuration and outcome evidence together.
    """
    name = job["name"]
    backend = platform_schedule_backend()
    context = ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=JOBS_FILE,
        log_dir=LOG_DIR,
    )
    try:
        reason = check_job_configuration(
            backend_name=backend.name,
            job=ScheduleJob.from_mapping(job),
            context=context,
        )
    except ScheduleBackendUnsupported as exc:
        reason = str(exc)
    if reason:
        log(f"  FAIL: {reason}")
        return reason

    # Freshness comes from the record the run itself wrote, not from a log
    # file's modification time: a run that starts and never finishes refreshes
    # that timestamp without ever completing.
    try:
        interval_mins = parse_schedule_interval(job["schedule"])
    except ValueError as exc:
        reason = f"{name}: unusable schedule: {exc}"
        log(f"  FAIL: {reason}")
        return reason

    # A run that started and never finished leaves an in-flight marker. The
    # run log's mtime was already refreshed by "--- RUN START ---", so
    # freshness alone would report a killed job as healthy indefinitely.
    marker = LOG_DIR / name / "running.json"
    if marker.exists():
        marker_age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            marker.stat().st_mtime, tz=timezone.utc
        )
        if marker_age > timedelta(seconds=INCOMPLETE_RUN_GRACE_SECONDS):
            reason = (
                f"{name}: run started {marker_age.total_seconds() / 60:.0f}m ago and "
                "never completed (killed, timed out, or interrupted)"
            )
            log(f"  FAIL: {reason}")
            return reason
        # Younger than the grace window: a run is legitimately in flight.
        log(f"  OK: {name} (run in progress)")
        return None

    latest_path = LOG_DIR / name / "latest.json"
    latest = read_latest_run_record(log_dir=LOG_DIR, job_name=name)
    if latest is None:
        reason = (
            f"{name}: run record unreadable"
            if latest_path.exists()
            else f"{name}: no completed run recorded"
        )
        log(f"  FAIL: {reason}")
        return reason

    finished_at = latest.get("finished_at")
    try:
        finished = datetime.fromisoformat(str(finished_at))
    except (TypeError, ValueError):
        reason = f"{name}: run record has no usable finish time"
        log(f"  FAIL: {reason}")
        return reason
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)

    age = datetime.now(timezone.utc) - finished
    if age > timedelta(minutes=interval_mins * 2):
        reason = (
            f"{name}: last completed run was "
            f"{age.total_seconds() / 60:.0f}m ago"
        )
        log(f"  FAIL: {reason}")
        return reason

    if latest.get("success") is not True:
        detail = latest.get("reason") or "no failure reason recorded"
        reason = f"{name}: latest run failed ({detail})"
        log(f"  FAIL: {reason}")
        return reason

    try:
        is_active = backend.check_job_active(name)
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
    """Estimate a supported cron schedule interval in minutes.

    Health checks need a simple duration threshold without duplicating the
    scheduler's full cron parser.
    """
    parts = schedule.split()
    if len(parts) != 5:
        raise ValueError(f"expected a 5-field cron schedule, got {schedule!r}")
    minute, hour, _dom, _month, dow = parts

    if minute.startswith("*/"):
        return int(minute[2:])
    if hour.startswith("*/"):
        return int(hour[2:]) * 60
    if minute == "*":
        return 1  # every minute
    if hour == "*":
        return 60  # hourly, at a fixed minute
    if dow != "*":
        return 10080  # weekly: a healthy weekly job must not read as stale
    return 1440  # daily, at a fixed time


class Interface(PythonArgvMachineInterface):
    """Expose the health checker through the Python machine interface.

    The shared runtime requires a typed interface object for process-bound
    execution.
    """

    prog = "healthcheck_probe.py"

    def run(self, argv: list[str]) -> int:
        """Run the health-check command with explicit arguments.

        One forwarding method keeps direct and dispatcher-driven execution
        behavior identical.
        """
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run all enabled recurring-task health checks and return status.

    Cron can independently notify on any nonzero result while operators retain
    the underlying reasons in one report.
    """
    if argv:
        print(f"error: unexpected arguments: {' '.join(argv)}", file=sys.stderr)
        return 2

    log("=== healthcheck start ===")

    # Load jobs
    try:
        jobs = load_jobs(JOBS_FILE)
    except Exception as e:
        log(f"FAIL: Failed to load jobs.yaml: {e}")
        return 1

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
        clear_failure_summary()
    else:
        log(f"FAIL: {problems} problem(s) found")
        write_failure_summary(failures)

    log("=== healthcheck done ===\n")
    return 1 if problems > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

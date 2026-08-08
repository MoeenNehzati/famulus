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
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_ROOT = SKILL_DIR.parent
RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
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
_DEFAULT_TOLERANCE_TAIL_BYTES = 32768


def log(msg: str) -> None:
    """Write one health-check report line.

    Intent
    ------
    Persist manual reports while letting cron-owned stdout redirection persist scheduled reports exactly once.

    Rationale
    ---------
    A single logging path avoids duplicate cron lines without losing durable output from direct invocations.

    Pseudocode
    ----------
    - if invocation is not cron_managed:
      - set persisted_report = timestamped_message
    - set standard_output = message

    Wraps
    -----
    - none
    """
    if not os.environ.get("RECURRING_TASKS_HEALTHCHECK_CRON"):
        HEALTHCHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(HEALTHCHECK_LOG, "a", encoding="utf-8") as report:
            report.write(f"[{timestamp}] {msg}\n")
    print(msg)


def _read_log_tail(path: Path, *, max_bytes: int = _DEFAULT_TOLERANCE_TAIL_BYTES) -> str:
    """Read a bounded suffix of a file for heuristic failure classification.

    Intent
    ------
    Return only the newest part of a potentially large log file so failure heuristics are fast.

    Rationale
    ---------
    Unbounded reads can be expensive for long-running job logs and can
    miss only by memory pressure; a bounded read keeps checks predictable.

    Pseudocode
    ----------
    - if path is not readable:
      - return ""
    - set size = file_size_bytes(path)
    - set offset = max(size - max_bytes, 0)
    - set payload = read_file_range(path, offset)
    - set decoded_tail_text = decode(payload, errors="replace")
    - return decoded_tail_text

    Wraps
    -----
    - none
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size > max_bytes:
                handle.seek(size - max_bytes)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _allowed_failure_pattern(job: dict, latest: dict, log_file: Path) -> str | None:
    """Return a matched policy pattern when the latest failure should be tolerated.

    Intent
    ------
    Apply per-job policy overrides to decide whether a failing run should be treated as tolerated.

    Rationale
    ---------
    Some jobs fail with known, acceptable exit codes and log motifs, and repeatedly
    failing on those cases should not block the global health check.

    Pseudocode
    ----------
    - set contract = job.get("success") or {}
    - set ignore_exit_codes = contract.get("ignore_exit_codes")
    - set ignore_patterns = contract.get("ignore_exit_log_patterns")
    - set process_exit_code = int_or_none(latest.get("process_exit_code"))
    - if latest.get("success") is true:
      - return none
    - if process_exit_code is none:
      - return none
    - if process_exit_code == 0:
      - return none
    - if ignore_patterns is none:
      - return none
    - set first_match = first_pattern_match(ignore_patterns, _read_log_tail(log_file))
    - if first_match is none:
      - return none
    - return first_match

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._read_log_tail:
      why:
        constructs: "Provides a bounded log tail to avoid expensive reads before regex scanning."
    """
    if latest.get("success") is True:
        return None

    contract = job.get("success") or {}
    ignore_exit_codes = contract.get("ignore_exit_codes")
    ignore_patterns = contract.get("ignore_exit_log_patterns")

    if not ignore_patterns or not isinstance(ignore_patterns, list):
        return None
    if not ignore_patterns:
        return None

    try:
        process_exit_code = int(latest.get("process_exit_code"))
    except (TypeError, ValueError):
        process_exit_code = None
    if process_exit_code == 0:
        return None

    if ignore_exit_codes is not None:
        if not isinstance(ignore_exit_codes, list):
            ignore_exit_codes = [ignore_exit_codes]
        normalized_codes = set()
        for item in ignore_exit_codes:
            try:
                normalized_codes.add(int(item))
            except (TypeError, ValueError):
                continue
        if not normalized_codes or process_exit_code not in normalized_codes:
            return None

    log_tail = _read_log_tail(log_file)
    for pattern in ignore_patterns:
        if not isinstance(pattern, str):
            continue
        try:
            if re.search(pattern, log_tail):
                return pattern
        except re.error:
            continue
    return None


def check_systemd_manager() -> str | None:
    """Return a scheduler-manager failure reason when unavailable.

    Intent
    ------
    Confirm that the selected native per-user scheduler can answer health queries.

    Rationale
    ---------
    Per-job evidence is unreliable when the owning scheduler manager is unavailable.

    Pseudocode
    ----------
    - set reason = scheduler_manager_health
    - if scheduler_backend_is_unsupported:
      - set reason = unsupported_reason
    - set healthcheck_report = reason_or_success
    - return reason_or_none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        writes: "Records the manager result in the durable health-check report before returning it to the aggregator."
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

    Intent
    ------
    Verify that the scheduler environment declares an agent command whose executable resolves on PATH.

    Rationale
    ---------
    A healthy timer cannot run useful AI work when its configured agent launcher is absent or unresolved.

    Pseudocode
    ----------
    - set template = scheduler_agent_command_template
    - if scheduler_backend_is_unsupported:
      - set healthcheck_report = unsupported_reason
      - return failure
    - if template is missing or executable is unresolved:
      - set healthcheck_report = environment_failure
      - return failure
    - set healthcheck_report = environment_success
    - return none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        writes: "Records each environment diagnosis so a failed independent sentinel leaves actionable evidence."
    """
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
    """Return the first registration or execution failure for one job.

    Intent
    ------
    Check native registration, log freshness, latest structured outcome, and scheduler activity for an enabled job.

    Rationale
    ---------
    Fresh logs alone can mask stale units or failed runs, so health requires configuration and outcome evidence together.

    Pseudocode
    ----------
    - set backend = selected_schedule_backend
    - set schedule_context = configured_recurring_task_paths
    - set reason = first_registration_or_execution_failure
    - if reason exists:
      - set healthcheck_report = reason
      - return reason
    - set healthcheck_report = job_success
    - return none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        writes: "Records the first decisive job-health result for operator diagnosis."

    InstantiationsFromRepo
    ----------------------
    .parse_schedule_interval:
      why:
        constructs: "Converts the declared schedule into the freshness window used to classify the run log."
    ._allowed_failure_pattern:
      why:
        constructs: "Classifies selected non-zero failure outcomes as tolerated instead of hard failures."
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

    latest_path = LOG_DIR / name / "latest.json"
    latest = read_latest_run_record(log_dir=LOG_DIR, job_name=name)
    if latest_path.exists() and latest is None:
        reason = f"{name}: latest run record unreadable"
        log(f"  FAIL: {reason}")
        return reason
    if latest is not None and latest.get("success") is not True:
        allowed_failure = _allowed_failure_pattern(
            job=job,
            latest=latest,
            log_file=log_file,
        )
        if allowed_failure is not None:
            detail = latest.get("reason") or "no failure reason recorded"
            log(
                "  WARN: "
                f"{name}: latest run failed ({detail}); tolerated by policy ({allowed_failure})"
            )
            return None
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

    Intent
    ------
    Produce a conservative freshness interval for hourly, stepped, and daily recurring schedules.

    Rationale
    ---------
    Health checks need a simple duration threshold without duplicating the scheduler's full cron parser.

    Pseudocode
    ----------
    - set minute = schedule_minute_field
    - set hour = schedule_hour_field
    - if minute or hour is stepped:
      - return step_in_minutes
    - if schedule is hourly:
      - return sixty_minutes
    - if schedule has a fixed hour:
      - return one_day
    - return sixty_minutes

    Wraps
    -----
    - none
    """
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
    """Expose the health checker through the Python machine interface.

    Intent
    ------
    Bind dispatcher-provided arguments to the health-check command entrypoint.

    Rationale
    ---------
    The shared runtime requires a typed interface object for process-bound execution.

    Pseudocode
    ----------
    - set program_name = healthcheck_probe
    - set interface_run = received_argv

    Wraps
    -----
    - none
    """

    prog = "healthcheck_probe.py"

    def run(self, argv: list[str]) -> int:
        """Run the health-check command with explicit arguments.

        Intent
        ------
        Forward machine-interface arguments to the command entrypoint and return its status.

        Rationale
        ---------
        One forwarding method keeps direct and dispatcher-driven execution behavior identical.

        Pseudocode
        ----------
        - return @.main(argv)

        Wraps
        -----
        .main -> preprocess: receives machine-interface argv unchanged; postprocess: returns the health-check status unchanged; fixed_arguments: none
        """
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run all enabled recurring-task health checks and return status.

    Intent
    ------
    Aggregate scheduler, environment, and per-job failures into a durable report and truthful process exit code.

    Rationale
    ---------
    Cron can independently notify on any nonzero result while operators retain the underlying reasons in one report.

    Pseudocode
    ----------
    - if unexpected_arguments exist:
      - return usage_failure
    - set healthcheck_report = start_marker
    - set jobs = loaded_job_configuration
    - if configuration_load_fails:
      - set healthcheck_report = configuration_failure
      - return failure
    - set failures = manager_and_environment_failures
    - for job in enabled_jobs:
      - set failures = failures_with_job_result
    - set healthcheck_report = aggregate_result
    - return failure_when_any_problem_else_success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        writes: "Writes lifecycle markers and aggregate results to the health-check report."

    InstantiationsFromRepo
    ----------------------
    .check_systemd_manager:
      why:
        constructs: "Produces the scheduler-manager failure value accumulated into the final status."
    .check_environment:
      why:
        constructs: "Produces the agent-environment failure value accumulated into the final status."
    .check_job:
      why:
        constructs: "Produces each enabled job's failure value accumulated into the final status."
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
    else:
        log(f"FAIL: {problems} problem(s) found")

    log("=== healthcheck done ===\n")
    return 1 if problems > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

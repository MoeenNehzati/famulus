#!/usr/bin/env python3
"""Execute one recurring task job without invoking a shell."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

SKILL_DIR = Path(__file__).parent
REPO_ROOT = SKILL_DIR.parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

if __package__:
    from ._jobs_config import load_jobs
    from ._run_record import (
        SPAWN_FAILURE_EXIT_CODE,
        JobRunRecord,
        SuccessEvaluation,
        evaluate_success_contract,
        read_inner_status,
        write_run_record,
    )
else:
    from _jobs_config import load_jobs  # noqa: E402
    from _run_record import (  # noqa: E402
        SPAWN_FAILURE_EXIT_CODE,
        JobRunRecord,
        SuccessEvaluation,
        evaluate_success_contract,
        read_inner_status,
        write_run_record,
    )

LOG_DIR = SKILL_DIR / "logs"
SKILLS_ROOT = REPO_ROOT / "skills"
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _strip_matching_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def parse_command(command: str, *, platform: str = sys.platform) -> tuple[dict[str, str], list[str]]:
    """Parse a job command string into leading environment assignments and argv."""
    tokens = shlex.split(command, posix=(platform != "win32"))
    if platform == "win32":
        posix_tokens = shlex.split(command, posix=True)
        if posix_tokens and ENV_ASSIGNMENT_RE.fullmatch(posix_tokens[0]):
            tokens = posix_tokens
        else:
            tokens = [_strip_matching_quotes(token) for token in tokens]
    env: dict[str, str] = {}
    index = 0
    for token in tokens:
        if not ENV_ASSIGNMENT_RE.fullmatch(token):
            break
        key, value = token.split("=", 1)
        env[key] = value
        index += 1
    argv = tokens[index:]
    if not argv:
        raise ValueError("job command did not contain an executable")
    return env, argv


def load_job(jobs_file: Path, job_name: str) -> dict:
    for job in load_jobs(jobs_file):
        if job.get("name") == job_name:
            return job
    raise ValueError(f"Job not found: {job_name}")


def resolve_executable(argv: list[str], env: dict[str, str], *, platform: str = sys.platform) -> list[str]:
    """Resolve Windows launcher shims such as invoke-skill.bat without a shell."""
    if platform != "win32" or not argv:
        return argv
    resolved = shutil.which(argv[0], path=env.get("PATH"))
    if not resolved:
        return argv
    return [resolved, *argv[1:]]


# No job may run unbounded. The systemd units carry TimeoutStartUSec=infinity
# and RuntimeMaxUSec=infinity, so before this the only bound on a hung agent
# was the heat death of the session. One hour is far above these jobs' real
# durations (email-triage ~2min, daily-plan ~1min).
JOB_TIMEOUT_SECONDS = 3600
# Out-of-range sentinel, like SPAWN_FAILURE_EXIT_CODE, so it cannot be
# confused with a real exit code or a negated signal number.
TIMEOUT_EXIT_CODE = -1001

RUNNING_MARKER_NAME = "running.json"


def running_marker_path(*, log_dir: Path, job_name: str) -> Path:
    return log_dir / job_name / RUNNING_MARKER_NAME


def _write_running_marker(
    *, log_dir: Path, job_name: str, started_at: str, run_id: str
) -> Path:
    """Record that a run is in flight; removed only on normal completion."""
    marker = running_marker_path(log_dir=log_dir, job_name=job_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"job_name": job_name, "started_at": started_at, "run_id": run_id})
        + "\n",
        encoding="utf-8",
    )
    return marker


def _read_run_output(log_file: Path, start: int) -> str:
    """Read what this run appended to its log, starting at `start`."""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as reader:
            reader.seek(start)
            return reader.read()
    except OSError:
        return ""


def run_job(*, jobs_file: Path, job_name: str, log_dir: Path = LOG_DIR) -> int:
    job = load_job(jobs_file, job_name)
    command = str(job["command"]).replace("{skill_dir}", str(SKILL_DIR))
    env_overrides, argv = parse_command(command)

    log_file = log_dir / job_name / "run.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **env_overrides}
    resolved_argv = resolve_executable(argv, env)

    started_dt = datetime.datetime.now(datetime.timezone.utc)
    started_at = started_dt.isoformat(timespec="seconds")
    # A fresh id per run, independent of finished_at's second-resolution
    # timestamp: two runs (e.g. a fast spawn failure followed by a manual
    # retry) can legitimately finish within the same second, and test_job()
    # needs a way to tell "a new run happened" apart from "no new run yet"
    # that doesn't depend on sub-second timing.
    run_id = uuid.uuid4().hex
    with log_file.open("a", encoding="utf-8") as log:
        log.write("--- RUN START ---\n")
        log.flush()

        # An in-flight marker, removed only on normal completion. A killed
        # executor (stop, OOM, reboot, suspend mid-run) writes no run record,
        # so this file is the only evidence that a run began and never
        # finished. Freshness alone would not notice until the job's next
        # two scheduled slots had passed.
        marker = _write_running_marker(
            log_dir=log_dir, job_name=job_name, started_at=started_at, run_id=run_id
        )

        # Where this run's output begins, so success is judged against it
        # alone rather than a slice of the job's cumulative log.
        output_start = log.tell()

        spawn_error: OSError | None = None
        timed_out = False
        try:
            result = subprocess.run(
                resolved_argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
                timeout=JOB_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run has already killed the child by this point.
            timed_out = True
            result = None
        except OSError as exc:
            # The child process never spawned at all (missing/misconfigured
            # executable, permission denied, etc.) -- this is a real,
            # observed failure mode, not merely a nonzero exit. We still owe
            # a RUN END marker and a run record so a caller polling for one
            # (e.g. manage_job.py's test_job()) doesn't wait out its full
            # timeout for something that failed instantly.
            spawn_error = exc
            result = None

        finished_at = _utc_now_iso()

        if timed_out:
            process_exit_code = TIMEOUT_EXIT_CODE
            inner_status = None
            evaluation = SuccessEvaluation(
                success=False,
                reason=f"job exceeded its {JOB_TIMEOUT_SECONDS}s timeout and was killed",
            )
            log.write(f"--- process killed after {JOB_TIMEOUT_SECONDS}s timeout ---\n")
        elif spawn_error is not None:
            # SPAWN_FAILURE_EXIT_CODE (-1000) is a deliberately
            # out-of-range sentinel -- see its definition in _run_record.py
            # -- so it can't be misread as "killed by signal N" the way a
            # small negative number like -1 could be. `reason` always spells
            # out what actually happened; don't infer from the number alone.
            process_exit_code = SPAWN_FAILURE_EXIT_CODE
            inner_status = None
            evaluation = SuccessEvaluation(
                success=False, reason=f"failed to spawn process: {spawn_error}"
            )
            log.write(f"--- process failed to spawn: {spawn_error} ---\n")
        else:
            process_exit_code = result.returncode
            inner_status = read_inner_status(
                skills_root=SKILLS_ROOT,
                job_name=job_name,
                # Scope the status to THIS run: a file left by an earlier run
                # must not vouch for one that never wrote its own.
                not_before=started_dt,
            )
            log.flush()
            evaluation = evaluate_success_contract(
                process_exit_code=process_exit_code,
                inner_status=inner_status,
                contract=job.get("success"),
                run_output=_read_run_output(log_file, output_start),
            )

        write_run_record(
            log_dir=log_dir,
            record=JobRunRecord(
                job_name=job_name,
                started_at=started_at,
                finished_at=finished_at,
                process_exit_code=process_exit_code,
                inner_status=inner_status,
                success=evaluation.success,
                reason=evaluation.reason,
                run_id=run_id,
            ),
        )

        log.write(f"--- RUN END (success={evaluation.success}) ---\n")
        log.flush()

    # Only a run that reached this point completed. A killed executor leaves
    # the marker behind, which is exactly what makes the failure visible.
    marker.unlink(missing_ok=True)

    return process_exit_code


class Interface(PythonArgvMachineInterface):
    prog = "job_executor.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-file", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        return run_job(
            jobs_file=args.jobs_file, job_name=args.job, log_dir=args.log_dir
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

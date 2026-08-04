#!/usr/bin/env python3
"""Separates "the scheduler triggered a process" from "the job actually
succeeded" by recording process exit code and inner task status together,
evaluated against each job's declared success contract.

Background: a job's OS-level exit code only tells us the wrapper process
launched and returned. A job can exit 0 while the work it wrapped actually
failed (e.g. an inner script caught its own exception and degraded
gracefully) or a scheduler can accept a trigger request without the job
having run at all yet. This module gives recurring-tasks a single place to
decide "did this run actually succeed" by combining:

  1. process_exit_code — the literal subprocess return code.
  2. inner_status — an optional self-reported status a job writes to its own
     state/status.json (see read_inner_status()), for jobs that have such a
     mechanism. Jobs that don't have one simply never populate this, and a
     job's success contract in jobs.yaml should not require it in that case
     (see evaluate_success_contract()'s "no contract declared" default).

The result is persisted per-job as `logs/<job>/latest.json` (see
write_run_record()), which manage_job.py's `test` subcommand and the
healthcheck probe can read to answer "did the run actually succeed" without
re-deriving it themselves.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

RTX_DIR = Path(__file__).resolve().parent
SRC_DIR = RTX_DIR.parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from officina.common import atomic_files  # noqa: E402


@dataclass(frozen=True)
class SuccessEvaluation:
    """The result of checking a run's outcome against a job's success contract."""

    success: bool
    reason: str = ""


# POSIX subprocess return codes are either >= 0 (normal exit) or in
# [-1, -64] roughly (negated signal number: -1 for SIGHUP, -9 for SIGKILL,
# etc). SPAWN_FAILURE_EXIT_CODE is deliberately far outside that whole
# range so a reader of process_exit_code alone -- without also checking
# `reason` -- can't mistake "the process never spawned" for "the process
# ran and was killed by some signal."
SPAWN_FAILURE_EXIT_CODE = -1000


@dataclass(frozen=True)
class JobRunRecord:
    """A durable record of one job execution: what actually happened, not
    merely that a trigger was accepted."""

    job_name: str
    started_at: str
    finished_at: str
    # The subprocess's real exit code, or SPAWN_FAILURE_EXIT_CODE (-1000)
    # when the process never spawned at all (missing/misconfigured
    # executable, permission denied, etc). -1000 is intentionally outside
    # the range POSIX ever uses for a real exit code or negated signal
    # number (roughly 0..255 and -1..-64 respectively), so it can't be
    # confused with "killed by signal 1" or similar. `reason` always
    # explains the -1000 case in prose; don't infer from the number alone.
    process_exit_code: int
    inner_status: str | None
    success: bool
    reason: str = ""
    # Unique per run (see run_job()'s uuid4 assignment), so callers like
    # test_job() can detect "a new run happened" even when two runs finish
    # within the same second-resolution `finished_at` timestamp.
    run_id: str = ""


def evaluate_success_contract(
    *, process_exit_code: int, inner_status: str | None, contract: dict | None
) -> SuccessEvaluation:
    """Decide whether a run succeeded.

    A nonzero process exit code always fails the run. Beyond that, a job's
    `success:` contract in jobs.yaml can require the job's self-reported
    inner_status (from its own state/status.json, if it has one) to equal a
    specific value — e.g. `require_inner_status: ok`. A job with no
    `success:` block declared (or an empty one) passes on exit code alone;
    this is intentional so jobs without an inner-status mechanism aren't
    penalized for never reporting one.
    """
    contract = contract or {}

    if process_exit_code != 0:
        return SuccessEvaluation(
            success=False, reason=f"process exit code {process_exit_code}"
        )

    required = contract.get("require_inner_status")
    if required is not None and inner_status != required:
        return SuccessEvaluation(
            success=False,
            reason=f"inner status {inner_status!r} != required {required!r}",
        )

    return SuccessEvaluation(success=True)


# Jobs whose inner status.json has moved off the SKILLS_ROOT/<job>/state/
# convention below, plus the env var (if any) that overrides *that* job's
# resolved directory for tests/CI. email-triage moved its state to the
# shared Famulus state root (see officina.common.famulus_paths) because its
# installed skill tree may be read-only -- see
# email-triage/_rtx/_watermark_writer.py's default_state_dir(), which this
# mirrors (including its EMAIL_TRIAGE_STATE_DIR override) so both sides of
# that convention agree on where status.json actually lives.
_JOB_STATE_DIR_ENV_OVERRIDE = {
    "email-triage": "EMAIL_TRIAGE_STATE_DIR",
}


def _resolve_job_state_dir(*, skills_root: Path, job_name: str) -> Path:
    """Resolve the directory containing a job's status.json.

    Most jobs -- if they have any inner-status mechanism at all -- still use
    the original convention: SKILLS_ROOT/<job>/state/. Jobs listed in
    _JOB_STATE_DIR_ENV_OVERRIDE have moved elsewhere; see that dict's
    comment.
    """
    override_env = _JOB_STATE_DIR_ENV_OVERRIDE.get(job_name)
    if override_env:
        override = os.environ.get(override_env)
        if override:
            return Path(override)
        if job_name == "email-triage":
            from officina.common.famulus_paths import resolve_famulus_paths

            return resolve_famulus_paths(
                platform=sys.platform, home=Path.home()
            ).email_triage_state_root

    return skills_root / job_name / "state"


def read_inner_status(*, skills_root: Path, job_name: str) -> str | None:
    """Read a job's self-reported status from its state/status.json, if
    present.

    The file shape is the convention already used by email-triage's
    _watermark_floor.py / _watermark_writer.py / _failure_clearer.py:
    status.json containing {"result": "ok" | "error" | "warning", ...}. The
    directory it lives in is resolved per-job by _resolve_job_state_dir()
    since not every job keeps state under SKILLS_ROOT/<job>/state/ (see
    that function). Jobs that don't write this file at all (e.g. daily-plan,
    as of this writing) simply have no inner status, and callers should
    treat that as None rather than a failure by itself.
    """
    status_file = _resolve_job_state_dir(skills_root=skills_root, job_name=job_name) / "status.json"
    if not status_file.exists():
        return None
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    result = payload.get("result")
    return result if isinstance(result, str) else None


def write_run_record(*, log_dir: Path, record: JobRunRecord) -> None:
    """Atomically persist a run record as logs/<job>/latest.json."""
    job_dir = log_dir / record.job_name
    job_dir.mkdir(parents=True, exist_ok=True)
    destination = job_dir / "latest.json"
    data = (json.dumps(asdict(record), indent=2) + "\n").encode("utf-8")

    if os.name == "nt":
        _windows_atomic_write(destination, data)
        return

    # allow_non_atomic=True lets atomic_files fall back to a confined,
    # non-atomic write on capability-limited platforms/filesystems -- that
    # is the only sanctioned escape hatch. We deliberately do NOT catch
    # AtomicWriteError here: a genuine integrity violation (destination is
    # a symlink, resolves outside allowed_root, or isn't a regular file)
    # must propagate as a real error, not be silently papered over with a
    # plain write that bypasses those checks.
    atomic_files.atomic_replace_bytes(
        destination,
        data,
        allowed_root=job_dir,
        mode=0o644,
        allow_non_atomic=True,
    )


def _windows_atomic_write(destination: Path, data: bytes) -> None:
    import tempfile

    descriptor, temporary = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}."
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_latest_run_record(*, log_dir: Path, job_name: str) -> dict | None:
    """Read back the most recent run record for a job, if one exists."""
    latest = log_dir / job_name / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

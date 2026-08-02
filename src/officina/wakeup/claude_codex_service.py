"""Wakeup scheduling and guarded provider delivery orchestration.

Scheduling resolves a provider session, snapshots its latest meaningful turn,
and appends a persistent job. ``run_due`` is safe to invoke repeatedly from a
systemd timer: a non-blocking scanner lock prevents overlapping workers, queue
operations use a separate short-lived lock, and failed deliveries are retained
for retry. A job is deleted without delivery when its transcript has advanced.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import WakeupError
from .deadlines import utc_now
from .claude_codex_sessions import resolve_session, session_cwd, transcript_state
from .store import append_job, data_dir, due_jobs, update_job


def emit(event: str, **fields: object) -> None:
    """Write one journald-friendly event to the inherited stdout stream."""
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{event} {details}".rstrip(), flush=True)


def schedule(
    provider: str,
    session: str,
    deadline: datetime,
    message: str,
    *,
    context: str = "",
) -> dict:
    """Resolve a session, snapshot progress, and append one persistent job.

    Args:
        provider: ``claude`` or ``codex``.
        session: Exact UUID or provider-defined title alias.
        deadline: Timezone-aware delivery instant.
        message: Continuation prompt sent after the limit resets.
        context: Optional timeout/resume text used for alias disambiguation.

    Returns:
        The JSON-serializable job record written to the queue.
    """
    session_id, transcript = resolve_session(provider, session, context)
    job = {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "session_id": session_id,
        "run_at": deadline.astimezone(timezone.utc).isoformat(),
        "message": message,
        "transcript": str(transcript),
        "state": transcript_state(provider, transcript),
        "attempts": 0,
    }
    working_directory = session_cwd(provider, transcript)
    if working_directory is not None:
        job["cwd"] = str(working_directory)
    append_job(job)
    return job


def _provider_executable(provider: str) -> str:
    """Resolve a provider executable from override, PATH, then known locations."""
    override = os.environ.get(f"LLM_WAKEUP_{provider.upper()}_BIN")
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise WakeupError(f"configured {provider} executable is unavailable: {path}")

    discovered = shutil.which(provider)
    if discovered:
        return discovered
    fallbacks = {
        "claude": [Path.home() / ".local/bin/claude"],
        "codex": [Path.home() / ".npm-global/bin/codex", Path.home() / ".local/bin/codex"],
    }
    for path in fallbacks[provider]:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise WakeupError(f"could not find the {provider} executable")


def _provider_command(job: dict) -> list[str]:
    """Build the non-shell resume command for one persisted job."""
    executable = _provider_executable(job["provider"])
    if job["provider"] == "claude":
        return [executable, "--print", "--resume", job["session_id"], job["message"]]
    return [executable, "exec", "resume", job["session_id"], job["message"]]


def _retry(job: dict, now: datetime, event: str, detail: object) -> None:
    """Retain a failed job, increment attempts, and defer it for five minutes."""
    job["attempts"] = int(job.get("attempts", 0)) + 1
    job["run_at"] = (now + timedelta(minutes=5)).isoformat()
    update_job(job["id"], job)
    emit(
        event,
        id=job["id"],
        provider=job["provider"],
        session=job["session_id"],
        detail=detail,
        retry=job["run_at"],
    )


def run_due() -> None:
    """Deliver every due unchanged job while allowing concurrent scheduling.

    The scanner lock spans the complete delivery pass to prevent duplicate
    provider invocations. Queue locks remain short-lived, so a user can append
    new jobs while a provider command is running. Missing transcripts and
    provider failures retry after five minutes; progressed sessions are removed
    immediately and logged as ``skipped``.
    """
    now = utc_now()
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "scanner.lock").open("a") as scanner_lock:
        try:
            fcntl.flock(scanner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("scanner-busy")
            return

        for job in due_jobs(now.isoformat()):
            try:
                current_state = transcript_state(
                    job["provider"],
                    Path(job["transcript"]),
                )
            except WakeupError as error:
                _retry(job, now, "transcript-error", error)
                continue

            if current_state != job["state"]:
                update_job(job["id"], None)
                emit(
                    "skipped",
                    id=job["id"],
                    provider=job["provider"],
                    session=job["session_id"],
                    reason="session-progressed",
                )
                continue

            try:
                working_directory = job.get("cwd")
                if not working_directory:
                    discovered_cwd = session_cwd(
                        job["provider"], Path(job["transcript"])
                    )
                    working_directory = str(discovered_cwd) if discovered_cwd else None
                result = subprocess.run(
                    _provider_command(job),
                    check=False,
                    cwd=working_directory,
                )
            except (OSError, WakeupError) as error:
                _retry(job, now, "delivery-error", error)
                continue

            if result.returncode == 0:
                update_job(job["id"], None)
                emit(
                    "sent",
                    id=job["id"],
                    provider=job["provider"],
                    session=job["session_id"],
                )
            else:
                _retry(job, now, "delivery-error", f"exit-{result.returncode}")


__all__ = ["run_due", "schedule"]

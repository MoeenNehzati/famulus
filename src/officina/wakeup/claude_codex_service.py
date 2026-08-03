"""Guarded scheduling and delivery over provider adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import DEFAULT_MESSAGE, WakeupError
from .claude_codex_sessions import resolve_session, session_cwd, transcript_state
from .deadlines import utc_now
from .locking import LockUnavailable, locked_file
from .providers import provider_for
from .store import append_job, data_dir, due_jobs, update_job


def emit(event: str, **fields: object) -> None:
    """Write one journald-friendly line-oriented event."""

    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{event} {details}".rstrip(), flush=True)


def schedule(
    provider: str,
    session: str,
    deadline: datetime,
    message: str | None,
    *,
    context: str = "",
    transcript_path: Path | None = None,
) -> dict:
    """Resolve, snapshot, and persist one minute-deduplicated wakeup job.

    ``transcript_path`` lets trusted local integrations supply the exact path
    received from a host payload. Interactive callers continue to resolve
    ``session`` through the provider adapter.
    """

    if transcript_path is None:
        session_id, transcript = resolve_session(provider, session, context)
    else:
        session_id, transcript = session, transcript_path
        if not transcript.is_file():
            raise WakeupError(f"could not find transcript for {provider} session {session_id}")
    job = {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "session_id": session_id,
        "run_at": deadline.astimezone(timezone.utc).isoformat(),
        "message": DEFAULT_MESSAGE if message is None else message,
        "transcript": str(transcript),
        "state": transcript_state(provider, transcript),
        "attempts": 0,
    }
    working_directory = session_cwd(provider, transcript)
    if working_directory is not None:
        job["cwd"] = str(working_directory)
    persisted, created = append_job(job)
    persisted["deduplicated"] = not created
    return persisted


def _provider_executable(provider: str) -> str:
    """Resolve an executable override, PATH entry, or known provider location."""

    adapter = provider_for(provider)
    override = adapter.executable_override()
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise WakeupError(f"configured {provider} executable is unavailable: {path}")
    discovered = shutil.which(adapter.name)
    if discovered:
        return discovered
    for path in adapter.executable_candidates():
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise WakeupError(f"could not find the {provider} executable")


def _provider_command(job: dict) -> list[str]:
    """Compile one persisted job into the provider's unattended resume argv."""

    adapter = provider_for(job["provider"])
    return adapter.resume_command(
        _provider_executable(job["provider"]), job["session_id"], job["message"]
    )


def _retry(job: dict, now: datetime, event: str, detail: object) -> None:
    """Persist a five-minute retry and emit the operational failure event."""

    job["attempts"] = int(job.get("attempts", 0)) + 1
    job["run_at"] = (now + timedelta(minutes=5)).isoformat()
    update_job(job["id"], job)
    emit(event, id=job["id"], provider=job["provider"], session=job["session_id"], detail=detail, retry=job["run_at"])


def _run_locked_due(now: datetime) -> None:
    """Deliver due jobs while the process-wide worker lock is held.

    Each job is first compared with its scheduled transcript snapshot. A changed
    snapshot proves that the session progressed after scheduling, so the stale
    wakeup is removed without invoking either provider.
    """

    for job in due_jobs(now.isoformat()):
        try:
            current_state = transcript_state(job["provider"], Path(job["transcript"]))
        except WakeupError as error:
            _retry(job, now, "transcript-error", error)
            continue
        if current_state != job["state"]:
            update_job(job["id"], None)
            emit("skipped", id=job["id"], provider=job["provider"], session=job["session_id"], reason="session-progressed")
            continue
        try:
            working_directory = job.get("cwd")
            if not working_directory:
                discovered = session_cwd(job["provider"], Path(job["transcript"]))
                working_directory = str(discovered) if discovered else None
            result = subprocess.run(_provider_command(job), check=False, cwd=working_directory)
        except (OSError, WakeupError) as error:
            _retry(job, now, "delivery-error", error)
            continue
        if result.returncode == 0:
            update_job(job["id"], None)
            emit("sent", id=job["id"], provider=job["provider"], session=job["session_id"])
        else:
            _retry(job, now, "delivery-error", f"exit-{result.returncode}")


def run_due() -> None:
    """Run one non-overlapping due-job scan."""

    now = utc_now()
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with locked_file(root / "scanner.lock", blocking=False):
            _run_locked_due(now)
    except LockUnavailable:
        emit("scanner-busy")


__all__ = ["run_due", "schedule"]

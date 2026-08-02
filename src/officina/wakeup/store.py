"""Atomic, dependency-free persistence for wakeup jobs.

The queue is ``jobs.json`` under ``LLM_WAKEUP_HOME`` (defaulting to
``~/.local/share/llm-wakeup``). Writers serialize through an advisory ``flock``
and replace the JSON file atomically, so reboot persistence does not require a
daemon or database. The queue lock is intentionally distinct from the service
scanner lock: scheduling remains available while a provider delivery runs.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import WakeupError


def data_dir() -> Path:
    """Return the persistent state root, honoring ``LLM_WAKEUP_HOME``."""
    return Path(os.environ.get("LLM_WAKEUP_HOME", "~/.local/share/llm-wakeup")).expanduser()


def _read_jobs(path: Path) -> list[dict]:
    """Read and minimally validate the JSON queue; absence means no jobs."""
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WakeupError(f"could not read queue {path}: {error}") from error
    if not isinstance(value, list):
        raise WakeupError(f"invalid queue format: {path}")
    return value


def _write_jobs(path: Path, jobs: list[dict]) -> None:
    """Persist the complete queue through an atomic same-directory replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        delete=False,
        encoding="utf-8",
    ) as handle:
        json.dump(jobs, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


@contextmanager
def locked_jobs() -> Iterator[list[dict]]:
    """Yield the mutable queue while holding its short-lived write lock."""
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    queue = root / "jobs.json"
    with (root / "jobs.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        jobs = _read_jobs(queue)
        yield jobs
        _write_jobs(queue, jobs)


def append_job(job: dict) -> None:
    """Append one JSON-serializable job under the queue write lock."""
    with locked_jobs() as jobs:
        jobs.append(job)


def due_jobs(now_iso: str) -> list[dict]:
    """Return copies of jobs due at or before a UTC ISO-8601 instant."""
    with locked_jobs() as jobs:
        return [job.copy() for job in jobs if job["run_at"] <= now_iso]


def update_job(job_id: str, replacement: dict | None) -> None:
    """Replace one job by ID, or delete it when ``replacement`` is ``None``."""
    with locked_jobs() as jobs:
        updated: list[dict] = []
        for job in jobs:
            if job.get("id") != job_id:
                updated.append(job)
            elif replacement is not None:
                updated.append(replacement)
        jobs[:] = updated


__all__ = ["append_job", "data_dir", "due_jobs", "locked_jobs", "update_job"]

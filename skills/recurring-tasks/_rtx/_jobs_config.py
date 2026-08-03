"""Single validated read/write boundary for recurring-tasks configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from officina.common.configured_schema import load_configuration, validate_configuration


Job = dict[str, Any]


def load_jobs_document(path: str | Path) -> dict[str, Any]:
    """Load one jobs configuration through the central repository schema."""
    return load_configuration(Path(path))


def load_jobs(path: str | Path) -> list[Job]:
    """Return detached job mappings from a validated jobs document."""
    return [dict(job) for job in load_jobs_document(path)["jobs"]]


def write_jobs_document(path: str | Path, document: Mapping[str, Any]) -> None:
    """Validate and persist one complete jobs configuration."""
    validate_configuration(document, document_name=str(path))
    Path(path).write_text(
        yaml.safe_dump(dict(document), sort_keys=False),
        encoding="utf-8",
    )


def write_jobs(path: str | Path, jobs: Sequence[Mapping[str, Any]]) -> None:
    """Validate and persist a sequence of recurring job mappings."""
    write_jobs_document(path, {"jobs": [dict(job) for job in jobs]})


__all__ = ["Job", "load_jobs", "load_jobs_document", "write_jobs", "write_jobs_document"]

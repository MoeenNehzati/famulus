"""Scheduler backend contract for recurring-tasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class ScheduleJob:
    name: str
    description: str
    command: str
    schedule: str
    enabled: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "ScheduleJob":
        return cls(
            name=str(raw["name"]),
            description=str(raw.get("description", raw["name"])),
            command=str(raw["command"]),
            schedule=str(raw["schedule"]),
            enabled=bool(raw.get("enabled", False)),
        )


@dataclass(frozen=True)
class ScheduleContext:
    skill_dir: Path
    jobs_file: Path
    log_dir: Path
    unit_dir: Path | None = None
    live: bool = True


class ScheduleBackendUnsupported(RuntimeError):
    """Raised when the current host has no recurring-tasks scheduler backend."""


class ScheduleBackend(Protocol):
    name: str

    def sync(self, jobs: list[ScheduleJob], context: ScheduleContext) -> None:
        """Install or refresh the enabled recurring jobs for this backend."""

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        """Run one job immediately through the host scheduler."""

    def status(self, context: ScheduleContext) -> str:
        """Return scheduler status text for recurring jobs."""

    def check_manager(self) -> str | None:
        """Return a scheduler manager failure reason, or None when healthy."""

    def get_agent_command_template(self) -> str | None:
        """Return the configured agent command template, if available."""

    def check_job_active(self, job_name: str) -> bool:
        """Return whether a scheduled job is active/enabled in the host scheduler."""


def schedule_jobs_from_mappings(jobs: list[Mapping[str, object]]) -> list[ScheduleJob]:
    return [ScheduleJob.from_mapping(job) for job in jobs]

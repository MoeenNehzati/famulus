"""Scheduler backend contract for recurring-tasks."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from officina.common.famulus_paths import resolve_famulus_paths


def _default_famulus_paths():
    return resolve_famulus_paths(platform=sys.platform, home=Path.home(), environ=os.environ)


def _default_runtime_resolver() -> Path:
    """Fixed, release-independent launch-resolver path beneath runtime_root.

    Mirrors the same ``bootstrap/resolvers/v1/launch.py`` relative path the
    installer's generated dispatcher/invoke-skill launchers already invoke:
    the file deployed there is ``officina.install.resolvers.launch``'s
    source, which reads ``current.json`` and execs into the active
    managed-runtime release's interpreter. Backends invoke this stable path
    instead of embedding ``sys.executable`` -- the interpreter that happened
    to run the sync script -- so scheduled jobs keep working across runtime
    upgrades.
    """
    return _default_famulus_paths().runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"


def _default_config_root() -> Path:
    return _default_famulus_paths().recurring_config_root


def _default_state_root() -> Path:
    return _default_famulus_paths().recurring_state_root


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
    # Backend-owned scheduler context (feedback items 7, 17): the stable
    # launch resolver and config/state roots backends need to generate
    # release-independent launch configs, plus the default assistant
    # backend to select when a job doesn't override it. All default to the
    # real host's resolution so existing call sites that construct
    # ScheduleContext without these keep working unchanged.
    runtime_resolver: Path = field(default_factory=_default_runtime_resolver)
    config_root: Path = field(default_factory=_default_config_root)
    state_root: Path = field(default_factory=_default_state_root)
    assistant_default: str = "claude"
    installation_id: str = "standard"
    backend_executables: Mapping[str, Path] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    bootstrap_python: Path | None = None


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

    def registrations_present(self, context: ScheduleContext) -> bool:
        """Return whether this installation ID has native registrations."""

    def check_manager(self) -> str | None:
        """Return a scheduler manager failure reason, or None when healthy."""

    def check_job_active(
        self, job_name: str, context: ScheduleContext | None = None
    ) -> bool:
        """Return whether a scheduled job is active/enabled in the host scheduler."""

    def job_search_dirs(self) -> list[Path] | None:
        """Directories a scheduled job resolves commands from.

        ``None`` means this scheduler does not set a PATH for its jobs, so the
        job inherits the ambient one and the caller should fall back to it.
        Backends that DO pin a job PATH must derive this from the same
        expression that renders it, so the two cannot drift apart.
        """


def schedule_jobs_from_mappings(jobs: list[Mapping[str, object]]) -> list[ScheduleJob]:
    return [ScheduleJob.from_mapping(job) for job in jobs]
_INSTALLATION_ID = re.compile(r"dev-[0-9a-f]{32}\Z")


def registration_token(installation_id: str) -> str:
    if installation_id == "standard":
        return ""
    if not _INSTALLATION_ID.fullmatch(installation_id):
        raise ValueError(f"invalid scheduler installation ID: {installation_id!r}")
    return f"{installation_id}-"

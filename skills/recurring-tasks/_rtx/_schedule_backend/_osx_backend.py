"""macOS scheduler backend placeholder for recurring-tasks."""

from __future__ import annotations

from ._base_backend import ScheduleBackendUnsupported, ScheduleContext, ScheduleJob


class OSXScheduleBackend:
    name = "macos-launchd"

    def _unsupported(self) -> None:
        raise ScheduleBackendUnsupported(
            "recurring-tasks scheduling is not implemented for macOS launchd yet"
        )

    def sync(self, jobs: list[ScheduleJob], context: ScheduleContext) -> None:
        self._unsupported()

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        self._unsupported()

    def status(self, context: ScheduleContext) -> str:
        self._unsupported()

    def check_manager(self) -> str | None:
        self._unsupported()

    def get_agent_command_template(self) -> str | None:
        self._unsupported()

    def check_job_active(self, job_name: str) -> bool:
        self._unsupported()

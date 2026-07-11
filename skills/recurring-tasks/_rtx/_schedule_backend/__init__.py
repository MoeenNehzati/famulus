"""Private scheduler backend interface for recurring-tasks."""

from ._base_backend import (
    ScheduleBackend,
    ScheduleBackendUnsupported,
    ScheduleContext,
    ScheduleJob,
    schedule_jobs_from_mappings,
)
from ._linux_backend import LinuxScheduleBackend
from ._osx_backend import OSXScheduleBackend
from ._windows_backend import WindowsScheduleBackend


def platform_schedule_backend(platform: str | None = None) -> ScheduleBackend:
    """Return the scheduler backend for the current platform."""
    import sys

    selected = platform if platform is not None else sys.platform
    if selected == "darwin":
        return OSXScheduleBackend()
    if selected == "win32":
        return WindowsScheduleBackend()
    return LinuxScheduleBackend()


__all__ = [
    "LinuxScheduleBackend",
    "OSXScheduleBackend",
    "ScheduleBackend",
    "ScheduleBackendUnsupported",
    "ScheduleContext",
    "ScheduleJob",
    "WindowsScheduleBackend",
    "platform_schedule_backend",
    "schedule_jobs_from_mappings",
]

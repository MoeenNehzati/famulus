#!/usr/bin/env python3
"""Tests for the recurring-tasks private scheduler backend package."""

import sys
from pathlib import Path
from unittest import mock

import pytest

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[1] / "src"
RTX_DIR = SKILL_DIR / "_rtx"

sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

from _schedule_backend import (  # noqa: E402
    ScheduleBackendUnsupported,
    ScheduleContext,
    ScheduleJob,
    platform_schedule_backend,
)
from _schedule_backend._linux_backend import (  # noqa: E402
    LinuxScheduleBackend,
    cron_to_systemd_calendar,
)
from _schedule_backend._osx_backend import OSXScheduleBackend  # noqa: E402
from _schedule_backend._windows_backend import WindowsScheduleBackend  # noqa: E402


def test_platform_schedule_backend_selects_linux_by_default_family():
    assert isinstance(platform_schedule_backend("linux"), LinuxScheduleBackend)


def test_platform_schedule_backend_selects_osx():
    assert isinstance(platform_schedule_backend("darwin"), OSXScheduleBackend)


def test_platform_schedule_backend_selects_windows():
    assert isinstance(platform_schedule_backend("win32"), WindowsScheduleBackend)


def test_osx_backend_is_explicitly_unsupported():
    with pytest.raises(ScheduleBackendUnsupported):
        OSXScheduleBackend().status(_context())


def test_windows_backend_is_explicitly_unsupported():
    with pytest.raises(ScheduleBackendUnsupported):
        WindowsScheduleBackend().status(_context())


def test_linux_cron_conversion_stays_systemd_compatible():
    assert cron_to_systemd_calendar("*/5 * * * *") == "*-*-* *:00/5:00"
    assert cron_to_systemd_calendar("0 9 * * 1") == "Mon *-*-* 09:00:00"


def test_linux_sync_writes_units_and_enables_timer(tmp_path):
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 * * * *",
        enabled=True,
    )

    with mock.patch("_schedule_backend._linux_backend.subprocess.run") as run:
        LinuxScheduleBackend().sync([job], context)

    service = (tmp_path / "ai-my-job.service").read_text()
    timer = (tmp_path / "ai-my-job.timer").read_text()
    assert "ExecStart=/usr/bin/env python3" in service
    assert "_job_executor.py --jobs-file" in service
    assert "/bin/bash" not in service
    assert ">>" not in service
    assert "OnCalendar=*-*-* *:00:00" in timer
    assert ["systemctl", "--user", "daemon-reload"] in [call.args[0] for call in run.call_args_list]
    assert ["systemctl", "--user", "enable", "--now", "ai-my-job.timer"] in [
        call.args[0] for call in run.call_args_list
    ]


def test_linux_test_starts_expected_service():
    with mock.patch("_schedule_backend._linux_backend.subprocess.run") as run:
        run.return_value.returncode = 0
        assert LinuxScheduleBackend().test("my-job", _context()) is True

    assert run.call_args.args[0] == ["systemctl", "--user", "start", "--wait", "ai-my-job.service"]


def test_linux_status_lists_ai_timers():
    with mock.patch("_schedule_backend._linux_backend.subprocess.run") as run:
        run.return_value.stdout = "ai-my-job.timer\n"
        assert LinuxScheduleBackend().status(_context()) == "ai-my-job.timer\n"

    assert run.call_args.args[0] == ["systemctl", "--user", "list-timers", "ai-*.timer", "--no-pager"]


def _context(unit_dir: Path | None = None) -> ScheduleContext:
    return ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=SKILL_DIR / "jobs.yaml",
        log_dir=SKILL_DIR / "logs",
        unit_dir=unit_dir,
    )

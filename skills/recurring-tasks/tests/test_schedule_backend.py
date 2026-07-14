#!/usr/bin/env python3
"""Tests for the recurring-tasks private scheduler backend package."""

import sys
import plistlib
import subprocess
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[1] / "src"
RTX_DIR = SKILL_DIR / "_rtx"

sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

from _schedule_backend import (  # noqa: E402
    ScheduleContext,
    ScheduleJob,
    platform_schedule_backend,
)
from _schedule_backend._linux_backend import (  # noqa: E402
    LinuxScheduleBackend,
    cron_to_systemd_calendar,
)
from _schedule_backend._osx_backend import (  # noqa: E402
    OSXScheduleBackend,
    cron_to_launchd_intervals,
    launchd_label,
)
from _schedule_backend._windows_backend import (  # noqa: E402
    WindowsScheduleBackend,
    cron_to_schtasks_args,
    task_name,
)


def test_platform_schedule_backend_selects_linux_by_default_family():
    assert isinstance(platform_schedule_backend("linux"), LinuxScheduleBackend)


def test_platform_schedule_backend_selects_osx():
    assert isinstance(platform_schedule_backend("darwin"), OSXScheduleBackend)


def test_platform_schedule_backend_selects_windows():
    assert isinstance(platform_schedule_backend("win32"), WindowsScheduleBackend)


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

    with (
        mock.patch("_schedule_backend._linux_backend.subprocess.run") as run,
        mock.patch(
            "_schedule_backend._linux_backend.shutil.which",
            return_value="/opt/famulus/bin/invoke-skill",
        ),
    ):
        LinuxScheduleBackend().sync([job], context)

    service = (tmp_path / "ai-my-job.service").read_text()
    timer = (tmp_path / "ai-my-job.timer").read_text()
    assert f'ExecStart="{sys.executable}"' in service
    assert f'Environment="PATH=/opt/famulus/bin:{Path(sys.executable).parent}:' in service
    assert '_job_executor.py" --jobs-file' in service
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


def test_osx_cron_conversion_stays_launchd_compatible():
    assert cron_to_launchd_intervals("0 9 * * 1") == {"Hour": 9, "Minute": 0, "Weekday": 1}

    every_five = cron_to_launchd_intervals("*/5 * * * *")
    assert isinstance(every_five, list)
    assert every_five[0] == {"Hour": 0, "Minute": 0}
    assert every_five[-1] == {"Hour": 23, "Minute": 55}


def test_osx_sync_writes_plist_and_loads_launch_agent(tmp_path):
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * 1",
        enabled=True,
    )

    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        OSXScheduleBackend().sync([job], context)

    plist = plistlib.loads((tmp_path / "ai-my-job.plist").read_bytes())
    assert plist["Label"] == launchd_label("my-job")
    assert plist["ProgramArguments"][0] == sys.executable
    assert "_job_executor.py" in plist["ProgramArguments"][1]
    assert plist["StartCalendarInterval"] == {"Hour": 9, "Minute": 0, "Weekday": 1}
    calls = [call.args[0] for call in run.call_args_list]
    assert ["launchctl", "bootstrap", mock.ANY, str(tmp_path / "ai-my-job.plist")] in calls


def test_osx_sync_removes_disabled_launch_agent(tmp_path):
    old = tmp_path / "ai-old-job.plist"
    old.write_bytes(b"stale")

    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        OSXScheduleBackend().sync([], _context(unit_dir=tmp_path))

    assert not old.exists()
    assert ["launchctl", "bootout", mock.ANY, str(old)] in [call.args[0] for call in run.call_args_list]


def test_osx_test_kickstarts_expected_label():
    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        run.return_value.returncode = 0
        assert OSXScheduleBackend().test("my-job", _context()) is True

    assert run.call_args.args[0][:3] == ["launchctl", "kickstart", "-k"]
    assert run.call_args.args[0][3].endswith("/com.famulus.ai.my-job")


def test_windows_cron_conversion_stays_task_scheduler_compatible():
    assert cron_to_schtasks_args("* * * * *") == ["/SC", "MINUTE", "/MO", "1"]
    assert cron_to_schtasks_args("*/5 * * * *") == ["/SC", "MINUTE", "/MO", "5"]
    assert cron_to_schtasks_args("15 * * * *") == ["/SC", "HOURLY", "/MO", "1", "/ST", "00:15"]
    assert cron_to_schtasks_args("0 9 * * *") == ["/SC", "DAILY", "/ST", "09:00"]
    assert cron_to_schtasks_args("0 9 * * 1") == ["/SC", "WEEKLY", "/D", "MON", "/ST", "09:00"]


def test_windows_sync_creates_task_scheduler_entry():
    context = _context()
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )

    with mock.patch("_schedule_backend._windows_backend.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        WindowsScheduleBackend().sync([job], context)

    calls = [call.args[0] for call in run.call_args_list]
    create = next(args for args in calls if args[:2] == ["schtasks", "/Create"])
    assert create[create.index("/TN") + 1] == task_name("my-job")
    assert "_job_executor.py" in create[create.index("/TR") + 1]
    assert create[-4:] == ["/SC", "DAILY", "/ST", "09:00"]


def test_windows_sync_removes_stale_task_scheduler_entry():
    job = ScheduleJob(
        name="new-job",
        description="New Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )

    def fake_run(args, **kwargs):
        if args[:4] == ["schtasks", "/Query", "/FO", "CSV"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='"\\Famulus-AI-ai-old-job","N/A","Ready"\n',
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with mock.patch("_schedule_backend._windows_backend.subprocess.run", side_effect=fake_run) as run:
        WindowsScheduleBackend().sync([job], _context())

    calls = [call.args[0] for call in run.call_args_list]
    assert ["schtasks", "/Delete", "/TN", r"\Famulus-AI-ai-old-job", "/F"] in calls
    assert any(args[:2] == ["schtasks", "/Create"] for args in calls)


def test_windows_test_runs_expected_task():
    with mock.patch("_schedule_backend._windows_backend.subprocess.run") as run:
        run.return_value.returncode = 0
        assert WindowsScheduleBackend().test("my-job", _context()) is True

    assert run.call_args.args[0] == ["schtasks", "/Run", "/TN", task_name("my-job")]


def _context(unit_dir: Path | None = None) -> ScheduleContext:
    return ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=SKILL_DIR / "jobs.yaml",
        log_dir=SKILL_DIR / "logs",
        unit_dir=unit_dir,
    )

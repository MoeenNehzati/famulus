#!/usr/bin/env python3
"""Tests for the recurring-tasks private scheduler backend package."""

import json
import os
import re
import shutil
import sys
import plistlib
import subprocess
from pathlib import Path, PurePosixPath
from unittest import mock

import pytest

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[2] / "src"
RTX_DIR = SKILL_DIR

sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

if __package__ and __package__.count('.') >= 1:
    from .._schedule_backend import ScheduleContext, ScheduleJob, platform_schedule_backend
else:
    from _schedule_backend import (  # noqa: E402
    ScheduleContext,
    ScheduleJob,
    platform_schedule_backend,
)
if __package__ and __package__.count('.') >= 1:
    from .._schedule_backend._linux_backend import (
        LinuxScheduleBackend,
        cron_to_systemd_calendar,
        service_content,
    )
else:
    from _schedule_backend._linux_backend import (  # noqa: E402
    LinuxScheduleBackend,
    cron_to_systemd_calendar,
    service_content,
)
if __package__ and __package__.count('.') >= 1:
    from .._schedule_backend._osx_backend import OSXScheduleBackend, cron_to_launchd_intervals, launchd_label
else:
    from _schedule_backend._osx_backend import (  # noqa: E402
    OSXScheduleBackend,
    cron_to_launchd_intervals,
    launchd_label,
)
if __package__ and __package__.count('.') >= 1:
    from .._schedule_backend._windows_backend import (
        WindowsScheduleBackend,
        _quote_cmd_arg,
        cron_to_schtasks_args,
        default_unit_dir,
        task_run_command,
        task_name,
        wrapper_content,
        wrapper_name,
    )
else:
    from _schedule_backend._windows_backend import (  # noqa: E402
    WindowsScheduleBackend,
    _quote_cmd_arg,
    cron_to_schtasks_args,
    default_unit_dir,
    task_run_command,
    task_name,
    wrapper_content,
    wrapper_name,
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


def test_linux_stepped_hour_uses_an_explicit_start_value():
    """A bare `*/N` hour is rejected by systemd.

    It produces a unit that loads and reports active while never firing --
    the timer's NEXT is empty and the job silently stops running.
    """
    assert cron_to_systemd_calendar("0 */3 * * *") == "*-*-* 00/3:00:00"
    assert cron_to_systemd_calendar("30 */6 * * *") == "*-*-* 00/6:30:00"


# famulus-skip: category=native-backend-unavailable; reason=systemd-analyze is the only authority on OnCalendar syntax and is absent off systemd hosts; alternate=the exact-string assertions above pin the same conversions
@pytest.mark.skipif(
    shutil.which("systemd-analyze") is None,
    reason="systemd-analyze is unavailable on this host",
)
@pytest.mark.parametrize(
    "cron",
    ["0 */3 * * *", "*/5 * * * *", "0 9 * * 1", "0 7 * * *", "30 */6 * * *"],
)
def test_generated_calendars_are_accepted_by_systemd(cron):
    """Pin the output against systemd rather than our reading of its docs."""
    calendar = cron_to_systemd_calendar(cron)
    assert subprocess.run(
        ["systemd-analyze", "calendar", calendar],
        capture_output=True,
    ).returncode == 0, f"systemd rejected {calendar!r} generated from {cron!r}"


def test_linux_sync_writes_units_and_enables_timer(tmp_path):
    # Patch the install-layout lookup rather than HOME: Path.home() ignores
    # HOME on Windows, and win32's user_bin is LOCALAPPDATA-based, so a
    # home-driven fixture asserts a Linux-only layout on all three CI legs.
    expected_bin = "/opt/famulus/bin"
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 * * * *",
        enabled=True,
    )

    linux_backend = sys.modules[LinuxScheduleBackend.__module__]
    with (
        mock.patch.object(linux_backend.subprocess, "run") as run,
        # sync now inspects each enable's returncode so one bad unit cannot
        # silently leave the remaining jobs unregistered.

        mock.patch.object(
            linux_backend,
            "_launcher_bin_dir",
            return_value=PurePosixPath(expected_bin),
        ),
    ):
        run.return_value.returncode = 0
        LinuxScheduleBackend().sync([job], context)

    service = (tmp_path / "ai-my-job.service").read_text()
    timer = (tmp_path / "ai-my-job.timer").read_text()
    # This unit file always targets a systemd (Linux) host regardless of
    # which host OS generated it (the portability sentinel runs this exact
    # test on all 3 CI platforms) -- assert the POSIX-normalized form, not
    # the raw native path, and assert no backslash leaked in anywhere as a
    # direct, host-independent regression guard.
    executor_path = context.skill_dir / "_job_executor.py"
    assert f'ExecStart="{context.runtime_resolver.as_posix()}"' in service
    assert executor_path.as_posix() in service
    assert context.jobs_file.as_posix() in service
    assert "\\" not in service
    assert sys.executable not in service
    assert f'Environment="PATH={expected_bin}:' in service
    assert 'Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus"' in service
    assert '_job_executor.py" --jobs-file' in service
    assert "/bin/bash" not in service
    assert ">>" not in service
    assert "OnCalendar=*-*-* *:00:00" in timer
    assert ["systemctl", "--user", "daemon-reload"] in [call.args[0] for call in run.call_args_list]
    assert ["systemctl", "--user", "enable", "--now", "ai-my-job.timer"] in [
        call.args[0] for call in run.call_args_list
    ]


def test_linux_service_content_posix_normalizes_windows_style_input_paths():
    """The portability sentinel runs this generator's real caller
    (test_linux_sync_writes_units_and_enables_timer) on Windows CI, where
    every native Path is backslash-separated -- but this unit file always
    targets a systemd (Linux) host. Inject PureWindowsPath-shaped paths
    directly (bypassing needing a real Windows host) to prove the output
    never contains a raw host-native separator, host-independently."""
    from pathlib import PureWindowsPath

    content = service_content(
        "my-job",
        "My Job",
        PureWindowsPath(r"D:\a\famulus\famulus\skills\recurring-tasks\jobs.yaml"),
        PureWindowsPath(r"D:\a\famulus\famulus\skills\recurring-tasks\_rtx\_job_executor.py"),
        PureWindowsPath(r"C:\Users\runneradmin\AppData\Local\Famulus\runtime\bootstrap\resolvers\v1\launch.py"),
    )
    assert "\\" not in content
    assert "D:/a/famulus/famulus/skills/recurring-tasks/jobs.yaml" in content
    assert "D:/a/famulus/famulus/skills/recurring-tasks/_rtx/_job_executor.py" in content
    assert "C:/Users/runneradmin/AppData/Local/Famulus/runtime/bootstrap/resolvers/v1/launch.py" in content


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
        run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        OSXScheduleBackend().sync([job], context)

    plist = plistlib.loads((tmp_path / "ai-my-job.plist").read_bytes())
    assert plist["Label"] == launchd_label("my-job")
    assert plist["ProgramArguments"][0] == str(context.runtime_resolver)
    assert plist["ProgramArguments"][0] != sys.executable
    assert "_job_executor.py" in plist["ProgramArguments"][1]
    assert plist["StartCalendarInterval"] == {"Hour": 9, "Minute": 0, "Weekday": 1}
    calls = [call.args[0] for call in run.call_args_list]
    assert ["launchctl", "bootstrap", mock.ANY, str(tmp_path / "ai-my-job.plist")] in calls


def test_osx_sync_bootout_by_label_before_bootstrap_when_already_loaded(tmp_path):
    """Regression test for the stale-prior-location gap: when launchd already
    has the job's label loaded (from any path, possibly a stale one), sync()
    must probe by label and bootout by service-target (label) form *before*
    bootstrapping the new plist, rather than relying on a path-form bootout
    that can't reach a job loaded from a different path."""
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * 1",
        enabled=True,
    )
    backend = OSXScheduleBackend()
    target = backend._target()
    label = launchd_label("my-job")
    plist_path = tmp_path / "ai-my-job.plist"

    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        backend.sync([job], context)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["launchctl", "print", f"{target}/{label}"],
        ["launchctl", "bootout", f"{target}/{label}"],
        ["launchctl", "bootstrap", target, str(plist_path)],
    ]


def test_osx_sync_skips_bootout_when_not_already_loaded(tmp_path):
    """When the probe reports the label is not currently loaded, sync() must
    not issue a bootout at all -- just bootstrap the new plist."""
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * 1",
        enabled=True,
    )
    backend = OSXScheduleBackend()
    target = backend._target()
    label = launchd_label("my-job")
    plist_path = tmp_path / "ai-my-job.plist"

    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        backend.sync([job], context)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["launchctl", "print", f"{target}/{label}"],
        ["launchctl", "bootstrap", target, str(plist_path)],
    ]
    assert ["launchctl", "bootout", f"{target}/{label}"] not in calls


def test_osx_sync_removes_disabled_launch_agent_by_label_when_loaded(tmp_path):
    """Regression test for the same stale-path bug in the disabled-job
    cleanup loop: if a disabled job's label is still loaded (possibly from a
    stale path), sync() must bootout by service-target (label) form before
    deleting the on-disk plist, otherwise the label leaks in launchd forever
    even though the plist file is gone."""
    old = tmp_path / "ai-old-job.plist"
    old.write_bytes(b"stale")
    backend = OSXScheduleBackend()
    target = backend._target()
    label = launchd_label("old-job")

    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        backend.sync([], _context(unit_dir=tmp_path))

    assert not old.exists()
    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["launchctl", "print", f"{target}/{label}"],
        ["launchctl", "bootout", f"{target}/{label}"],
    ]


def test_osx_sync_removes_disabled_launch_agent_skips_bootout_when_not_loaded(tmp_path):
    old = tmp_path / "ai-old-job.plist"
    old.write_bytes(b"stale")
    backend = OSXScheduleBackend()
    target = backend._target()
    label = launchd_label("old-job")

    with mock.patch("_schedule_backend._osx_backend.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        backend.sync([], _context(unit_dir=tmp_path))

    assert not old.exists()
    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [["launchctl", "print", f"{target}/{label}"]]
    assert ["launchctl", "bootout", f"{target}/{label}"] not in calls


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


def test_windows_sync_creates_task_scheduler_entry(tmp_path):
    context = _context(unit_dir=tmp_path)
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
    tr_value = create[create.index("/TR") + 1]
    # The /TR value must invoke the short wrapper through cmd.exe, not embed
    # the full executor command -- schtasks /Create /TR has a hard
    # 261-character limit
    # on this value ("ERROR: Value for '/TR' option cannot be more than 261
    # character(s)"), and the full interpreter+resolver+executor+args
    # command line routinely exceeds that under a real install path.
    assert "_job_executor.py" not in tr_value
    assert tr_value == task_run_command(tmp_path / wrapper_name("my-job"))
    assert create[-4:] == ["/SC", "DAILY", "/ST", "09:00"]

    wrapper_text = (tmp_path / wrapper_name("my-job")).read_text(encoding="utf-8")
    assert "_job_executor.py" in wrapper_text
    assert "_rtx/_rtx" not in wrapper_text.replace("\\", "/")
    assert "--jobs-file" in wrapper_text
    assert f'--log-dir" "{context.log_dir}' in wrapper_text
    assert "--job" in wrapper_text and '"my-job"' in wrapper_text


def test_windows_wrapper_content_uses_explicit_crlf_only(tmp_path):
    """``wrapper_content`` must build its string with explicit ``\\r\\n``
    line endings throughout -- never a bare ``\\n`` -- so the on-disk write
    can safely disable newline translation entirely (see the write-time
    test below) without leaving any line ending un-terminated."""
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )

    content = wrapper_content(job, context)

    assert "\r\n" in content
    # No bare '\n' that isn't immediately preceded by '\r'.
    assert re.search(r"(?<!\r)\n", content) is None
    assert f'if not exist "{context.log_dir / job.name}" mkdir ' in content
    assert f'>> "{context.log_dir / job.name / "scheduler.log"}" 2>&1' in content
    assert content.endswith("exit /b %errorlevel%\r\n")


def test_windows_task_run_command_uses_cmd_for_space_containing_wrapper_path():
    wrapper_path = Path(r"C:\Users\Jane Doe\AppData\Local\Famulus\wrapper.cmd")

    command = task_run_command(
        wrapper_path, comspec=r"C:\Windows\System32\cmd.exe"
    )

    assert command == subprocess.list2cmdline(
        [
            r"C:\Windows\System32\cmd.exe",
            "/D",
            "/C",
            "CALL",
            str(wrapper_path),
        ]
    )


def test_windows_sync_writes_wrapper_without_newline_translation(tmp_path):
    """``wrapper_content`` already bakes explicit ``\\r\\n`` line endings into
    its returned string. ``Path.write_text(..., encoding="utf-8")`` with no
    ``newline=""`` performs Python's default text-mode newline translation,
    which rewrites every ``\\n`` in the string to ``os.linesep`` at write
    time -- so on a real Windows host (``os.linesep == "\\r\\n"``) each
    already-explicit ``\\r\\n`` becomes ``\\r\\r\\n`` on disk. This translation
    is OS-dependent and can't be reproduced by inspecting bytes on a Linux
    test host (``os.linesep`` is ``"\\n"`` here, so no doubling would occur
    regardless of the fix) -- so assert the fix at the call-site level: the
    wrapper file must be written with ``newline=""`` so Python performs no
    newline translation at all, since the string already contains the exact
    literal bytes intended for disk."""
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )

    with mock.patch("_schedule_backend._windows_backend.subprocess.run") as run, \
            mock.patch.object(Path, "write_text", autospec=True) as write_text:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        WindowsScheduleBackend().sync([job], context)

    calls_for_wrapper = [
        call for call in write_text.call_args_list
        if call.args[0] == (tmp_path / wrapper_name("my-job"))
    ]
    assert len(calls_for_wrapper) == 1
    call = calls_for_wrapper[0]
    assert call.kwargs.get("newline") == ""
    assert call.kwargs.get("encoding") == "utf-8"


def test_quote_cmd_arg_doubles_literal_percent():
    """``%`` is cmd.exe's environment-variable/batch-parameter expansion
    marker (``%VAR%``, ``%1``, ``%*``), expanded in a separate parsing pass
    that ordinary double-quoting does not suppress. Inside a batch (.cmd)
    file, the documented escape for a literal ``%`` is doubling: ``%%``.
    Assert ``_quote_cmd_arg`` doubles embedded ``%`` characters, in addition
    to its existing double-quote doubling."""
    assert _quote_cmd_arg("100%") == '"100%%"'
    assert _quote_cmd_arg("%LOCALAPPDATA%") == '"%%LOCALAPPDATA%%"'
    assert _quote_cmd_arg('50%"quoted"') == '"50%%""quoted"""'


def test_windows_wrapper_content_neutralizes_percent_in_job_name(tmp_path):
    """A free-text job name containing ``%`` (nothing in jobs.yaml currently
    restricts job-name characters) must appear in the generated wrapper
    ``.cmd`` as a literal, batch-escaped ``%%`` -- not a bare ``%`` that
    cmd.exe would attempt to expand as an environment variable or batch
    parameter reference when the wrapper is invoked by Task Scheduler."""
    context = _context(unit_dir=tmp_path)
    job = ScheduleJob(
        name="release-100%-done",
        description="Release job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )

    content = wrapper_content(job, context)

    assert '"release-100%%-done"' in content
    # No lone, un-doubled '%' anywhere in the job-name argument's rendering.
    assert '"release-100%-done"' not in content


def test_windows_sync_removes_stale_task_scheduler_entry(tmp_path):
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

    stale_wrapper = tmp_path / wrapper_name("old-job")
    stale_wrapper.write_text("stale", encoding="utf-8")

    with mock.patch("_schedule_backend._windows_backend.subprocess.run", side_effect=fake_run) as run:
        WindowsScheduleBackend().sync([job], _context(unit_dir=tmp_path))

    calls = [call.args[0] for call in run.call_args_list]
    assert ["schtasks", "/Delete", "/TN", r"\Famulus-AI-ai-old-job", "/F"] in calls
    assert any(args[:2] == ["schtasks", "/Create"] for args in calls)
    assert not stale_wrapper.exists()


def test_windows_default_unit_dir_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    unit_dir = default_unit_dir()
    assert unit_dir == tmp_path / "AppData" / "Local" / "Famulus" / "state" / "recurring-tasks" / "task-wrappers"


def test_windows_default_unit_dir_falls_back_without_local_app_data(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    unit_dir = default_unit_dir()
    assert unit_dir == Path.home() / "AppData" / "Local" / "Famulus" / "state" / "recurring-tasks" / "task-wrappers"


def test_windows_wrapper_tr_value_stays_well_under_261_char_limit(tmp_path):
    """Reproduce the real CI failure's measured overage: a realistic long
    Windows temp-dir-based install path (interpreter + resolver + executor +
    --jobs-file + --job all chained inline) pushed the OLD /TR command to
    352 characters, well past schtasks' documented 261-character /TR limit
    (observed as schtasks /Create exit 2147500037 / 0x80004005). The fix
    writes that full command into a wrapper .cmd file under a short,
    fixed wrapper directory instead -- independent of how deep the
    runtime/resolver/jobs-file paths are -- and points /TR at a short
    ``cmd.exe`` invocation of the wrapper. Assert that NEW /TR value stays
    well under the limit, with safety margin for real usernames/paths longer than CI's
    ``RUNNER~1`` short name. The wrapper directory itself
    (``unit_dir``) is deliberately kept short here, mirroring
    ``default_unit_dir()``'s fixed, LOCALAPPDATA-rooted location on a
    real Windows host -- it does not need to nest under the (long) release
    path the way the interpreter/resolver/executor/jobs-file paths do."""
    unit_dir = tmp_path / "task-wrappers"

    long_root = (
        tmp_path
        / "Users"
        / "RUNNER~1"
        / "AppData"
        / "Local"
        / "Temp"
        / "recurring-tasks-smoke-abcdef01"
        / "runtime"
        / "releases"
        / "test-release-2026-08-01T00-00-00Z"
        / "venv"
        / "Scripts"
    )
    context = ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=long_root / "config" / "recurring-tasks" / "jobs.yaml",
        log_dir=long_root / "logs",
        unit_dir=unit_dir,
        live=False,
        runtime_resolver=long_root / "bootstrap" / "resolvers" / "v1" / "launch.py",
    )
    job = ScheduleJob(
        name="codex-ci-smoke-1234567890",
        description="recurring-tasks live scheduler smoke",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )

    with mock.patch("_schedule_backend._windows_backend.shutil.which") as which:
        which.side_effect = (
            lambda name: str(long_root / "python.exe") if name == "python" else None
        )
        WindowsScheduleBackend().sync([job], context)

    wrapper_path = unit_dir / wrapper_name(job.name)
    tr_value = task_run_command(wrapper_path)
    assert len(tr_value) <= 261, f"/TR value too long ({len(tr_value)} chars): {tr_value!r}"

    # The measured, previously-failing inline command for comparison: the
    # same interpreter/resolver/executor/args chained on one /TR line.
    old_style_tr_value = subprocess.list2cmdline(
        [
            str(long_root / "python.exe"),
            str(context.runtime_resolver),
            str(SKILL_DIR / "_job_executor.py"),
            "--jobs-file",
            str(context.jobs_file),
            "--job",
            job.name,
        ]
    )
    assert len(old_style_tr_value) > 261, (
        "test setup should reproduce the real overage that motivated this fix "
        f"(got {len(old_style_tr_value)} chars)"
    )

    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert str(long_root / "python.exe") in wrapper_text
    assert str(context.runtime_resolver) in wrapper_text
    assert "_job_executor.py" in wrapper_text
    assert "--jobs-file" in wrapper_text
    assert str(context.jobs_file) in wrapper_text
    assert '"codex-ci-smoke-1234567890"' in wrapper_text


def test_windows_test_runs_expected_task():
    with mock.patch("_schedule_backend._windows_backend.subprocess.run") as run:
        run.return_value.returncode = 0
        assert WindowsScheduleBackend().test("my-job", _context()) is True

    assert run.call_args.args[0] == ["schtasks", "/Run", "/TN", task_name("my-job")]


def test_schedule_context_carries_stable_resolver_path(tmp_path):
    ctx = ScheduleContext(
        skill_dir=tmp_path,
        jobs_file=tmp_path / "jobs.yaml",
        log_dir=tmp_path / "logs",
        unit_dir=tmp_path / "units",
        live=False,
        runtime_resolver=tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py",
        config_root=tmp_path / "config",
        state_root=tmp_path / "state",
        assistant_default="codex",
    )
    assert ctx.runtime_resolver.name == "launch.py"
    assert ctx.config_root == tmp_path / "config"
    assert ctx.state_root == tmp_path / "state"
    assert ctx.assistant_default == "codex"


def test_schedule_context_defaults_new_fields_for_untouched_call_sites():
    """Existing call sites that construct ScheduleContext without the new
    fields (job_control.py, unit_writer.py, setup_runner.py) must keep
    working -- the new fields resolve sensible host defaults instead of
    becoming required constructor arguments."""
    ctx = ScheduleContext(skill_dir=SKILL_DIR, jobs_file=SKILL_DIR / "jobs.yaml", log_dir=SKILL_DIR / "logs")
    assert ctx.runtime_resolver.name == "launch.py"
    assert isinstance(ctx.config_root, Path)
    assert isinstance(ctx.state_root, Path)
    assert ctx.assistant_default == "claude"


def test_osx_plist_content_uses_stable_resolver_not_sys_executable(tmp_path):
    from _schedule_backend._osx_backend import plist_content

    resolver = tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py"
    raw = plist_content(
        job_name="my-job",
        description="My Job",
        jobs_file=tmp_path / "jobs.yaml",
        log_file=tmp_path / "run.log",
        executor=tmp_path / "_job_executor.py",
        runtime_resolver=resolver,
        schedule="0 9 * * 1",
    )
    plist = plistlib.loads(raw)
    assert plist["ProgramArguments"][0] == str(resolver)
    assert sys.executable not in plist["ProgramArguments"]


def test_windows_executor_command_uses_python_interpreter_with_stable_resolver(tmp_path):
    from _schedule_backend._windows_backend import executor_command

    context = _context()
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )
    with mock.patch("_schedule_backend._windows_backend.shutil.which") as which:
        which.side_effect = lambda name: r"C:\Python312\python.exe" if name == "python" else None
        command = executor_command(job, context)
    assert command.split()[0] == r"C:\Python312\python.exe"
    assert str(context.runtime_resolver) in command
    assert sys.executable not in command
    assert '"python"' not in command
    assert command.split()[0] != "python"


def test_windows_executor_command_falls_back_to_py_launcher(tmp_path):
    """When ``python`` isn't on PATH but the ``py`` launcher is, that
    resolved absolute path is used instead of the bare unqualified name."""
    from _schedule_backend._windows_backend import executor_command

    context = _context()
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )
    with mock.patch("_schedule_backend._windows_backend.shutil.which") as which:
        which.side_effect = lambda name: r"C:\Windows\py.exe" if name == "py" else None
        command = executor_command(job, context)
    assert command.split()[0] == r"C:\Windows\py.exe"


def test_windows_executor_command_raises_clear_error_when_no_interpreter_found(tmp_path):
    """schtasks /TR rejects a bare, unqualified 'python' string at task-
    creation time; if neither 'python' nor 'py' resolves on PATH, fail
    loudly instead of silently constructing a broken command."""
    from _schedule_backend._windows_backend import WindowsPythonNotFoundError, executor_command

    context = _context()
    job = ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 9 * * *",
        enabled=True,
    )
    with mock.patch("_schedule_backend._windows_backend.shutil.which", return_value=None):
        with pytest.raises(WindowsPythonNotFoundError):
            executor_command(job, context)


def test_deploy_test_resolver_writes_real_executable_resolver_copy(tmp_path):
    """The live-smoke tests' resolver-deployment helper must deploy a real,
    executable copy of the actual resolver source (byte-for-byte) into the
    test's own isolated tmp_dir, at the exact relative path the resolver's
    own runtime_root derivation requires -- not the real, system-wide
    ``_default_runtime_resolver()`` path. This is the one piece of Bug 1's
    fix that IS verifiable on this Linux sandbox; the full launchd/schtasks
    exec integration can only be proven by a real CI run."""
    live_smoke = _import_live_smoke_module()

    resolver_path = live_smoke._deploy_test_resolver(tmp_path)

    assert resolver_path == tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py"
    assert resolver_path.exists()
    assert resolver_path.read_bytes() == live_smoke._RESOLVER_SOURCE.read_bytes()
    assert os.access(resolver_path, os.X_OK)

    # Never touches the real, system-wide default resolver location.
    from _schedule_backend._base_backend import _default_runtime_resolver

    assert resolver_path != _default_runtime_resolver()


def test_deploy_test_resolver_produces_a_resolvable_current_json(tmp_path):
    """The deployed fake current.json + trusted-roots.json must let the
    real resolver's own containment check accept the fake python_bin -- if
    this fails, the resolver would refuse to exec even once the file-not-
    found problem (Bug 1) is fixed."""
    live_smoke = _import_live_smoke_module()

    resolver_path = live_smoke._deploy_test_resolver(tmp_path)
    runtime_root = resolver_path.parents[3]

    current = json.loads((runtime_root / "current.json").read_text())
    assert current["schema_version"] == 1
    python_bin = Path(current["python_bin"])
    assert python_bin.is_symlink()
    assert python_bin.resolve() == Path(sys.executable).resolve()

    trusted_roots = json.loads((resolver_path.parent / "trusted-roots.json").read_text())
    assert str(Path(sys.executable).resolve().parent) in trusted_roots

    # Exercise the REAL resolver's own containment check against the fake
    # current.json this helper wrote, proving the deployed data will
    # actually let officina.install.resolvers.launch.main() resolve a
    # python_bin instead of refusing it.
    from officina.install.resolvers.launch import _load_current_pointer

    resolved = _load_current_pointer(
        runtime_root, trusted_roots=tuple(Path(entry) for entry in trusted_roots)
    )
    # The resolver returns the validated *entry* path (python_bin itself,
    # still a symlink), not its resolved target -- resolving it would bypass
    # the venv's own pyvenv.cfg/site-packages. See
    # officina.install.resolvers.launch._require_contained_or_trusted.
    assert resolved == python_bin


def _import_live_smoke_module():
    import importlib.util

    module_path = Path(__file__).parent / "test_scheduler_live_smoke.py"
    spec = importlib.util.spec_from_file_location("_test_scheduler_live_smoke", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(unit_dir: Path | None = None) -> ScheduleContext:
    return ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=SKILL_DIR / "jobs.yaml",
        log_dir=SKILL_DIR / "logs",
        unit_dir=unit_dir,
    )


def test_non_systemd_backends_do_not_pin_a_job_search_path():
    """launchd and schtasks set no PATH for their jobs, so None means "ambient".

    check_environment is platform-neutral; returning a systemd-shaped list
    here would make macOS/Windows report a false "command not found" for a
    launcher those schedulers resolve perfectly well.
    """
    assert OSXScheduleBackend().job_search_dirs() is None
    assert WindowsScheduleBackend().job_search_dirs() is None


def test_linux_backend_pins_the_units_own_search_path():
    dirs = LinuxScheduleBackend().job_search_dirs()
    assert dirs is not None and len(dirs) > 0

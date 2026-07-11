#!/usr/bin/env python3
"""Opt-in live scheduler smoke tests for recurring-tasks backends."""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[1] / "src"
RTX_DIR = SKILL_DIR / "_rtx"

sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

from _schedule_backend._linux_backend import (  # noqa: E402
    PREFIX as SYSTEMD_PREFIX,
    service_content,
    timer_content,
)
from _schedule_backend._osx_backend import (  # noqa: E402
    OSXScheduleBackend,
    launchd_label,
    plist_content,
    plist_name,
)
from _schedule_backend._windows_backend import (  # noqa: E402
    cron_to_schtasks_args,
    executor_command,
    task_name,
)


# famulus-skip: category=live-smoke-opt-in; reason=live scheduler smoke mutates host scheduler state; alternate=scheduler backend unit tests run in the normal suite
pytestmark = pytest.mark.skipif(
    os.environ.get("FAMULUS_RUN_SCHEDULER_SMOKE") != "1",
    reason="live scheduler smoke is opt-in; set FAMULUS_RUN_SCHEDULER_SMOKE=1",
)


def test_live_scheduler_fires_and_cleans_up():
    system = platform.system()
    if system == "Linux":
        _linux_smoke()
    elif system == "Darwin":
        _macos_smoke()
    elif system == "Windows":
        _windows_smoke()
    else:
        # famulus-skip: category=unsupported-platform; reason=no scheduler backend exists for this OS; alternate=Linux macOS and Windows backend tests cover supported systems
        pytest.skip(f"no recurring-tasks live scheduler smoke for {system}")


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _write_marker_script(tmp_dir: Path) -> Path:
    script = tmp_dir / "write_marker.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys, time",
                "from pathlib import Path",
                "Path(sys.argv[1]).write_text(json.dumps({'ran_at': time.time()}) + '\\n', encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def _next_minute_cron() -> str:
    target = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
    return f"{target.minute} {target.hour} * * *"


def _jobs_file(tmp_dir: Path, job_name: str, command: str, schedule: str) -> Path:
    path = tmp_dir / "jobs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "jobs": [
                    {
                        "name": job_name,
                        "description": "recurring-tasks live scheduler smoke",
                        "command": command,
                        "schedule": schedule,
                        "enabled": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _command_for_marker(script: Path, marker: Path) -> str:
    if platform.system() == "Windows":
        return subprocess.list2cmdline([sys.executable, str(script), str(marker)])
    return shlex.join([sys.executable, str(script), str(marker)])


def _wait_for_marker(marker: Path, timeout: int = 120) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip()
        time.sleep(1)
    raise AssertionError(f"scheduler marker was not written: {marker}")


def _missing_marker_message(marker: Path, log_file: Path | None = None) -> str:
    detail = f"scheduler marker was not written: {marker}"
    if log_file and log_file.exists():
        detail += f"\n--- scheduler log ---\n{log_file.read_text(encoding='utf-8', errors='replace')}"
    return detail


def _assert_marker_written(marker: Path, *, log_file: Path | None = None) -> None:
    try:
        assert json.loads(_wait_for_marker(marker))["ran_at"]
    except AssertionError as exc:
        raise AssertionError(_missing_marker_message(marker, log_file)) from exc


def _linux_smoke() -> None:
    manager = _run(["systemctl", "--user", "is-system-running"], check=False)
    if manager.returncode != 0:
        # famulus-skip: category=native-backend-unavailable; reason=systemd user manager is not available on this host; alternate=systemd unit generation tests cover backend output
        pytest.skip(f"systemd user manager unavailable: {manager.stderr.strip() or manager.stdout.strip()}")

    with tempfile.TemporaryDirectory(prefix="recurring-tasks-smoke-") as raw_tmp:
        tmp_dir = Path(raw_tmp)
        job_name = f"codex-ci-smoke-{int(time.time())}"
        marker = tmp_dir / "marker.json"
        command = _command_for_marker(_write_marker_script(tmp_dir), marker)
        jobs_file = _jobs_file(tmp_dir, job_name, command, _next_minute_cron())
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        service_name = f"{SYSTEMD_PREFIX}{job_name}.service"
        timer_name = f"{SYSTEMD_PREFIX}{job_name}.timer"
        service_path = unit_dir / service_name
        timer_path = unit_dir / timer_name

        try:
            unit_dir.mkdir(parents=True, exist_ok=True)
            service_path.write_text(
                service_content(
                    job_name,
                    "recurring-tasks live scheduler smoke",
                    jobs_file,
                    RTX_DIR / "_job_executor.py",
                ),
                encoding="utf-8",
            )
            timer_path.write_text(
                timer_content("recurring-tasks live scheduler smoke", _systemd_calendar(jobs_file), service_name),
                encoding="utf-8",
            )
            _run(["systemctl", "--user", "daemon-reload"])
            _run(["systemctl", "--user", "enable", "--now", timer_name])
            _assert_marker_written(marker, log_file=tmp_dir / "run.log")
        finally:
            _run(["systemctl", "--user", "disable", "--now", timer_name], check=False)
            service_path.unlink(missing_ok=True)
            timer_path.unlink(missing_ok=True)
            _run(["systemctl", "--user", "daemon-reload"], check=False)
            assert not service_path.exists()
            assert not timer_path.exists()


def _systemd_calendar(jobs_file: Path) -> str:
    from _schedule_backend._linux_backend import cron_to_systemd_calendar

    with jobs_file.open(encoding="utf-8") as f:
        schedule = yaml.safe_load(f)["jobs"][0]["schedule"]
    return cron_to_systemd_calendar(schedule)


def _macos_smoke() -> None:
    backend = OSXScheduleBackend()
    manager = _run(["launchctl", "print", backend._target()], check=False)
    if manager.returncode != 0:
        # famulus-skip: category=native-backend-unavailable; reason=launchd user manager is not available on this host; alternate=launchd plist generation tests cover backend output
        pytest.skip(f"launchd user manager unavailable: {manager.stderr.strip() or manager.stdout.strip()}")

    with tempfile.TemporaryDirectory(prefix="recurring-tasks-smoke-") as raw_tmp:
        tmp_dir = Path(raw_tmp)
        job_name = f"codex-ci-smoke-{int(time.time())}"
        marker = tmp_dir / "marker.json"
        command = _command_for_marker(_write_marker_script(tmp_dir), marker)
        schedule = _next_minute_cron()
        jobs_file = _jobs_file(tmp_dir, job_name, command, schedule)
        plist_path = tmp_dir / plist_name(job_name)
        log_file = tmp_dir / "run.log"

        try:
            plist_path.write_bytes(
                plist_content(
                    job_name=job_name,
                    description="recurring-tasks live scheduler smoke",
                    jobs_file=jobs_file,
                    log_file=log_file,
                    executor=RTX_DIR / "_job_executor.py",
                    schedule=schedule,
                )
            )
            _run(["launchctl", "bootstrap", backend._target(), str(plist_path)])
            _run(["launchctl", "kickstart", "-k", f"{backend._target()}/{launchd_label(job_name)}"])
            _assert_marker_written(marker, log_file=log_file)
        finally:
            _run(["launchctl", "bootout", backend._target(), str(plist_path)], check=False)
            plist_path.unlink(missing_ok=True)
            assert not plist_path.exists()


def _windows_smoke() -> None:
    available = _run(["schtasks", "/Query", "/FO", "LIST"], check=False)
    if available.returncode != 0:
        # famulus-skip: category=native-backend-unavailable; reason=Task Scheduler is not available on this host; alternate=Task Scheduler command generation tests cover backend output
        pytest.skip(f"Task Scheduler unavailable: {available.stderr.strip() or available.stdout.strip()}")

    with tempfile.TemporaryDirectory(prefix="recurring-tasks-smoke-") as raw_tmp:
        tmp_dir = Path(raw_tmp)
        job_name = f"codex-ci-smoke-{int(time.time())}"
        marker = tmp_dir / "marker.json"
        schedule = _next_minute_cron()
        command = _command_for_marker(_write_marker_script(tmp_dir), marker)
        jobs_file = _jobs_file(tmp_dir, job_name, command, schedule)
        from _schedule_backend import ScheduleContext, ScheduleJob

        job = ScheduleJob(
            name=job_name,
            description="recurring-tasks live scheduler smoke",
            command=command,
            schedule=schedule,
            enabled=True,
        )
        context = ScheduleContext(skill_dir=SKILL_DIR, jobs_file=jobs_file, log_dir=tmp_dir)
        name = task_name(job_name)
        try:
            _run(
                [
                    "schtasks",
                    "/Create",
                    "/TN",
                    name,
                    "/TR",
                    executor_command(job, context),
                    "/F",
                    *cron_to_schtasks_args(schedule),
                ]
            )
            _run(["schtasks", "/Run", "/TN", name])
            _assert_marker_written(marker, log_file=tmp_dir / job_name / "run.log")
        finally:
            _run(["schtasks", "/Delete", "/TN", name, "/F"], check=False)
            post = _run(["schtasks", "/Query", "/TN", name], check=False)
            assert post.returncode != 0

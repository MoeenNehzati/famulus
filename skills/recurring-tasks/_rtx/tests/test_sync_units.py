#!/usr/bin/env python3
"""Tests for sync_units.py: unit file generation and cron->systemd conversion."""
import tempfile, os
import sys
from pathlib import Path, PurePosixPath

import pytest

from .. import _unit_writer as unit_writer

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT    = SKILL_DIR / "_unit_writer.py"


def _load():
    return unit_writer


def test_unit_writer_cli_rejects_non_live_unit_directory(tmp_path):
    with pytest.raises(SystemExit):
        unit_writer.main(["--unit-dir", str(tmp_path / "units")])


def test_skill_dir_is_absolute_when_entrypoint_uses_a_logical_path(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_SRC))
    monkeypatch.chdir(SKILL_DIR)

    namespace = vars(unit_writer)

    assert namespace["SKILL_DIR"] == SKILL_DIR.resolve()


# ── cron_to_systemd_calendar ──────────────────────────────────────────────────

def test_hourly():
    mod = _load()
    assert mod.cron_to_systemd_calendar("0 * * * *") == "*-*-* *:00:00"
    print("PASS: hourly at :00")

def test_daily_at_8():
    mod = _load()
    assert mod.cron_to_systemd_calendar("0 8 * * *") == "*-*-* 08:00:00"
    print("PASS: daily at 08:00")

def test_every_5_minutes():
    mod = _load()
    assert mod.cron_to_systemd_calendar("*/5 * * * *") == "*-*-* *:00/5:00"
    print("PASS: every 5 minutes")

def test_weekly_monday():
    mod = _load()
    assert mod.cron_to_systemd_calendar("0 9 * * 1") == "Mon *-*-* 09:00:00"
    print("PASS: weekly Monday 09:00")

def test_unsupported_dom_raises():
    mod = _load()
    try:
        mod.cron_to_systemd_calendar("0 8 1 * *")
        assert False, "Should have raised"
    except ValueError:
        print("PASS: dom != * raises ValueError")

def test_unsupported_month_raises():
    mod = _load()
    try:
        mod.cron_to_systemd_calendar("0 8 * 6 *")
        assert False, "Should have raised"
    except ValueError:
        print("PASS: month != * raises ValueError")


# ── sync_units file generation ────────────────────────────────────────────────

JOBS_ONE_ENABLED = """\
jobs:
  - name: test-job
    description: "Test job"
    command: "/usr/bin/echo hello"
    schedule: "0 * * * *"
    enabled: true
"""

JOBS_ONE_DISABLED = """\
jobs:
  - name: test-job
    description: "Test job"
    command: "/usr/bin/echo hello"
    schedule: "0 * * * *"
    enabled: false
"""

JOBS_TWO_ENABLED = """\
jobs:
  - name: job-a
    description: "Job A"
    command: "/usr/bin/echo a"
    schedule: "0 * * * *"
    enabled: true
  - name: job-b
    description: "Job B"
    command: "/usr/bin/echo b"
    schedule: "0 8 * * *"
    enabled: true
"""


def _run_sync(jobs_yaml: str, unit_dir: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(jobs_yaml)
        jobs_path = f.name
    try:
        mod = _load()
        if __package__ and __package__.count('.') >= 1:
            from .._schedule_backend._linux_backend import LinuxScheduleBackend
        else:
            from _schedule_backend._linux_backend import LinuxScheduleBackend

        with open(jobs_path, encoding="utf-8") as jobs_stream:
            import yaml

            jobs = (yaml.safe_load(jobs_stream) or {}).get("jobs", [])
        mod.sync_units(
            jobs,
            Path(unit_dir),
            mod.LOG_DIR,
            live=False,
            jobs_file=Path(jobs_path),
            backend=LinuxScheduleBackend(),
        )
    finally:
        os.unlink(jobs_path)


def test_writes_service_and_timer_for_enabled_job():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        files = [f.name for f in Path(d).iterdir() if f.is_file()]
        assert "ai-test-job.service" in files, f"Missing service, got: {files}"
        assert "ai-test-job.timer"   in files, f"Missing timer, got: {files}"
        print("PASS: writes service and timer for enabled job")


def test_timer_has_persistent_true():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        content = (Path(d) / "ai-test-job.timer").read_text()
        assert "Persistent=true" in content
        print("PASS: timer has Persistent=true")


def test_timer_has_correct_oncalendar():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        content = (Path(d) / "ai-test-job.timer").read_text()
        assert "OnCalendar=*-*-* *:00:00" in content
        print("PASS: timer has correct OnCalendar")


def test_disabled_job_produces_no_unit_files():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_DISABLED, d)
        files = [f for f in Path(d).glob("ai-*.service")] + [f for f in Path(d).glob("ai-*.timer")]
        assert len(files) == 0, f"Expected no units, got: {[f.name for f in files]}"
        print("PASS: disabled job produces no unit files")


def test_two_enabled_jobs_each_get_units():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_TWO_ENABLED, d)
        timers = {f.stem for f in Path(d).glob("ai-*.timer")}
        assert "ai-job-a" in timers
        assert "ai-job-b" in timers
        print("PASS: two enabled jobs each get unit files")


def test_no_per_job_runner_scripts_written():
    """No per-job runner shell script is generated."""
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        runners = list(Path(d).rglob("*.sh"))
        assert runners == [], f"Expected no runner scripts, got: {runners}"
        print("PASS: no per-job runner scripts written")


def test_service_runs_python_executor_without_shell(monkeypatch):
    # Patch the install-layout lookup, not HOME: Path.home() ignores HOME on
    # Windows and win32's user_bin is LOCALAPPDATA-based, so a home-driven
    # fixture would assert a Linux-only layout on all three CI legs.
    expected_bin = "/opt/famulus/bin"
    _load()  # ensures the skill dir is importable before reaching the backend
    if __package__ and __package__.count(".") >= 1:
        from .._schedule_backend import _linux_backend
    else:
        from _schedule_backend import _linux_backend
    monkeypatch.setattr(
        _linux_backend, "_launcher_bin_dir", lambda: PurePosixPath(expected_bin)
    )
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        content = (Path(d) / "ai-test-job.service").read_text()
        # ExecStart must invoke the stable, release-independent launch
        # resolver -- not sys.executable, which would pin the job to
        # whatever interpreter happened to run the sync script.
        assert sys.executable not in content
        assert 'ExecStart="' in content
        assert "bootstrap/resolvers/v1/launch.py" in content
        assert f'Environment="PATH={expected_bin}:' in content
        assert 'Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus"' in content
        assert ' -m officina.recurring.executor ' in content
        assert '--descriptor ' in content
        assert '--job test-job' in content
        assert '--log-root ' in content
        assert "_job_executor.py" not in content
        assert "/bin/bash" not in content
        assert ">>" not in content
        print("PASS: service ExecStart uses the stable launch resolver without shell redirection")


def test_orphaned_units_removed_when_job_disabled():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        assert (Path(d) / "ai-test-job.timer").exists()
        _run_sync(JOBS_ONE_DISABLED, d)
        assert not (Path(d) / "ai-test-job.timer").exists()
        assert not (Path(d) / "ai-test-job.service").exists()
        assert not (Path(d) / "runners" / "test-job.sh").exists()
        print("PASS: orphaned units removed when job disabled")


def test_idempotent():
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        c1 = (Path(d) / "ai-test-job.timer").read_text()
        _run_sync(JOBS_ONE_ENABLED, d)
        c2 = (Path(d) / "ai-test-job.timer").read_text()
        assert c1 == c2
        print("PASS: idempotent")


if __name__ == "__main__":
    test_hourly()
    test_daily_at_8()
    test_every_5_minutes()
    test_weekly_monday()
    test_unsupported_dom_raises()
    test_unsupported_month_raises()
    test_writes_service_and_timer_for_enabled_job()
    test_timer_has_persistent_true()
    test_timer_has_correct_oncalendar()
    test_disabled_job_produces_no_unit_files()
    test_two_enabled_jobs_each_get_units()
    test_no_per_job_runner_scripts_written()
    test_service_runs_python_executor_without_shell()
    test_orphaned_units_removed_when_job_disabled()
    test_idempotent()
    print("\nAll tests passed.")


# ── sync repairs the independent health-check cron entry ───────────────────────
#
# The cron entry was installed by setup alone. Once it went stale nothing
# restored it, and a stale entry means the watchdog is silently dead -- there is
# no second watchdog to notice. Sync renders it too, so it self-repairs.

def test_live_sync_repairs_the_healthcheck_cron_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(unit_writer.sys, "platform", "linux")
    # The suite itself runs from a temp mirror under the repository checks, and
    # the repair refuses to run from one. Pin a real-looking tree so this case
    # exercises the repair rather than the guard.
    monkeypatch.setattr(
        unit_writer, "SKILL_DIR", Path.home() / "not-a-temp-checkout" / "_rtx"
    )
    calls = []

    def _fake_install(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        unit_writer, "platform_schedule_backend", lambda: _NullBackend()
    )
    _install_fake_cron_installer(monkeypatch, _fake_install)
    _install_test_schedule_context(monkeypatch, tmp_path / "units")

    unit_writer.sync_units(
        [], tmp_path / "units", unit_writer.LOG_DIR, live=True,
    )

    assert len(calls) == 1, "live sync must render the health-check cron entry"
    assert calls[0]["module"] == "officina.recurring.healthcheck"
    assert calls[0]["descriptor"].name == "schedule-descriptor.json"


def test_sync_survives_a_host_with_no_cron(monkeypatch, tmp_path, capsys):
    """A missing cron command must not fail an otherwise successful sync.

    `crontab -l` raising FileNotFoundError previously propagated, so on a
    cron-less Linux host this would have taken the scheduler sync down with it.
    """
    monkeypatch.setattr(unit_writer.sys, "platform", "linux")
    monkeypatch.setattr(
        unit_writer, "SKILL_DIR", Path.home() / "not-a-temp-checkout" / "_rtx"
    )

    def _raise(**kwargs):
        from .._setup_runner import CronUnavailableError

        raise CronUnavailableError("no crontab command on this host")

    monkeypatch.setattr(
        unit_writer, "platform_schedule_backend", lambda: _NullBackend()
    )
    _install_fake_cron_installer(monkeypatch, _raise)
    _install_test_schedule_context(monkeypatch, tmp_path / "units")

    unit_writer.sync_units([], tmp_path / "units", unit_writer.LOG_DIR, live=True)

    assert "Skipped health-check cron repair" in capsys.readouterr().out


class _NullBackend:
    """A backend that records nothing; these tests are about the cron entry."""

    name = "linux-systemd"

    def sync(self, jobs, context):
        return None


def _install_fake_cron_installer(monkeypatch, replacement):
    """Point _repair_healthcheck_cron's function-local import at a double."""
    from .. import _setup_runner

    # These tests deliberately simulate the Linux-only cron path even when the
    # suite itself runs on Windows, where os.getuid is not defined.
    monkeypatch.setattr(unit_writer.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(_setup_runner, "install_healthcheck_cron", replacement)


def _install_test_schedule_context(monkeypatch, unit_dir: Path) -> None:
    monkeypatch.setattr(
        unit_writer,
        "production_schedule_context",
        lambda: unit_writer.ScheduleContext(
            skill_dir=unit_writer.SKILL_DIR,
            jobs_file=unit_writer.DEFAULT_JOBS,
            log_dir=unit_writer.LOG_DIR,
            unit_dir=unit_dir,
            live=False,
        ),
    )


def test_sync_identity_is_installation_id_not_checkout_path(
    monkeypatch, tmp_path
):
    """The 2026-08-17 defect, at its source.

    A sync run from a worktree repointed the installation at that worktree: no
    error, exit 0. The registrations then named a directory that could be
    deleted at any time, stranding the jobs and the sentinel together.
    """
    from .. import _install_owner as install_owner

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "ai-my-job.service").write_text("[Service]\n", encoding="utf-8")
    install_owner.write_owner(unit_dir=unit_dir, owner=tmp_path / "canonical" / "_rtx")
    monkeypatch.setattr(unit_writer.sys, "platform", "linux")
    monkeypatch.setattr(unit_writer, "SKILL_DIR", tmp_path / "worktree" / "_rtx")

    synced = []

    class _RecordingBackend(_NullBackend):
        def sync(self, jobs, context):
            synced.append(context)

    monkeypatch.setattr(
        unit_writer, "platform_schedule_backend", lambda: _RecordingBackend()
    )
    cron_calls = []
    _install_fake_cron_installer(monkeypatch, lambda **kwargs: cron_calls.append(kwargs))
    _install_test_schedule_context(monkeypatch, unit_dir)

    unit_writer.sync_units([], unit_dir, unit_writer.LOG_DIR, live=True)

    assert len(synced) == 1
    assert len(cron_calls) == 1
    assert install_owner.read_owner(unit_dir) == tmp_path / "worktree" / "_rtx"


def test_adopt_flag_is_retired_from_public_managed_sync():
    with pytest.raises(SystemExit):
        unit_writer.main(["--adopt"])


def test_live_sync_records_this_checkout_as_the_owner(monkeypatch, tmp_path):
    from .. import _install_owner as install_owner

    monkeypatch.setattr(unit_writer.sys, "platform", "linux")
    owner = Path.home() / "not-a-temp-checkout" / "_rtx"
    monkeypatch.setattr(unit_writer, "SKILL_DIR", owner)
    monkeypatch.setattr(
        unit_writer, "platform_schedule_backend", lambda: _NullBackend()
    )
    _install_fake_cron_installer(monkeypatch, lambda **kwargs: None)
    unit_dir = tmp_path / "units"
    _install_test_schedule_context(monkeypatch, unit_dir)

    unit_writer.sync_units([], unit_dir, unit_writer.LOG_DIR, live=True)

    assert install_owner.read_owner(unit_dir) == owner


def test_sync_from_a_temporary_copy_leaves_the_real_crontab_alone(
    monkeypatch, tmp_path, capsys
):
    """A sync running from a mirrored checkout must not touch the user's cron.

    The repository checks run validators from a mirror under the temp
    directory. Because the rendered entry embeds this file's own location, a
    sync there rewrote the real crontab to invoke the mirror -- which is then
    deleted, so the health check began failing every four hours by pointing at
    a path that no longer existed, and reported it as a job problem.

    Covered now by ownership rather than by a temp-directory predicate: the
    mirror is refused because it is not the recorded owner, which also covers
    the worktree that reproduced this in 2026-08-17 and that the narrower
    predicate missed.
    """
    from .. import _install_owner as install_owner

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "ai-my-job.service").write_text("[Service]\n", encoding="utf-8")
    install_owner.write_owner(unit_dir=unit_dir, owner=Path.home() / "checkout" / "_rtx")
    monkeypatch.setattr(unit_writer.sys, "platform", "linux")
    monkeypatch.setattr(
        unit_writer, "SKILL_DIR", Path(tempfile.gettempdir()) / "mirror" / "_rtx"
    )
    monkeypatch.setattr(unit_writer, "DEFAULT_UNIT_DIR", unit_dir)
    monkeypatch.setattr(
        unit_writer, "platform_schedule_backend", lambda: _NullBackend()
    )

    def _must_not_run(**kwargs):
        raise AssertionError("a mirrored checkout must not rewrite the crontab")

    _install_fake_cron_installer(monkeypatch, _must_not_run)
    _install_test_schedule_context(monkeypatch, unit_dir)

    try:
        unit_writer.sync_units([], unit_dir, unit_writer.LOG_DIR, live=True)
    except install_owner.NotTheOwnerError:
        pass
    else:
        raise AssertionError("a mirrored checkout must not be allowed to sync")

#!/usr/bin/env python3
"""Tests for sync_units.py: unit file generation and cron->systemd conversion."""
import importlib.util, tempfile, os
import runpy
import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[1] / "src"
SCRIPT    = SKILL_DIR / "_rtx" / "_unit_writer.py"


def _load():
    import sys
    sys.path.insert(0, str(REPO_SRC))
    spec = importlib.util.spec_from_file_location("sync_units", SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skill_dir_is_absolute_when_entrypoint_uses_a_logical_path(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_SRC))
    monkeypatch.chdir(SKILL_DIR)

    namespace = runpy.run_path(
        "_rtx/_unit_writer.py",
        run_name="_unit_writer_logical_path_test",
    )

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
    monkeypatch.setattr(shutil, "which", lambda _name: "/opt/famulus/bin/invoke-skill")
    with tempfile.TemporaryDirectory() as d:
        _run_sync(JOBS_ONE_ENABLED, d)
        content = (Path(d) / "ai-test-job.service").read_text()
        assert f'ExecStart="{sys.executable}"' in content
        assert f'Environment="PATH=/opt/famulus/bin:{Path(sys.executable).parent}:' in content
        assert '_job_executor.py" --jobs-file' in content
        assert "/bin/bash" not in content
        assert ">>" not in content
        print("PASS: service ExecStart uses the active Python job executor without shell redirection")


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

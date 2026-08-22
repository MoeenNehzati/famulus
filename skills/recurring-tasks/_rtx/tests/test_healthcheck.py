#!/usr/bin/env python3
"""Tests for health-check preflight, job checks, logging, and exit status."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from .. import _healthcheck_probe as healthcheck

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT = SKILL_DIR / "_healthcheck_probe.py"


@pytest.fixture(autouse=True)
def restore_runtime_paths(monkeypatch):
    """Restore shared module paths after every healthcheck test."""
    original = (
        healthcheck.LOG_DIR,
        healthcheck.HEALTHCHECK_LOG,
        healthcheck.JOBS_FILE,
    )
    monkeypatch.setattr(
        healthcheck,
        "production_schedule_context",
        lambda: healthcheck.ScheduleContext(
            skill_dir=healthcheck.SKILL_DIR,
            jobs_file=healthcheck.JOBS_FILE,
            log_dir=healthcheck.LOG_DIR,
            unit_dir=Path("/tmp/famulus-healthcheck-test-units"),
            live=False,
        ),
    )
    yield
    (
        healthcheck.LOG_DIR,
        healthcheck.HEALTHCHECK_LOG,
        healthcheck.JOBS_FILE,
    ) = original


def _load(tmp_dir: Path):
    """Redirect the package runtime into this test's temporary directory."""
    mod = healthcheck
    mod.LOG_DIR = tmp_dir / "logs"
    mod.HEALTHCHECK_LOG = mod.LOG_DIR / "healthcheck" / "run.log"
    mod.JOBS_FILE = tmp_dir / "jobs.yaml"
    return mod


# ── check_systemd_manager ──────────────────────────────────────────────────────

def test_systemd_manager_running_is_ok():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        backend = mock.Mock()
        backend.check_manager.return_value = None
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            assert mod.check_systemd_manager() is None
    print("PASS: systemd 'running' state is OK")


def test_systemd_manager_degraded_is_ok():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        backend = mock.Mock()
        backend.check_manager.return_value = None
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            assert mod.check_systemd_manager() is None
    print("PASS: systemd 'degraded' state is OK")


def test_systemd_manager_other_state_fails_with_reason():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        backend = mock.Mock()
        backend.check_manager.return_value = "systemd user manager: stopping"
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            reason = mod.check_systemd_manager()
        assert reason == "systemd user manager: stopping"
    print("PASS: unexpected systemd state returns a descriptive reason")


def test_systemd_manager_empty_output_reports_unresponsive():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        backend = mock.Mock()
        backend.check_manager.return_value = "systemd user manager: unresponsive"
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            reason = mod.check_systemd_manager()
        assert reason == "systemd user manager: unresponsive"
    print("PASS: empty systemctl output reports 'unresponsive'")


# ── check_job ───────────────────────────────────────────────────────────────────

def _job(name="test-job", schedule="0 * * * *"):
    return {
        "name": name,
        "description": "Test Job",
        "command": "true",
        "schedule": schedule,
        "enabled": True,
    }


def _write_latest_record(
    log_dir: Path, name: str, *, success: bool, reason: str = "",
    finished_minutes_ago: int = 0,
):
    from datetime import datetime, timedelta, timezone

    finished = datetime.now(timezone.utc) - timedelta(minutes=finished_minutes_ago)
    record = {
        "job_name": name,
        "started_at": finished.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "process_exit_code": 0 if success else 1,
        "inner_status": None,
        "success": success,
        "reason": reason,
        "run_id": "test-run",
    }
    destination = log_dir / name / "latest.json"
    destination.write_text(json.dumps(record))


def test_check_job_with_no_recorded_run_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        backend = mock.Mock()
        backend.check_job_configuration.return_value = None
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            reason = mod.check_job(_job())
        assert reason == "test-job: no completed run recorded"
    print("PASS: a job with no recorded run fails")


def test_check_job_reports_scheduler_configuration_drift_before_log_state():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        backend = mock.Mock()
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(
                 mod,
                 "check_job_configuration",
                 return_value="test-job: service unit stale",
             ):
            reason = mod.check_job(
                {
                    "name": "test-job",
                    "description": "Test Job",
                    "command": "true",
                    "schedule": "0 * * * *",
                    "enabled": True,
                }
            )
        assert reason == "test-job: service unit stale"


def test_check_job_fresh_log_and_active_timer_is_ok():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("ran fine\n")
        _write_latest_record(mod.LOG_DIR, "test-job", success=True)
        backend = mock.Mock()
        backend.check_job_configuration.return_value = None
        backend.check_job_active.return_value = True
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            assert mod.check_job(_job()) is None
    print("PASS: fresh log + active timer is OK")


def test_check_job_latest_failed_run_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("--- RUN END (success=False) ---\n")
        _write_latest_record(
            mod.LOG_DIR,
            "test-job",
            success=False,
            reason="process exit code 1",
        )
        backend = mock.Mock()
        backend.check_job_configuration.return_value = None
        backend.check_job_active.return_value = True
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            reason = mod.check_job(_job())
        assert reason == "test-job: latest run failed (process exit code 1)"


def test_check_job_stale_run_fails():
    """A job whose last COMPLETED run is old is stale.

    Measured from the run record: a killed run refreshes the log file's
    timestamp without ever finishing, so the log cannot answer this.
    """
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        (mod.LOG_DIR / "test-job").mkdir(parents=True)
        # hourly schedule -> stale past 2h; record a finish 3h ago
        _write_latest_record(
            mod.LOG_DIR, "test-job", success=True, finished_minutes_ago=180
        )
        backend = mock.Mock()
        backend.check_job_configuration.return_value = None
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod, "check_job_configuration", return_value=None):
            reason = mod.check_job(_job(schedule="0 * * * *"))
        assert reason is not None and "last completed run" in reason
    print("PASS: stale run fails")


def test_check_job_inactive_timer_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("ran fine\n")
        _write_latest_record(mod.LOG_DIR, "test-job", success=True)
        backend = mock.Mock()
        backend.check_job_configuration.return_value = None
        backend.check_job_active.return_value = False
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
            reason = mod.check_job(_job())
        assert reason == "test-job: timer not active"
    print("PASS: inactive timer fails")


# ── parse_schedule_interval ─────────────────────────────────────────────────────

def test_parse_interval_every_n_minutes():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        assert mod.parse_schedule_interval("*/15 * * * *") == 15
    print("PASS: every-N-minutes interval")


def test_parse_interval_every_n_hours():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        assert mod.parse_schedule_interval("0 */4 * * *") == 240
    print("PASS: every-N-hours interval")


def test_parse_interval_hourly():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        assert mod.parse_schedule_interval("30 * * * *") == 60
    print("PASS: hourly interval")


def test_parse_interval_daily():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        assert mod.parse_schedule_interval("0 8 * * *") == 1440
    print("PASS: daily interval")


def test_log_relies_on_stdout_redirection_during_cron_invocation():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.dict(
            os.environ,
            {"RECURRING_TASKS_HEALTHCHECK_CRON": "1"},
            clear=False,
        ):
            mod.log("one line")
        assert not mod.HEALTHCHECK_LOG.exists()


def test_log_appends_once_during_manual_invocation():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.dict(os.environ, {}, clear=True):
            mod.log("one line")
        assert mod.HEALTHCHECK_LOG.read_text().count("one line") == 1


# ── main(): failure aggregation and reporting ──────────────────────────────────

def test_main_delegates_healthcheck_to_managed_boundary():
    mod = _load(Path("/tmp"))
    with mock.patch.object(mod, "run_managed_control", return_value=0) as managed:
        assert mod.main([]) == 0
        managed.assert_called_once_with("healthcheck")


def test_main_returns_managed_healthcheck_failure_status():
    mod = _load(Path("/tmp"))
    with mock.patch.object(mod, "run_managed_control", return_value=1):
        assert mod.main([]) == 1


def test_main_rejects_source_local_healthcheck_arguments(capsys):
    mod = _load(Path("/tmp"))
    assert mod.main(["--jobs-file", "/tmp/other"]) == 2
    assert "unexpected arguments" in capsys.readouterr().err


if __name__ == "__main__":
    test_systemd_manager_running_is_ok()
    test_systemd_manager_degraded_is_ok()
    test_systemd_manager_other_state_fails_with_reason()
    test_systemd_manager_empty_output_reports_unresponsive()
    test_check_job_with_no_recorded_run_fails()
    test_check_job_fresh_log_and_active_timer_is_ok()
    test_check_job_stale_run_fails()
    test_check_job_inactive_timer_fails()
    test_parse_interval_every_n_minutes()
    test_parse_interval_every_n_hours()
    test_parse_interval_hourly()
    test_parse_interval_daily()
    test_log_relies_on_stdout_redirection_during_cron_invocation()
    test_log_appends_once_during_manual_invocation()
    print("\nAll tests passed.")


# ── in-flight / interrupted runs ────────────────────────────────────────────────

def _stage_job_logs(mod, name="test-job", *, latest_success=True):
    """A job whose log is fresh and whose last recorded run succeeded."""
    log_file = mod.LOG_DIR / name / "run.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("--- RUN START ---\n")
    _write_latest_record(mod.LOG_DIR, name, success=latest_success)
    return log_file


def test_check_job_flags_a_run_that_started_and_never_finished():
    """The killed-job case: fresh log, stale success record, no completion.

    A killed executor (systemd stop, OOM, reboot, suspend) writes no run
    record, but "--- RUN START ---" already refreshed run.log's mtime. Before
    the in-flight marker this reported HEALTHY indefinitely.
    """
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        _stage_job_logs(mod)
        marker = mod.LOG_DIR / "test-job" / "running.json"
        marker.write_text('{"job_name": "test-job"}')
        stale = time.time() - (mod.INCOMPLETE_RUN_GRACE_SECONDS + 120)
        os.utime(marker, (stale, stale))

        backend = mock.Mock()
        backend.check_job_active.return_value = True
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod, "check_job_configuration", return_value=None):
            reason = mod.check_job(_job())

        assert reason is not None, "a killed run must not report healthy"
        assert "never completed" in reason
    print("PASS: an interrupted run is reported, not masked by log freshness")


def test_check_job_allows_a_run_that_is_legitimately_in_flight():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        _stage_job_logs(mod)
        (mod.LOG_DIR / "test-job" / "running.json").write_text('{"job_name": "test-job"}')

        backend = mock.Mock()
        backend.check_job_active.return_value = True
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod, "check_job_configuration", return_value=None):
            assert mod.check_job(_job()) is None
    print("PASS: a fresh in-flight run is not a failure")


def test_parse_interval_every_minute():
    """A per-minute job must not read as fresh for two hours after it dies."""
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        assert mod.parse_schedule_interval("* * * * *") == 1


def test_parse_interval_weekly_is_not_treated_as_daily():
    """A healthy weekly job was flagged stale after two days."""
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        assert mod.parse_schedule_interval("0 3 * * 1") == 10080


def test_parse_interval_rejects_a_malformed_schedule():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        try:
            mod.parse_schedule_interval("@daily")
        except ValueError:
            pass
        else:
            raise AssertionError("a malformed schedule must be rejected")


def test_check_job_reports_an_unusable_schedule_instead_of_crashing():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("x")
        backend = mock.Mock()
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod, "check_job_configuration", return_value=None):
            reason = mod.check_job(_job(schedule="@daily"))
        assert reason is not None and "unusable schedule" in reason

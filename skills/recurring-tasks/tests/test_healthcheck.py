#!/usr/bin/env python3
"""Tests for healthcheck.py: pre-flight/per-job checks (each returning a
failure reason or None), desktop notification wiring, and main()'s
failure-aggregation/reporting."""
import importlib.util
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = SKILL_DIR / "_rtx" / "_healthcheck_probe.py"


def _load(tmp_dir: Path):
    """Load a fresh copy of the module with its log/jobs paths redirected
    into tmp_dir, so tests never touch this skill's real logs/jobs.yaml."""
    spec = importlib.util.spec_from_file_location("healthcheck", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOG_DIR = tmp_dir / "logs"
    mod.HEALTHCHECK_LOG = mod.LOG_DIR / "healthcheck" / "run.log"
    mod.JOBS_FILE = tmp_dir / "jobs.yaml"
    return mod


# ── check_systemd_manager ──────────────────────────────────────────────────────

def test_systemd_manager_running_is_ok():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="running\n", stderr="")
            assert mod.check_systemd_manager() is None
    print("PASS: systemd 'running' state is OK")


def test_systemd_manager_degraded_is_ok():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="degraded\n", stderr="")
            assert mod.check_systemd_manager() is None
    print("PASS: systemd 'degraded' state is OK")


def test_systemd_manager_other_state_fails_with_reason():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="stopping\n", stderr="")
            reason = mod.check_systemd_manager()
        assert reason == "systemd user manager: stopping"
    print("PASS: unexpected systemd state returns a descriptive reason")


def test_systemd_manager_empty_output_reports_unresponsive():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
            reason = mod.check_systemd_manager()
        assert reason == "systemd user manager: unresponsive"
    print("PASS: empty systemctl output reports 'unresponsive'")


# ── check_environment ──────────────────────────────────────────────────────────

def test_environment_not_set_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="OTHER_VAR=1\n", stderr="")
            reason = mod.check_environment()
        assert reason == "AI_AGENT_COMMAND_TEMPLATE: not set"
    print("PASS: missing AI_AGENT_COMMAND_TEMPLATE fails")


def test_environment_command_not_found_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run, \
             mock.patch.object(mod.shutil, "which", return_value=None):
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n", stderr="",
            )
            reason = mod.check_environment()
        assert reason == "AI_AGENT_COMMAND_TEMPLATE: command not found: invoke-skill"
    print("PASS: unresolvable command in template fails")


def test_environment_ok_when_set_and_resolvable():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run, \
             mock.patch.object(mod.shutil, "which", return_value="/usr/local/bin/invoke-skill"):
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n", stderr="",
            )
            assert mod.check_environment() is None
    print("PASS: set + resolvable template is OK")


def test_environment_strips_bash_quoting():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        with mock.patch.object(mod.subprocess, "run") as run, \
             mock.patch.object(mod.shutil, "which", return_value="/usr/bin/invoke-skill") as which:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="AI_AGENT_COMMAND_TEMPLATE=$'invoke-skill {skill}'\n", stderr="",
            )
            assert mod.check_environment() is None
            which.assert_called_once_with("invoke-skill")
    print("PASS: bash $'...' quoting is stripped before resolving the command")


# ── check_job ───────────────────────────────────────────────────────────────────

def _job(name="test-job", schedule="0 * * * *"):
    return {"name": name, "schedule": schedule}


def test_check_job_missing_log_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        reason = mod.check_job(_job())
        assert reason == "test-job: no log file"
    print("PASS: missing log file fails")


def test_check_job_fresh_log_and_active_timer_is_ok():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("ran fine\n")
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            assert mod.check_job(_job()) is None
    print("PASS: fresh log + active timer is OK")


def test_check_job_stale_log_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("old\n")
        # schedule is hourly -> stale threshold is 2h; back-date mtime by 3h
        old_time = time.time() - 3 * 3600
        import os
        os.utime(log_file, (old_time, old_time))
        reason = mod.check_job(_job(schedule="0 * * * *"))
        assert reason is not None and "log stale" in reason
    print("PASS: stale log fails")


def test_check_job_inactive_timer_fails():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        log_file = mod.LOG_DIR / "test-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("ran fine\n")
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
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


# ── notify_desktop ──────────────────────────────────────────────────────────────

def test_notify_desktop_skips_when_script_missing():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        mod.NOTIFY_SCRIPT = Path(d) / "does-not-exist.py"
        with mock.patch.object(mod.subprocess, "run") as run:
            mod.notify_desktop("Title", "Body")
            run.assert_not_called()
        assert "not found" in mod.HEALTHCHECK_LOG.read_text()
    print("PASS: notify_desktop skips gracefully when the script is missing")


def test_notify_desktop_invokes_script_with_expected_args():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        fake_script = Path(d) / "assistant_desktop_notify.py"
        fake_script.write_text("#!/usr/bin/env python3\n")
        mod.NOTIFY_SCRIPT = fake_script
        with mock.patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            mod.notify_desktop("Recurring Tasks", "All checks passed", urgency="low")
        called_cmd = run.call_args[0][0]
        assert called_cmd[0] == str(fake_script)
        assert "--title" in called_cmd and "Recurring Tasks" in called_cmd
        assert "--body" in called_cmd and "All checks passed" in called_cmd
        assert "--urgency" in called_cmd and "low" in called_cmd
    print("PASS: notify_desktop invokes the sibling script with title/body/urgency")


def test_notify_desktop_never_raises_on_subprocess_error():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        fake_script = Path(d) / "assistant_desktop_notify.py"
        fake_script.write_text("#!/usr/bin/env python3\n")
        mod.NOTIFY_SCRIPT = fake_script
        with mock.patch.object(mod.subprocess, "run", side_effect=OSError("no permission")):
            mod.notify_desktop("Title", "Body")  # must not raise
        assert "desktop notification failed" in mod.HEALTHCHECK_LOG.read_text()
    print("PASS: notify_desktop never raises even if the subprocess call errors")


# ── main(): failure aggregation and reporting ──────────────────────────────────

def test_main_reports_success_when_no_problems():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        mod.JOBS_FILE.write_text("jobs: []\n")
        with mock.patch.object(mod, "check_systemd_manager", return_value=None), \
             mock.patch.object(mod, "check_environment", return_value=None), \
             mock.patch.object(mod, "notify_desktop") as notify:
            mod.main()
        notify.assert_called_once_with("Recurring Tasks", "All checks passed", urgency="low")
    print("PASS: main() reports success with urgency=low when nothing fails")


def test_main_aggregates_failure_reasons_into_body():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        mod.JOBS_FILE.write_text(
            "jobs:\n"
            "  - name: job-a\n"
            "    schedule: '0 * * * *'\n"
            "    enabled: true\n"
        )
        with mock.patch.object(mod, "check_systemd_manager", return_value="systemd user manager: degraded"), \
             mock.patch.object(mod, "check_environment", return_value=None), \
             mock.patch.object(mod, "check_job", return_value="job-a: no log file"), \
             mock.patch.object(mod, "notify_desktop") as notify:
            mod.main()
        notify.assert_called_once()
        title, body = notify.call_args[0]
        kwargs = notify.call_args[1]
        assert title == "Recurring Tasks"
        assert kwargs["urgency"] == "critical"
        assert "2 health check problem(s):" in body
        assert "- systemd user manager: degraded" in body
        assert "- job-a: no log file" in body
    print("PASS: main() aggregates failure reasons from all checks into the notification body")


def test_main_caps_listed_failures_at_five_with_overflow_note():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        jobs_yaml = "jobs:\n"
        for i in range(7):
            jobs_yaml += (
                f"  - name: job-{i}\n"
                "    schedule: '0 * * * *'\n"
                "    enabled: true\n"
            )
        mod.JOBS_FILE.write_text(jobs_yaml)
        with mock.patch.object(mod, "check_systemd_manager", return_value=None), \
             mock.patch.object(mod, "check_environment", return_value=None), \
             mock.patch.object(mod, "check_job", side_effect=lambda job: f"{job['name']}: no log file"), \
             mock.patch.object(mod, "notify_desktop") as notify:
            mod.main()
        _, body = notify.call_args[0]
        assert "7 health check problem(s):" in body
        assert body.count("\n- ") == 5, "should list at most 5 reasons"
        assert "(+2 more" in body
    print("PASS: main() caps the notification body at 5 listed failures with a '+N more' note")


def test_main_skips_disabled_jobs():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        mod.JOBS_FILE.write_text(
            "jobs:\n"
            "  - name: disabled-job\n"
            "    schedule: '0 * * * *'\n"
            "    enabled: false\n"
        )
        with mock.patch.object(mod, "check_systemd_manager", return_value=None), \
             mock.patch.object(mod, "check_environment", return_value=None), \
             mock.patch.object(mod, "check_job") as check_job, \
             mock.patch.object(mod, "notify_desktop") as notify:
            mod.main()
        check_job.assert_not_called()
        notify.assert_called_once_with("Recurring Tasks", "All checks passed", urgency="low")
    print("PASS: main() skips disabled jobs entirely")


def test_main_handles_missing_jobs_file_gracefully():
    with tempfile.TemporaryDirectory() as d:
        mod = _load(Path(d))
        # JOBS_FILE was never written -> open() raises, main() should log and return
        with mock.patch.object(mod, "notify_desktop") as notify:
            mod.main()  # must not raise
        notify.assert_not_called()
        assert "Failed to load jobs.yaml" in mod.HEALTHCHECK_LOG.read_text()
    print("PASS: main() handles a missing/unreadable jobs.yaml without crashing")


if __name__ == "__main__":
    test_systemd_manager_running_is_ok()
    test_systemd_manager_degraded_is_ok()
    test_systemd_manager_other_state_fails_with_reason()
    test_systemd_manager_empty_output_reports_unresponsive()
    test_environment_not_set_fails()
    test_environment_command_not_found_fails()
    test_environment_ok_when_set_and_resolvable()
    test_environment_strips_bash_quoting()
    test_check_job_missing_log_fails()
    test_check_job_fresh_log_and_active_timer_is_ok()
    test_check_job_stale_log_fails()
    test_check_job_inactive_timer_fails()
    test_parse_interval_every_n_minutes()
    test_parse_interval_every_n_hours()
    test_parse_interval_hourly()
    test_parse_interval_daily()
    test_notify_desktop_skips_when_script_missing()
    test_notify_desktop_invokes_script_with_expected_args()
    test_notify_desktop_never_raises_on_subprocess_error()
    test_main_reports_success_when_no_problems()
    test_main_aggregates_failure_reasons_into_body()
    test_main_caps_listed_failures_at_five_with_overflow_note()
    test_main_skips_disabled_jobs()
    test_main_handles_missing_jobs_file_gracefully()
    print("\nAll tests passed.")

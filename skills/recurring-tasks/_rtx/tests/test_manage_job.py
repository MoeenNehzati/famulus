#!/usr/bin/env python3
"""Tests for manage_job.py: the enable/disable/test/view-logs/status/sync
subcommands, at both the function level and the CLI level.

test_enable_disable.py already covers enable/disable through the CLI
end-to-end; this file covers the remaining subcommands (test, view-logs,
status, sync) and a few function-level edge cases (job-not-found, sync_units
argument passthrough) that aren't practical to exercise through subprocess."""
import subprocess
import tempfile
import sys
from pathlib import Path
from unittest import mock

from test_support.runtime_module import load_runtime_module

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[2] / "src"
SCRIPT = SKILL_DIR / "_job_control.py"


def _load():
    sys.path.insert(0, str(REPO_SRC))
    return load_runtime_module(SCRIPT)


# ── load_jobs / save_jobs ──────────────────────────────────────────────────────

def test_load_jobs_roundtrip():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("jobs:\n  - name: a\n    enabled: true\n")
        path = Path(f.name)
    try:
        jobs = mod.load_jobs(path)
        assert jobs == [{"name": "a", "enabled": True}]
        jobs[0]["enabled"] = False
        mod.save_jobs(jobs, path)
        assert mod.load_jobs(path) == [{"name": "a", "enabled": False}]
    finally:
        path.unlink()
    print("PASS: load_jobs/save_jobs roundtrip")


def test_load_jobs_empty_file_returns_empty_list():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("")
        path = Path(f.name)
    try:
        assert mod.load_jobs(path) == []
    finally:
        path.unlink()
    print("PASS: empty jobs.yaml yields an empty list")


# ── enable_job / disable_job: not-found and sync passthrough ──────────────────

def test_enable_job_raises_for_unknown_name():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("jobs:\n  - name: a\n    enabled: false\n")
        path = Path(f.name)
    try:
        try:
            mod.enable_job("no-such-job", jobs_file=path, sync=False)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "no-such-job" in str(e)
    finally:
        path.unlink()
    print("PASS: enable_job raises ValueError for an unknown job name")


def test_enable_job_skips_sync_when_requested():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("jobs:\n  - name: a\n    enabled: false\n")
        path = Path(f.name)
    try:
        with mock.patch.object(mod, "sync_units") as sync_units:
            mod.enable_job("a", jobs_file=path, sync=False)
            sync_units.assert_not_called()
    finally:
        path.unlink()
    print("PASS: enable_job does not sync when sync=False")


def test_disable_job_passes_custom_jobs_file_to_sync():
    mod = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("jobs:\n  - name: a\n    enabled: true\n")
        path = Path(f.name)
    try:
        with mock.patch.object(mod, "sync_units") as sync_units:
            mod.disable_job("a", jobs_file=path, sync=True)
            sync_units.assert_called_once_with(path)
    finally:
        path.unlink()
    print("PASS: disable_job threads a custom jobs_file through to sync_units")


def test_default_jobs_file_calls_sync_units_with_no_override():
    """When jobs_file is the module's own default JOBS_FILE (i.e. a real
    install, not a test), sync_units should be called with no --jobs-file
    override, since sync_units.py's own default already points at the same
    file."""
    mod = _load()
    with mock.patch.object(mod, "load_jobs", return_value=[{"name": "a", "enabled": False}]), \
         mock.patch.object(mod, "save_jobs"), \
         mock.patch.object(mod, "sync_units") as sync_units:
        mod.enable_job("a")
        sync_units.assert_called_once_with(None)
    print("PASS: default jobs_file calls sync_units with no override")


# ── sync_units: backend dispatch ───────────────────────────────────────────────

def test_sync_units_invokes_platform_backend():
    mod = _load()
    backend = mock.Mock()
    with mock.patch.object(mod, "load_jobs", return_value=[]), \
         mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
        mod.sync_units()
        backend.sync.assert_called_once()
        context = backend.sync.call_args[0][1]
        assert context.jobs_file == mod.JOBS_FILE
    print("PASS: sync_units() with no override calls the platform backend")


def test_sync_units_passes_jobs_file_override():
    mod = _load()
    custom = Path("/tmp/custom-jobs.yaml")
    backend = mock.Mock()
    with mock.patch.object(mod, "load_jobs", return_value=[]), \
         mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
        mod.sync_units(custom)
        context = backend.sync.call_args[0][1]
        assert context.jobs_file == custom
    print("PASS: sync_units() passes through a jobs_file override")


# ── test_job ────────────────────────────────────────────────────────────────────
#
# test_job() must not report pass/fail based on the scheduler backend's own
# `test()` return value alone -- that only tells us the OS scheduler
# *accepted the trigger* (e.g. `launchctl kickstart` and `schtasks /Run`
# return immediately without waiting for the job to actually finish). The
# real signal is the JobRunRecord a completed run writes to
# logs/<job>/latest.json. So test_job() triggers the job, then waits
# (bounded) for a *new* run record and reports pass/fail from its
# `success` field.

def _write_run_record(
    log_dir: Path, name: str, *, success: bool, run_id: str = "r1", finished_at: str = "t1"
) -> None:
    job_dir = log_dir / name
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "latest.json").write_text(
        f'{{"job_name": "{name}", "run_id": "{run_id}", "finished_at": "{finished_at}", '
        f'"success": {str(success).lower()}, "reason": ""}}'
    )


def test_test_job_reports_pass_when_run_record_reports_success():
    mod = _load()
    backend = mock.Mock()
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)

        def fake_test(job_name, context):
            # Simulates a synchronous backend (e.g. systemd's `start
            # --wait`): the run record already exists by the time test()
            # returns.
            _write_run_record(mod.LOG_DIR, job_name, success=True, run_id="new")
            return True

        backend.test.side_effect = fake_test
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod.time, "sleep"):
            assert mod.test_job("my-job", timeout_seconds=1) is True
            backend.test.assert_called_once()
            assert backend.test.call_args[0][0] == "my-job"
    print("PASS: test_job reports True when the run record reports success")


def test_test_job_reports_failure_when_scheduler_rejects_trigger():
    mod = _load()
    backend = mock.Mock()
    backend.test.return_value = False
    with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
        assert mod.test_job("my-job") is False
    print("PASS: test_job reports False when the scheduler backend rejects the trigger")


def test_test_job_reports_failure_when_trigger_accepted_but_job_actually_failed():
    """The core bug this fixes: the OS scheduler can happily accept a
    trigger (backend.test() -> True) for a job whose underlying task then
    fails. test_job() must report that failure, not the trigger's own
    success."""
    mod = _load()
    backend = mock.Mock()

    def fake_test(job_name, context):
        _write_run_record(mod.LOG_DIR, job_name, success=False, run_id="new")
        return True

    backend.test.side_effect = fake_test
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod.time, "sleep"):
            assert mod.test_job("my-job", timeout_seconds=1) is False
    print("PASS: test_job reports False when the trigger is accepted but the run record reports failure")


def test_test_job_times_out_if_no_new_run_record_appears():
    """Bounded wait: a backend whose trigger fires asynchronously and never
    produces a run record (e.g. a wedged job) must not hang test_job()
    forever."""
    mod = _load()
    backend = mock.Mock()
    backend.test.return_value = True
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod.time, "monotonic", side_effect=[0, 0, 10, 10]), \
             mock.patch.object(mod.time, "sleep"):
            assert mod.test_job("my-job", timeout_seconds=1) is False
    print("PASS: test_job times out and reports False when no run record appears")


def test_test_job_ignores_a_stale_run_record_from_a_previous_run():
    """If logs/<job>/latest.json already exists from an earlier run before
    this trigger, test_job() must wait for a *new* record (different
    run_id), not immediately report the stale one's outcome."""
    mod = _load()
    backend = mock.Mock()
    backend.test.return_value = True
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)
        _write_run_record(mod.LOG_DIR, "my-job", success=False, run_id="stale")

        calls = {"n": 0}

        def fake_sleep(_interval):
            calls["n"] += 1
            if calls["n"] == 1:
                _write_run_record(mod.LOG_DIR, "my-job", success=True, run_id="fresh")

        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod.time, "sleep", side_effect=fake_sleep):
            assert mod.test_job("my-job", timeout_seconds=5) is True
    print("PASS: test_job waits for a fresh run record rather than trusting a stale one")


def test_test_job_detects_fresh_run_despite_same_second_finished_at_collision():
    """finished_at has only second resolution, so a fast run (e.g. an
    instant spawn failure) can legitimately share its timestamp string with
    the baseline record. test_job() must still recognize it as fresh via
    run_id rather than falsely timing out and reporting FAIL."""
    mod = _load()
    backend = mock.Mock()

    def fake_test(job_name, context):
        # Same finished_at as the pre-existing baseline record, but a
        # distinct run_id: this is the collision case.
        _write_run_record(
            mod.LOG_DIR, job_name, success=True, run_id="run-2", finished_at="same-second"
        )
        return True

    backend.test.side_effect = fake_test
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)
        _write_run_record(
            mod.LOG_DIR, "my-job", success=True, run_id="run-1", finished_at="same-second"
        )
        with mock.patch.object(mod, "platform_schedule_backend", return_value=backend), \
             mock.patch.object(mod.time, "sleep"):
            assert mod.test_job("my-job", timeout_seconds=1) is True
    print("PASS: test_job detects a fresh run via run_id even when finished_at collides")


# ── view_logs ───────────────────────────────────────────────────────────────────

def test_view_logs_prints_no_logs_message_when_missing(capsys):
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)
        mod.view_logs("missing-job")
    out = capsys.readouterr().out
    assert "No logs for: missing-job" in out
    print("PASS: view_logs reports a clear message when no log exists")


def test_view_logs_tails_last_n_lines(capsys):
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        mod.LOG_DIR = Path(d)
        log_file = mod.LOG_DIR / "my-job" / "run.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
        mod.view_logs("my-job", lines=3)
    out = capsys.readouterr().out
    assert "line 7" in out and "line 8" in out and "line 9" in out
    assert "line 6" not in out
    print("PASS: view_logs tails only the last N lines")


# ── status ──────────────────────────────────────────────────────────────────────

def test_status_lists_ai_timers(capsys):
    mod = _load()
    backend = mock.Mock()
    backend.status.return_value = "NEXT LEFT LAST UNIT\nai-daily-plan.timer\n"
    with mock.patch.object(mod, "platform_schedule_backend", return_value=backend):
        mod.status()
    out = capsys.readouterr().out
    assert "ai-daily-plan.timer" in out
    print("PASS: status prints scheduler backend status output")


# ── CLI dispatch (main) ─────────────────────────────────────────────────────────
#
# These test main()'s dispatch logic in-process (via sys.argv + mocked
# handlers), not through a real subprocess: several subcommands (sync,
# enable, disable) touch this skill's *real* jobs.yaml and systemd units by
# default when invoked for real, which would be an unacceptable side effect
# from a test run.

def test_cli_sync_subcommand_dispatches_to_sync_units():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "sync"]), \
         mock.patch.object(mod, "sync_units") as sync_units:
        mod.main()
        sync_units.assert_called_once_with()
    print("PASS: CLI 'sync' subcommand dispatches to sync_units()")


def test_cli_test_subcommand_dispatches_to_test_job():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "test", "my-job"]), \
         mock.patch.object(mod, "test_job") as test_job:
        mod.main()
        test_job.assert_called_once_with("my-job")
    print("PASS: CLI 'test' subcommand dispatches to test_job() with the job name")


def test_cli_test_subcommand_exits_zero_when_test_job_succeeds():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "test", "my-job"]), \
         mock.patch.object(mod, "test_job", return_value=True):
        assert mod.main() == 0
    print("PASS: CLI 'test' subcommand exits 0 when test_job() reports success")


def test_cli_test_subcommand_exits_nonzero_when_test_job_fails():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "test", "my-job"]), \
         mock.patch.object(mod, "test_job", return_value=False):
        assert mod.main() != 0
    print("PASS: CLI 'test' subcommand exits nonzero when test_job() reports failure")


def test_cli_view_logs_subcommand_passes_lines_flag():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "view-logs", "my-job", "--lines", "10"]), \
         mock.patch.object(mod, "view_logs") as view_logs:
        mod.main()
        view_logs.assert_called_once_with("my-job", 10)
    print("PASS: CLI 'view-logs' subcommand passes name and --lines through")


def test_cli_status_subcommand_dispatches_to_status():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "status"]), \
         mock.patch.object(mod, "status") as status_fn:
        mod.main()
        status_fn.assert_called_once_with()
    print("PASS: CLI 'status' subcommand dispatches to status()")


def test_cli_enable_subcommand_passes_jobs_file_and_no_sync():
    mod = _load()
    argv = ["manage_job.py", "enable", "my-job", "--jobs-file", "/tmp/x.yaml", "--no-sync"]
    with mock.patch.object(mod.sys, "argv", argv), \
         mock.patch.object(mod, "enable_job") as enable_job:
        mod.main()
        enable_job.assert_called_once_with("my-job", jobs_file=Path("/tmp/x.yaml"), sync=False)
    print("PASS: CLI 'enable' subcommand passes --jobs-file/--no-sync through")


def test_cli_reports_error_and_exits_nonzero_on_exception():
    mod = _load()
    with mock.patch.object(mod.sys, "argv", ["manage_job.py", "test", "my-job"]), \
         mock.patch.object(mod, "test_job", side_effect=RuntimeError("kaboom")):
        assert mod.main() != 0
    print("PASS: CLI reports an error and exits non-zero when a handler raises")


def test_cli_unknown_command_is_rejected():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "test_support.runtime_module",
            str(SCRIPT),
            "--",
            "not-a-real-command",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    print("PASS: CLI rejects an unrecognized subcommand")


def test_cli_requires_a_subcommand():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "test_support.runtime_module",
            str(SCRIPT),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    print("PASS: CLI requires a subcommand")


if __name__ == "__main__":
    test_load_jobs_roundtrip()
    test_load_jobs_empty_file_returns_empty_list()
    test_enable_job_raises_for_unknown_name()
    test_enable_job_skips_sync_when_requested()
    test_disable_job_passes_custom_jobs_file_to_sync()
    test_default_jobs_file_calls_sync_units_with_no_override()
    test_sync_units_invokes_platform_backend()
    test_sync_units_passes_jobs_file_override()
    test_test_job_reports_pass_when_run_record_reports_success()
    test_test_job_reports_failure_when_scheduler_rejects_trigger()
    test_test_job_reports_failure_when_trigger_accepted_but_job_actually_failed()
    test_test_job_times_out_if_no_new_run_record_appears()
    test_test_job_ignores_a_stale_run_record_from_a_previous_run()
    test_test_job_detects_fresh_run_despite_same_second_finished_at_collision()
    test_status_lists_ai_timers()
    test_cli_sync_subcommand_dispatches_to_sync_units()
    test_cli_test_subcommand_dispatches_to_test_job()
    test_cli_test_subcommand_exits_zero_when_test_job_succeeds()
    test_cli_test_subcommand_exits_nonzero_when_test_job_fails()
    test_cli_view_logs_subcommand_passes_lines_flag()
    test_cli_status_subcommand_dispatches_to_status()
    test_cli_enable_subcommand_passes_jobs_file_and_no_sync()
    test_cli_reports_error_and_exits_nonzero_on_exception()
    test_cli_unknown_command_is_rejected()
    test_cli_requires_a_subcommand()
    print("\nAll tests passed (note: capsys-based tests require pytest).")

from __future__ import annotations

import json
import os
import subprocess
import time
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _job_executor as job_executor
    from .. import _run_record
    from .._run_record import (
        JobRunRecord,
        evaluate_success_contract,
        read_inner_status,
        write_run_record,
    )
else:
    import _job_executor as job_executor
    import _run_record
    from _run_record import (
        JobRunRecord,
        evaluate_success_contract,
        read_inner_status,
        write_run_record,
    )


def test_direct_executor_entrypoint_finds_repo_package_without_pythonpath():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "_job_executor.py"),
            "--help",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0
    assert "--jobs-file" in result.stdout
    assert "--log-dir" in result.stdout


def test_main_forwards_explicit_log_dir(tmp_path):
    jobs_file = tmp_path / "jobs.yaml"
    log_dir = tmp_path / "logs"

    with mock.patch.object(job_executor, "run_job", return_value=0) as run_job:
        assert job_executor.main(
            [
                "--jobs-file",
                str(jobs_file),
                "--job",
                "demo",
                "--log-dir",
                str(log_dir),
            ]
        ) == 0

    run_job.assert_called_once_with(
        jobs_file=jobs_file, job_name="demo", log_dir=log_dir
    )


def test_parse_command_splits_leading_environment_assignments():
    env, argv = job_executor.parse_command("A=1 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1/bus invoke-skill daily-plan")

    assert env == {"A": "1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus"}
    assert argv == ["invoke-skill", "daily-plan"]


def test_parse_command_preserves_quoted_arguments():
    env, argv = job_executor.parse_command('GREETING="hello world" /usr/bin/echo "$GREETING"')

    assert env == {"GREETING": "hello world"}
    assert argv == ["/usr/bin/echo", "$GREETING"]


def test_parse_command_preserves_windows_backslash_paths():
    env, argv = job_executor.parse_command(
        r'"C:\Program Files\Tool\tool.exe" --flag C:\Users\tester\out.txt',
        platform="win32",
    )

    assert env == {}
    assert argv == [r"C:\Program Files\Tool\tool.exe", "--flag", r"C:\Users\tester\out.txt"]


def test_parse_command_rejects_empty_command():
    try:
        job_executor.parse_command("ONLY_ENV=1")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "executable" in str(e)


def test_resolve_executable_uses_pathext_resolution_on_windows():
    with mock.patch.object(job_executor.shutil, "which", return_value=r"C:\Tools\invoke-skill.bat") as which:
        argv = job_executor.resolve_executable(
            ["invoke-skill", "daily-plan"],
            {"PATH": r"C:\Tools"},
            platform="win32",
        )

    assert argv == [r"C:\Tools\invoke-skill.bat", "daily-plan"]
    which.assert_called_once_with("invoke-skill", path=r"C:\Tools")


def test_resolve_executable_leaves_unix_commands_unchanged():
    with mock.patch.object(job_executor.shutil, "which") as which:
        argv = job_executor.resolve_executable(["invoke-skill", "daily-plan"], {"PATH": "/tmp"}, platform="linux")

    assert argv == ["invoke-skill", "daily-plan"]
    which.assert_not_called()


def test_run_job_appends_output_without_shell(tmp_path):
    jobs_file = tmp_path / "jobs.yaml"
    jobs_file.write_text(
        "jobs:\n"
        "  - name: demo\n"
        "    description: Demo\n"
        "    command: FOO=bar invoke-skill demo\n"
        "    schedule: '0 * * * *'\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed) as run:
        assert job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=tmp_path / "logs") == 0

    kwargs = run.call_args.kwargs
    assert run.call_args.args[0] == ["invoke-skill", "demo"]
    assert kwargs.get("shell") is not True
    assert kwargs["env"]["FOO"] == "bar"
    assert (tmp_path / "logs" / "demo" / "run.log").exists()


# ── _run_record: JobRunRecord / write_run_record / evaluate_success_contract ──

def test_write_run_record_creates_run_boundary_markers(tmp_path):
    record = JobRunRecord(job_name="email-triage", started_at="2026-07-27T00:00:00Z",
                           finished_at="2026-07-27T00:00:05Z", process_exit_code=0,
                           inner_status="ok", success=True)
    write_run_record(log_dir=tmp_path, record=record)
    latest = json.loads((tmp_path / "email-triage" / "latest.json").read_text())
    assert latest["success"] is True
    assert latest["started_at"] == "2026-07-27T00:00:00Z"


def test_evaluate_success_contract_fails_when_inner_status_is_error_despite_zero_exit():
    contract = {"require_inner_status": "ok"}
    result = evaluate_success_contract(process_exit_code=0, inner_status="error", contract=contract)
    assert result.success is False


def test_evaluate_success_contract_passes_when_no_contract_declared():
    result = evaluate_success_contract(process_exit_code=0, inner_status=None, contract={})
    assert result.success is True


def test_evaluate_success_contract_fails_on_nonzero_exit_regardless_of_contract():
    result = evaluate_success_contract(process_exit_code=1, inner_status="ok", contract={})
    assert result.success is False
    assert "1" in result.reason


def test_tolerated_exit_does_not_excuse_a_missing_inner_status():
    """A tolerated exit code excuses the exit code, not the unfinished work.

    email-triage's contract pairs `require_inner_status: ok` with
    `ignore_exit_codes: [1]` and a pattern list including "Please try again".
    Tolerance used to return success before the inner status was read, so a
    run that stopped mid-way and never advanced its watermark scored a full
    success as long as its output contained that very common phrase.
    """
    contract = {
        "require_inner_status": "ok",
        "ignore_exit_codes": [1],
        "ignore_exit_log_patterns": ["Please try again"],
    }
    result = evaluate_success_contract(
        process_exit_code=1,
        inner_status="pending",
        contract=contract,
        run_output="rate limited. Please try again later.\n",
    )
    assert result.success is False
    # Both facts have to survive into the reason: the exit was tolerated AND
    # the job never reported finishing. Reporting only one sends whoever
    # reads the health check after the wrong problem.
    assert "tolerated" in result.reason
    assert "pending" in result.reason


def test_tolerated_exit_still_succeeds_when_inner_status_is_satisfied():
    """The tolerance path must keep working for a run that did finish."""
    contract = {
        "require_inner_status": "ok",
        "ignore_exit_codes": [1],
        "ignore_exit_log_patterns": ["Please try again"],
    }
    result = evaluate_success_contract(
        process_exit_code=1,
        inner_status="ok",
        contract=contract,
        run_output="transient hiccup. Please try again later.\n",
    )
    assert result.success is True
    assert "tolerated" in result.reason


def test_failure_reason_carries_the_error_from_the_run_output():
    """A bare exit code sent six days of outages to a log nobody read.

    The cause was in the job's own log the whole time; every reporting hop
    above it replaced that line with "process exit code 1". The record is
    where the two paths meet -- the health-check log renders `reason`
    verbatim -- so carrying the salient line here is what makes the failure
    legible without opening the run log.
    """
    output = (
        "warning: certification-status-unavailable: precomputed status\n"
        "Error: failed to read model instructions file "
        "/gone/agents/assistant.md: No such file or directory (os error 2)\n"
    )
    result = evaluate_success_contract(
        process_exit_code=1, inner_status=None, contract={}, run_output=output
    )
    assert result.success is False
    assert "process exit code 1" in result.reason
    assert "failed to read model instructions file" in result.reason


def test_failure_reason_carries_the_error_when_only_inner_status_failed():
    """The exit code is 0 here, so the run output is the only evidence."""
    output = "error: lists-read failed: dispatcher requires the exact repository configuration path\n"
    result = evaluate_success_contract(
        process_exit_code=0,
        inner_status="error",
        contract={"require_inner_status": "ok"},
        run_output=output,
    )
    assert result.success is False
    assert "lists-read failed" in result.reason


def test_failure_reason_skips_traceback_scaffolding_and_caps_length():
    output = (
        "Traceback (most recent call last):\n"
        '  File "/x/y.py", line 3, in run\n'
        "    ^^^^^^^^\n"
        "socket.gaierror: " + "z" * 400 + "\n"
    )
    result = evaluate_success_contract(
        process_exit_code=1, inner_status=None, contract={}, run_output=output
    )
    assert "socket.gaierror" in result.reason
    assert 'File "' not in result.reason
    assert len(result.reason) < 300


def test_failure_reason_is_unchanged_when_the_run_produced_no_usable_output():
    result = evaluate_success_contract(
        process_exit_code=1, inner_status=None, contract={}, run_output="\n\n"
    )
    assert result.reason == "process exit code 1"


def test_evaluate_success_contract_passes_when_inner_status_matches_required():
    contract = {"require_inner_status": "ok"}
    result = evaluate_success_contract(process_exit_code=0, inner_status="ok", contract=contract)
    assert result.success is True


# ── run_job: run boundary markers, run-record writing, success contract wiring ──

def _write_jobs_file(tmp_path: Path, *, command: str, success: dict | None = None) -> Path:
    jobs_file = tmp_path / "jobs.yaml"
    success_yaml = ""
    if success is not None:
        lines = "\n".join(f"      {k}: {v}" for k, v in success.items())
        success_yaml = f"    success:\n{lines}\n"
    jobs_file.write_text(
        "jobs:\n"
        "  - name: demo\n"
        "    description: Demo\n"
        f"    command: {command}\n"
        "    schedule: '0 * * * *'\n"
        "    enabled: true\n"
        f"{success_yaml}",
        encoding="utf-8",
    )
    return jobs_file


def test_run_job_writes_run_boundary_markers_around_execution(tmp_path):
    jobs_file = _write_jobs_file(tmp_path, command="invoke-skill demo")
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=tmp_path / "logs")

    log_text = (tmp_path / "logs" / "demo" / "run.log").read_text()
    assert "--- RUN START ---" in log_text
    assert "--- RUN END (success=True) ---" in log_text
    start_idx = log_text.index("--- RUN START ---")
    end_idx = log_text.index("--- RUN END")
    assert start_idx < end_idx


def test_run_job_writes_run_boundary_markers_on_failure(tmp_path):
    jobs_file = _write_jobs_file(tmp_path, command="invoke-skill demo")
    completed = subprocess.CompletedProcess(args=[], returncode=1)

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed):
        exit_code = job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=tmp_path / "logs")

    assert exit_code == 1
    log_text = (tmp_path / "logs" / "demo" / "run.log").read_text()
    assert "--- RUN START ---" in log_text
    assert "--- RUN END (success=False) ---" in log_text


def test_run_job_writes_run_record_with_success_from_exit_code(tmp_path):
    jobs_file = _write_jobs_file(tmp_path, command="invoke-skill demo")
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    log_dir = tmp_path / "logs"

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is True
    assert record["process_exit_code"] == 0
    assert record["job_name"] == "demo"


def test_run_job_fails_run_record_when_inner_status_does_not_match_contract(tmp_path, monkeypatch):
    jobs_file = _write_jobs_file(
        tmp_path, command="invoke-skill demo", success={"require_inner_status": "ok"}
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    log_dir = tmp_path / "logs"
    skills_root = tmp_path / "skills"
    (skills_root / "demo" / "state").mkdir(parents=True)
    (skills_root / "demo" / "state" / "status.json").write_text(
        json.dumps({"result": "error", "message": "boom"})
    )

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed), \
         mock.patch.object(job_executor, "SKILLS_ROOT", skills_root):
        exit_code = job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    # process exit code is still 0 (the wrapper launched fine); success is
    # what tells the difference.
    assert exit_code == 0
    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is False
    assert record["inner_status"] == "error"

    log_text = (log_dir / "demo" / "run.log").read_text()
    assert "--- RUN END (success=False) ---" in log_text


def test_run_job_writes_run_end_and_run_record_when_subprocess_fails_to_spawn(tmp_path):
    """A missing/misconfigured executable raises OSError (e.g.
    FileNotFoundError, PermissionError) from subprocess.run() itself --
    before any CompletedProcess exists. run_job() must still close out the
    RUN START marker with a RUN END marker and write a run record, instead
    of letting the exception propagate past both."""
    jobs_file = _write_jobs_file(tmp_path, command="invoke-skill demo")
    log_dir = tmp_path / "logs"

    with mock.patch.object(job_executor.subprocess, "run",
                            side_effect=FileNotFoundError("no such file: invoke-skill")):
        exit_code = job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    assert exit_code != 0

    log_text = (log_dir / "demo" / "run.log").read_text()
    assert "--- RUN START ---" in log_text
    assert "--- RUN END (success=False) ---" in log_text
    start_idx = log_text.index("--- RUN START ---")
    end_idx = log_text.index("--- RUN END")
    assert start_idx < end_idx

    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is False
    assert record["inner_status"] is None
    assert "spawn" in record["reason"].lower()


# ── read_inner_status: email-triage's relocated state root ─────────────────────
#
# email-triage's actual status.json moved off the SKILLS_ROOT/<job>/state/
# convention onto officina.common.famulus_paths' shared state root (see
# email-triage/_rtx/_watermark_writer.py's default_state_dir()). If
# read_inner_status() still only ever looks under SKILLS_ROOT/<job>/state/,
# it can never find email-triage's real status.json, so evaluate_success_
# contract() (which jobs.yaml wires up via `require_inner_status: ok` for
# email-triage) would mark every real run as failed regardless of outcome.

def test_read_inner_status_reads_email_triage_from_its_relocated_state_root(tmp_path, monkeypatch):
    relocated_state_dir = tmp_path / "relocated-famulus-state" / "email-triage"
    relocated_state_dir.mkdir(parents=True)
    (relocated_state_dir / "status.json").write_text(json.dumps({"result": "ok"}))

    monkeypatch.setenv("EMAIL_TRIAGE_STATE_DIR", str(relocated_state_dir))

    # The stale/legacy SKILLS_ROOT/<job>/state/status.json location, if it
    # existed, must NOT be what's read for email-triage anymore.
    skills_root = tmp_path / "skills"
    legacy_state_dir = skills_root / "email-triage" / "state"
    legacy_state_dir.mkdir(parents=True)
    (legacy_state_dir / "status.json").write_text(json.dumps({"result": "error"}))

    status = read_inner_status(skills_root=skills_root, job_name="email-triage")

    assert status == "ok"


def test_read_inner_status_still_uses_legacy_convention_for_other_jobs(tmp_path, monkeypatch):
    monkeypatch.delenv("EMAIL_TRIAGE_STATE_DIR", raising=False)
    skills_root = tmp_path / "skills"
    state_dir = skills_root / "some-other-job" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "status.json").write_text(json.dumps({"result": "ok"}))

    status = read_inner_status(skills_root=skills_root, job_name="some-other-job")

    assert status == "ok"


# The cell production actually occupies: email-triage, EMAIL_TRIAGE_STATE_DIR
# UNSET. The two tests above cover email-triage with the variable SET and
# another job with it unset, so this branch -- the only one a scheduled run
# takes -- went unexercised, and the `sys.platform` call it reaches raised
# NameError for four days while every triage run died after finishing its work.
# Do not "simplify" this by setting the env var; setting it is what hid the bug.

def test_read_inner_status_resolves_email_triage_without_the_env_override(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("EMAIL_TRIAGE_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    resolved = _run_record._resolve_job_state_dir(
        skills_root=tmp_path / "skills", job_name="email-triage"
    )
    resolved.mkdir(parents=True)
    (resolved / "status.json").write_text(json.dumps({"result": "ok"}))

    status = read_inner_status(
        skills_root=tmp_path / "skills", job_name="email-triage"
    )

    assert status == "ok"


# ── in-flight marker and timeout ────────────────────────────────────────────────

def test_run_job_clears_the_in_flight_marker_on_normal_completion(tmp_path):
    jobs_file = _write_jobs_file(tmp_path, command="invoke-skill demo")
    log_dir = tmp_path / "logs"
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    assert not job_executor.running_marker_path(
        log_dir=log_dir, job_name="demo"
    ).exists()


def test_run_job_leaves_the_in_flight_marker_when_the_executor_is_killed(tmp_path):
    """The real failure: SIGKILL mid-run, no record written.

    run.log's mtime is already fresh from "--- RUN START ---", so without a
    surviving marker the health check reads the job as healthy while it never
    completed.
    """
    jobs_file = _write_jobs_file(
        tmp_path,
        command=json.dumps(
            f'"{sys.executable}" -c "import time; time.sleep(300)"'
        ),
    )
    log_dir = tmp_path / "logs"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(job_executor.__file__).parent)!r})\n"
        f"sys.path.insert(0, {str(Path(job_executor.__file__).parents[3] / 'src')!r})\n"
        "import _job_executor as je\n"
        f"je.run_job(jobs_file={str(jobs_file)!r}, job_name='demo', "
        f"log_dir=__import__('pathlib').Path({str(log_dir)!r}))\n",
        encoding="utf-8",
    )

    child = subprocess.Popen([sys.executable, str(runner)])
    marker = job_executor.running_marker_path(log_dir=log_dir, job_name="demo")
    for _ in range(100):  # wait for the marker to appear, then kill hard
        if marker.exists():
            break
        time.sleep(0.05)
    child.kill()
    child.wait(timeout=10)

    assert marker.exists(), "killed run left no evidence it never completed"
    assert not (log_dir / "demo" / "latest.json").exists(), (
        "a killed run must not leave a completed-looking record"
    )


def test_run_job_kills_and_records_a_job_that_exceeds_its_timeout(tmp_path, monkeypatch):
    jobs_file = _write_jobs_file(
        tmp_path,
        command=json.dumps(
            f'"{sys.executable}" -c "import time; time.sleep(60)"'
        ),
    )
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(job_executor, "JOB_TIMEOUT_SECONDS", 1)

    exit_code = job_executor.run_job(
        jobs_file=jobs_file, job_name="demo", log_dir=log_dir
    )

    assert exit_code == job_executor.TIMEOUT_EXIT_CODE
    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is False
    assert "timeout" in record["reason"]
    # A timed-out run DID complete its bookkeeping, so the marker is cleared.
    assert not job_executor.running_marker_path(
        log_dir=log_dir, job_name="demo"
    ).exists()


def test_read_inner_status_ignores_a_status_left_by_an_earlier_run(tmp_path):
    """A previous run's status.json must not vouch for the current run.

    Without run correlation, `require_inner_status: ok` degrades to "exit 0
    plus a stale artifact": a run that fails before writing its own status
    inherits yesterday's ok and records success.
    """
    from datetime import datetime, timedelta, timezone

    state = tmp_path / "some-job" / "state"
    state.mkdir(parents=True)
    status = state / "status.json"
    status.write_text(json.dumps({"result": "ok"}), encoding="utf-8")

    stale = time.time() - 86400
    os.utime(status, (stale, stale))
    run_started = datetime.now(timezone.utc)

    assert read_inner_status(
        skills_root=tmp_path, job_name="some-job", not_before=run_started
    ) is None
    # Without the run's start time the old behaviour is preserved for callers
    # that genuinely want "whatever is on disk".
    assert read_inner_status(skills_root=tmp_path, job_name="some-job") == "ok"


def test_read_inner_status_accepts_a_status_written_by_this_run(tmp_path):
    from datetime import datetime, timezone

    state = tmp_path / "some-job" / "state"
    state.mkdir(parents=True)
    (state / "status.json").write_text(json.dumps({"result": "ok"}), encoding="utf-8")
    run_started = datetime.now(timezone.utc)

    assert read_inner_status(
        skills_root=tmp_path, job_name="some-job", not_before=run_started
    ) == "ok"


def test_run_job_fails_when_a_contract_job_writes_no_status_at_all(tmp_path):
    """The daily-plan failure shape: exit 0, nothing accomplished.

    The agent CLI exits 0 even when it could not reach the skill's
    interfaces, so with an exit-code-only contract such a run recorded
    success:true. Requiring a self-reported status makes "did nothing"
    distinguishable from "worked".
    """
    jobs_file = _write_jobs_file(
        tmp_path, command="invoke-skill demo", success={"require_inner_status": "ok"}
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    log_dir = tmp_path / "logs"
    skills_root = tmp_path / "skills"
    (skills_root / "demo" / "state").mkdir(parents=True)  # state dir, no status.json

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed), \
         mock.patch.object(job_executor, "SKILLS_ROOT", skills_root):
        exit_code = job_executor.run_job(
            jobs_file=jobs_file, job_name="demo", log_dir=log_dir
        )

    assert exit_code == 0, "the launcher itself succeeded"
    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is False, "a run that reported nothing is not a success"
    assert record["inner_status"] is None


def test_run_job_ignores_a_previous_runs_status_under_a_contract(tmp_path):
    """End-to-end guard for the stale-status false green."""
    jobs_file = _write_jobs_file(
        tmp_path, command="invoke-skill demo", success={"require_inner_status": "ok"}
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    log_dir = tmp_path / "logs"
    skills_root = tmp_path / "skills"
    state = skills_root / "demo" / "state"
    state.mkdir(parents=True)
    status = state / "status.json"
    status.write_text(json.dumps({"result": "ok"}))
    stale = time.time() - 86400
    os.utime(status, (stale, stale))

    with mock.patch.object(job_executor.subprocess, "run", return_value=completed), \
         mock.patch.object(job_executor, "SKILLS_ROOT", skills_root):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is False, "yesterday's status must not vouch for today"


# ── tolerance belongs to the run, not to the check ─────────────────────────────

def test_tolerated_exit_code_is_recorded_as_success(tmp_path):
    """A known-transient failure is decided once, by the run that saw it."""
    jobs_file = _write_jobs_file(
        tmp_path,
        command="invoke-skill demo",
        success={
            "ignore_exit_codes": "[1]",
            "ignore_exit_log_patterns": '["hit your usage limit"]',
        },
    )
    log_dir = tmp_path / "logs"

    def fake_run(argv, **kwargs):
        kwargs["stdout"].write("ERROR: you have hit your usage limit\n")
        return subprocess.CompletedProcess(args=argv, returncode=1)

    with mock.patch.object(job_executor.subprocess, "run", side_effect=fake_run):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is True
    assert "tolerated" in record["reason"]


def test_an_earlier_runs_message_cannot_excuse_a_later_failure(tmp_path):
    """The old check scanned a fixed tail of the cumulative log.

    A quota message from a previous run could therefore excuse an unrelated
    later failure. Tolerance now sees only the output of the run it judges.
    """
    jobs_file = _write_jobs_file(
        tmp_path,
        command="invoke-skill demo",
        success={
            "ignore_exit_codes": "[1]",
            "ignore_exit_log_patterns": '["hit your usage limit"]',
        },
    )
    log_dir = tmp_path / "logs"

    def tolerated_run(argv, **kwargs):
        kwargs["stdout"].write("ERROR: you have hit your usage limit\n")
        return subprocess.CompletedProcess(args=argv, returncode=1)

    def unrelated_failure(argv, **kwargs):
        kwargs["stdout"].write("Traceback: something else broke entirely\n")
        return subprocess.CompletedProcess(args=argv, returncode=1)

    with mock.patch.object(job_executor.subprocess, "run", side_effect=tolerated_run):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)
    with mock.patch.object(job_executor.subprocess, "run", side_effect=unrelated_failure):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    record = json.loads((log_dir / "demo" / "latest.json").read_text())
    assert record["success"] is False, (
        "a previous run's quota message must not excuse this failure"
    )


def test_run_log_is_rotated_once_it_exceeds_its_cap(tmp_path, monkeypatch):
    """Unbounded run logs were the only thing limiting disk use."""
    jobs_file = _write_jobs_file(tmp_path, command="invoke-skill demo")
    log_dir = tmp_path / "logs"
    log_file = log_dir / "demo" / "run.log"
    log_file.parent.mkdir(parents=True)
    monkeypatch.setattr(job_executor, "MAX_RUN_LOG_BYTES", 100)
    log_file.write_text("x" * 500)

    completed = subprocess.CompletedProcess(args=[], returncode=0)
    with mock.patch.object(job_executor.subprocess, "run", return_value=completed):
        job_executor.run_job(jobs_file=jobs_file, job_name="demo", log_dir=log_dir)

    assert (log_dir / "demo" / "run.log.1").read_text() == "x" * 500
    assert "RUN START" in log_file.read_text()
    assert len(log_file.read_text()) < 500, "the live log restarted after rotation"

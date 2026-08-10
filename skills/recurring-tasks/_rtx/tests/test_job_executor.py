from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _job_executor as job_executor
    from .._run_record import (
        JobRunRecord,
        evaluate_success_contract,
        read_inner_status,
        write_run_record,
    )
else:
    import _job_executor as job_executor
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
            "-m",
            "test_support.runtime_module",
            str(Path(__file__).resolve().parents[1] / "_job_executor.py"),
            "--",
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

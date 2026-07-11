from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

import _job_executor as job_executor


def test_parse_command_splits_leading_environment_assignments():
    env, argv = job_executor.parse_command("A=1 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1/bus invoke-skill daily-plan")

    assert env == {"A": "1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus"}
    assert argv == ["invoke-skill", "daily-plan"]


def test_parse_command_preserves_quoted_arguments():
    env, argv = job_executor.parse_command('GREETING="hello world" /usr/bin/echo "$GREETING"')

    assert env == {"GREETING": "hello world"}
    assert argv == ["/usr/bin/echo", "$GREETING"]


def test_parse_command_rejects_empty_command():
    try:
        job_executor.parse_command("ONLY_ENV=1")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "executable" in str(e)


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

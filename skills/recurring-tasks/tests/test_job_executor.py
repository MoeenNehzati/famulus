from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

import _job_executor as job_executor


def test_direct_executor_entrypoint_finds_repo_package_without_pythonpath():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "_rtx" / "_job_executor.py"), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0
    assert "--jobs-file" in result.stdout


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

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "famulus-refresh"


def _dry_run_commands(stdout: str) -> list[list[str]]:
    return [
        shlex.split(line[2:])
        for line in stdout.splitlines()
        if line.startswith("+ ")
    ]


def _bash_executable(env: dict[str, str]) -> str:
    if sys.platform != "win32":
        return "bash"
    git_executable = shutil.which("git", path=env.get("PATH"))
    if git_executable is None:
        raise FileNotFoundError("Git for Windows is required to run famulus-refresh")
    git_bash = Path(git_executable).parent.parent / "bin" / "bash.exe"
    if not git_bash.is_file():
        raise FileNotFoundError(f"Git Bash is unavailable at {git_bash}")
    return str(git_bash)


def run_refresh(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.update(env or {})
    return subprocess.run(
        [_bash_executable(process_env), str(SCRIPT), *args],
        cwd=ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_windows_refresh_bypasses_system_wsl_bash_launcher(
    monkeypatch,
    tmp_path: Path,
) -> None:
    system_bash = tmp_path / "Windows" / "System32" / "bash.exe"
    git_executable = tmp_path / "Program Files" / "Git" / "cmd" / "git.exe"
    git_bash = tmp_path / "Program Files" / "Git" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.touch()
    runner_path = os.pathsep.join((str(system_bash.parent), str(git_executable.parent)))
    commands: list[list[str]] = []

    def find_executable(name: str, *, path: str | None = None) -> str | None:
        assert path == runner_path
        return str(git_executable) if name == "git" else str(system_bash)

    def launch(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[0] == str(git_bash):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        wsl_prompt = "Use 'wsl.exe --install <Distro>' to install."
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=wsl_prompt.encode("utf-16le").decode("utf-8", errors="replace"),
            stderr="",
        )

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", find_executable)
    monkeypatch.setattr(subprocess, "run", launch)

    result = run_refresh("--codex", "--github", "--dry-run", env={"PATH": runner_path})

    assert result.returncode == 0, repr(result.stdout)
    assert commands == [[str(git_bash), str(SCRIPT), "--codex", "--github", "--dry-run"]]


def test_default_dry_run_refreshes_both_from_local_and_preserves_plugin_data(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / r"Famulus Checkout\windows-path"
    checkout.mkdir(parents=True)
    result = run_refresh("--dry-run", env={"AI": str(checkout)})

    assert result.returncode == 0, result.stderr
    commands = _dry_run_commands(result.stdout)
    assert ["codex", "plugin", "remove", "famulus@nullkit", "--json"] in commands
    assert [
        "codex",
        "plugin",
        "marketplace",
        "add",
        str(checkout),
        "--json",
    ] in commands
    assert ["codex", "plugin", "add", "famulus@nullkit", "--json"] in commands
    assert [
        "claude",
        "plugin",
        "uninstall",
        "famulus@nullkit",
        "--keep-data",
    ] in commands
    assert [
        "claude",
        "plugin",
        "marketplace",
        "add",
        str(checkout),
        "--scope",
        "user",
    ] in commands
    assert [
        "claude",
        "plugin",
        "install",
        "famulus@nullkit",
        "--scope",
        "user",
        "-y",
    ] in commands
    assert not any(command[:2] == ["rm", "-rf"] for command in commands)


def test_reset_refuses_a_codex_data_path_outside_the_agent_plugin_root(tmp_path: Path) -> None:
    result = run_refresh(
        "--codex",
        "--reset-plugin-data",
        "--dry-run",
        env={
            "AI": str(ROOT),
            "CODEX_HOME": str(tmp_path / "codex"),
            "FAMULUS_CODEX_PLUGIN_DATA": str(tmp_path / "wrong-place"),
        },
    )

    assert result.returncode != 0
    assert "refusing unsafe Codex plugin-data path" in result.stderr

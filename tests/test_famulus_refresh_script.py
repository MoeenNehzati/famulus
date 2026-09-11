from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from test_support.git_repository import GitTestRepository


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


def _write_fake_host(tmp_path: Path, name: str, body: str) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / name
    executable.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    executable.chmod(0o755)
    log = tmp_path / f"{name}.log"
    return fake_bin, log


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
    package = checkout / "_build" / "plugin"
    assert ["rm", "-rf", "--", "_build/plugin.tmp"] in commands
    assert [
        "git",
        "archive",
        "--format=tar",
        "--output=_build/plugin.tar",
        "HEAD",
    ] in commands
    assert ["codex", "plugin", "remove", "famulus@nullkit", "--json"] in commands
    assert any(
        command[:4] == ["codex", "plugin", "marketplace", "add"]
        and Path(command[4]) == package
        and command[5:] == ["--json"]
        for command in commands
    )
    assert ["codex", "plugin", "add", "famulus@nullkit", "--json"] in commands
    assert [
        "claude",
        "plugin",
        "uninstall",
        "famulus@nullkit",
        "--scope",
        "user",
        "--keep-data",
    ] in commands
    assert [
        "claude",
        "plugin",
        "marketplace",
        "add",
        str(package),
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
    assert all(
        command[-1].startswith("_build/")
        for command in commands
        if command[:2] == ["rm", "-rf"]
    )


def test_local_refresh_packages_only_committed_files(tmp_path: Path) -> None:
    repository = GitTestRepository.create(tmp_path / "checkout")
    checkout = repository.root
    (checkout / ".gitignore").write_text("_build/\nignored/\n", encoding="utf-8")
    (checkout / "tracked.txt").write_text("included\n", encoding="utf-8")
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "runtime.txt").write_text("excluded\n", encoding="utf-8")
    repository.git("add", ".")
    repository.git("commit", "-qm", "fixture")
    (checkout / "tracked.txt").write_text("uncommitted\n", encoding="utf-8")
    fake_bin, log = _write_fake_host(
        tmp_path,
        "codex",
        'printf "%s\\n" "$*" >> "$FAKE_CODEX_LOG"\n',
    )
    path = os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))

    result = run_refresh(
        "--codex",
        "--local",
        env={"AI": str(checkout), "FAKE_CODEX_LOG": str(log), "PATH": path},
    )

    package = checkout / "_build" / "plugin"
    assert result.returncode == 0, result.stderr
    assert "uncommitted tracked changes are excluded" in result.stderr
    assert (package / "tracked.txt").read_text(encoding="utf-8") == "included\n"
    assert not (package / "ignored").exists()
    marketplace_lines = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("plugin marketplace add ")
    ]
    assert len(marketplace_lines) == 1
    assert Path(
        marketplace_lines[0].removeprefix("plugin marketplace add ").removesuffix(
            " --json"
        )
    ) == package


def test_claude_refresh_warns_for_absent_state_and_continues_installing(
    tmp_path: Path,
) -> None:
    fake_bin, log = _write_fake_host(
        tmp_path,
        "claude",
        """
printf '%s\n' "$*" >> "$FAKE_CLAUDE_LOG"
case "$*" in
    "plugin uninstall famulus@nullkit --scope user --keep-data")
        printf '%s\n' 'Plugin "famulus@nullkit" is not installed in user scope. Use --scope to specify the correct scope.' >&2
        exit 1
        ;;
    "plugin marketplace remove nullkit --scope user")
        printf "%s\n" "Marketplace 'nullkit' not found" >&2
        exit 1
        ;;
esac
""",
    )
    path = os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))

    result = run_refresh(
        "--claude",
        "--github",
        env={"FAKE_CLAUDE_LOG": str(log), "PATH": path},
    )

    assert result.returncode == 0, result.stderr
    assert 'Plugin "famulus@nullkit" is not installed in user scope' in result.stderr
    assert "warning: could not remove Claude Famulus plugin; continuing" in result.stderr
    assert "Marketplace 'nullkit' not found" in result.stderr
    assert "warning: could not remove Claude nullkit marketplace; continuing" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "plugin uninstall famulus@nullkit --scope user --keep-data",
        "plugin marketplace remove nullkit --scope user",
        "plugin marketplace add MoeenNehzati/famulus --scope user",
        "plugin install famulus@nullkit --scope user -y",
        "plugin list --json",
    ]


def test_claude_refresh_reports_other_removal_failures_and_continues(
    tmp_path: Path,
) -> None:
    fake_bin, log = _write_fake_host(
        tmp_path,
        "claude",
        """
printf '%s\n' "$*" >> "$FAKE_CLAUDE_LOG"
if [[ "$*" == "plugin uninstall famulus@nullkit --scope user --keep-data" ]]; then
    printf '%s\n' 'permission denied' >&2
    exit 1
fi
""",
    )
    path = os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))

    result = run_refresh(
        "--claude",
        "--github",
        env={"FAKE_CLAUDE_LOG": str(log), "PATH": path},
    )

    assert result.returncode == 0, result.stderr
    assert "permission denied" in result.stderr
    assert "warning: could not remove Claude Famulus plugin; continuing" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "plugin uninstall famulus@nullkit --scope user --keep-data",
        "plugin marketplace remove nullkit --scope user",
        "plugin marketplace add MoeenNehzati/famulus --scope user",
        "plugin install famulus@nullkit --scope user -y",
        "plugin list --json",
    ]


def test_codex_refresh_reports_removal_failures_and_continues_installing(
    tmp_path: Path,
) -> None:
    fake_bin, log = _write_fake_host(
        tmp_path,
        "codex",
        """
printf '%s\n' "$*" >> "$FAKE_CODEX_LOG"
case "$*" in
    "plugin remove famulus@nullkit --json")
        printf '%s\n' 'Error: Famulus plugin removal failed' >&2
        exit 1
        ;;
    "plugin marketplace remove nullkit --json")
        printf '%s\n' 'Error: marketplace `nullkit` is not configured or installed' >&2
        exit 1
        ;;
esac
""",
    )
    path = os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))

    result = run_refresh(
        "--codex",
        "--github",
        env={"FAKE_CODEX_LOG": str(log), "PATH": path},
    )

    assert result.returncode == 0, result.stderr
    assert "Famulus plugin removal failed" in result.stderr
    assert "warning: could not remove Codex Famulus plugin; continuing" in result.stderr
    assert "marketplace `nullkit` is not configured or installed" in result.stderr
    assert "warning: could not remove Codex nullkit marketplace; continuing" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "plugin remove famulus@nullkit --json",
        "plugin marketplace remove nullkit --json",
        "plugin marketplace add MoeenNehzati/famulus --json",
        "plugin add famulus@nullkit --json",
        "plugin list --json",
    ]


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

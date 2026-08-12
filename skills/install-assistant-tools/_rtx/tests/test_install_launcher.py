from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from .._install_launcher import platform_launcher_installer
from .._install_launcher import _windows_launcher as windows_launcher
from .._install_launcher._base_launcher import (
    LauncherBundleSpec,
    LauncherFileSpec,
    LauncherInstallerBase,
)
from .._install_launcher._linux_launcher import _unix_dispatcher_content
from .._install_launcher._windows_launcher import (
    WindowsPythonNotFoundError,
    _windows_dispatcher_content,
)


@pytest.fixture
def tmp_repo_root(tmp_path: Path) -> Path:
    return tmp_path / "repo"


def test_generated_launcher_bundle_writes_file(tmp_path):
    installer = LauncherInstallerBase()
    result = installer.install_bundle(
        LauncherBundleSpec(
            name="demo",
            workflows=("test workflow",),
            files=[
                LauncherFileSpec(
                    destination=tmp_path / "bin" / "demo",
                    mode="generate",
                    content="#!/bin/sh\necho demo\n",
                    executable=True,
                )
            ],
        ),
        dry_run=False,
        manifest=None,
    )

    launcher = tmp_path / "bin" / "demo"
    assert result.status == "installed"
    assert launcher.read_text(encoding="utf-8") == "#!/bin/sh\necho demo\n"
    if os.name != "nt":
        assert launcher.stat().st_mode & 0o111


def test_copy_mode_replaces_old_symlink_with_real_file(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    old = tmp_path / "old"
    source.write_text("new\n", encoding="utf-8")
    old.write_text("old\n", encoding="utf-8")
    target.symlink_to(old)

    installer = LauncherInstallerBase()
    installer.install_bundle(
        LauncherBundleSpec(
            name="demo",
            workflows=("test workflow",),
            files=[
                LauncherFileSpec(
                    source=source,
                    destination=target,
                    mode="copy",
                )
            ],
        ),
        dry_run=False,
        manifest=None,
    )

    assert not target.is_symlink()
    assert target.read_text(encoding="utf-8") == "new\n"


def test_platform_installer_selects_host_implementation():
    assert type(platform_launcher_installer("linux")).__name__ == "LinuxLauncherInstaller"
    assert type(platform_launcher_installer("darwin")).__name__ == "OSXLauncherInstaller"
    assert type(platform_launcher_installer("win32")).__name__ == "WindowsLauncherInstaller"


def test_linux_dispatcher_and_invoke_skill_are_extensionless(tmp_path):
    installer = platform_launcher_installer("linux")
    repo_root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"

    dispatcher = installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run=False, home=home)
    invoke_skill = installer.install_invoke_skill_launcher(bin_dir, dry_run=False)

    assert dispatcher.status == "installed"
    assert invoke_skill.status == "installed"
    assert (bin_dir / "dispatcher").is_file()
    assert (bin_dir / "invoke-skill").is_file()
    if os.name != "nt":
        assert (bin_dir / "dispatcher").stat().st_mode & 0o111
    dispatcher_text = (bin_dir / "dispatcher").read_text(encoding="utf-8")
    invoke_text = (bin_dir / "invoke-skill").read_text(encoding="utf-8")
    assert dispatcher_text.startswith("#!/usr/bin/env python3")
    assert "bootstrap" in dispatcher_text and "resolvers" in dispatcher_text and "launch.py" in dispatcher_text
    assert "os.execv(RESOLVER" in dispatcher_text
    assert "'officina.dispatcher.cli'" in dispatcher_text
    assert str(repo_root) not in dispatcher_text
    assert sys.executable not in dispatcher_text
    assert invoke_text.startswith("#!/usr/bin/env python3")
    assert "os.execvp(command[0], command)" in invoke_text
    assert "_agent_invoker.sh" not in invoke_text
    assert sys.executable not in invoke_text


# famulus-skip: category=platform-contract; reason=the Linux wakeup bundle executes POSIX launchers; alternate=test_windows_dispatcher_and_invoke_skill_are_batch_launchers covers native Windows launchers
@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher execution")
def test_linux_wakeup_bundle_runs_both_names_through_managed_resolver(tmp_path, monkeypatch):
    """Removing either public command or forwarding it to the wrong module breaks the installed wakeup CLI."""
    installer = platform_launcher_installer("linux")
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    result = installer.install_wakeup_launcher(
        bin_dir,
        dry_run=False,
        home=home,
    )

    assert result.status == "installed"
    resolver = home / ".local" / "share" / "famulus" / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py"
    resolver.parent.mkdir(parents=True)
    resolver.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    resolver.chmod(0o755)

    for command in ("llm-wakeup", "lw"):
        launcher = bin_dir / command
        assert launcher.is_file()
        if os.name != "nt":
            assert launcher.stat().st_mode & 0o111
        completed = subprocess.run(
            [str(launcher), "doctor", "--example"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == [
            "-m",
            "officina.wakeup.cli",
            "doctor",
            "--example",
        ]
        content = launcher.read_text(encoding="utf-8")
        assert str(tmp_path / "repo") not in content
        assert sys.executable not in content


def test_osx_wakeup_bundle_installs_both_unix_commands(tmp_path, monkeypatch):
    """Overriding the macOS adapter without the wakeup bundle would drop its public commands."""
    installer = platform_launcher_installer("darwin")
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(sys, "platform", "darwin")

    result = installer.install_wakeup_launcher(
        bin_dir,
        dry_run=False,
        home=tmp_path / "home",
    )

    assert result.status == "installed"
    assert (bin_dir / "llm-wakeup").is_file()
    assert (bin_dir / "lw").is_file()
    assert not (bin_dir / "llm-wakeup.bat").exists()
    assert not (bin_dir / "lw.bat").exists()


def test_generated_dispatcher_does_not_embed_repo_root_or_sys_executable(tmp_repo_root, tmp_path):
    content = _unix_dispatcher_content(repo_root=tmp_repo_root, home=tmp_path / "unrelated-home")
    assert str(tmp_repo_root) not in content
    assert sys.executable not in content
    assert "launcher_entry" in content or "resolvers/v1/launch.py" in content or "resolvers" in content


def test_generated_launcher_content_has_no_legacy_vendor_paths(tmp_repo_root, tmp_path):
    content = _unix_dispatcher_content(repo_root=tmp_repo_root, home=tmp_path / "unrelated-home")
    for legacy_marker in ("openai-bundled", "release-2026-07"):
        assert legacy_marker not in content


def test_osx_uses_unix_launcher_contract(tmp_path):
    installer = platform_launcher_installer("darwin")
    repo_root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"

    dispatcher = installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run=False, home=home)

    assert dispatcher.status == "installed"
    assert (bin_dir / "dispatcher").is_file()
    assert not (bin_dir / "dispatcher.bat").exists()


def test_windows_dispatcher_and_invoke_skill_are_batch_launchers(tmp_path):
    installer = platform_launcher_installer("win32")
    repo_root = Path(r"C:\Users\tester\AI")
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"

    dispatcher = installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run=False, home=home)
    invoke_skill = installer.install_invoke_skill_launcher(bin_dir, dry_run=False)

    content = (bin_dir / "dispatcher.bat").read_text(encoding="utf-8")
    invoke_content = (bin_dir / "invoke-skill.bat").read_text(encoding="utf-8")
    assert dispatcher.status == "installed"
    assert "-m officina.dispatcher.cli %*" in content
    assert "bootstrap" in content and "resolvers" in content and "launch.py" in content
    assert "py -3" not in content
    # No longer embeds the repo checkout or a specific interpreter path: the
    # resolver (invoked here) reads current.json at launch time instead.
    assert r"C:\Users\tester\AI" not in content
    assert sys.executable not in content
    assert not (bin_dir / "dispatcher").exists()
    assert invoke_skill.status == "installed"
    assert "assistant --local --claude" in invoke_content
    assert "assistant --local --codex exec" in invoke_content
    assert not (bin_dir / "invoke-skill").exists()


def test_windows_wakeup_bundle_installs_both_batch_commands(tmp_path, monkeypatch):
    """Dropping the Windows alias or its resolver forwarding breaks the cross-platform command contract."""
    installer = platform_launcher_installer("win32")
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    with mock.patch.object(
        windows_launcher.shutil,
        "which",
        side_effect=lambda name: r"C:\Python312\python.exe" if name == "python" else None,
    ):
        result = installer.install_wakeup_launcher(
            bin_dir,
            dry_run=False,
            home=tmp_path / "home",
        )

    assert result.status == "installed"
    for command in ("llm-wakeup.bat", "lw.bat"):
        launcher = bin_dir / command
        assert launcher.is_file()
        content = launcher.read_text(encoding="utf-8")
        assert "-m officina.wakeup.cli %*" in content
        assert "bootstrap" in content
        assert "resolvers" in content
        assert "launch.py" in content
        assert r'"C:\Python312\python.exe"' in content
    assert not (bin_dir / "llm-wakeup").exists()
    assert not (bin_dir / "lw").exists()


def test_windows_dispatcher_bakes_in_resolved_python_path(tmp_path):
    """The generated dispatcher.bat must invoke a concrete, resolved
    interpreter path (mirroring recurring-tasks' _resolve_python_interpreter
    fix) instead of a bare, unqualified 'python' token that has no PATH
    validation and no 'py'-launcher fallback."""
    repo_root = Path(r"C:\Users\tester\AI")
    with mock.patch.object(
        windows_launcher.shutil,
        "which",
    ) as which:
        which.side_effect = lambda name: r"C:\Python312\python.exe" if name == "python" else None
        content = _windows_dispatcher_content(repo_root, home=tmp_path / "home")

    assert r'"C:\Python312\python.exe"' in content
    assert '"python" "' not in content
    assert not content.split("\n")[2].startswith("python ")


def test_windows_dispatcher_falls_back_to_py_launcher(tmp_path):
    """When 'python' isn't on PATH but the 'py' launcher is, that resolved
    absolute path is used instead."""
    repo_root = Path(r"C:\Users\tester\AI")
    with mock.patch.object(
        windows_launcher.shutil,
        "which",
    ) as which:
        which.side_effect = lambda name: r"C:\Windows\py.exe" if name == "py" else None
        content = _windows_dispatcher_content(repo_root, home=tmp_path / "home")

    assert r'"C:\Windows\py.exe"' in content


def test_windows_dispatcher_raises_clear_error_when_no_interpreter_found(tmp_path):
    """If neither 'python' nor 'py' resolves on PATH, fail loudly at
    generation time instead of silently baking a broken bare token."""
    repo_root = Path(r"C:\Users\tester\AI")
    with mock.patch.object(
        windows_launcher.shutil,
        "which",
        return_value=None,
    ):
        with pytest.raises(WindowsPythonNotFoundError):
            _windows_dispatcher_content(repo_root, home=tmp_path / "home")


def test_windows_agent_launcher_files_are_copied(tmp_path):
    source_bin = tmp_path / "repo" / "skills" / "install-assistant-tools" / "_rtx/assets/bin"
    source_bin.mkdir(parents=True)
    for name in ["assistant", "_agent_launch.py", "assistant.bat"]:
        (source_bin / name).write_text("stub\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"

    installer = platform_launcher_installer("win32")
    installer.install_agent_launcher_files(
        source_bin_dir=source_bin,
        bin_dir=bin_dir,
        agent="assistant",
        dry_run=False,
        manifest=None,
    )

    assert (bin_dir / "assistant").is_file()
    assert (bin_dir / "_agent_launch.py").is_file()
    assert (bin_dir / "assistant.bat").is_file()
    assert not (bin_dir / "assistant").is_symlink()
    assert not (bin_dir / "_agent_launch.py").is_symlink()


def test_windows_tw_is_skipped(tmp_path):
    source_bin = tmp_path / "repo" / "skills" / "install-assistant-tools" / "_rtx/assets/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "tmux-workspace").write_text("stub\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"

    installer = platform_launcher_installer("win32")
    installer.install_agent_launcher_files(
        source_bin_dir=source_bin,
        bin_dir=bin_dir,
        agent="tw",
        dry_run=False,
        manifest=None,
    )

    assert not (bin_dir / "tw").exists()
    assert not (bin_dir / "tmux-workspace").exists()

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

from _install_launcher import platform_launcher_installer
from _install_launcher._base_launcher import (
    LauncherBundleSpec,
    LauncherFileSpec,
    LauncherInstallerBase,
)


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

    dispatcher = installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run=False)
    invoke_skill = installer.install_invoke_skill_launcher(bin_dir, dry_run=False)

    assert dispatcher.status == "installed"
    assert invoke_skill.status == "installed"
    assert (bin_dir / "dispatcher").is_file()
    assert (bin_dir / "invoke-skill").is_file()
    assert (bin_dir / "dispatcher").stat().st_mode & 0o111


def test_osx_uses_unix_launcher_contract(tmp_path):
    installer = platform_launcher_installer("darwin")
    repo_root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"

    dispatcher = installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run=False)

    assert dispatcher.status == "installed"
    assert (bin_dir / "dispatcher").is_file()
    assert not (bin_dir / "dispatcher.bat").exists()


def test_windows_dispatcher_is_batch_and_invoke_skill_is_unsupported(tmp_path):
    installer = platform_launcher_installer("win32")
    repo_root = Path(r"C:\Users\tester\AI")
    bin_dir = tmp_path / "bin"

    dispatcher = installer.install_dispatcher_launcher(repo_root, bin_dir, dry_run=False)
    invoke_skill = installer.install_invoke_skill_launcher(bin_dir, dry_run=False)

    content = (bin_dir / "dispatcher.bat").read_text(encoding="utf-8")
    assert dispatcher.status == "installed"
    assert "py -3 -m officina.dispatcher.cli %*" in content
    assert r"C:\Users\tester\AI" in content
    assert not (bin_dir / "dispatcher").exists()
    assert invoke_skill.status == "unsupported"
    assert invoke_skill.required is False
    assert not (bin_dir / "invoke-skill").exists()


def test_windows_agent_launcher_files_are_copied(tmp_path):
    source_bin = tmp_path / "repo" / "skills" / "install-assistant-tools" / "bin"
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
    source_bin = tmp_path / "repo" / "skills" / "install-assistant-tools" / "bin"
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

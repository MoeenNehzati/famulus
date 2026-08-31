from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest

from officina.common import command_files
from officina.wakeup.linux_osx_windows import setup_integration, teardown_integration


def roots(tmp_path: Path):
    python = tmp_path / "selected python" / "python"
    python.parent.mkdir()
    python.write_text("")
    plugin = tmp_path / "selected plugin"
    (plugin / "src").mkdir(parents=True)
    return python, plugin, tmp_path / "bin", tmp_path / "native"


def test_linux_setup_replaces_selected_plugin_and_teardown_is_idempotent(tmp_path: Path):
    """Catch stale native or command files after selected-plugin replacement."""
    python, plugin, bin_dir, native = roots(tmp_path)
    calls = []
    run = lambda argv, **kwargs: calls.append(argv)

    setup_integration(
        python=python,
        plugin_root=plugin,
        bin_dir=bin_dir,
        native_root=native,
        platform="linux",
        run=run,
    )
    plugin_b = tmp_path / "replacement plugin"
    (plugin_b / "src").mkdir(parents=True)
    setup_integration(
        python=python,
        plugin_root=plugin_b,
        bin_dir=bin_dir,
        native_root=native,
        platform="linux",
        run=run,
    )

    assert str(plugin.resolve()) not in (bin_dir / "llm-wakeup").read_text()
    assert str(plugin_b.resolve()) in (native / "famulus-llm-wakeup.service").read_text()
    assert str(python.resolve()) in (native / "famulus-llm-wakeup.service").read_text()
    teardown_integration(native_root=native, bin_dir=bin_dir, platform="linux", run=run)
    teardown_integration(native_root=native, bin_dir=bin_dir, platform="linux", run=run)
    assert not (bin_dir / "llm-wakeup").exists()


def test_late_failure_restores_command_file_identity(tmp_path: Path, monkeypatch):
    python, plugin, bin_dir, native = roots(tmp_path)
    bin_dir.mkdir()
    command = bin_dir / "llm-wakeup"
    command.write_text("original command\n")
    command.chmod(0o751)
    target = tmp_path / "user-owned-lw"
    target.write_text("target\n")
    alias = bin_dir / "lw"
    alias.symlink_to(target)
    original_writer = command_files.write_generated_launcher_file
    writes = 0

    def fail_after_writing(*args, **kwargs):
        nonlocal writes
        original_writer(*args, **kwargs)
        writes += 1
        if writes == 2:
            raise RuntimeError("late command failure")

    monkeypatch.setattr(command_files, "write_generated_launcher_file", fail_after_writing)

    with pytest.raises(RuntimeError, match="late command failure"):
        setup_integration(
            python=python,
            plugin_root=plugin,
            bin_dir=bin_dir,
            native_root=native,
            platform="linux",
            run=lambda *args, **kwargs: None,
        )

    assert command.read_text() == "original command\n"
    assert stat.S_IMODE(command.stat().st_mode) == 0o751
    assert alias.is_symlink()
    assert alias.readlink() == target


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_controlled_outer_adapters_preserve_spaced_paths(tmp_path: Path, platform: str):
    python, plugin, bin_dir, native = roots(tmp_path)
    calls = []
    setup_integration(python=python, plugin_root=plugin, bin_dir=bin_dir, native_root=native, platform=platform, run=lambda argv, **kwargs: calls.append(argv))
    if platform == "darwin":
        payload = plistlib.loads((native / "com.famulus.llm-wakeup.plist").read_bytes())
        assert payload["ProgramArguments"] == [str(python.resolve()), "-m", "officina.wakeup.cli", "run-due"]
    else:
        assert str(python.resolve()) in (native / "famulus-llm-wakeup-due.cmd").read_text()
        assert calls[-1][-1] == str(native / "famulus-llm-wakeup-due.cmd")

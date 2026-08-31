from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest
import yaml

from officina.common import command_files
from officina.wakeup import WakeupError
from officina.wakeup.linux_osx_windows import setup_integration, teardown_integration


LEGACY = {
    "name": "llm-wakeup",
    "description": "Deliver any wakeups that have come due",
    "command": "launch.py -m officina.wakeup.cli run-due",
    "schedule": "*/10 * * * *",
    "enabled": True,
}


def roots(tmp_path: Path):
    python = tmp_path / "selected python" / "python"
    python.parent.mkdir()
    python.write_text("")
    plugin = tmp_path / "selected plugin"
    (plugin / "src").mkdir(parents=True)
    return python, plugin, tmp_path / "bin", tmp_path / "native"


def test_linux_setup_migrates_exact_legacy_and_teardown_is_idempotent(tmp_path: Path):
    python, plugin, bin_dir, native = roots(tmp_path)
    jobs = tmp_path / "recurring" / "jobs.yaml"
    jobs.parent.mkdir()
    jobs.write_text(yaml.safe_dump({"jobs": [LEGACY]}))
    history = jobs.parent / "logs" / "llm-wakeup" / "latest.json"
    history.parent.mkdir(parents=True)
    history.write_text('{"kept": true}\n')
    calls = []
    run = lambda argv, **kwargs: calls.append(argv)
    recurring_states = []

    def sync_recurring(path: Path) -> None:
        recurring_states.append(yaml.safe_load(path.read_text())["jobs"][0]["enabled"])

    setup_integration(python=python, plugin_root=plugin, bin_dir=bin_dir, native_root=native, jobs_file=jobs, platform="linux", run=run, sync_recurring=sync_recurring)
    setup_integration(python=python, plugin_root=plugin, bin_dir=bin_dir, native_root=native, jobs_file=jobs, platform="linux", run=run, sync_recurring=sync_recurring)

    assert yaml.safe_load(jobs.read_text())["jobs"][0]["enabled"] is False
    assert recurring_states == [False, False]
    assert history.read_text() == '{"kept": true}\n'
    assert "selected plugin/src" in (bin_dir / "llm-wakeup").read_text()
    assert str(python.resolve()) in (native / "famulus-llm-wakeup.service").read_text()
    assert sum(call[-1] == "famulus-llm-wakeup.timer" and "enable" in call for call in calls) == 2
    plugin_b = tmp_path / "replacement plugin"
    (plugin_b / "src").mkdir(parents=True)
    setup_integration(python=python, plugin_root=plugin_b, bin_dir=bin_dir, native_root=native, jobs_file=jobs, platform="linux", run=run, sync_recurring=sync_recurring)
    assert str(plugin.resolve()) not in (bin_dir / "llm-wakeup").read_text()
    assert str(plugin_b.resolve()) in (native / "famulus-llm-wakeup.service").read_text()
    teardown_integration(native_root=native, bin_dir=bin_dir, platform="linux", run=run)
    teardown_integration(native_root=native, bin_dir=bin_dir, platform="linux", run=run)
    assert not (bin_dir / "llm-wakeup").exists()


def test_conflict_and_native_failure_precede_or_rollback_all_writes(tmp_path: Path):
    python, plugin, bin_dir, native = roots(tmp_path)
    jobs = tmp_path / "jobs.yaml"
    jobs.write_text(yaml.safe_dump({"jobs": [{**LEGACY, "schedule": "1 * * * *"}]}))
    with pytest.raises(WakeupError, match="conflicting user-authored"):
        setup_integration(python=python, plugin_root=plugin, bin_dir=bin_dir, native_root=native, jobs_file=jobs, platform="linux", run=lambda *a, **k: None)
    assert not bin_dir.exists() and not native.exists()

    original = jobs.read_bytes()
    jobs.write_text(yaml.safe_dump({"jobs": [LEGACY]}))
    expected = jobs.read_bytes()
    with pytest.raises(RuntimeError, match="native failed"):
        setup_integration(python=python, plugin_root=plugin, bin_dir=bin_dir, native_root=native, jobs_file=jobs, platform="linux", run=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("native failed")))
    assert jobs.read_bytes() == expected
    assert not (native / "famulus-llm-wakeup.service").exists()
    assert not (bin_dir / "llm-wakeup").exists()
    assert original != expected


@pytest.mark.parametrize(
    "payload",
    [
        {"jobs": [LEGACY], "history": {}},
        {"jobs": [LEGACY, LEGACY]},
        {"jobs": [{**LEGACY, "name": "Invalid Name"}]},
    ],
)
def test_noncanonical_recurring_state_is_rejected_before_mutation(
    tmp_path: Path,
    payload: dict,
):
    python, plugin, bin_dir, native = roots(tmp_path)
    jobs = tmp_path / "jobs.yaml"
    jobs.write_text(yaml.safe_dump(payload))

    with pytest.raises(WakeupError, match="cannot validate recurring jobs"):
        setup_integration(
            python=python,
            plugin_root=plugin,
            bin_dir=bin_dir,
            native_root=native,
            jobs_file=jobs,
            platform="linux",
            run=lambda *args, **kwargs: None,
        )

    assert not bin_dir.exists()
    assert not native.exists()


def test_failed_recurring_handoff_restores_legacy_execution(tmp_path: Path):
    python, plugin, bin_dir, native = roots(tmp_path)
    native.mkdir()
    service = native / "famulus-llm-wakeup.service"
    service.write_text("preexisting service\n")
    service.chmod(0o751)
    timer = native / "famulus-llm-wakeup.timer"
    timer.write_text("preexisting timer\n")
    timer.chmod(0o710)
    owner = native / "famulus-llm-wakeup-owner.json"
    owner.write_text('{"preexisting": true}\n')
    owner.chmod(0o640)
    jobs = tmp_path / "jobs.yaml"
    jobs.write_text(yaml.safe_dump({"jobs": [LEGACY]}))
    jobs.chmod(0o640)
    original = jobs.read_bytes()
    observed = []

    def sync_recurring(path: Path) -> None:
        enabled = yaml.safe_load(path.read_text())["jobs"][0]["enabled"]
        observed.append(enabled)
        if len(observed) == 1:
            raise RuntimeError("recurring handoff failed")

    with pytest.raises(RuntimeError, match="recurring handoff failed"):
        setup_integration(
            python=python,
            plugin_root=plugin,
            bin_dir=bin_dir,
            native_root=native,
            jobs_file=jobs,
            platform="linux",
            run=lambda *args, **kwargs: None,
            sync_recurring=sync_recurring,
        )

    assert observed == [False, True]
    assert jobs.read_bytes() == original
    assert stat.S_IMODE(jobs.stat().st_mode) == 0o640
    assert service.read_text() == "preexisting service\n"
    assert stat.S_IMODE(service.stat().st_mode) == 0o751
    assert timer.read_text() == "preexisting timer\n"
    assert stat.S_IMODE(timer.stat().st_mode) == 0o710
    assert owner.read_text() == '{"preexisting": true}\n'
    assert stat.S_IMODE(owner.stat().st_mode) == 0o640
    assert not (bin_dir / "llm-wakeup").exists()


def test_late_jobs_symlink_failure_restores_native_owner_and_jobs(tmp_path: Path):
    python, plugin, bin_dir, native = roots(tmp_path)
    native.mkdir()
    service = native / "famulus-llm-wakeup.service"
    service.write_text("preexisting service\n")
    service.chmod(0o751)
    timer = native / "famulus-llm-wakeup.timer"
    timer.write_text("preexisting timer\n")
    timer.chmod(0o710)
    owner = native / "famulus-llm-wakeup-owner.json"
    owner.write_text('{"preexisting": true}\n')
    owner.chmod(0o640)
    jobs_target = tmp_path / "user-owned-jobs.yaml"
    jobs_target.write_text(yaml.safe_dump({"jobs": [LEGACY]}))
    jobs = tmp_path / "jobs.yaml"
    jobs.symlink_to(jobs_target)

    with pytest.raises(OSError, match="symbolic link"):
        setup_integration(
            python=python,
            plugin_root=plugin,
            bin_dir=bin_dir,
            native_root=native,
            jobs_file=jobs,
            platform="linux",
            run=lambda *args, **kwargs: None,
            sync_recurring=lambda path: pytest.fail("sync must not run"),
        )

    assert jobs.is_symlink() and jobs.readlink() == jobs_target
    assert service.read_text() == "preexisting service\n"
    assert stat.S_IMODE(service.stat().st_mode) == 0o751
    assert timer.read_text() == "preexisting timer\n"
    assert stat.S_IMODE(timer.stat().st_mode) == 0o710
    assert owner.read_text() == '{"preexisting": true}\n'
    assert stat.S_IMODE(owner.stat().st_mode) == 0o640
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

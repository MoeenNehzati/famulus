from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _setup_runner as setup_runner
else:
    import _setup_runner as setup_runner


def test_default_bin_dir_uses_installed_invoke_skill_location(tmp_path):
    resolved = {
        "invoke-skill": str(tmp_path / "managed-bin" / "invoke-skill"),
        "assistant": str(tmp_path / "other-bin" / "assistant"),
    }
    with mock.patch.object(
        setup_runner.shutil,
        "which",
        side_effect=lambda command: resolved.get(command),
    ):
        assert setup_runner._default_bin_dir(tmp_path) == tmp_path / "managed-bin"


def test_render_healthcheck_cron_uses_runtime_resolver_and_direct_failure_popup(tmp_path):
    resolver = tmp_path / "runtime" / "bootstrap" / "resolvers" / "v1" / "launch.py"
    healthcheck = tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py"
    line = setup_runner.render_healthcheck_cron(
        runtime_resolver=resolver,
        healthcheck=healthcheck,
        log_file=tmp_path / "logs" / "healthcheck" / "run.log",
        uid=1000,
    )

    assert line.startswith("0 */4 * * * ")
    assert str(resolver) in line
    assert str(healthcheck) in line
    assert "RECURRING_TASKS_HEALTHCHECK_CRON=1" in line
    assert "XDG_RUNTIME_DIR=/run/user/1000" in line
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in line
    assert "/usr/bin/notify-send" in line
    assert "--urgency=critical" in line
    assert setup_runner.CRON_MARKER in line


def test_install_healthcheck_cron_adds_managed_entry_and_creates_log_dir(tmp_path):
    written: list[str] = []
    skill_root = tmp_path / "skill"
    resolver = tmp_path / "runtime" / "launch.py"
    healthcheck = tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py"

    with mock.patch.object(setup_runner, "_read_crontab", return_value=""), \
         mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        setup_runner.install_healthcheck_cron(
            skill_root=skill_root,
            runtime_resolver=resolver,
            healthcheck=healthcheck,
            uid=1000,
        )

    assert len(written) == 1
    assert str(resolver) in written[0]
    assert str(healthcheck) in written[0]
    assert setup_runner.CRON_MARKER in written[0]
    assert (skill_root / "logs" / "healthcheck").is_dir()


def test_install_healthcheck_cron_replaces_stale_managed_entry_and_preserves_unrelated_lines(tmp_path):
    existing = (
        "MAILTO=\"\"\n"
        "15 * * * * unrelated-command\n"
        f"0 */4 * * * python3 /old/healthcheck.py {setup_runner.CRON_MARKER}\n"
    )
    written: list[str] = []

    with mock.patch.object(setup_runner, "_read_crontab", return_value=existing), \
         mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        setup_runner.install_healthcheck_cron(
            skill_root=tmp_path / "skill",
            runtime_resolver=tmp_path / "runtime" / "launch.py",
            healthcheck=tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py",
            uid=1000,
        )

    assert len(written) == 1
    assert "MAILTO=\"\"\n15 * * * * unrelated-command\n" in written[0]
    assert "/old/healthcheck.py" not in written[0]
    assert written[0].count(setup_runner.CRON_MARKER) == 1


def test_install_healthcheck_cron_is_idempotent_without_duplicate_entries(tmp_path):
    desired = setup_runner.render_healthcheck_cron(
        runtime_resolver=tmp_path / "runtime" / "launch.py",
        healthcheck=tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py",
        log_file=tmp_path / "skill" / "logs" / "healthcheck" / "run.log",
        uid=1000,
    )
    written: list[str] = []

    with mock.patch.object(setup_runner, "_read_crontab", return_value=desired + "\n"), \
         mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        setup_runner.install_healthcheck_cron(
            skill_root=tmp_path / "skill",
            runtime_resolver=tmp_path / "runtime" / "launch.py",
            healthcheck=tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py",
            uid=1000,
        )

    assert written == []


def test_install_healthcheck_cron_migrates_old_recurring_lines(tmp_path):
    existing = (
        "15 * * * * old-command # ai-recurring\n"
        "0 1 * * * unrelated-command\n"
    )
    written: list[str] = []

    with mock.patch.object(setup_runner, "_read_crontab", return_value=existing), \
         mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        setup_runner.install_healthcheck_cron(
            skill_root=tmp_path / "skill",
            runtime_resolver=tmp_path / "runtime" / "launch.py",
            healthcheck=tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py",
            uid=1000,
            migrate_cron=True,
        )

    assert "old-command" not in written[0]
    assert "unrelated-command" in written[0]
    assert setup_runner.CRON_MARKER in written[0]


def test_run_setup_uses_python_runtimes_and_scheduler_backend(tmp_path, monkeypatch):
    backend = mock.Mock()
    backend.status.return_value = "timers\n"
    monkeypatch.setattr(setup_runner, "_default_bin_dir", lambda home: tmp_path / "bin")

    with mock.patch.object(setup_runner._ensure_agent_env, "run") as ensure_env, \
         mock.patch.object(setup_runner._unit_writer, "main") as unit_writer_main, \
         mock.patch.object(setup_runner, "install_healthcheck_cron") as install_cron, \
         mock.patch.object(setup_runner, "platform_schedule_backend", return_value=backend):
        setup_runner.run_setup(argv=["--migrate-cron", "--unit-dir", str(tmp_path / "units")], home=tmp_path)

    ensure_env.assert_called_once()
    assert ensure_env.call_args.kwargs["repo_root"] == setup_runner.SKILL_DIR.parents[2]
    unit_writer_main.assert_called_once_with(["--unit-dir", str(tmp_path / "units")])
    install_cron.assert_called_once()
    assert install_cron.call_args.kwargs["migrate_cron"] is True
    backend.status.assert_called_once()

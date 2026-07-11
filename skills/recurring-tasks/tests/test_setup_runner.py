from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

import _setup_runner as setup_runner


def test_install_healthcheck_cron_adds_python_entry_without_shell_redirection(tmp_path):
    written: list[str] = []

    with mock.patch.object(setup_runner, "_read_crontab", return_value=""), \
         mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        setup_runner.install_healthcheck_cron(skill_dir=tmp_path)

    assert len(written) == 1
    assert "python3" in written[0]
    assert "_healthcheck_probe.py" in written[0]
    assert setup_runner.CRON_MARKER in written[0]
    assert ">>" not in written[0]
    assert "2>&1" not in written[0]


def test_install_healthcheck_cron_is_idempotent(tmp_path):
    existing = f"0 */4 * * * python3 {tmp_path}/_rtx/_healthcheck_probe.py {setup_runner.CRON_MARKER}\n"

    with mock.patch.object(setup_runner, "_read_crontab", return_value=existing), \
         mock.patch.object(setup_runner, "_write_crontab") as write_crontab:
        setup_runner.install_healthcheck_cron(skill_dir=tmp_path)

    write_crontab.assert_not_called()


def test_install_healthcheck_cron_migrates_old_recurring_lines(tmp_path):
    existing = (
        "15 * * * * old-command # ai-recurring\n"
        "0 1 * * * unrelated-command\n"
    )
    written: list[str] = []

    with mock.patch.object(setup_runner, "_read_crontab", return_value=existing), \
         mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        setup_runner.install_healthcheck_cron(skill_dir=tmp_path, migrate_cron=True)

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
    unit_writer_main.assert_called_once_with(["--unit-dir", str(tmp_path / "units")])
    install_cron.assert_called_once()
    assert install_cron.call_args.kwargs["migrate_cron"] is True
    backend.status.assert_called_once()

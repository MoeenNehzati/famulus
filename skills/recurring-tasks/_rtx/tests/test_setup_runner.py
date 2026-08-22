from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _setup_runner as setup_runner
else:
    import _setup_runner as setup_runner


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
    assert "-m officina.recurring.healthcheck" in line
    assert str(healthcheck) not in line
    assert "--descriptor" in line
    assert "--log-root" in line
    assert "--cron" in line
    assert "XDG_RUNTIME_DIR=/run/user/1000" in line
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in line
    assert "/usr/bin/notify-send" in line
    assert "--urgency=critical" in line
    assert setup_runner.CRON_MARKER in line


def test_render_healthcheck_cron_quotes_exact_bounded_environment(tmp_path):
    environment = {
        "HOME": str(tmp_path / "home with spaces"),
        "PATH": str(tmp_path / "bin 雪"),
        "CODEX_HOME": str(tmp_path / 'codex "quoted" % !'),
    }
    line = setup_runner.render_healthcheck_cron(
        runtime_resolver=tmp_path / "runtime" / "launch.py",
        log_file=tmp_path / "logs" / "healthcheck" / "run.log",
        uid=1000,
        environment=environment,
    )

    assert all(f"{name}=" in line for name in environment)
    assert "SECRET_CANARY" not in line
    assert "home with spaces" in line and "雪" in line and "%" in line and "!" in line


def test_render_healthcheck_cron_rejects_crlf_environment(tmp_path):
    with pytest.raises(ValueError, match="CR or LF"):
        setup_runner.render_healthcheck_cron(
            runtime_resolver=tmp_path / "runtime" / "launch.py",
            log_file=tmp_path / "logs" / "healthcheck" / "run.log",
            uid=1000,
            environment={"PATH": "bad\nvalue"},
        )


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
    assert "-m officina.recurring.healthcheck" in written[0]
    assert str(healthcheck) not in written[0]
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
    monkeypatch.setattr(setup_runner.sys, "platform", "linux")
    monkeypatch.setattr(setup_runner.os, "getuid", lambda: 1000, raising=False)
    context = mock.Mock(
        runtime_resolver=tmp_path / "runtime" / "launch.py",
        config_root=tmp_path / "config",
        installation_id="standard",
        log_dir=tmp_path / "state" / "logs",
    )
    monkeypatch.setattr(setup_runner, "production_schedule_context", lambda: context)

    with mock.patch.object(setup_runner._unit_writer, "main") as unit_writer_main, \
         mock.patch.object(setup_runner, "install_healthcheck_cron") as install_cron, \
         mock.patch.object(setup_runner, "platform_schedule_backend", return_value=backend):
        setup_runner.run_setup(argv=["--migrate-cron"], home=tmp_path)

    unit_writer_main.assert_called_once_with([])
    install_cron.assert_called_once()
    assert install_cron.call_args.kwargs["migrate_cron"] is True
    backend.status.assert_called_once()


def test_run_setup_rejects_unit_dir_before_mutating_scheduler(tmp_path):
    with mock.patch.object(setup_runner._unit_writer, "main") as unit_writer_main, \
         mock.patch.object(setup_runner, "install_healthcheck_cron") as install_cron:
        with pytest.raises(SystemExit):
            setup_runner.run_setup(argv=["--unit-dir", str(tmp_path / "units")], home=tmp_path)

    unit_writer_main.assert_not_called()
    install_cron.assert_not_called()


def _crontab_result(returncode: int, stdout: str = "", stderr: str = ""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_read_crontab_returns_existing_table():
    with mock.patch.object(
        setup_runner.subprocess,
        "run",
        return_value=_crontab_result(0, stdout="0 5 * * * backup.sh\n"),
    ):
        assert setup_runner._read_crontab() == "0 5 * * * backup.sh\n"


def test_read_crontab_treats_absent_table_as_empty():
    with mock.patch.object(
        setup_runner.subprocess,
        "run",
        return_value=_crontab_result(1, stderr="no crontab for someuser"),
    ):
        assert setup_runner._read_crontab() == ""


def test_read_crontab_refuses_to_guess_when_the_table_cannot_be_read():
    """A read failure must never be mistaken for "there is no crontab".

    install_healthcheck_cron writes back whatever _read_crontab returns, so
    mapping an unreadable table to "" silently deletes every unrelated entry
    the user has -- backup jobs, sync jobs, everything.
    """
    for returncode, stderr in (
        (1, "crontab: you (someuser) are not allowed to use this program"),
        (1, "/var/spool/cron/crontabs/someuser: Permission denied"),
        (2, ""),
    ):
        with mock.patch.object(
            setup_runner.subprocess,
            "run",
            return_value=_crontab_result(returncode, stderr=stderr),
        ):
            try:
                setup_runner._read_crontab()
            except setup_runner.CrontabUnreadableError as exc:
                assert "refusing to rewrite" in str(exc)
            else:
                raise AssertionError(
                    f"expected refusal for rc={returncode} stderr={stderr!r}"
                )


def test_install_healthcheck_cron_does_not_write_when_crontab_unreadable(tmp_path):
    """The wipe scenario, end to end: nothing may be written."""
    written: list[str] = []
    with mock.patch.object(
        setup_runner,
        "_read_crontab",
        side_effect=setup_runner.CrontabUnreadableError("boom"),
    ), mock.patch.object(setup_runner, "_write_crontab", side_effect=written.append):
        try:
            setup_runner.install_healthcheck_cron(
                skill_root=tmp_path / "skill",
                runtime_resolver=tmp_path / "runtime" / "launch.py",
                healthcheck=tmp_path / "skill" / "_rtx" / "_healthcheck_probe.py",
                uid=1000,
            )
        except setup_runner.CrontabUnreadableError:
            pass
        else:
            raise AssertionError("install should not swallow an unreadable crontab")

    assert written == [], f"crontab was rewritten despite being unreadable: {written}"

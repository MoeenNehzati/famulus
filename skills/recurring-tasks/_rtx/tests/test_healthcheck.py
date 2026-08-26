from __future__ import annotations

from unittest import mock

from .. import _healthcheck_probe as healthcheck


def test_main_delegates_healthcheck_to_managed_boundary():
    with mock.patch.object(
        healthcheck, "run_managed_control", return_value=0
    ) as managed:
        assert healthcheck.main([]) == 0
        managed.assert_called_once_with("healthcheck")


def test_main_returns_managed_healthcheck_failure_status():
    with mock.patch.object(healthcheck, "run_managed_control", return_value=1):
        assert healthcheck.main([]) == 1


def test_main_rejects_source_local_healthcheck_arguments(capsys):
    assert healthcheck.main(["--jobs-file", "/tmp/other"]) == 2
    assert "unexpected arguments" in capsys.readouterr().err

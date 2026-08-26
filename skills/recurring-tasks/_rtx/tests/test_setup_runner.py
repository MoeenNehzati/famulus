from __future__ import annotations

from unittest import mock

import pytest

from .. import _setup_runner as setup_runner


def test_setup_entrypoint_delegates_to_managed_control(monkeypatch):
    delegated = mock.Mock(return_value=7)
    monkeypatch.setattr(setup_runner, "run_managed_control", delegated)

    assert setup_runner.main([]) == 7
    delegated.assert_called_once_with("setup")


def test_setup_entrypoint_preserves_deferred_migrate_cron_rejection():
    with pytest.raises(
        ValueError,
        match="legacy cron migration remains owned by the later migration checkpoint",
    ):
        setup_runner.main(["--migrate-cron"])

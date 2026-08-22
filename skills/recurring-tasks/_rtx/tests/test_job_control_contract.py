from __future__ import annotations

from unittest import mock

import pytest

from .. import _job_control as job_control


@pytest.mark.parametrize("operation", ["enable", "disable"])
def test_job_edit_interface_uses_only_managed_canonical_authority(monkeypatch, operation):
    delegated = mock.Mock(return_value=0)
    monkeypatch.setattr(job_control, "run_managed_control", delegated)

    assert job_control.Interface().run([operation, "target"]) == 0
    delegated.assert_called_once_with(operation, ["target"])


def test_job_edit_interface_retires_custom_file_and_no_sync_modes():
    with pytest.raises(SystemExit):
        job_control.Interface().run(
            ["enable", "target", "--jobs-file", "/tmp/other.yaml", "--no-sync"]
        )

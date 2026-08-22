from __future__ import annotations

from unittest import mock

import pytest

from .. import _job_control as job_control


@pytest.mark.parametrize("operation", ["enable", "disable"])
def test_canonical_enable_disable_delegate_to_managed_control(monkeypatch, operation):
    delegated = mock.Mock(return_value=0)
    monkeypatch.setattr(job_control, "run_managed_control", delegated)

    assert job_control.main([operation, "email-triage"]) == 0
    delegated.assert_called_once_with(operation, ["email-triage"])


@pytest.mark.parametrize(
    "arguments",
    [
        ["enable", "email-triage", "--jobs-file", "/tmp/other.yaml"],
        ["disable", "email-triage", "--no-sync"],
    ],
)
def test_custom_or_unsynchronized_job_edits_are_not_public_routes(arguments):
    with pytest.raises(SystemExit):
        job_control.main(arguments)

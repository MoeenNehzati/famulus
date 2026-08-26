from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from .. import _job_control as job_control

SCRIPT = Path(__file__).resolve().parents[1] / "_job_control.py"


@pytest.mark.parametrize("status", [0, 7])
def test_cli_test_subcommand_forwards_name_and_managed_status(status):
    with mock.patch.object(
        job_control, "run_managed_control", return_value=status
    ) as managed:
        assert job_control.main(["test", "my-job"]) == status
        managed.assert_called_once_with("test", ["my-job"])


@pytest.mark.parametrize(
    ("arguments", "forwarded"),
    [
        (["view-logs", "my-job"], ["my-job"]),
        (["view-logs", "my-job", "--lines", "10"], ["my-job", "--lines", "10"]),
    ],
)
def test_cli_view_logs_subcommand_preserves_lines_forwarding(arguments, forwarded):
    with mock.patch.object(
        job_control, "run_managed_control", return_value=0
    ) as managed:
        assert job_control.main(arguments) == 0
        managed.assert_called_once_with("view-logs", forwarded)


def test_cli_status_subcommand_dispatches_without_arguments():
    with mock.patch.object(
        job_control, "run_managed_control", return_value=0
    ) as managed:
        assert job_control.main(["status"]) == 0
        managed.assert_called_once_with("status", [])


@pytest.mark.parametrize("arguments", [[], ["not-a-real-command"]])
def test_cli_requires_a_known_subcommand(arguments):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0

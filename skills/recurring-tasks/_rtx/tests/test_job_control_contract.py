"""Owner-local behavioral contracts for recurring-task job edits."""

from pathlib import Path

import pytest

from test_support.runtime_module import load_runtime_module


SCRIPT = Path(__file__).resolve().parents[1] / "_job_control.py"


@pytest.mark.parametrize(
    ("operation", "initial_enabled", "expected_enabled"),
    [
        ("enable", False, True),
        ("disable", True, False),
    ],
)
def test_job_edit_interface_uses_custom_file_without_scheduler_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    initial_enabled: bool,
    expected_enabled: bool,
) -> None:
    job_control = load_runtime_module(SCRIPT)
    jobs_file = tmp_path / "jobs.yaml"
    job_control.save_jobs(
        [
            {
                "name": "target",
                "command": "true",
                "schedule": "0 * * * *",
                "enabled": initial_enabled,
            },
            {
                "name": "sibling",
                "command": "true",
                "schedule": "0 1 * * *",
                "enabled": True,
            },
        ],
        jobs_file,
    )

    def reject_sync(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--no-sync must not invoke scheduler synchronization")

    monkeypatch.setattr(job_control, "sync_units", reject_sync)

    result = job_control.Interface().run(
        [
            operation,
            "target",
            "--jobs-file",
            str(jobs_file),
            "--no-sync",
        ]
    )

    assert result == 0
    jobs_by_name = {
        job["name"]: job for job in job_control.load_jobs(jobs_file)
    }
    assert jobs_by_name["target"]["enabled"] is expected_enabled
    assert jobs_by_name["sibling"]["enabled"] is True

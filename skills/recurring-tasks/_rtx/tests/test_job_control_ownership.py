#!/usr/bin/env python3
"""enable/disable must not edit jobs.yaml when the sync that follows is refused."""
from pathlib import Path

import pytest

from .. import _install_owner as install_owner
from .. import _job_control as job_control
from .. import _unit_writer as unit_writer

_JOBS = """jobs:
  - name: demo
    description: Demo
    command: "invoke-skill demo"
    schedule: "0 * * * *"
    enabled: false
"""


def test_enable_from_a_non_owning_checkout_leaves_jobs_yaml_untouched(
    monkeypatch, tmp_path
):
    """The config edit and the sync have to stand or fall together.

    enable_job writes jobs.yaml and *then* syncs. With the sync refused, an
    un-gated edit would leave the file claiming a job is enabled while no
    registration exists for it -- the config and the installation disagreeing,
    with nothing to reconcile them.
    """
    jobs_file = tmp_path / "jobs.yaml"
    jobs_file.write_text(_JOBS, encoding="utf-8")
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    installation_id = "dev-0123456789abcdef0123456789abcdef"
    (unit_dir / f"ai-{installation_id}-demo.service").write_text(
        "[Service]\n", encoding="utf-8"
    )
    monkeypatch.setattr(unit_writer, "DEFAULT_UNIT_DIR", unit_dir)
    monkeypatch.setattr(unit_writer, "SKILL_DIR", Path.home() / "worktree" / "_rtx")
    context = unit_writer.ScheduleContext(
        skill_dir=unit_writer.SKILL_DIR,
        jobs_file=jobs_file,
        log_dir=tmp_path / "logs",
        unit_dir=unit_dir,
        live=False,
        installation_id=installation_id,
    )
    monkeypatch.setattr(job_control, "schedule_context", lambda _: context)

    class ExistingRegistrationBackend:
        def registrations_present(self, selected_context):
            assert selected_context is context
            return True

    monkeypatch.setattr(
        job_control, "platform_schedule_backend", ExistingRegistrationBackend
    )

    with pytest.raises(install_owner.NotTheOwnerError):
        job_control.enable_job("demo", jobs_file=jobs_file)

    assert jobs_file.read_text(encoding="utf-8") == _JOBS

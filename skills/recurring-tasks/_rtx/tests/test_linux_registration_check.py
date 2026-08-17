"""Focused tests for Linux recurring-task registration drift detection."""

from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR.parents[2] / "src"))
sys.path.insert(0, str(SKILL_DIR))

from _schedule_backend import ScheduleContext, ScheduleJob  # noqa: E402
from _schedule_backend._linux_backend import LinuxScheduleBackend  # noqa: E402
from _schedule_backend._linux_registration_check import (  # noqa: E402
    check_job_configuration,
)
import _install_owner as install_owner  # noqa: E402


def _context(unit_dir: Path) -> ScheduleContext:
    return ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=SKILL_DIR / "jobs.yaml",
        log_dir=SKILL_DIR / "logs",
        unit_dir=unit_dir,
        live=False,
    )


def _job() -> ScheduleJob:
    return ScheduleJob(
        name="my-job",
        description="My Job",
        command="/usr/bin/echo hello",
        schedule="0 3 * * *",
        enabled=True,
    )


def test_reports_missing_service(tmp_path):
    reason = check_job_configuration(
        backend_name="linux-systemd",
        job=_job(),
        context=_context(tmp_path),
    )

    assert reason == "my-job: service unit missing"


# famulus-skip: category=platform-contract; reason=systemd unit text uses POSIX paths and newlines; alternate=Windows registration drift tests cover the native scheduler backend
@pytest.mark.skipif(os.name == "nt", reason="systemd unit contract")
def test_detects_service_and_timer_drift(tmp_path):
    backend = LinuxScheduleBackend()
    context = _context(tmp_path)
    job = _job()
    backend.sync([job], context)
    assert check_job_configuration(
        backend_name=backend.name, job=job, context=context
    ) is None

    # A service whose ExecStart points at a jobs.yaml that no longer exists is
    # the case this check exists for: systemd would fail to exec it, and
    # nothing else notices until the log goes stale a full interval later.
    service = tmp_path / "ai-my-job.service"
    service.write_text(
        service.read_text().replace(str(context.jobs_file), "/stale/jobs.yaml")
    )
    assert check_job_configuration(
        backend_name=backend.name, job=job, context=context
    ) == "my-job: service unit stale"

    backend.sync([job], context)
    timer = tmp_path / "ai-my-job.timer"
    timer.write_text(
        timer.read_text().replace(
            "OnCalendar=*-*-* 03:00:00", "OnCalendar=*-*-* *:00:00"
        )
    )
    assert check_job_configuration(
        backend_name=backend.name, job=job, context=context
    ) == "my-job: timer unit stale"


# famulus-skip: category=platform-contract; reason=systemd unit text uses POSIX paths and newlines; alternate=Windows registration drift tests cover the native scheduler backend
@pytest.mark.skipif(os.name == "nt", reason="systemd unit contract")
def test_a_non_owning_copy_declines_to_judge_instead_of_reporting_drift(tmp_path):
    """The 2026-08-17 regression.

    A probe run from a checkout that does not own the installation used to
    render its expectation from its own location, which can never match the
    installed units, and reported ``service unit stale`` for every job against a
    healthy install -- every four hours, via a desktop popup. It must say it
    cannot judge instead of blaming the jobs.
    """
    backend = LinuxScheduleBackend()
    context = _context(tmp_path)
    job = _job()
    backend.sync([job], context)
    install_owner.write_owner(
        unit_dir=tmp_path, owner=tmp_path / "some" / "other" / "checkout" / "_rtx"
    )

    reason = check_job_configuration(
        backend_name=backend.name, job=job, context=context
    )

    assert reason is not None
    assert "stale" not in reason
    assert "does not own" in reason


# famulus-skip: category=platform-contract; reason=systemd unit text uses POSIX paths and newlines; alternate=Windows registration drift tests cover the native scheduler backend
@pytest.mark.skipif(os.name == "nt", reason="systemd unit contract")
def test_reports_a_unit_whose_executor_no_longer_exists(tmp_path):
    """Byte-identical units are not enough: the target has to be there.

    This is the stranding case -- a sync from a checkout that was later deleted
    leaves units that compare clean and can never run.
    """
    backend = LinuxScheduleBackend()
    absent = tmp_path / "deleted-checkout" / "_rtx"
    context = ScheduleContext(
        skill_dir=absent,
        jobs_file=absent / "jobs.yaml",
        log_dir=absent / "logs",
        unit_dir=tmp_path / "units",
        live=False,
    )
    job = _job()
    backend.sync([job], context)

    reason = check_job_configuration(
        backend_name=backend.name, job=job, context=context
    )

    assert reason is not None
    assert "stale" not in reason
    assert "missing" in reason

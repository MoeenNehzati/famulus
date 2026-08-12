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

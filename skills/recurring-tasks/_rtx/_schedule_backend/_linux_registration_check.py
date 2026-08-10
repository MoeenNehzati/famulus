"""Compare installed Linux scheduler units with recurring-task configuration."""

from __future__ import annotations

from ._base_backend import ScheduleContext, ScheduleJob
from ._linux_backend import (
    PREFIX,
    cron_to_systemd_calendar,
    default_unit_dir,
    service_content,
    timer_content,
)


def check_job_configuration(
    *,
    backend_name: str,
    job: ScheduleJob,
    context: ScheduleContext,
) -> str | None:
    """Return registration drift for a supported scheduler job.

    Intent
    ------
    Detect missing, unreadable, or stale generated unit files before run-log
    freshness could incorrectly make an outdated registration look healthy.

    Rationale
    ---------
    Registration rendering remains owned by the platform backend. This checker
    reuses those renderers without widening the backend protocol on platforms
    where the independent sentinel does not inspect native registration bytes.

    Both units are byte-compared. The rendered expectation must therefore be
    a function of the install layout and ``jobs.yaml`` only -- never of the
    calling process's environment. That property was broken once, when
    ``_launcher_bin_dir()`` resolved through ``shutil.which``: cron's PATH
    lacks the launcher directory, so every cron-invoked check re-rendered a
    different ``PATH=`` line and reported drift that did not exist, 12 times
    in a row. It is now resolved from the install layout instead.

    Known residual coupling: ``ScheduleContext.runtime_resolver`` resolves
    through ``XDG_DATA_HOME`` (``LOCALAPPDATA`` on Windows), so a host that
    sets those in a desktop session but not in cron would see the same false
    "service unit stale" report. Those variables are unset on the current
    host in both contexts. Deriving the expectation from a recorded install
    manifest, rather than re-deriving it, is the fix if that changes.

    Note ``command:`` is deliberately not checked: the executor reads it live
    from ``jobs.yaml`` at run time, so it cannot go stale in a unit.

    Pseudocode
    ----------
    - if backend_name is not linux-systemd:
      - return none
    - set unit_dir = configured unit directory or default unit directory
    - set service_name = generated service filename for job
    - set expected_service = rendered service for job and current context
    - set expected_timer = rendered timer for job schedule
    - for installed_unit in expected_units:
      - if installed_path is missing:
        - return missing unit reason
      - if installed_path cannot be read:
        - return unreadable unit reason
      - if installed bytes differ from expected_text:
        - return stale unit reason
    - return none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._linux_backend.default_unit_dir:
      why:
        computes: "Resolves the native unit directory when the context does not provide a test or alternate location."
    ._linux_backend.cron_to_systemd_calendar:
      why:
        transforms: "Converts the job schedule to the calendar value required for expected timer rendering."

    InstantiationsFromRepo
    ----------------------
    ._linux_backend.service_content:
      why:
        constructs: "Builds the canonical service bytes used for exact installed-registration comparison."
    ._linux_backend.timer_content:
      why:
        constructs: "Builds the canonical timer bytes used for exact installed-registration comparison."
    """
    if backend_name != "linux-systemd":
        return None

    unit_dir = context.unit_dir or default_unit_dir()
    service_name = f"{PREFIX}{job.name}.service"
    timer_name = f"{PREFIX}{job.name}.timer"
    expected = (
        (
            unit_dir / service_name,
            service_content(
                job.name,
                job.description,
                context.jobs_file,
                context.skill_dir / "_job_executor.py",
                context.runtime_resolver,
            ),
            "service",
        ),
        (
            unit_dir / timer_name,
            timer_content(
                job.description,
                cron_to_systemd_calendar(job.schedule),
                service_name,
            ),
            "timer",
        ),
    )
    for path, wanted, kind in expected:
        if not path.exists():
            return f"{job.name}: {kind} unit missing"
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"{job.name}: {kind} unit unreadable: {exc}"
        if actual != wanted:
            return f"{job.name}: {kind} unit stale"
    return None

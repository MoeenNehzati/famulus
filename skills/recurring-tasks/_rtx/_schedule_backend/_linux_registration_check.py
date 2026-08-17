"""Compare installed Linux scheduler units with recurring-task configuration."""

from __future__ import annotations

from ._base_backend import ScheduleContext, ScheduleJob

try:  # Mirrors the sibling-import dance the rest of _rtx uses when run unpackaged.
    from .._install_owner import read_owner
except ImportError:  # pragma: no cover - exercised only outside the package
    from _install_owner import read_owner
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

    Registration rendering remains owned by the platform backend. This checker
    reuses those renderers without widening the backend protocol on platforms
    where the independent sentinel does not inspect native registration bytes.

    Both units are byte-compared. The rendered expectation must therefore be a
    function of the install layout and ``jobs.yaml`` only -- never of the
    calling process's environment. That property was broken once, when
    ``_launcher_bin_dir()`` resolved through ``shutil.which``: cron's PATH
    lacks the launcher directory, so every cron-invoked check re-rendered a
    different ``PATH=`` line and reported drift that did not exist, 12 times in
    a row. It is now resolved from the install layout instead.

    Known residual coupling: ``ScheduleContext.runtime_resolver`` resolves
    through ``XDG_DATA_HOME`` (``LOCALAPPDATA`` on Windows), so a host that
    sets those in a desktop session but not in cron would see the same false
    "service unit stale" report. Those variables are unset on the current host
    in both contexts. Deriving the expectation from a recorded install
    manifest, rather than re-deriving it, is the fix if that changes.

    Note ``command:`` is deliberately not checked: the executor reads it live
    from ``jobs.yaml`` at run time, so it cannot go stale in a unit.
    """
    if backend_name != "linux-systemd":
        return None

    unit_dir = context.unit_dir or default_unit_dir()
    # Rendering an expectation from a copy that does not own the installation
    # can only ever produce a mismatch, because the executor path it renders is
    # its own. Reporting that as drift is what turned a worktree sync into
    # "service unit stale" for every job, four-hourly, against an installation
    # that was healthy. Decline to judge registration instead; the caller's
    # remaining checks (freshness, activity) do not depend on this copy's paths.
    # A *missing* record is deliberately not a refusal here: there is nothing to
    # contradict, and SYNC owns that case.
    owner = read_owner(unit_dir)
    if owner is not None and owner != context.skill_dir:
        return (
            f"{job.name}: registration not verified -- this copy does not own "
            f"the installation (owner: {owner})"
        )
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
    # Byte-identical units still cannot run if what they invoke is gone. A sync
    # from a checkout that was later deleted leaves exactly that: a clean
    # comparison over a registration that fails to exec, with nothing else to
    # notice until the outcome record goes stale a full interval later.
    executor = context.skill_dir / "_job_executor.py"
    if not executor.exists():
        return f"{job.name}: executor missing ({executor})"
    return None

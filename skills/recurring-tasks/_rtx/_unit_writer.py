#!/usr/bin/env python3
"""Regenerate host scheduler entries from jobs.yaml."""
import os
import sys
from pathlib import Path
from argparse import ArgumentParser

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).resolve().parent
RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from ._jobs_config import load_jobs
    from ._schedule_backend import ScheduleBackend, ScheduleContext, platform_schedule_backend, schedule_jobs_from_mappings
    from ._schedule_context import _test_schedule_context, production_schedule_context
else:
    from _jobs_config import load_jobs  # noqa: E402
    from _schedule_backend import (  # noqa: E402
    ScheduleBackend,
    ScheduleContext,
    platform_schedule_backend,
    schedule_jobs_from_mappings,
)
    from _schedule_context import _test_schedule_context, production_schedule_context  # noqa: E402
if __package__:
    from ._schedule_backend._linux_backend import PREFIX, cron_to_systemd_calendar, default_unit_dir, service_content, timer_content, unit_prefix
    from ._install_owner import require_ownership, write_owner
    from ._managed_control import run as run_managed_control
else:
    from _schedule_backend._linux_backend import (  # noqa: E402
    PREFIX,
    cron_to_systemd_calendar,
    default_unit_dir,
    service_content,
    timer_content,
    unit_prefix,
)
    from _install_owner import require_ownership, write_owner  # noqa: E402
    from _managed_control import run as run_managed_control  # noqa: E402

DEFAULT_JOBS = SKILL_DIR / "default_jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"
DEFAULT_UNIT_DIR = default_unit_dir()


def sync_units(
    jobs: list,
    unit_dir: Path,
    log_dir: Path,
    live: bool = True,
    jobs_file: Path = DEFAULT_JOBS,
    backend: ScheduleBackend | None = None,
    adopt: bool = False,
) -> None:
    """Generate or update host scheduler entries to match jobs.yaml.

    Refuses before writing anything unless this copy owns the installation:
    the rendered registrations embed this file's own location, so a sync from
    another checkout silently repoints the installation at a directory that may
    later be deleted. See _install_owner.
    """
    if live:
        context = production_schedule_context()
        if unit_dir is not None and unit_dir.resolve(strict=False) != context.unit_dir.resolve(strict=False):
            raise ValueError("live scheduler registration root comes only from the canonical descriptor")
    else:
        if unit_dir is None:
            raise ValueError("non-live scheduler tests require an explicit unit_dir")
        context = _test_schedule_context(
            skill_dir=SKILL_DIR,
            jobs_file=jobs_file,
            log_dir=log_dir,
            unit_dir=unit_dir,
        )
    selected_backend = backend or platform_schedule_backend()
    resolved_unit_dir = ensure_owner(
        adopt=adopt,
        unit_dir=context.unit_dir,
        installation_id=context.installation_id,
        context=context,
        backend=selected_backend,
    )
    selected_backend.sync(schedule_jobs_from_mappings(jobs), context)
    if live:
        _repair_healthcheck_cron(context)
    # Recorded only after the writes succeed. Claiming ownership first would
    # leave a record naming a checkout whose sync then failed partway, and the
    # health check would read that record and report every job as drifted.
    write_owner(
        unit_dir=resolved_unit_dir,
        owner=SKILL_DIR,
        installation_id=context.installation_id,
    )


def ensure_owner(
    adopt: bool = False,
    unit_dir: Path | None = None,
    installation_id: str = "standard",
    context: ScheduleContext | None = None,
    backend: ScheduleBackend | None = None,
) -> Path:
    """Refuse unless this copy owns the installation; return the unit directory.

    Callers that mutate state of their own before syncing (enable/disable edit
    jobs.yaml first) must call this *before* that edit, so a refused sync does
    not leave the configuration claiming something the installation does not
    reflect.

    ``unit_dir`` is None when the caller lets the backend pick its own
    location; the record has to land in the same place either way. Resolving it
    here mirrors how the registration check resolves it.
    """
    resolved = unit_dir if unit_dir is not None else DEFAULT_UNIT_DIR
    registrations_present = (
        backend.registrations_present(context)
        if backend is not None
        and context is not None
        and hasattr(backend, "registrations_present")
        else _registrations_present(resolved, installation_id)
    )
    require_ownership(
        unit_dir=resolved,
        skill_dir=SKILL_DIR,
        registrations_present=registrations_present,
        adopt=adopt,
        # Only the machine's real registration directory is an installation
        # worth protecting from a throwaway copy; an overridden one is a test's
        # own scratch space.
        live_install=resolved == DEFAULT_UNIT_DIR,
        installation_id=installation_id,
    )
    return resolved


def _registrations_present(
    unit_dir: Path, installation_id: str = "standard"
) -> bool:
    """Whether this host already has scheduler entries for these jobs.

    Distinguishes a genuinely fresh install, where adopting is right, from a
    missing record over an existing installation, where adopting would let one
    deleted file disarm the guard.
    """
    return any(Path(unit_dir).glob(f"{unit_prefix(installation_id)}*"))


def _repair_healthcheck_cron(context: ScheduleContext) -> None:
    """Bring the independent health-check cron entry back into line.

    The entry was installed by setup alone, so once it went stale nothing
    restored it -- and a stale entry means the watchdog is silently dead, with
    no second watchdog to notice. Rendering it on every sync makes it
    self-repairing. The write is already idempotent: an identical line is
    detected and not rewritten, so this does not increase how often the user's
    crontab is touched.

    Cron is Linux-only here, and a host may have no cron at all. Neither is a
    reason to fail a scheduler sync that otherwise succeeded.
    """
    if not sys.platform.startswith("linux"):
        return
    # The rendered entry embeds this file's own location, so a sync running from
    # a copy of the repository would point the user's real crontab at that copy.
    # That is now refused before any of this runs -- sync_units gates on
    # ownership, which covers the temp-directory mirror this used to check for
    # and the worktree it missed alike (see _install_owner).
    #
    # Imported here, not at module scope: _setup_runner imports this module,
    # so a top-level import back would be circular.
    if __package__:
        from ._setup_runner import (
            CronUnavailableError,
            CrontabUnreadableError,
            install_healthcheck_cron,
        )
    else:
        from _setup_runner import (  # noqa: E402
            CronUnavailableError,
            CrontabUnreadableError,
            install_healthcheck_cron,
        )

    try:
        install_healthcheck_cron(
            skill_root=SKILL_DIR.parent,
            runtime_resolver=context.runtime_resolver,
            module="officina.recurring.healthcheck",
            descriptor=context.config_root / "schedule-descriptor.json",
            uid=os.getuid(),
            installation_id=context.installation_id,
            log_root=context.log_dir,
            environment=context.environment,
        )
    except (CronUnavailableError, CrontabUnreadableError) as exc:
        print(f"Skipped health-check cron repair: {exc}")


class Interface(PythonArgvMachineInterface):
    prog = "unit_writer.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    p = ArgumentParser(description=__doc__)
    p.parse_args(argv)
    return run_managed_control("sync")


if __name__ == "__main__":
    raise SystemExit(main())

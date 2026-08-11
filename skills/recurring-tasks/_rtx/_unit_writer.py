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
else:
    from _jobs_config import load_jobs  # noqa: E402
    from _schedule_backend import (  # noqa: E402
    ScheduleBackend,
    ScheduleContext,
    platform_schedule_backend,
    schedule_jobs_from_mappings,
)
if __package__:
    from ._schedule_backend._linux_backend import cron_to_systemd_calendar, default_unit_dir, service_content, timer_content
else:
    from _schedule_backend._linux_backend import (  # noqa: E402
    cron_to_systemd_calendar,
    default_unit_dir,
    service_content,
    timer_content,
)

DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
LOG_DIR = SKILL_DIR / "logs"
DEFAULT_UNIT_DIR = default_unit_dir()


def sync_units(
    jobs: list,
    unit_dir: Path,
    log_dir: Path,
    live: bool = True,
    jobs_file: Path = DEFAULT_JOBS,
    backend: ScheduleBackend | None = None,
) -> None:
    """Generate or update host scheduler entries to match jobs.yaml."""
    context = ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=jobs_file,
        log_dir=log_dir,
        unit_dir=unit_dir,
        live=live,
    )
    selected_backend = backend or platform_schedule_backend()
    selected_backend.sync(schedule_jobs_from_mappings(jobs), context)
    if live:
        _repair_healthcheck_cron(context)


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
            healthcheck=SKILL_DIR / "_healthcheck_probe.py",
            uid=os.getuid(),
        )
    except (CronUnavailableError, CrontabUnreadableError) as exc:
        print(f"Skipped health-check cron repair: {exc}")


class Interface(PythonArgvMachineInterface):
    prog = "unit_writer.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    p = ArgumentParser(description=__doc__)
    p.add_argument("--unit-dir", default=None, help="Override unit dir (testing; skips systemctl)")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS), help="Override jobs.yaml location")
    args = p.parse_args(argv)

    live = args.unit_dir is None
    unit_dir = Path(args.unit_dir) if args.unit_dir else DEFAULT_UNIT_DIR

    jobs = load_jobs(args.jobs_file)

    sync_units(jobs, unit_dir, LOG_DIR, live=live, jobs_file=Path(args.jobs_file))
    if live:
        print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

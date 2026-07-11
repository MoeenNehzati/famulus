#!/usr/bin/env python3
"""Regenerate host scheduler entries from jobs.yaml."""
import sys
import yaml
from pathlib import Path
from argparse import ArgumentParser

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent.parent
RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _schedule_backend import (  # noqa: E402
    ScheduleBackend,
    ScheduleContext,
    platform_schedule_backend,
    schedule_jobs_from_mappings,
)
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

    with open(args.jobs_file) as f:
        jobs = (yaml.safe_load(f) or {}).get("jobs", [])

    sync_units(jobs, unit_dir, LOG_DIR, live=live, jobs_file=Path(args.jobs_file))
    if live:
        print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

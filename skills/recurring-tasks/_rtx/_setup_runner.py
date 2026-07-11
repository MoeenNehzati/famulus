#!/usr/bin/env python3
"""Set up recurring-tasks scheduler state for this host."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent.parent
RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

import _ensure_agent_env  # noqa: E402
import _unit_writer  # noqa: E402
from _schedule_backend import ScheduleContext, platform_schedule_backend  # noqa: E402

CRON_MARKER = "# ai-recurring-healthcheck"
OLD_CRON_MARKER = "# ai-recurring"


def _default_bin_dir(home: Path) -> Path:
    assistant = shutil.which("assistant")
    if assistant:
        return Path(assistant).parent
    return home / "Documents" / "scripts" / "bin"


def _read_crontab() -> str:
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _write_crontab(content: str) -> None:
    subprocess.run(
        ["crontab", "-"],
        input=content,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )


def _without_old_recurring_lines(existing: str) -> str:
    kept = [
        line
        for line in existing.splitlines()
        if OLD_CRON_MARKER not in line or CRON_MARKER in line
    ]
    return "\n".join(kept)


def install_healthcheck_cron(*, skill_dir: Path, migrate_cron: bool = False) -> None:
    """Install the recurring-tasks healthcheck cron entry idempotently."""
    log_dir = skill_dir / "logs" / "healthcheck"
    log_dir.mkdir(parents=True, exist_ok=True)
    existing = _read_crontab()
    normalized = _without_old_recurring_lines(existing) if migrate_cron else existing.rstrip("\n")
    lines = normalized.splitlines() if normalized else []

    if any(CRON_MARKER in line for line in lines):
        if normalized != existing.rstrip("\n"):
            _write_crontab(normalized + "\n")
        print("Healthcheck cron entry already present.")
        return

    healthcheck = skill_dir / "_rtx" / "_healthcheck_probe.py"
    lines.append(f"0 */4 * * * python3 {healthcheck} {CRON_MARKER}")
    _write_crontab("\n".join(lines) + "\n")
    print("Added healthcheck cron entry (every 4 hours).")


def run_setup(*, argv: list[str], home: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrate-cron",
        action="store_true",
        help="Remove old ai-recurring cron entries before installing the healthcheck entry.",
    )
    args, unit_writer_args = parser.parse_known_args(argv)

    import yaml  # noqa: F401

    print("Prerequisites")
    print("PyYAML ok")

    selected_home = home or Path.home()
    repo_root = SKILL_DIR.parent.parent
    bin_dir = _default_bin_dir(selected_home)
    _ensure_agent_env.run(
        repo_root=repo_root,
        home=selected_home,
        bin_dir=bin_dir,
        dry_run=False,
    )

    print("")
    print("Syncing scheduler entries")
    _unit_writer.main(unit_writer_args)

    print("")
    print("Installing healthcheck cron entry")
    install_healthcheck_cron(skill_dir=SKILL_DIR, migrate_cron=args.migrate_cron)

    print("")
    print("Active scheduled jobs")
    context = ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=_unit_writer.DEFAULT_JOBS,
        log_dir=_unit_writer.LOG_DIR,
    )
    print(platform_schedule_backend().status(context))


class Interface(PythonArgvMachineInterface):
    prog = "setup_runner.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    run_setup(argv=list(argv or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

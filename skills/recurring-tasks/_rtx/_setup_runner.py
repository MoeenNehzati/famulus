#!/usr/bin/env python3
"""Set up recurring-tasks scheduler state for this host."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent
SKILL_ROOT = SKILL_DIR.parent
RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from . import _ensure_agent_env
else:
    import _ensure_agent_env  # noqa: E402
if __package__:
    from . import _unit_writer
else:
    import _unit_writer  # noqa: E402
if __package__:
    from ._schedule_backend import ScheduleContext, platform_schedule_backend
else:
    from _schedule_backend import ScheduleContext, platform_schedule_backend  # noqa: E402

CRON_MARKER = "# ai-recurring-healthcheck"
OLD_CRON_MARKER = "# ai-recurring"


class CrontabUnreadableError(RuntimeError):
    """The existing crontab could not be read, so it must not be rewritten."""


class CronUnavailableError(RuntimeError):
    """This host has no cron implementation, so there is no entry to manage."""


# vixie-cron / cronie report an absent table as "no crontab for <user>". Any
# other nonzero result means we failed to READ an existing table, which is a
# different thing entirely.
_NO_CRONTAB_MARKER = "no crontab for"


def _read_crontab() -> str:
    """Read the current user's crontab, or return empty text when there is none.

    "No crontab exists" and "the crontab could not be read" are both reported
    by a nonzero status, but they must not be treated alike: the merge writes
    back whatever this returns, so mapping a read FAILURE to empty text
    silently deletes every unrelated entry the user has -- backups, sync jobs,
    everything. Only the recognised absent-table message is treated as empty;
    anything else refuses to proceed.
    """
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
    except FileNotFoundError as exc:
        # No cron implementation installed. Distinct from "the table could not
        # be read": there is nothing to preserve and nothing to write, so the
        # caller should skip cron work rather than abort the whole sync.
        raise CronUnavailableError("no crontab command on this host") from exc
    if result.returncode == 0:
        return result.stdout

    stderr = (result.stderr or "").strip()
    if _NO_CRONTAB_MARKER in stderr.lower():
        return ""

    raise CrontabUnreadableError(
        "refusing to rewrite the crontab: could not read the existing one "
        f"(exit {result.returncode}): {stderr or 'no error output'}"
    )


def _write_crontab(content: str) -> None:
    """Replace the current user's crontab with supplied complete text.

    Passing the full text on standard input preserves unrelated entries while
    allowing one atomic managed update.
    """
    subprocess.run(
        ["crontab", "-"],
        input=content,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )


def _without_old_recurring_lines(existing: str) -> str:
    """Remove obsolete recurring-task cron lines during migration.

    Explicit migration prevents duplicate legacy launches without broadly
    rewriting user-owned cron configuration.
    """
    kept = [
        line
        for line in existing.splitlines()
        if OLD_CRON_MARKER not in line or CRON_MARKER in line
    ]
    return "\n".join(kept)


def render_healthcheck_cron(
    *,
    runtime_resolver: Path,
    healthcheck: Path,
    log_file: Path,
    uid: int,
) -> str:
    """Render the independent health-check cron command.

    Keeping notification fallback in cron reports resolver and checker startup
    failures that checker-owned logic cannot observe.
    """
    resolver_arg = shlex.quote(str(runtime_resolver))
    healthcheck_arg = shlex.quote(str(healthcheck))
    log_arg = shlex.quote(str(log_file))
    title = shlex.quote("Recurring tasks need attention")
    body = shlex.quote(
        "The recurring-tasks health check failed. See its health-check log."
    )
    runtime_dir = f"/run/user/{uid}"
    return (
        "0 */4 * * * "
        f"RECURRING_TASKS_HEALTHCHECK_CRON=1 {resolver_arg} {healthcheck_arg} "
        f">> {log_arg} 2>&1 || "
        f"XDG_RUNTIME_DIR={runtime_dir} "
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime_dir}/bus "
        f"/usr/bin/notify-send --urgency=critical {title} {body} "
        f"{CRON_MARKER}"
    )


def _replace_managed_cron_line(existing: str, desired: str) -> str:
    """Replace all managed sentinel lines with one desired line.

    Exact replacement makes setup idempotent and repairs stale command paths
    without accumulating duplicate sentinels.
    """
    kept = [
        line
        for line in existing.splitlines(keepends=True)
        if CRON_MARKER not in line
    ]
    prefix = "".join(kept)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + desired + "\n"


def install_healthcheck_cron(
    *,
    skill_root: Path,
    runtime_resolver: Path,
    healthcheck: Path,
    uid: int,
    migrate_cron: bool = False,
) -> None:
    """Install or repair the independent health-check cron entry.

    Setup must repair stale registrations while preserving unrelated cron
    entries and avoiding unnecessary writes.
    """
    log_dir = skill_root / "logs" / "healthcheck"
    log_dir.mkdir(parents=True, exist_ok=True)
    existing = _read_crontab()
    normalized = _without_old_recurring_lines(existing) if migrate_cron else existing
    desired = render_healthcheck_cron(
        runtime_resolver=runtime_resolver,
        healthcheck=healthcheck,
        log_file=log_dir / "run.log",
        uid=uid,
    )
    updated = _replace_managed_cron_line(normalized, desired)

    if updated == existing:
        print("Healthcheck cron entry already current.")
        return

    _write_crontab(updated)
    print("Installed healthcheck cron entry (every 4 hours).")


def run_setup(*, argv: list[str], home: Path | None = None) -> None:
    """Configure recurring-task launchers, scheduler entries, and sentinel.

    One orchestration path keeps agent environment, per-job registrations, and
    independent failure monitoring synchronized.
    """
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
    _ensure_agent_env.run(home=selected_home, dry_run=False)

    print("")
    print("Syncing scheduler entries")
    _unit_writer.main(unit_writer_args)

    context = ScheduleContext(
        skill_dir=SKILL_DIR,
        jobs_file=_unit_writer.DEFAULT_JOBS,
        log_dir=_unit_writer.LOG_DIR,
    )

    print("")
    print("Installing healthcheck cron entry")
    if sys.platform.startswith("linux"):
        install_healthcheck_cron(
            skill_root=SKILL_ROOT,
            runtime_resolver=context.runtime_resolver,
            healthcheck=SKILL_DIR / "_healthcheck_probe.py",
            uid=os.getuid(),
            migrate_cron=args.migrate_cron,
        )
    else:
        print("Independent cron healthcheck is available on Linux hosts only.")

    print("")
    print("Active scheduled jobs")
    print(platform_schedule_backend().status(context))


class Interface(PythonArgvMachineInterface):
    """Expose host setup through the Python machine interface.

    The shared runtime requires a typed interface object for process-bound
    execution.
    """

    prog = "setup_runner.py"

    def run(self, argv: list[str]) -> int:
        """Run recurring-task setup with explicit arguments.

        One forwarding method keeps direct and dispatcher-driven setup behavior
        identical.
        """
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run recurring-task host setup and return success.

    A small process entrypoint gives direct and machine-interface callers one
    stable exit-code contract.
    """
    run_setup(argv=list(argv or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Set up recurring-tasks scheduler state for this host."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent
SKILL_ROOT = SKILL_DIR.parent
RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from . import _unit_writer
else:
    import _unit_writer  # noqa: E402
if __package__:
    from ._schedule_backend import platform_schedule_backend
    from ._schedule_context import production_schedule_context
    from ._managed_control import run as run_managed_control
else:
    from _schedule_backend import platform_schedule_backend  # noqa: E402
    from _schedule_context import production_schedule_context  # noqa: E402
    from _managed_control import run as run_managed_control  # noqa: E402

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
    log_file: Path,
    uid: int,
    healthcheck: Path | None = None,
    module: str = "officina.recurring.healthcheck",
    descriptor: Path | None = None,
    installation_id: str = "standard",
    environment: Mapping[str, str] | None = None,
) -> str:
    """Render the independent health-check cron command.

    Keeping notification fallback in cron reports resolver and checker startup
    failures that checker-owned logic cannot observe.
    """
    resolver_arg = shlex.quote(str(runtime_resolver))
    descriptor_path = descriptor or (log_file.parents[1] / "schedule-descriptor.json")
    module_arg = shlex.quote(module)
    descriptor_arg = shlex.quote(str(descriptor_path))
    log_root_arg = shlex.quote(str(log_file.parents[1]))
    log_arg = shlex.quote(str(log_file))
    title = shlex.quote("Recurring tasks need attention")
    # The checker leaves its findings beside its log; read them into the body
    # so the popup names what broke instead of only that something did. When
    # the checker could not start at all there is no file to read, and the
    # fallback says exactly that rather than implying a job failed.
    summary_arg = shlex.quote(str(log_file.parent / "last-failure.txt"))
    fallback = shlex.quote(
        "The recurring-tasks health check could not run. See its health-check log."
    )
    body = f'"$(cat {summary_arg} 2>/dev/null || echo {fallback})"'
    runtime_dir = f"/run/user/{uid}"
    assignments = []
    for name, value in sorted((environment or {}).items()):
        if not name.replace("_", "a").isalnum() or not (name[0].isalpha() or name[0] == "_"):
            raise ValueError(f"invalid environment name: {name!r}")
        if "\r" in value or "\n" in value:
            raise ValueError(f"environment {name} must not contain CR or LF")
        assignments.append(f"{name}={shlex.quote(value)}")
    environment_prefix = (" ".join(assignments) + " ") if assignments else ""
    return (
        "0 */4 * * * "
        f"{environment_prefix}{resolver_arg} -m {module_arg} "
        f"--descriptor {descriptor_arg} --log-root {log_root_arg} "
        "--cron "
        f">> {log_arg} 2>&1 || "
        f"XDG_RUNTIME_DIR={runtime_dir} "
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime_dir}/bus "
        f"/usr/bin/notify-send --urgency=critical {title} {body} "
        f"{healthcheck_marker(installation_id)}"
    )


def _replace_managed_cron_line(
    existing: str, desired: str, installation_id: str = "standard"
) -> str:
    """Replace all managed sentinel lines with one desired line.

    Exact replacement makes setup idempotent and repairs stale command paths
    without accumulating duplicate sentinels.
    """
    marker = healthcheck_marker(installation_id)
    kept = [
        line
        for line in existing.splitlines(keepends=True)
        if not line.rstrip("\r\n").rstrip().endswith(marker)
    ]
    prefix = "".join(kept)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + desired + "\n"


def install_healthcheck_cron(
    *,
    skill_root: Path,
    runtime_resolver: Path,
    uid: int,
    healthcheck: Path | None = None,
    module: str = "officina.recurring.healthcheck",
    descriptor: Path | None = None,
    migrate_cron: bool = False,
    installation_id: str = "standard",
    log_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Install or repair the independent health-check cron entry.

    Setup must repair stale registrations while preserving unrelated cron
    entries and avoiding unnecessary writes.
    """
    log_dir = (log_root or (skill_root / "logs")) / "healthcheck"
    log_dir.mkdir(parents=True, exist_ok=True)
    existing = _read_crontab()
    normalized = _without_old_recurring_lines(existing) if migrate_cron else existing
    desired = render_healthcheck_cron(
        runtime_resolver=runtime_resolver,
        healthcheck=healthcheck,
        module=module,
        descriptor=descriptor,
        log_file=log_dir / "run.log",
        uid=uid,
        installation_id=installation_id,
        environment=environment,
    )
    updated = _replace_managed_cron_line(
        normalized, desired, installation_id=installation_id
    )

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
    args = parser.parse_args(argv)

    import yaml  # noqa: F401

    print("Prerequisites")
    print("PyYAML ok")

    print("")
    print("Syncing scheduler entries")
    _unit_writer.main([])
    context = production_schedule_context()

    print("")
    print("Installing healthcheck cron entry")
    if sys.platform.startswith("linux"):
        install_healthcheck_cron(
            skill_root=SKILL_ROOT,
            runtime_resolver=context.runtime_resolver,
            healthcheck=SKILL_DIR / "_healthcheck_probe.py",
            module="officina.recurring.healthcheck",
            descriptor=context.config_root / "schedule-descriptor.json",
            uid=os.getuid(),
            migrate_cron=args.migrate_cron,
            installation_id=context.installation_id,
            log_root=context.log_dir,
            environment=context.environment,
        )
    else:
        print(healthcheck_capability(sys.platform).detail)

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate-cron", action="store_true")
    args = parser.parse_args(argv)
    if args.migrate_cron:
        raise ValueError("legacy cron migration remains owned by the later migration checkpoint")
    return run_managed_control("setup")


if __name__ == "__main__":
    raise SystemExit(main())
def healthcheck_marker(installation_id: str = "standard") -> str:
    return CRON_MARKER if installation_id == "standard" else f"{CRON_MARKER}:{installation_id}"


@dataclass(frozen=True)
class HealthcheckCapability:
    independent_scheduler: bool
    detail: str


def healthcheck_capability(platform: str) -> HealthcheckCapability:
    if platform.startswith("linux"):
        return HealthcheckCapability(True, "independent cron sentinel")
    return HealthcheckCapability(False, "on-demand healthcheck only; independent second scheduler unsupported")

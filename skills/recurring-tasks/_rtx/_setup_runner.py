#!/usr/bin/env python3
"""Set up recurring-tasks scheduler state for this host."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).parent
SKILL_ROOT = SKILL_DIR.parent
RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
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


def _default_bin_dir(home: Path) -> Path:
    """Select the directory containing installed assistant launchers.

    Intent
    ------
    Reuse the active invoke-skill installation directory or derive the conventional user-local fallback.

    Rationale
    ---------
    Agent environment setup must update the same launcher installation that scheduled jobs will resolve.

    Pseudocode
    ----------
    - set installed_launcher = invoke_skill_on_path
    - if installed_launcher exists:
      - return installed_launcher_parent
    - return conventional_user_bin_directory

    Wraps
    -----
    - none
    """
    invoke_skill = shutil.which("invoke-skill")
    if invoke_skill:
        return Path(invoke_skill).parent
    return home / "Documents" / "scripts" / "bin"


def _read_crontab() -> str:
    """Read the current user's crontab or return empty text when absent.

    Intent
    ------
    Obtain the complete crontab text needed for a bounded managed-line update.

    Rationale
    ---------
    Crontab reports a missing table through a nonzero status, which setup treats as an empty starting state.

    Pseudocode
    ----------
    - set result = run crontab_list
    - if result failed:
      - return empty_text
    - return crontab_text

    Wraps
    -----
    - none
    """
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
    """Replace the current user's crontab with supplied complete text.

    Intent
    ------
    Install the already-merged crontab through the native user scheduler command.

    Rationale
    ---------
    Passing the full text on standard input preserves unrelated entries while allowing one atomic managed update.

    Pseudocode
    ----------
    - set installed_crontab = content
    - return none

    Wraps
    -----
    - none
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

    Intent
    ------
    Filter legacy recurring markers while retaining the current health-check marker and unrelated entries.

    Rationale
    ---------
    Explicit migration prevents duplicate legacy launches without broadly rewriting user-owned cron configuration.

    Pseudocode
    ----------
    - set kept_lines = lines_without_legacy_marker_or_with_current_marker
    - return joined_kept_lines

    Wraps
    -----
    - none
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

    Intent
    ------
    Run the checker every four hours through the managed resolver and show a critical popup after any nonzero result.

    Rationale
    ---------
    Keeping notification fallback in cron reports resolver and checker startup failures that checker-owned logic cannot observe.

    Pseudocode
    ----------
    - set quoted_arguments = shell_safe_installation_inputs
    - set desktop_bus_environment = runtime_directory_for_user
    - return four_hour_cron_line with logging and failure_popup

    Wraps
    -----
    - none
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

    Intent
    ------
    Preserve unrelated crontab content while converging the managed health-check registration to one entry.

    Rationale
    ---------
    Exact replacement makes setup idempotent and repairs stale command paths without accumulating duplicate sentinels.

    Pseudocode
    ----------
    - set prefix = existing_lines_without_managed_marker
    - if prefix lacks trailing_newline:
      - set prefix = prefix_with_trailing_newline
    - return prefix with desired_line

    Wraps
    -----
    - none
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

    Intent
    ------
    Create the report directory, optionally remove legacy lines, and converge crontab to the desired sentinel command.

    Rationale
    ---------
    Setup must repair stale registrations while preserving unrelated cron entries and avoiding unnecessary writes.

    Pseudocode
    ----------
    - set healthcheck_log_directory = ensured_directory
    - set existing = current_crontab
    - if migration_requested:
      - set normalized = crontab_without_legacy_lines
    - set desired = rendered_healthcheck_cron
    - set updated = crontab_with_managed_line_replaced
    - if updated equals existing:
      - return none
    - set installed_crontab = updated_crontab

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._without_old_recurring_lines:
      why:
        transforms: "Filters obsolete recurring-task entries only when the caller explicitly requests cron migration."
    ._write_crontab:
      why:
        writes: "Installs the complete merged crontab after detecting that its managed entry differs."

    InstantiationsFromRepo
    ----------------------
    ._read_crontab:
      why:
        constructs: "Obtains the current full crontab used as the preservation and idempotency baseline."
    .render_healthcheck_cron:
      why:
        constructs: "Builds the exact four-hour resolver command and direct desktop-failure fallback."
    ._replace_managed_cron_line:
      why:
        constructs: "Produces the complete desired crontab while retaining every unrelated line."
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

    Intent
    ------
    Perform the host setup sequence and report the resulting native scheduler status.

    Rationale
    ---------
    One orchestration path keeps agent environment, per-job registrations, and independent failure monitoring synchronized.

    Pseudocode
    ----------
    - set setup_arguments = parsed_setup_argv
    - set scheduler_arguments = remaining_setup_argv
    - set yaml_dependency = imported_yaml_runtime
    - set agent_environment = ensured_launcher_environment
    - set scheduler_state = synchronized_native_entries
    - set schedule_context = recurring_task_paths
    - if host supports independent_cron:
      - set sentinel_state = installed_healthcheck_cron
    - set standard_output = active_scheduler_status

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .install_healthcheck_cron:
      why:
        orchestrates: "Converges the independent cron sentinel after native scheduler entries are synchronized."

    InstantiationsFromRepo
    ----------------------
    ._default_bin_dir:
      why:
        constructs: "Produces the managed launcher directory supplied to agent-environment setup."
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
    repo_root = SKILL_DIR.parents[2]
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

    Intent
    ------
    Bind dispatcher-provided arguments to the recurring-task setup entrypoint.

    Rationale
    ---------
    The shared runtime requires a typed interface object for process-bound execution.

    Pseudocode
    ----------
    - set program_name = setup_runner
    - set interface_run = received_argv

    Wraps
    -----
    - none
    """

    prog = "setup_runner.py"

    def run(self, argv: list[str]) -> int:
        """Run recurring-task setup with explicit arguments.

        Intent
        ------
        Forward machine-interface arguments to the command entrypoint and return its status.

        Rationale
        ---------
        One forwarding method keeps direct and dispatcher-driven setup behavior identical.

        Pseudocode
        ----------
        - return @.main(argv)

        Wraps
        -----
        .main -> preprocess: receives machine-interface argv unchanged; postprocess: returns setup status unchanged; fixed_arguments: none
        """
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run recurring-task host setup and return success.

    Intent
    ------
    Normalize optional command arguments and invoke the setup orchestrator.

    Rationale
    ---------
    A small process entrypoint gives direct and machine-interface callers one stable exit-code contract.

    Pseudocode
    ----------
    - set normalized_argv = supplied_argv_or_empty
    - @.run_setup(normalized_argv)
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .run_setup:
      why:
        orchestrates: "Runs environment, scheduler, and sentinel setup before this entrypoint returns a process success code."

    """
    run_setup(argv=list(argv or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

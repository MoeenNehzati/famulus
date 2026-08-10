"""Linux systemd scheduler backend for recurring-tasks.

Registration
------------
Each job becomes an ``ai-<name>.timer`` + ``ai-<name>.service`` pair under
``~/.config/systemd/user/``.

The service's ``PATH`` is built explicitly (launcher directory, the launch
resolver's own directory, ``~/.npm-global/bin``, ``~/.local/bin``, then the
standard system directories) rather than relying on shell inheritance, and is
rendered from the single ordered list in ``_job_path_entries`` so the health
check searches exactly what the unit provides.

``DBUS_SESSION_BUS_ADDRESS`` uses ``unix:path=%t/bus``. systemd expands ``%t``
to the runtime directory root (``$XDG_RUNTIME_DIR``, i.e. ``/run/user/<uid>``)
for whichever uid the user manager actually runs as, so no uid is baked into
configuration.

Triggering blocks: ``systemctl --user start --wait`` returns only once the
unit has finished. Inspect with ``systemctl --user list-timers 'ai-*.timer'``,
``systemctl --user status ai-<name>.service``, or ``journalctl --user -u
ai-<name>.service``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from ._base_backend import (
    ScheduleContext,
    ScheduleJob,
    _default_famulus_paths,
    _default_runtime_resolver,
)

PREFIX = "ai-"


def default_unit_dir() -> Path:
    return Path.home() / ".config/systemd/user"


def _session_env() -> dict[str, str]:
    """Environment in which ``systemctl --user`` can reach the user manager.

    cron sessions get no ``pam_systemd``, so they have neither
    ``XDG_RUNTIME_DIR`` nor ``DBUS_SESSION_BUS_ADDRESS`` and every
    ``systemctl --user`` call fails to connect. Both values are fixed
    properties of the uid, so derive them when absent rather than requiring
    every caller to export them. (The original healthcheck.sh did exactly
    this; the port to Python dropped it.)
    """
    env = dict(os.environ)
    # os.getuid is POSIX-only. This module renders Linux units from any host
    # (the portability sentinel runs it on Windows CI), so guard it the way
    # _osx_backend already does rather than crashing on import-time platforms.
    getuid = getattr(os, "getuid", lambda: 0)
    derived = f"/run/user/{getuid()}"
    # An inherited value wins only if it actually exists: a stale or foreign
    # XDG_RUNTIME_DIR (su, sudo -u, a leaked container value) points at a bus
    # this user cannot reach, and silently produces worse diagnostics than
    # deriving the correct one. Empty is treated as absent.
    inherited = env.get("XDG_RUNTIME_DIR")
    runtime_dir = inherited if inherited and Path(inherited).is_dir() else derived
    env["XDG_RUNTIME_DIR"] = runtime_dir
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    return env


def _systemctl(
    *args: str, check: bool = False, capture: bool = True
) -> subprocess.CompletedProcess:
    """Run one ``systemctl --user`` command with a usable session environment.

    ``capture=False`` lets systemd's own diagnostics reach the operator's
    terminal directly. Use it for the mutating commands (``daemon-reload``,
    ``enable``), where a captured failure would otherwise surface only as
    "Command '[...]' returned non-zero exit status 1" -- the CalledProcessError
    message does not include stderr.
    """
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=capture,
        text=True,
        encoding="utf-8" if capture else None,
        errors="strict" if capture else None,
        env=_session_env(),
        check=check,
    )


def cron_to_systemd_calendar(cron: str) -> str:
    """Convert 5-field cron to systemd OnCalendar format."""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron: {cron!r}")
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        raise ValueError(f"dom and month must be '*': {cron!r}")

    dow_names = {
        "0": "Sun", "7": "Sun", "1": "Mon", "2": "Tue",
        "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat",
    }

    def field(value: str, pad: bool = False, step_base: str | None = None) -> str:
        if value == "*":
            return "*"
        if re.fullmatch(r"\*/\d+", value):
            step = value[2:]
            return f"{step_base}/{step}" if step_base else value
        if re.fullmatch(r"\d+", value):
            return value.zfill(2) if pad else value
        raise ValueError(f"Unsupported field: {value!r}")

    dow_prefix = ""
    if dow != "*":
        if dow not in dow_names:
            raise ValueError(f"Invalid day of week: {dow!r}")
        dow_prefix = dow_names[dow] + " "

    return f'{dow_prefix}*-*-* {field(hour, pad=True)}:{field(minute, pad=True, step_base="00")}:00'


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _launcher_bin_dir() -> PurePosixPath:
    """Directory the launchers are installed into.

    Resolved from the Famulus install layout, never from ``PATH``. A
    ``shutil.which("invoke-skill")`` lookup here would make the rendered unit
    -- and therefore the health check's notion of a correct unit -- a function
    of whoever happens to be calling. Cron has no ``~/.local/bin`` on ``PATH``,
    so every cron-invoked check reported drift that did not exist.
    """
    return PurePosixPath(_default_famulus_paths().user_bin.as_posix())


def _job_path_entries(
    launcher_dir: PurePosixPath, resolver: PurePosixPath
) -> list[tuple[str, PurePosixPath]]:
    """Ordered ``(unit form, concrete path)`` pairs for a job's ``PATH``.

    Single source of truth for two consumers that must never disagree: the
    ``PATH=`` line written into the generated unit (which uses systemd's
    ``%h`` specifier) and the directory list the health check searches when
    asking whether the scheduler can resolve the agent command. Keeping them
    as one ordered list means a new entry cannot be added to the unit while
    the checker keeps looking somewhere else.
    """
    home = PurePosixPath(Path.home().as_posix())
    return [
        (str(launcher_dir), launcher_dir),
        (str(resolver.parent), resolver.parent),
        ("%h/.npm-global/bin", home / ".npm-global" / "bin"),
        ("%h/.local/bin", home / ".local" / "bin"),
        ("/usr/local/bin", PurePosixPath("/usr/local/bin")),
        ("/usr/bin", PurePosixPath("/usr/bin")),
        ("/bin", PurePosixPath("/bin")),
    ]


def job_search_dirs() -> list[PurePosixPath]:
    """Concrete directories a scheduled job resolves commands from."""
    resolver = PurePosixPath(_default_runtime_resolver().as_posix())
    return [path for _, path in _job_path_entries(_launcher_bin_dir(), resolver)]


def service_content(
    job_name: str,
    description: str,
    jobs_file: Path,
    executor: Path,
    runtime_resolver: Path,
    launcher_bin: Path | None = None,
) -> str:
    """Generate systemd service unit for a job.

    ``runtime_resolver`` is the stable, release-independent launch resolver
    (``ScheduleContext.runtime_resolver``) -- the same mechanism the
    dispatcher/invoke-skill shims use -- rather than ``sys.executable``,
    which would pin the job to whatever interpreter happened to run the
    sync script and break on the next managed-runtime upgrade.

    ``DBUS_SESSION_BUS_ADDRESS`` is resolved by systemd itself at process
    launch via the ``%t`` specifier (systemd.unit(5): "Runtime directory
    root" -- ``$XDG_RUNTIME_DIR``, i.e. ``/run/user/<uid>`` for the user
    manager), so it is correct for whichever UID this systemd --user
    instance runs as instead of a hardcoded UID baked into jobs.yaml.
    """
    # This unit file always targets a systemd (Linux) host, regardless of
    # which host OS actually generated it (the portability sentinel runs
    # this same generator on all 3 CI platforms specifically to catch a
    # host-OS-dependent leak here) -- every embedded path must render in
    # clean POSIX form. ``Path.as_posix()`` (not ``PurePosixPath(other_path)``
    # cross-flavour construction, which reconstructs from the *other* path's
    # already-parsed native parts and can leave a mixed separator artifact
    # behind, e.g. a drive anchor) is the documented, stable way to get a
    # forward-slash string from any concrete Path regardless of the host
    # that produced it.
    resolver = PurePosixPath(runtime_resolver.as_posix())
    executor_posix = executor.as_posix()
    jobs_file_posix = jobs_file.as_posix()
    launcher_dir = launcher_bin or _launcher_bin_dir()
    path_value = ":".join(
        unit_form for unit_form, _ in _job_path_entries(launcher_dir, resolver)
    )
    return (
        "[Unit]\n"
        f"Description=AI job: {description}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment={_systemd_quote(f'PATH={path_value}')}\n"
        f"Environment={_systemd_quote('DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus')}\n"
        f"ExecStart={_systemd_quote(str(resolver))} {_systemd_quote(executor_posix)} "
        f"--jobs-file {_systemd_quote(jobs_file_posix)} --job {job_name}\n"
    )


def timer_content(description: str, calendar: str, service_name: str) -> str:
    """Generate systemd timer unit for a job."""
    return (
        "[Unit]\n"
        f"Description=Timer for AI job: {description}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={calendar}\n"
        "Persistent=true\n"
        f"Unit={service_name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


class LinuxScheduleBackend:
    name = "linux-systemd"

    def sync(self, jobs: list[ScheduleJob], context: ScheduleContext) -> None:
        unit_dir = context.unit_dir or default_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        enabled_names: set[str] = set()

        for job in jobs:
            if not job.enabled:
                continue

            enabled_names.add(job.name)
            calendar = cron_to_systemd_calendar(job.schedule)
            svc_name = f"{PREFIX}{job.name}.service"
            executor = context.skill_dir / "_job_executor.py"

            (unit_dir / svc_name).write_text(
                service_content(
                    job.name,
                    job.description,
                    context.jobs_file,
                    executor,
                    context.runtime_resolver,
                )
            )
            (unit_dir / f"{PREFIX}{job.name}.timer").write_text(
                timer_content(job.description, calendar, svc_name)
            )
            print(f"Synced '{job.name}' (OnCalendar={calendar})")

        for timer in sorted(unit_dir.glob(f"{PREFIX}*.timer")):
            name = timer.stem[len(PREFIX):]
            if name not in enabled_names:
                if context.live:
                    _systemctl("disable", "--now", timer.name)
                timer.unlink(missing_ok=True)
                (unit_dir / f"{PREFIX}{name}.service").unlink(missing_ok=True)
                print(f"Removed disabled job: '{name}'")

        if context.live:
            _systemctl("daemon-reload", check=True, capture=False)
            for name in sorted(enabled_names):
                _systemctl(
                    "enable", "--now", f"{PREFIX}{name}.timer",
                    check=True, capture=False,
                )
                print(f"Enabled {PREFIX}{name}.timer")

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        result = _systemctl("start", "--wait", f"{PREFIX}{job_name}.service")
        if result.returncode == 0:
            return True
        print("stderr:", result.stderr)
        return False

    def status(self, context: ScheduleContext) -> str:
        result = _systemctl("list-timers", f"{PREFIX}*.timer", "--no-pager")
        return result.stdout

    def check_manager(self) -> str | None:
        result = _systemctl("is-system-running")
        state = result.stdout.strip()
        if state in ("running", "degraded"):
            return None
        if not state:
            # Empty stdout means systemctl never reached the manager; the
            # reason is on stderr and is far more actionable than
            # "unresponsive".
            detail = result.stderr.strip().splitlines()
            if detail:
                return f"systemd user manager: {detail[0]}"
        return f"systemd user manager: {state or 'unresponsive'}"

    def get_agent_command_template(self) -> str | None:
        result = _systemctl("show-environment")
        for line in result.stdout.splitlines():
            if line.startswith("AI_AGENT_COMMAND_TEMPLATE="):
                template = line.split("=", 1)[1]
                if template.startswith("$'") and template.endswith("'"):
                    template = template[2:-1]
                return template
        return None

    def check_job_active(self, job_name: str) -> bool:
        result = _systemctl("is-active", f"{PREFIX}{job_name}.timer")
        return result.returncode == 0

    def job_search_dirs(self) -> list[Path] | None:
        """The unit's own ``PATH=``, expanded -- see ``_job_path_entries``."""
        return [Path(str(entry)) for entry in job_search_dirs()]

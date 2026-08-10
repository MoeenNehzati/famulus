"""Windows Task Scheduler backend for recurring-tasks.

Registration
------------
Each job becomes a scheduled task named ``Famulus-AI-ai-<name>``.

Windows has no shebang-based exec, so unlike the Unix backends the task's
command line hands the launch resolver to an explicit ``python`` interpreter,
matching the convention of the installer's generated Windows launcher shims.

The cron expression is translated to the nearest ``schtasks`` schedule
(``/SC MINUTE``, ``HOURLY``, ``DAILY``, or ``WEEKLY`` with ``/D <weekday>``)
within the cron subset this skill accepts.

Triggering does NOT block: the task is started and the call returns before the
job completes, so callers must read the run outcome record rather than the
trigger result.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
from pathlib import Path

from ._base_backend import ScheduleContext, ScheduleJob

TASK_PREFIX = "Famulus-AI-ai-"


class WindowsPythonNotFoundError(RuntimeError):
    """Raised when no ``python``/``py`` interpreter can be resolved on PATH."""


def task_name(job_name: str) -> str:
    return f"{TASK_PREFIX}{job_name}"


def _short_task_name(name: str) -> str:
    return name.rsplit("\\", 1)[-1]


def default_unit_dir() -> Path:
    """Default directory for generated Task Scheduler wrapper ``.cmd`` files.

    Mirrors ``resolve_famulus_paths``'s Windows layout
    (``%LOCALAPPDATA%\\Famulus\\state\\...``) but tolerates a missing
    ``LOCALAPPDATA`` -- e.g. when this backend's ``sync()`` is unit-tested
    on a non-Windows CI host -- by falling back to the conventional
    ``%LOCALAPPDATA%`` default location instead of raising
    ``FamulusLocalAppDataMissingError``.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Famulus" / "state" / "recurring-tasks" / "task-wrappers"


def wrapper_name(job_name: str) -> str:
    return f"{task_name(job_name)}.cmd"


def task_run_command(wrapper_path: Path, *, comspec: str | None = None) -> str:
    """Build the short executable command stored in ``schtasks /TR``.

    Task Scheduler execution actions are executable-oriented.  Invoke the
    generated batch wrapper through the Windows command processor explicitly
    so task execution does not depend on implicit ``.cmd`` file association.
    ``CALL`` keeps a space-containing wrapper path as one batch-file operand.
    """
    command_processor = comspec or os.environ.get("COMSPEC") or "cmd.exe"
    return subprocess.list2cmdline(
        [command_processor, "/D", "/C", "CALL", str(wrapper_path)]
    )


def _quote_cmd_arg(value: str) -> str:
    """Double-quote one wrapper ``.cmd`` argument, doubling embedded quotes
    and ``%`` characters.

    Always quotes -- not just when the value contains whitespace -- so a
    wrapper generated today, when CI/dev paths happen not to contain
    spaces, still safely handles real installs under paths like
    ``C:\\Program Files\\...`` or a mixed-case, space-containing username
    directory.

    ``%`` is doubled too: inside a batch (``.cmd``) file, ``%`` is cmd.exe's
    environment-variable (``%VAR%``) and batch-positional-parameter
    (``%1``, ``%*``) expansion marker, expanded in a separate parsing pass
    that ordinary double-quoting does not suppress. Doubling (``%%``) is the
    standard batch-file-internal escape for a literal ``%``. This matters
    because job names are free text (``jobs.yaml`` enforces no character
    restriction) and resolved paths can contain ``%`` (e.g. via an unusual
    username or a ``%LOCALAPPDATA%`` value that itself contains ``%``), so
    without this a literal ``%`` in an argument would be corrupted by
    cmd.exe's expansion when the wrapper is invoked by Task Scheduler.
    """
    return '"' + value.replace('"', '""').replace('%', '%%') + '"'


def _resolve_python_interpreter() -> str:
    """Resolve a concrete, absolute path to a python interpreter on PATH.

    Tries ``python`` then falls back to the ``py`` launcher, raising a clear
    error if neither is found rather than silently falling back to a bare,
    unqualified name schtasks can't validate.
    """
    resolved = shutil.which("python") or shutil.which("py")
    if not resolved:
        raise WindowsPythonNotFoundError(
            "could not resolve a python interpreter on PATH (tried 'python' and 'py'); "
            "schtasks /TR requires a concrete, validatable command"
        )
    return resolved


def _command_parts(job: ScheduleJob, context: ScheduleContext) -> list[str]:
    """Build the argv for one job's real, full command line.

    Windows has no shebang-based exec: unlike the Unix backends (which can
    invoke ``context.runtime_resolver`` directly since it carries its own
    ``#!/usr/bin/env python3`` shebang), the resolver script must be handed
    to an explicit python interpreter -- the same convention the installer's
    generated Windows dispatcher launcher uses
    (``python "{resolver}" -m officina.dispatcher.cli %*``). The interpreter
    is resolved to a concrete, absolute path via ``shutil.which`` rather than
    hardcoding ``sys.executable`` (which would pin the job to whichever
    interpreter happened to run the sync/installer script).
    """
    executor = context.skill_dir / "_job_executor.py"
    return [
        _resolve_python_interpreter(),
        str(context.runtime_resolver),
        str(executor),
        "--jobs-file",
        str(context.jobs_file),
        "--log-dir",
        str(context.log_dir),
        "--job",
        job.name,
    ]


def executor_command(job: ScheduleJob, context: ScheduleContext) -> str:
    """Build the full, real command line for one job (interpreter + resolver
    + executor + args), quoted as a single string via
    ``subprocess.list2cmdline``.

    This is NOT what gets passed to ``schtasks /Create /TR`` directly --
    ``/TR`` has a hard, documented 261-character limit on its value
    ("ERROR: Value for '/TR' option cannot be more than 261 character(s)"),
    and this full command line (absolute python interpreter + resolver
    script + job executor script + ``--jobs-file <path>`` + ``--job
    <name>``) routinely exceeds that once assembled under a real install
    path. Instead this string is written into a short wrapper ``.cmd`` file
    (see ``wrapper_content``/``sync``), and ``/TR`` contains only a short
    ``cmd.exe`` invocation of that wrapper.
    """
    return subprocess.list2cmdline(_command_parts(job, context))


def wrapper_content(job: ScheduleJob, context: ScheduleContext) -> str:
    """Generate the wrapper ``.cmd`` file content ``schtasks``' ``/TR`` will
    invoke, working around the 261-character ``/TR`` value limit (see
    ``executor_command``'s docstring): the full command line lives here
    instead, and ``/TR`` is pointed at just this file's own short path.
    """
    quoted = " ".join(_quote_cmd_arg(part) for part in _command_parts(job, context))
    job_log_dir = context.log_dir / job.name
    bootstrap_log = job_log_dir / "scheduler.log"
    quoted_log_dir = _quote_cmd_arg(str(job_log_dir))
    quoted_bootstrap_log = _quote_cmd_arg(str(bootstrap_log))
    return (
        "@echo off\r\nsetlocal\r\n"
        + f"if not exist {quoted_log_dir} mkdir {quoted_log_dir}\r\n"
        + quoted
        + f" >> {quoted_bootstrap_log} 2>&1\r\n"
        + "exit /b %errorlevel%\r\n"
    )


def _cron_weekday(value: str) -> str | None:
    if value == "*":
        return None
    names = {
        "0": "SUN",
        "7": "SUN",
        "1": "MON",
        "2": "TUE",
        "3": "WED",
        "4": "THU",
        "5": "FRI",
        "6": "SAT",
    }
    if value not in names:
        raise ValueError(f"Invalid day of week: {value!r}")
    return names[value]


def _cron_number(value: str, *, low: int, high: int, field_name: str) -> int:
    if re.fullmatch(r"\d+", value):
        number = int(value)
        if low <= number <= high:
            return number
    raise ValueError(f"Unsupported {field_name}: {value!r}")


def cron_to_schtasks_args(cron: str) -> list[str]:
    """Convert the supported 5-field cron subset to schtasks schedule args."""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron: {cron!r}")
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        raise ValueError(f"dom and month must be '*': {cron!r}")

    weekday = _cron_weekday(dow)
    if minute == "*" and hour == "*" and weekday is None:
        return ["/SC", "MINUTE", "/MO", "1"]

    step_match = re.fullmatch(r"\*/(\d+)", minute)
    if step_match and hour == "*" and weekday is None:
        step = int(step_match.group(1))
        if step <= 0:
            raise ValueError(f"Invalid cron step: {minute!r}")
        return ["/SC", "MINUTE", "/MO", str(step)]

    selected_minute = _cron_number(minute, low=0, high=59, field_name="minute")
    if hour == "*" and weekday is None:
        return ["/SC", "HOURLY", "/MO", "1", "/ST", f"00:{selected_minute:02d}"]

    selected_hour = _cron_number(hour, low=0, high=23, field_name="hour")
    start_time = f"{selected_hour:02d}:{selected_minute:02d}"
    if weekday is None:
        return ["/SC", "DAILY", "/ST", start_time]
    return ["/SC", "WEEKLY", "/D", weekday, "/ST", start_time]


class WindowsScheduleBackend:
    name = "windows-task-scheduler"

    def sync(self, jobs: list[ScheduleJob], context: ScheduleContext) -> None:
        unit_dir = context.unit_dir or default_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        enabled_names = {job.name for job in jobs if job.enabled}
        if context.live:
            for existing in self._existing_task_names():
                short_name = _short_task_name(existing)
                if short_name.startswith(TASK_PREFIX) and short_name[len(TASK_PREFIX):] not in enabled_names:
                    subprocess.run(
                        ["schtasks", "/Delete", "/TN", existing, "/F"],
                        capture_output=True,
                    )
                    (unit_dir / wrapper_name(short_name[len(TASK_PREFIX):])).unlink(missing_ok=True)
                    print(f"Removed disabled job: '{short_name[len(TASK_PREFIX):]}'")

        for job in jobs:
            wrapper_path = unit_dir / wrapper_name(job.name)
            if not job.enabled:
                if context.live:
                    subprocess.run(
                        ["schtasks", "/Delete", "/TN", task_name(job.name), "/F"],
                        capture_output=True,
                    )
                wrapper_path.unlink(missing_ok=True)
                continue
            # schtasks /Create /TR has a hard 261-character limit on its
            # value; the wrapper file (see wrapper_content) carries the
            # real, full command line so /TR only needs the wrapper's own
            # short path -- see executor_command's docstring for detail.
            # wrapper_content() already builds its string with explicit
            # \r\n line endings throughout. Path.write_text()'s default
            # text-mode newline translation (newline=None) would rewrite
            # every \n to os.linesep on write -- doubling each already-
            # explicit \r\n into \r\r\n on a real Windows host (where
            # os.linesep == "\r\n"). newline="" disables that translation
            # entirely so the exact literal bytes intended for disk are
            # written as-is.
            wrapper_path.write_text(wrapper_content(job, context), encoding="utf-8", newline="")
            args = [
                "schtasks",
                "/Create",
                "/TN",
                task_name(job.name),
                "/TR",
                task_run_command(wrapper_path),
                "/F",
                *cron_to_schtasks_args(job.schedule),
            ]
            if context.live:
                subprocess.run(args, check=True)
            print(f"Synced '{job.name}' (Task Scheduler task={task_name(job.name)})")

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        result = subprocess.run(
            ["schtasks", "/Run", "/TN", task_name(job_name)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        if result.returncode == 0:
            return True
        print("stderr:", result.stderr)
        return False

    def status(self, context: ScheduleContext) -> str:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST", "/V"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        return result.stdout if result.returncode == 0 else result.stderr

    def check_manager(self) -> str | None:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        if result.returncode == 0:
            return None
        return f"Windows Task Scheduler: {result.stderr.strip() or 'unresponsive'}"

    def get_agent_command_template(self) -> str | None:
        return os.environ.get("AI_AGENT_COMMAND_TEMPLATE")

    def job_search_dirs(self) -> list[Path] | None:
        """schtasks jobs inherit the ambient PATH; nothing is pinned here."""
        return None

    def check_job_active(self, job_name: str) -> bool:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name(job_name)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        return result.returncode == 0

    def _existing_task_names(self) -> list[str]:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        if result.returncode != 0:
            return []
        names: list[str] = []
        for row in csv.reader(result.stdout.splitlines()):
            if row and _short_task_name(row[0]).startswith(TASK_PREFIX):
                names.append(row[0])
        return names

"""Windows Task Scheduler backend for recurring-tasks."""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess

from ._base_backend import ScheduleContext, ScheduleJob

TASK_PREFIX = "Famulus-AI-ai-"


class WindowsPythonNotFoundError(RuntimeError):
    """Raised when no ``python``/``py`` interpreter can be resolved on PATH."""


def task_name(job_name: str) -> str:
    return f"{TASK_PREFIX}{job_name}"


def _short_task_name(name: str) -> str:
    return name.rsplit("\\", 1)[-1]


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


def executor_command(job: ScheduleJob, context: ScheduleContext) -> str:
    """Build the schtasks ``/TR`` command line for one job.

    Windows has no shebang-based exec: unlike the Unix backends (which can
    invoke ``context.runtime_resolver`` directly since it carries its own
    ``#!/usr/bin/env python3`` shebang), the resolver script must be handed
    to an explicit python interpreter -- the same convention the installer's
    generated Windows dispatcher launcher uses
    (``python "{resolver}" -m officina.dispatcher.cli %*``).

    Unlike that ``.cmd`` shim, though, this command line is registered via
    ``schtasks /Create /TR "..."``: Task Scheduler validates/resolves ``/TR``
    at CREATE time using its own logic, not ``cmd.exe``'s PATH search (which
    is what makes a bare ``"python"`` work inside a ``.cmd`` shim invoked at
    run time). A bare, unqualified ``"python"`` string in ``/TR`` fails
    schtasks task creation outright (observed: exit 2147500037 /
    0x80004005, E_FAIL). So the interpreter is resolved to a concrete,
    absolute path via ``shutil.which`` at command-construction time instead
    -- this still avoids hardcoding ``sys.executable`` (pinning the job to
    whichever interpreter happened to run the sync/installer script), while
    producing a path schtasks can actually validate.
    """
    executor = context.skill_dir / "_rtx" / "_job_executor.py"
    return subprocess.list2cmdline(
        [
            _resolve_python_interpreter(),
            str(context.runtime_resolver),
            str(executor),
            "--jobs-file",
            str(context.jobs_file),
            "--job",
            job.name,
        ]
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
        enabled_names = {job.name for job in jobs if job.enabled}
        if context.live:
            for existing in self._existing_task_names():
                short_name = _short_task_name(existing)
                if short_name.startswith(TASK_PREFIX) and short_name[len(TASK_PREFIX):] not in enabled_names:
                    subprocess.run(
                        ["schtasks", "/Delete", "/TN", existing, "/F"],
                        capture_output=True,
                    )
                    print(f"Removed disabled job: '{short_name[len(TASK_PREFIX):]}'")

        for job in jobs:
            if not job.enabled:
                if context.live:
                    subprocess.run(
                        ["schtasks", "/Delete", "/TN", task_name(job.name), "/F"],
                        capture_output=True,
                    )
                continue
            args = [
                "schtasks",
                "/Create",
                "/TN",
                task_name(job.name),
                "/TR",
                executor_command(job, context),
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

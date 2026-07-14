"""Linux systemd scheduler backend for recurring-tasks."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from ._base_backend import ScheduleContext, ScheduleJob

PREFIX = "ai-"


def default_unit_dir() -> Path:
    return Path.home() / ".config/systemd/user"


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


def _launcher_bin_dir() -> Path:
    launcher = shutil.which("invoke-skill")
    if launcher:
        return Path(launcher).parent
    return Path.home() / "Documents" / "_rtx" / "bin"


def service_content(
    job_name: str,
    description: str,
    jobs_file: Path,
    executor: Path,
    python_executable: Path | None = None,
    launcher_bin: Path | None = None,
) -> str:
    """Generate systemd service unit for a job."""
    python = python_executable or Path(sys.executable)
    launcher_dir = launcher_bin or _launcher_bin_dir()
    path_value = (
        f"{launcher_dir}:{python.parent}:%h/.npm-global/bin:"
        "%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
    )
    return (
        "[Unit]\n"
        f"Description=AI job: {description}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment={_systemd_quote(f'PATH={path_value}')}\n"
        f"ExecStart={_systemd_quote(str(python))} {_systemd_quote(str(executor))} "
        f"--jobs-file {_systemd_quote(str(jobs_file))} --job {job_name}\n"
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
            executor = context.skill_dir / "_rtx" / "_job_executor.py"

            (unit_dir / svc_name).write_text(
                service_content(job.name, job.description, context.jobs_file, executor)
            )
            (unit_dir / f"{PREFIX}{job.name}.timer").write_text(
                timer_content(job.description, calendar, svc_name)
            )
            print(f"Synced '{job.name}' (OnCalendar={calendar})")

        for timer in sorted(unit_dir.glob(f"{PREFIX}*.timer")):
            name = timer.stem[len(PREFIX):]
            if name not in enabled_names:
                if context.live:
                    subprocess.run(
                        ["systemctl", "--user", "disable", "--now", timer.name],
                        capture_output=True,
                    )
                timer.unlink(missing_ok=True)
                (unit_dir / f"{PREFIX}{name}.service").unlink(missing_ok=True)
                print(f"Removed disabled job: '{name}'")

        if context.live:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            for name in sorted(enabled_names):
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", f"{PREFIX}{name}.timer"],
                    check=True,
                )
                print(f"Enabled {PREFIX}{name}.timer")

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        result = subprocess.run(
            ["systemctl", "--user", "start", "--wait", f"{PREFIX}{job_name}.service"],
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
            ["systemctl", "--user", "list-timers", f"{PREFIX}*.timer", "--no-pager"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        return result.stdout

    def check_manager(self) -> str | None:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        state = result.stdout.strip()
        if state in ("running", "degraded"):
            return None
        return f"systemd user manager: {state or 'unresponsive'}"

    def get_agent_command_template(self) -> str | None:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        for line in result.stdout.splitlines():
            if line.startswith("AI_AGENT_COMMAND_TEMPLATE="):
                template = line.split("=", 1)[1]
                if template.startswith("$'") and template.endswith("'"):
                    template = template[2:-1]
                return template
        return None

    def check_job_active(self, job_name: str) -> bool:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{PREFIX}{job_name}.timer"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        return result.returncode == 0

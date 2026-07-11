"""macOS launchd scheduler backend for recurring-tasks."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

from ._base_backend import ScheduleContext, ScheduleJob

PREFIX = "ai-"
LABEL_PREFIX = "com.famulus.ai."


def default_launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def launchd_label(job_name: str) -> str:
    return f"{LABEL_PREFIX}{job_name}"


def plist_name(job_name: str) -> str:
    return f"{PREFIX}{job_name}.plist"


def _expand_cron_field(value: str, *, low: int, high: int) -> list[int]:
    if value == "*":
        return list(range(low, high + 1))
    step_match = re.fullmatch(r"\*/(\d+)", value)
    if step_match:
        step = int(step_match.group(1))
        if step <= 0:
            raise ValueError(f"Invalid cron step: {value!r}")
        return list(range(low, high + 1, step))
    if re.fullmatch(r"\d+", value):
        number = int(value)
        if low <= number <= high:
            return [number]
    raise ValueError(f"Unsupported cron field: {value!r}")


def _launchd_weekday(value: str) -> int | None:
    if value == "*":
        return None
    if value == "7":
        return 0
    if re.fullmatch(r"\d+", value):
        number = int(value)
        if 0 <= number <= 6:
            return number
    raise ValueError(f"Invalid day of week: {value!r}")


def cron_to_launchd_intervals(cron: str) -> dict[str, int] | list[dict[str, int]]:
    """Convert the supported 5-field cron subset to launchd intervals."""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron: {cron!r}")
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        raise ValueError(f"dom and month must be '*': {cron!r}")

    minutes = _expand_cron_field(minute, low=0, high=59)
    hours = _expand_cron_field(hour, low=0, high=23)
    weekday = _launchd_weekday(dow)
    intervals: list[dict[str, int]] = []
    for selected_hour in hours:
        for selected_minute in minutes:
            interval = {"Hour": selected_hour, "Minute": selected_minute}
            if weekday is not None:
                interval["Weekday"] = weekday
            intervals.append(interval)
    return intervals[0] if len(intervals) == 1 else intervals


def plist_content(
    *,
    job_name: str,
    description: str,
    jobs_file: Path,
    log_file: Path,
    executor: Path,
    schedule: str,
) -> bytes:
    """Generate a launchd plist for one recurring job."""
    payload = {
        "Label": launchd_label(job_name),
        "ProgramArguments": [
            sys.executable,
            str(executor),
            "--jobs-file",
            str(jobs_file),
            "--job",
            job_name,
        ],
        "StandardErrorPath": str(log_file),
        "StandardOutPath": str(log_file),
        "StartCalendarInterval": cron_to_launchd_intervals(schedule),
        "WorkingDirectory": str(executor.parent.parent),
    }
    if description:
        payload["ProcessType"] = "Background"
    return plistlib.dumps(payload, sort_keys=True)


class OSXScheduleBackend:
    name = "macos-launchd"

    def _target(self) -> str:
        getuid = getattr(os, "getuid", lambda: 0)
        return f"gui/{getuid()}"

    def sync(self, jobs: list[ScheduleJob], context: ScheduleContext) -> None:
        unit_dir = context.unit_dir or default_launch_agents_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        enabled_names: set[str] = set()
        executor = context.skill_dir / "_rtx" / "_job_executor.py"

        for job in jobs:
            if not job.enabled:
                continue
            enabled_names.add(job.name)
            log_file = context.log_dir / job.name / "run.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            plist_path = unit_dir / plist_name(job.name)
            plist_path.write_bytes(
                plist_content(
                    job_name=job.name,
                    description=job.description,
                    jobs_file=context.jobs_file,
                    log_file=log_file,
                    executor=executor,
                    schedule=job.schedule,
                )
            )
            print(f"Synced '{job.name}' (launchd label={launchd_label(job.name)})")

        for plist_path in sorted(unit_dir.glob(f"{PREFIX}*.plist")):
            name = plist_path.stem[len(PREFIX):]
            if name not in enabled_names:
                if context.live:
                    subprocess.run(
                        ["launchctl", "bootout", self._target(), str(plist_path)],
                        capture_output=True,
                    )
                plist_path.unlink(missing_ok=True)
                print(f"Removed disabled job: '{name}'")

        if context.live:
            for name in sorted(enabled_names):
                plist_path = unit_dir / plist_name(name)
                subprocess.run(
                    ["launchctl", "bootout", self._target(), str(plist_path)],
                    capture_output=True,
                )
                subprocess.run(
                    ["launchctl", "bootstrap", self._target(), str(plist_path)],
                    check=True,
                )
                print(f"Loaded {launchd_label(name)}")

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"{self._target()}/{launchd_label(job_name)}"],
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
        unit_dir = context.unit_dir or default_launch_agents_dir()
        chunks: list[str] = []
        for plist_path in sorted(unit_dir.glob(f"{PREFIX}*.plist")):
            name = plist_path.stem[len(PREFIX):]
            result = subprocess.run(
                ["launchctl", "print", f"{self._target()}/{launchd_label(name)}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            chunks.append(result.stdout or result.stderr)
        return "\n".join(chunk.rstrip() for chunk in chunks if chunk)

    def check_manager(self) -> str | None:
        result = subprocess.run(
            ["launchctl", "print", self._target()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        if result.returncode == 0:
            return None
        return f"launchd user manager: {result.stderr.strip() or 'unresponsive'}"

    def get_agent_command_template(self) -> str | None:
        return os.environ.get("AI_AGENT_COMMAND_TEMPLATE")

    def check_job_active(self, job_name: str) -> bool:
        result = subprocess.run(
            ["launchctl", "print", f"{self._target()}/{launchd_label(job_name)}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        return result.returncode == 0

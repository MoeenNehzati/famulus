"""macOS launchd scheduler backend for recurring-tasks.

Registration
------------
Each job becomes a plist at ``~/Library/LaunchAgents/ai-<name>.plist``,
labeled ``com.famulus.ai.<name>``.

``ProgramArguments`` invokes the launch resolver directly as ``argv[0]`` with
no separate interpreter, which works because the resolver carries its own
shebang and launchd execs it like any other program.

``StartCalendarInterval`` is computed from the cron expression -- one entry,
or a list when a step expands within an hour (``*/15 9 * * *`` becomes four
quarter-hour intervals). ``StandardOutPath``/``StandardErrorPath`` both point
at the job's run log, alongside the runner's own writes to it.

Load and reload with ``launchctl bootout gui/<uid> <plist>`` followed by
``launchctl bootstrap gui/<uid> <plist>``; sync performs the bootout
defensively first, so re-syncing an already-loaded job is safe.

Triggering does NOT block: ``launchctl kickstart -k`` returns as soon as the
trigger is accepted, not when the job finishes -- which is why callers must
read the run outcome record rather than the trigger result.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

from ._base_backend import ScheduleContext, ScheduleJob, registration_token
from officina.recurring.native import launchd_label as managed_launchd_label

PREFIX = "ai-"
LABEL_PREFIX = "com.famulus.ai."


def default_launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def launchd_label(job_name: str, installation_id: str = "standard") -> str:
    return managed_launchd_label(job_name, installation_id)


def plist_name(job_name: str, installation_id: str = "standard") -> str:
    return f"{PREFIX}{registration_token(installation_id)}{job_name}.plist"


def _job_from_plist_name(name: str, installation_id: str) -> str | None:
    prefix = f"{PREFIX}{registration_token(installation_id)}"
    if not name.startswith(prefix) or not name.endswith(".plist"):
        return None
    job_name = name[len(prefix) : -len(".plist")]
    if installation_id == "standard" and re.match(
        r"dev-[0-9a-f]{32}-", job_name
    ):
        return None
    return job_name


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
    runtime_resolver: Path,
    schedule: str,
    installation_id: str = "standard",
    log_root: Path | None = None,
) -> bytes:
    """Generate a launchd plist for one recurring job."""
    payload = {
        "Label": launchd_label(job_name, installation_id),
        "ProgramArguments": [
            str(runtime_resolver),
            "-m",
            "officina.recurring.executor",
            "--descriptor",
            str(jobs_file.parent / "schedule-descriptor.json"),
            "--job",
            job_name,
            "--log-root",
            str(log_root or jobs_file.parent / "logs"),
        ],
        "StandardErrorPath": str(log_file),
        "StandardOutPath": str(log_file),
        "StartCalendarInterval": cron_to_launchd_intervals(schedule),
    }
    if description:
        payload["ProcessType"] = "Background"
    return plistlib.dumps(payload, sort_keys=True)


class OSXScheduleBackend:
    name = "macos-launchd"

    def _target(self) -> str:
        getuid = getattr(os, "getuid", lambda: 0)
        return f"gui/{getuid()}"

    def _bootout_by_label_if_loaded(self, name: str, installation_id: str = "standard") -> None:
        """Unload a label if launchd currently has it loaded, regardless of
        which plist path it was loaded from.

        A prior sync (or an earlier release using a different unit_dir
        convention) may have this label loaded under a stale plist path.
        ``launchctl bootout <target> <path>`` only unloads the job if
        ``<path>`` is exactly what launchd currently has loaded for the
        label, so a path-form bootout silently no-ops in the stale-path
        case. Probing by label first and, if loaded, bootout-ing by the
        service-target (label) form works regardless of which path was
        originally used to load it.
        """
        service_target = f"{self._target()}/{launchd_label(name, installation_id)}"
        probe = subprocess.run(
            ["launchctl", "print", service_target],
            capture_output=True,
        )
        if probe.returncode == 0:
            subprocess.run(
                ["launchctl", "bootout", service_target],
                capture_output=True,
            )

    def sync(self, jobs: list[ScheduleJob], context: ScheduleContext) -> None:
        unit_dir = context.unit_dir or default_launch_agents_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        enabled_names: set[str] = set()
        executor = context.skill_dir / "_job_executor.py"

        for job in jobs:
            if not job.enabled:
                continue
            enabled_names.add(job.name)
            log_file = context.log_dir / job.name / "run.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            plist_path = unit_dir / plist_name(job.name, context.installation_id)
            plist_path.write_bytes(
                plist_content(
                    job_name=job.name,
                    description=job.description,
                    jobs_file=context.jobs_file,
                    log_file=log_file,
                    executor=executor,
                    runtime_resolver=context.runtime_resolver,
                    schedule=job.schedule,
                    installation_id=context.installation_id,
                    log_root=context.log_dir,
                )
            )
            print(f"Synced '{job.name}' (launchd label={launchd_label(job.name, context.installation_id)})")

        selected_prefix = f"{PREFIX}{registration_token(context.installation_id)}"
        for plist_path in sorted(unit_dir.glob(f"{selected_prefix}*.plist")):
            name = _job_from_plist_name(plist_path.name, context.installation_id)
            if name is None:
                continue
            if name not in enabled_names:
                if context.live:
                    self._bootout_by_label_if_loaded(name, context.installation_id)
                plist_path.unlink(missing_ok=True)
                print(f"Removed disabled job: '{name}'")

        if context.live:
            for name in sorted(enabled_names):
                plist_path = unit_dir / plist_name(name, context.installation_id)
                self._bootout_by_label_if_loaded(name, context.installation_id)
                subprocess.run(
                    ["launchctl", "bootstrap", self._target(), str(plist_path)],
                    check=True,
                )
                print(f"Loaded {launchd_label(name, context.installation_id)}")

    def test(self, job_name: str, context: ScheduleContext) -> bool:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"{self._target()}/{launchd_label(job_name, context.installation_id)}"],
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
        selected_prefix = f"{PREFIX}{registration_token(context.installation_id)}"
        for plist_path in sorted(unit_dir.glob(f"{selected_prefix}*.plist")):
            name = _job_from_plist_name(plist_path.name, context.installation_id)
            if name is None:
                continue
            result = subprocess.run(
                ["launchctl", "print", f"{self._target()}/{launchd_label(name, context.installation_id)}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            chunks.append(result.stdout or result.stderr)
        return "\n".join(chunk.rstrip() for chunk in chunks if chunk)

    def registrations_present(self, context: ScheduleContext) -> bool:
        unit_dir = context.unit_dir or default_launch_agents_dir()
        selected_prefix = f"{PREFIX}{registration_token(context.installation_id)}"
        return any(
            _job_from_plist_name(path.name, context.installation_id) is not None
            for path in unit_dir.glob(f"{selected_prefix}*.plist")
        )

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

    def job_search_dirs(self) -> list[Path] | None:
        """launchd jobs inherit the ambient PATH; nothing is pinned here."""
        return None

    def check_job_active(self, job_name: str, context: ScheduleContext | None = None) -> bool:
        installation_id = context.installation_id if context is not None else "standard"
        result = subprocess.run(
            ["launchctl", "print", f"{self._target()}/{launchd_label(job_name, installation_id)}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        return result.returncode == 0

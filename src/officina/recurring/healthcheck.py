from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from officina.common.atomic_files import atomic_replace_bytes
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from .native import (
    launchd_label,
    linux_session_environment,
    linux_names,
    load_jobs,
    render_linux_service,
    render_linux_timer,
    render_macos_plist,
    render_windows_wrapper,
    windows_task_name,
    windows_wrapper_name,
)
from .records import read_record
from .runtime import ManagedSchedule, load_managed_schedule


def _manager_failure() -> str | None:
    if sys.platform.startswith("linux"):
        result = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=linux_session_environment())
        return None if result.stdout.strip() in {"running", "degraded"} else "systemd user manager is unavailable"
    if sys.platform == "darwin":
        return None if subprocess.run(["launchctl", "print", f"gui/{os.getuid()}"], capture_output=True).returncode == 0 else "launchd user manager is unavailable"
    return None if subprocess.run(["schtasks", "/Query"], capture_output=True).returncode == 0 else "Windows Task Scheduler is unavailable"


def _registration_failure(schedule: ManagedSchedule, job: dict[str, object]) -> str | None:
    name = str(job["name"])
    root = schedule.native_registration_root
    if sys.platform.startswith("linux"):
        service, timer = linux_names(name, schedule.installation_id)
        expected = {root / service: render_linux_service(schedule, job), root / timer: render_linux_timer(schedule, job)}
        for path, content in expected.items():
            try:
                if path.read_text(encoding="utf-8") != content:
                    return f"{name}: managed registration is missing or stale ({path})"
            except OSError:
                return f"{name}: managed registration is missing ({path})"
        active = subprocess.run(["systemctl", "--user", "is-active", timer], capture_output=True, env=linux_session_environment()).returncode == 0
    elif sys.platform == "darwin":
        path = root / f"ai-{name}.plist"
        try:
            stale = path.read_bytes() != render_macos_plist(schedule, job)
        except OSError:
            stale = True
        if stale:
            return f"{name}: managed registration is missing or stale ({path})"
        active = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{launchd_label(name, schedule.installation_id)}"], capture_output=True).returncode == 0
    else:
        path = root / windows_wrapper_name(name, schedule.installation_id)
        try:
            stale = path.read_text(encoding="utf-8") != render_windows_wrapper(schedule, job).replace("\r\n", "\n")
        except OSError:
            stale = True
        if stale:
            return f"{name}: managed wrapper is missing or stale ({path})"
        active = subprocess.run(["schtasks", "/Query", "/TN", windows_task_name(name, schedule.installation_id)], capture_output=True).returncode == 0
    return None if active else f"{name}: registration is not active"


def check(schedule: ManagedSchedule) -> list[str]:
    failures: list[str] = []
    manager = _manager_failure()
    if manager:
        failures.append(manager)
    now = dt.datetime.now(dt.timezone.utc)
    for job in load_jobs(schedule):
        if job.get("enabled") is not True:
            continue
        registration = _registration_failure(schedule, job)
        if registration:
            failures.append(registration)
        name = str(job["name"])
        marker = schedule.log_root / name / "running.json"
        if marker.exists():
            age = now - dt.datetime.fromtimestamp(marker.stat().st_mtime, tz=dt.timezone.utc)
            if age > dt.timedelta(seconds=4200):
                failures.append(f"{name}: run is stale in flight")
            continue
        record = read_record(log_root=schedule.log_root, job_name=name)
        if record is None:
            failures.append(f"{name}: no completed run recorded")
        elif record.get("success") is not True:
            failures.append(f"{name}: {record.get('reason') or 'last run failed'}")
    return failures


def run(schedule: ManagedSchedule, *, cron: bool) -> int:
    try:
        failures = check(schedule)
    except Exception as exc:
        failures = [f"Failed to load or check recurring jobs: {exc}"]
    health_root = schedule.log_root / "healthcheck"
    health_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    summary_path = health_root / "last-failure.txt"
    if failures:
        summary = "\n".join(failures) + "\n"
        atomic_replace_bytes(
            summary_path, summary.encode("utf-8"), allowed_root=health_root, mode=0o600
        )
        lines = [*(f"FAIL: {failure}" for failure in failures), f"FAIL: {len(failures)} problem(s) found"]
    else:
        summary_path.unlink(missing_ok=True)
        lines = ["OK: recurring tasks healthy"]
    for line in lines:
        print(line)
    if not cron:
        report = health_root / "run.log"
        with report.open("a", encoding="utf-8") as stream:
            stream.write(f"=== healthcheck {dt.datetime.now(dt.timezone.utc).isoformat()} ===\n")
            stream.write("\n".join(lines) + "\n")
    return 1 if failures else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--log-root", type=Path, required=True)
    parser.add_argument("--cron", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        schedule = load_managed_schedule(runtime_root=args.runtime_root, descriptor_path=args.descriptor, log_root=args.log_root)
        return run(schedule, cron=args.cron)
    except Exception as exc:
        print(f"recurring healthcheck: {exc}", file=sys.stderr)
        return 1


class Interface(PythonArgvMachineInterface):
    prog = "famulus-recurring-healthcheck"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Interface", "check", "main", "run"]

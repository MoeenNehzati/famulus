from __future__ import annotations

import os
import csv
import plistlib
import re
import subprocess
import sys
import json
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from officina.install.context import InstallationContext

from .runtime import ManagedSchedule, native_registration_root
from officina.common.atomic_files import atomic_replace_bytes
from .jobs import confined_child, validate_job, validate_job_name, validate_jobs_payload


def linux_session_environment() -> dict[str, str]:
    runtime = f"/run/user/{os.getuid()}"
    return {
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }


def registration_token(installation_id: str) -> str:
    return "" if installation_id == "standard" else f"{installation_id}-"


def executor_argv(schedule: ManagedSchedule, job_name: str) -> list[str]:
    job_name = validate_job_name(job_name)
    return [
        str(schedule.runtime_resolver), "-m", "officina.recurring.executor",
        "--descriptor", str(schedule.descriptor_path), "--job", job_name,
        "--log-root", str(schedule.log_root),
    ]


def windows_executor_argv(schedule: ManagedSchedule, job_name: str) -> list[str]:
    if schedule.bootstrap_python is None:
        raise ValueError("Windows schedule descriptor has no validated bootstrap interpreter")
    return [str(schedule.bootstrap_python), *executor_argv(schedule, job_name)]


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def _environment(schedule: ManagedSchedule) -> list[tuple[str, str]]:
    values = []
    for name, value in schedule.environment.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"invalid environment name: {name!r}")
        if "\r" in value or "\n" in value:
            raise ValueError(f"environment {name} must not contain CR or LF")
        values.append((name, value))
    return sorted(values)


def cron_to_systemd(value: str) -> str:
    minute, hour, dom, month, dow = value.split()
    if dom != "*" or month != "*":
        raise ValueError("only wildcard day-of-month and month are supported")
    weekdays = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}
    day = "" if dow == "*" else weekdays[dow] + " "
    if minute.startswith("*/"):
        clock = f"*:00/{int(minute[2:])}:00"
    else:
        rendered_hour = f"00/{int(hour[2:])}" if hour.startswith("*/") else hour
        clock = f"{rendered_hour if rendered_hour != '*' else '*'}:{int(minute):02d}:00"
    return f"{day}*-*-* {clock}"


def linux_names(job_name: str, installation_id: str) -> tuple[str, str]:
    job_name = validate_job_name(job_name)
    stem = f"ai-{registration_token(installation_id)}{job_name}"
    return stem + ".service", stem + ".timer"


def render_linux_service(schedule: ManagedSchedule, job: Mapping[str, object]) -> str:
    job = validate_job(job)
    argv = executor_argv(schedule, str(job["name"]))
    rendered_environment = "".join(
        f"Environment={_systemd_quote(name + '=' + value)}\n"
        for name, value in _environment(schedule)
    )
    return (
        "[Unit]\n" f"Description=AI job: {job.get('description', job['name'])}\n\n"
        "[Service]\nType=oneshot\n"
        + rendered_environment
        + f"Environment={_systemd_quote('DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus')}\n"
        + "ExecStart=" + " ".join(_systemd_quote(str(part)) for part in argv) + "\n"
    )


def render_linux_timer(schedule: ManagedSchedule, job: Mapping[str, object]) -> str:
    job = validate_job(job)
    service, _ = linux_names(str(job["name"]), schedule.installation_id)
    return (
        "[Unit]\n" f"Description=Timer for AI job: {job.get('description', job['name'])}\n\n"
        "[Timer]\n" f"OnCalendar={cron_to_systemd(str(job['schedule']))}\n"
        "Persistent=true\n" f"Unit={service}\n\n[Install]\nWantedBy=timers.target\n"
    )


def launchd_label(job_name: str, installation_id: str) -> str:
    if job_name:
        job_name = validate_job_name(job_name)
    token = registration_token(installation_id).removesuffix("-")
    return f"com.famulus.ai.{token + '.' if token else ''}{job_name}"


def _cron_values(value: str) -> dict[str, int] | list[dict[str, int]]:
    minute, hour, dom, month, dow = value.split()
    if dom != "*" or month != "*":
        raise ValueError("launchd renderer requires wildcard day-of-month and month")

    def expand(field: str, low: int, high: int) -> list[int]:
        if field == "*": return list(range(low, high + 1))
        match = re.fullmatch(r"\*/(\d+)", field)
        if match:
            step = int(match.group(1))
            if step <= 0: raise ValueError(f"invalid cron step: {field}")
            return list(range(low, high + 1, step))
        if field.isdigit() and low <= int(field) <= high: return [int(field)]
        raise ValueError(f"unsupported cron field: {field}")

    weekday = None if dow == "*" else (0 if dow == "7" else int(dow))
    if weekday is not None and not 0 <= weekday <= 6:
        raise ValueError(f"invalid day of week: {dow}")
    intervals = []
    for selected_hour in expand(hour, 0, 23):
        for selected_minute in expand(minute, 0, 59):
            interval = {"Hour": selected_hour, "Minute": selected_minute}
            if weekday is not None: interval["Weekday"] = weekday
            intervals.append(interval)
    return intervals[0] if len(intervals) == 1 else intervals


def render_macos_plist(schedule: ManagedSchedule, job: Mapping[str, object]) -> bytes:
    job = validate_job(job)
    name = str(job["name"])
    log = schedule.log_root / name / "run.log"
    environment = dict(_environment(schedule))
    return plistlib.dumps({
        "Label": launchd_label(name, schedule.installation_id),
        "ProgramArguments": executor_argv(schedule, name),
        "StandardErrorPath": str(log), "StandardOutPath": str(log),
        "StartCalendarInterval": _cron_values(str(job["schedule"])),
        "ProcessType": "Background",
        "EnvironmentVariables": environment,
    }, sort_keys=True)


def windows_task_name(job_name: str, installation_id: str) -> str:
    if job_name:
        job_name = validate_job_name(job_name)
    token = registration_token(installation_id)
    return f"Famulus-AI-{token}ai-{job_name}" if token else f"Famulus-AI-ai-{job_name}"


def windows_wrapper_name(job_name: str, installation_id: str) -> str:
    if job_name:
        job_name = validate_job_name(job_name)
    return windows_task_name(job_name, installation_id) + ".cmd"


def _legacy_managed_launchd_label(job_name: str, installation_id: str) -> str:
    return f"com.famulus.ai.{registration_token(installation_id)}{job_name}"


def _legacy_managed_windows_task_name(job_name: str, installation_id: str) -> str:
    return f"Famulus-AI-ai-{registration_token(installation_id)}{job_name}"


def _legacy_managed_windows_wrapper_name(job_name: str, installation_id: str) -> str:
    return f"ai-{registration_token(installation_id)}{job_name}.cmd"


def _cmd_quote(value: str) -> str:
    return '"' + value.replace("%", "%%").replace('"', '""') + '"'


def render_windows_wrapper(schedule: ManagedSchedule, job: Mapping[str, object]) -> str:
    job = validate_job(job)
    name = str(job["name"])
    directory = schedule.log_root / name
    command = " ".join(_cmd_quote(part) for part in windows_executor_argv(schedule, name))
    environment = "".join(
        f'set "{name}={value.replace("%", "%%")}"\r\n'
        for name, value in _environment(schedule)
    )
    return "@echo off\r\nsetlocal DisableDelayedExpansion\r\n" + environment + f"if not exist {_cmd_quote(str(directory))} mkdir {_cmd_quote(str(directory))}\r\n" + command + f" >> {_cmd_quote(str(directory / 'scheduler.log'))} 2>&1\r\nexit /b %errorlevel%\r\n"


def cron_to_schtasks_args(value: str) -> list[str]:
    minute, hour, dom, month, dow = value.split()
    if dom != "*" or month != "*": raise ValueError("schtasks requires wildcard day-of-month and month")
    weekdays = {"0": "SUN", "7": "SUN", "1": "MON", "2": "TUE", "3": "WED", "4": "THU", "5": "FRI", "6": "SAT"}
    weekday = None if dow == "*" else weekdays.get(dow)
    if dow != "*" and weekday is None: raise ValueError(f"invalid day of week: {dow}")
    if minute == "*" and hour == "*" and weekday is None: return ["/SC", "MINUTE", "/MO", "1"]
    step = re.fullmatch(r"\*/(\d+)", minute)
    if step and hour == "*" and weekday is None:
        if int(step.group(1)) <= 0: raise ValueError(f"invalid cron step: {minute}")
        return ["/SC", "MINUTE", "/MO", step.group(1)]
    if not minute.isdigit() or not 0 <= int(minute) <= 59: raise ValueError(f"unsupported minute: {minute}")
    if hour == "*" and weekday is None: return ["/SC", "HOURLY", "/MO", "1", "/ST", f"00:{int(minute):02d}"]
    if not hour.isdigit() or not 0 <= int(hour) <= 23: raise ValueError(f"unsupported hour: {hour}")
    start = f"{int(hour):02d}:{int(minute):02d}"
    return ["/SC", "DAILY", "/ST", start] if weekday is None else ["/SC", "WEEKLY", "/D", weekday, "/ST", start]


def _context_job(name: str, prefix: str, suffix: str, installation_id: str) -> str | None:
    if not name.startswith(prefix) or not name.endswith(suffix): return None
    job = name[len(prefix):] if not suffix else name[len(prefix):len(name) - len(suffix)]
    if installation_id == "standard" and re.match(r"dev-[0-9a-f]{32}-", job): return None
    try:
        return validate_job_name(job)
    except ValueError:
        return None


def _windows_existing_tasks() -> list[str]:
    inventory = _windows_task_inventory()
    return list(inventory.entries) if inventory.available else []


@dataclass(frozen=True)
class _NativeInventory:
    available: bool
    entries: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class RegistrationNamespaceStatus:
    certain: bool
    registrations_present: bool
    names: tuple[str, ...] = ()
    detail: str = ""


def inspect_registration_namespace(
    *,
    installation_id: str,
    state_root: Path,
    native_registration_root: Path,
    platform: str,
) -> RegistrationNamespaceStatus:
    """Read recurring-owned durable and native state without mutating it."""
    names: set[str] = set()
    state_present = False
    for filename in ("registrations.json", "registrations.pending.json"):
        path = state_root / filename
        if not path.exists():
            continue
        if filename == "registrations.pending.json":
            state_present = True
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            registrations = payload.get("registrations")
            expected_keys = {"schema_version", "installation_id", "registrations"}
            if filename == "registrations.pending.json":
                expected_keys.add("publication_state")
            if (
                not isinstance(payload, dict)
                or set(payload) != expected_keys
                or payload.get("schema_version") != 1
                or payload.get("installation_id") != installation_id
                or (
                    filename == "registrations.pending.json"
                    and payload.get("publication_state") != "pending"
                )
                or not isinstance(registrations, list)
            ):
                raise ValueError("state does not match the selected installation")
            names.update(validate_job_name(name) for name in registrations)
        except (OSError, UnicodeError, ValueError) as exc:
            return RegistrationNamespaceStatus(False, True, detail=f"{path}: {exc}")

    if installation_id == "standard":
        owner = native_registration_root / "install-owner.json"
    else:
        owner = native_registration_root / f"install-owner-{installation_id}.json"
    state_present = state_present or owner.exists()

    if platform.startswith("linux"):
        prefix = f"ai-{registration_token(installation_id)}"
        inventory = _systemd_unit_inventory(
            prefix, installation_id, linux_session_environment()
        )
        patterns = ((f"{prefix}*.service", ".service"), (f"{prefix}*.timer", ".timer"))
    elif platform == "darwin":
        prefix = f"ai-{registration_token(installation_id)}"
        inventory = _launchd_label_inventory(installation_id)
        patterns = ((f"{prefix}*.plist", ".plist"),)
    elif platform == "win32":
        prefix = windows_task_name("", installation_id)
        inventory = _windows_task_inventory()
        patterns = ((f"{prefix}*.cmd", ".cmd"),)
    else:
        return RegistrationNamespaceStatus(False, state_present, detail=f"unsupported scheduler platform: {platform}")
    if not inventory.available:
        return RegistrationNamespaceStatus(False, state_present, tuple(sorted(names)), inventory.detail)

    for entry in inventory.entries:
        short = entry.rsplit("\\", 1)[-1]
        suffix = ".timer" if short.endswith(".timer") else ".service" if short.endswith(".service") else ""
        native_prefix = launchd_label("", installation_id) if platform == "darwin" else prefix
        job = _context_job(short, native_prefix, suffix, installation_id)
        if job is not None:
            names.add(job)
    for pattern, suffix in patterns:
        for path in native_registration_root.glob(pattern):
            job = _context_job(path.name, prefix, suffix, installation_id)
            if job is None:
                return RegistrationNamespaceStatus(False, True, tuple(sorted(names)), f"invalid owned artifact: {path}")
            names.add(job)
    if platform.startswith("linux"):
        try:
            marker = _sentinel_marker(installation_id)
            if any(line.rstrip().endswith(marker) for line in _read_crontab().splitlines()):
                state_present = True
        except RuntimeError as exc:
            return RegistrationNamespaceStatus(False, True, tuple(sorted(names)), str(exc))
    return RegistrationNamespaceStatus(True, state_present or bool(names), tuple(sorted(names)))


def _windows_task_inventory() -> _NativeInventory:
    result = subprocess.run(
        ["schtasks", "/Query", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return _NativeInventory(False, detail=detail or f"exit {result.returncode}")
    return _NativeInventory(
        True,
        tuple(row[0] for row in csv.reader((result.stdout or "").splitlines()) if row),
    )


def _launchd_service_state(service: str) -> tuple[bool | None, str]:
    result = subprocess.run(
        ["launchctl", "print", service], capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    normalized = detail.lower()
    if result.returncode == 113 and (
        "could not find service" in normalized or "service not found" in normalized
    ):
        return False, detail
    return None, detail or f"exit {result.returncode}"


def _launchd_label_inventory(installation_id: str) -> _NativeInventory:
    result = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return _NativeInventory(False, detail=detail or f"exit {result.returncode}")
    prefix = launchd_label("", installation_id)
    entries = []
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if not fields:
            continue
        label = fields[-1]
        if not label.startswith(prefix):
            continue
        remainder = label[len(prefix):]
        if not remainder:
            continue
        if installation_id == "standard" and re.match(
            r"dev-[0-9a-f]{32}\.", remainder
        ):
            continue
        entries.append(label)
    return _NativeInventory(True, tuple(entries))


def _systemd_unit_state(
    unit: str, query: str, environment: Mapping[str, str]
) -> tuple[bool | None, str]:
    result = subprocess.run(
        ["systemctl", "--user", query, unit],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    detail = (result.stderr or result.stdout or "").strip()
    state = (result.stdout or "").strip().lower()
    if query == "is-active":
        if result.returncode == 0 and state in {"", "active", "reloading", "activating"}:
            return True, detail
        if state in {"inactive", "failed", "deactivating", "unknown", "not-found"}:
            return False, detail
        if result.returncode in {3, 4} and not detail:
            return False, detail
    elif query == "is-enabled":
        if result.returncode == 0 and state in {
            "", "enabled", "enabled-runtime", "linked", "linked-runtime", "alias"
        }:
            return True, detail
        if state in {
            "disabled", "disabled-runtime", "static", "indirect", "generated",
            "transient", "masked", "masked-runtime", "not-found", "bad",
        }:
            return False, detail
        if result.returncode in {1, 3, 4} and not detail:
            return False, detail
    return None, detail or f"exit {result.returncode}"


def _systemd_unit_inventory(
    prefix: str, installation_id: str, environment: Mapping[str, str]
) -> _NativeInventory:
    result = subprocess.run(
        [
            "systemctl", "--user", "list-unit-files",
            f"{prefix}*.timer", f"{prefix}*.service", "--no-legend", "--no-pager",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if result.returncode == 1 and not detail:
            return _NativeInventory(True)
        return _NativeInventory(False, detail=detail or f"exit {result.returncode}")
    selected = []
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if not fields:
            continue
        unit = fields[0]
        suffix = ".timer" if unit.endswith(".timer") else ".service"
        if _context_job(unit, prefix, suffix, installation_id) is not None:
            selected.append(unit)
    return _NativeInventory(True, tuple(selected))


def load_jobs(schedule: ManagedSchedule) -> list[dict[str, object]]:
    import yaml
    try:
        payload = yaml.safe_load(schedule.jobs_file.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read canonical jobs file: {exc}") from exc
    return validate_jobs_payload(payload)


def _summary_path(schedule: ManagedSchedule) -> Path:
    return schedule.state_root / "registrations.json"


def _pending_path(schedule: ManagedSchedule) -> Path:
    return schedule.state_root / "registrations.pending.json"


def _registration_payload(schedule: ManagedSchedule, registrations: list[str]) -> dict[str, object]:
    selected = sorted(validate_job_name(name) for name in registrations)
    if len(selected) != len(set(selected)):
        raise ValueError("registration summary contains duplicate job names")
    return {
        "schema_version": 1,
        "installation_id": schedule.installation_id,
        "registrations": selected,
    }


def _write_registration_state(
    schedule: ManagedSchedule, registrations: list[str], *, pending: bool
) -> None:
    payload = _registration_payload(schedule, registrations)
    payload["publication_state"] = "pending" if pending else "settled"
    if not pending:
        payload.pop("publication_state")
    schedule.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_replace_bytes(
        _pending_path(schedule) if pending else _summary_path(schedule),
        raw,
        allowed_root=schedule.state_root,
        mode=0o600,
    )


def _settle_registration_summary(schedule: ManagedSchedule, registrations: list[str]) -> None:
    expected = _registration_payload(schedule, registrations)
    write_registration_summary(schedule, registrations)
    try:
        observed = json.loads(_summary_path(schedule).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"cannot verify recurring registration summary: {exc}") from exc
    if observed != expected:
        raise RuntimeError("recurring registration summary verification failed")
    pending = confined_child(schedule.state_root, _pending_path(schedule).name)
    if pending.exists() and pending.resolve(strict=False) != pending:
        raise RuntimeError("pending registration state is not a confined regular path")
    pending.unlink(missing_ok=True)


def write_registration_summary(
    schedule: ManagedSchedule, registrations: list[str]
) -> None:
    _write_registration_state(schedule, registrations, pending=False)


def _sentinel_marker(installation_id: str) -> str:
    base = "# ai-recurring-healthcheck"
    return base if installation_id == "standard" else f"{base}:{installation_id}"


def _read_crontab() -> str:
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False
        )
    except FileNotFoundError:
        return ""
    if result.returncode == 0:
        return result.stdout or ""
    if "no crontab for" in (result.stderr or "").lower():
        return ""
    raise RuntimeError(
        "refusing to rewrite unreadable crontab: "
        + ((result.stderr or "").strip() or f"exit {result.returncode}")
    )


def _write_crontab(content: str) -> None:
    subprocess.run(
        ["crontab", "-"], input=content, text=True,
        encoding="utf-8", errors="strict", check=True,
    )


def _sentinel_line(schedule: ManagedSchedule) -> str:
    arguments = [
        str(schedule.runtime_resolver), "-m", "officina.recurring.healthcheck",
        "--descriptor", str(schedule.descriptor_path),
        "--log-root", str(schedule.log_root), "--cron",
    ]
    assignments = " ".join(
        f"{name}={shlex.quote(value)}" for name, value in _environment(schedule)
    )
    command = " ".join(shlex.quote(value) for value in arguments)
    log = shlex.quote(str(schedule.log_root / "healthcheck" / "run.log"))
    summary = shlex.quote(str(schedule.log_root / "healthcheck" / "last-failure.txt"))
    prefix = assignments + " " if assignments else ""
    return (
        f"0 */4 * * * {prefix}{command} >> {log} 2>&1 || "
        f"XDG_RUNTIME_DIR=/run/user/{os.getuid()} "
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{os.getuid()}/bus "
        f"/usr/bin/notify-send --urgency=critical 'Recurring tasks need attention' "
        f"\"$(cat {summary} 2>/dev/null || echo 'The recurring health check could not run.')\" "
        f"{_sentinel_marker(schedule.installation_id)}"
    )


def _update_sentinel(schedule: ManagedSchedule, *, remove: bool) -> None:
    existing = _read_crontab()
    marker = _sentinel_marker(schedule.installation_id)
    kept = [
        line for line in existing.splitlines(keepends=True)
        if not line.rstrip("\r\n").rstrip().endswith(marker)
    ]
    updated = "".join(kept)
    if not remove:
        if updated and not updated.endswith("\n"):
            updated += "\n"
        updated += _sentinel_line(schedule) + "\n"
    if updated != existing:
        _write_crontab(updated)


def _owner_path(schedule: ManagedSchedule) -> Path:
    name = (
        "install-owner.json"
        if schedule.installation_id == "standard"
        else f"install-owner-{schedule.installation_id}.json"
    )
    return schedule.native_registration_root / name


def _write_owner(schedule: ManagedSchedule) -> None:
    target = _owner_path(schedule)
    raw = (
        json.dumps(
            {"schema_version": 3, "installation_id": schedule.installation_id,
             "descriptor": str(schedule.descriptor_path)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    atomic_replace_bytes(target, raw, allowed_root=target.parent, mode=0o600)


def _native_child(schedule: ManagedSchedule, name: str) -> Path:
    return confined_child(schedule.native_registration_root, name)


def _write_native_bytes(schedule: ManagedSchedule, name: str, raw: bytes) -> Path:
    target = _native_child(schedule, name)
    atomic_replace_bytes(
        target, raw, allowed_root=schedule.native_registration_root, mode=0o600
    )
    return target


def _unlink_native(schedule: ManagedSchedule, name: str) -> None:
    target = _native_child(schedule, name)
    if target.is_symlink() or target.exists():
        resolved = target.resolve(strict=False)
        root = schedule.native_registration_root.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"refusing to unlink escaped recurring artifact: {target}") from exc
    target.unlink(missing_ok=True)


def _recorded_registration_names(schedule: ManagedSchedule) -> set[str]:
    names: set[str] = set()
    for path in (_summary_path(schedule), _pending_path(schedule)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"cannot read recurring registration state at {path}: {exc}") from exc
        registrations = payload.get("registrations") if isinstance(payload, dict) else None
        expected_keys = {"schema_version", "installation_id", "registrations"}
        if path == _pending_path(schedule):
            expected_keys.add("publication_state")
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_keys
            or payload.get("schema_version") != 1
            or payload.get("installation_id") != schedule.installation_id
            or (
                path == _pending_path(schedule)
                and payload.get("publication_state") != "pending"
            )
            or not isinstance(registrations, list)
        ):
            raise ValueError(f"recurring registration state at {path} has no registrations list")
        for value in registrations:
            names.add(validate_job_name(value))
    return names


def _teardown_candidates(schedule: ManagedSchedule) -> set[str]:
    names = _recorded_registration_names(schedule)
    try:
        names.update(str(job["name"]) for job in load_jobs(schedule))
    except (OSError, UnicodeError, ValueError, KeyError):
        pass
    return names


def _teardown_incomplete(schedule: ManagedSchedule, remaining: set[str]) -> None:
    selected = sorted(remaining) or ["<native-teardown-incomplete>"]
    raise RuntimeError(
        "recurring native teardown is incomplete for "
        f"{schedule.installation_id}: {', '.join(selected)}; "
        "retry recurring-tasks remove-context"
    )


def sync(schedule: ManagedSchedule) -> None:
    jobs = load_jobs(schedule)
    root = schedule.native_registration_root
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    enabled = [job for job in jobs if job.get("enabled") is True]
    selected_names = sorted(str(job["name"]) for job in enabled)
    _write_registration_state(schedule, selected_names, pending=True)
    if sys.platform.startswith("linux"):
        session_environment = linux_session_environment()
        for job in enabled:
            service, timer = linux_names(str(job["name"]), schedule.installation_id)
            _write_native_bytes(schedule, service, render_linux_service(schedule, job).encode("utf-8"))
            _write_native_bytes(schedule, timer, render_linux_timer(schedule, job).encode("utf-8"))
        prefix = f"ai-{registration_token(schedule.installation_id)}"
        enabled_names = {str(job["name"]) for job in enabled}
        for timer_path in sorted(root.glob(f"{prefix}*.timer")):
            name = _context_job(timer_path.name, prefix, ".timer", schedule.installation_id)
            if name is not None and name not in enabled_names:
                subprocess.run(["systemctl", "--user", "disable", "--now", timer_path.name], check=False, env=session_environment)
                _unlink_native(schedule, timer_path.name)
                _unlink_native(schedule, linux_names(name, schedule.installation_id)[0])
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, env=session_environment)
        for job in enabled:
            subprocess.run(["systemctl", "--user", "enable", "--now", linux_names(str(job['name']), schedule.installation_id)[1]], check=True, env=session_environment)
        _update_sentinel(schedule, remove=False)
    elif sys.platform == "darwin":
        target = f"gui/{os.getuid()}"
        enabled_names = {str(job["name"]) for job in enabled}
        prefix = f"ai-{registration_token(schedule.installation_id)}"
        legacy_prefix = _legacy_managed_launchd_label("", schedule.installation_id)
        current_label_prefix = launchd_label("", schedule.installation_id)
        legacy_services: set[str] = set()
        if legacy_prefix != current_label_prefix:
            legacy_services.update(
                _legacy_managed_launchd_label(str(job["name"]), schedule.installation_id)
                for job in jobs
            )
            listed = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            if listed.returncode == 0:
                legacy_services.update(
                    line.split()[-1] for line in listed.stdout.splitlines()
                    if line.split() and line.split()[-1].startswith(legacy_prefix)
                )
            for path in sorted(root.glob(f"{prefix}*.plist")):
                try:
                    label = plistlib.loads(path.read_bytes()).get("Label")
                except (OSError, plistlib.InvalidFileException, ValueError, TypeError):
                    continue
                if isinstance(label, str) and label.startswith(legacy_prefix):
                    legacy_services.add(label)
            for label in sorted(legacy_services):
                subprocess.run(["launchctl", "bootout", f"{target}/{label}"], capture_output=True)
        for path in sorted(root.glob(f"{prefix}*.plist")):
            name = _context_job(path.name, prefix, ".plist", schedule.installation_id)
            if name is not None and name not in enabled_names:
                service = f"{target}/{launchd_label(name, schedule.installation_id)}"
                if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
                    subprocess.run(["launchctl", "bootout", service], capture_output=True)
                _unlink_native(schedule, path.name)
        for job in enabled:
            name = f"ai-{registration_token(schedule.installation_id)}{job['name']}.plist"
            path = _write_native_bytes(schedule, name, render_macos_plist(schedule, job))
            service = f"{target}/{launchd_label(str(job['name']), schedule.installation_id)}"
            if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
                subprocess.run(["launchctl", "bootout", service], capture_output=True)
            subprocess.run(["launchctl", "bootstrap", target, str(path)], check=True)
    elif sys.platform == "win32":
        enabled_names = {str(job["name"]) for job in enabled}
        prefix = windows_task_name("", schedule.installation_id)
        existing_tasks = _windows_existing_tasks()
        legacy_prefix = _legacy_managed_windows_task_name("", schedule.installation_id)
        if legacy_prefix != prefix:
            for existing in existing_tasks:
                if existing.rsplit("\\", 1)[-1].startswith(legacy_prefix):
                    subprocess.run(["schtasks", "/Delete", "/TN", existing, "/F"], capture_output=True)
        legacy_wrapper_prefix = f"ai-{registration_token(schedule.installation_id)}"
        for path in sorted(root.glob(f"{legacy_wrapper_prefix}*.cmd")):
            if _context_job(path.name, legacy_wrapper_prefix, ".cmd", schedule.installation_id) is not None:
                _unlink_native(schedule, path.name)
        for existing in existing_tasks:
            short = existing.rsplit("\\", 1)[-1]
            name = _context_job(short, prefix, "", schedule.installation_id)
            if name is not None and name not in enabled_names:
                subprocess.run(["schtasks", "/Delete", "/TN", existing, "/F"], capture_output=True)
                _unlink_native(schedule, windows_wrapper_name(name, schedule.installation_id))
        for job in enabled:
            path = _write_native_bytes(
                schedule,
                windows_wrapper_name(str(job["name"]), schedule.installation_id),
                render_windows_wrapper(schedule, job).encode("utf-8"),
            )
            subprocess.run(["schtasks", "/Create", "/TN", windows_task_name(str(job['name']), schedule.installation_id), "/TR", f'cmd.exe /d /s /c "{path}"', "/F", *cron_to_schtasks_args(str(job["schedule"]))], check=True)
    else:
        raise RuntimeError(f"unsupported scheduler platform: {sys.platform}")
    _write_owner(schedule)
    _settle_registration_summary(schedule, selected_names)


@dataclass(frozen=True)
class _RegistrationTeardownContext:
    installation_id: str
    jobs_file: Path
    state_root: Path
    native_registration_root: Path


def remove_installation_context(
    context: InstallationContext, platform: str
) -> None:
    """Remove native recurring state for one explicit installation context."""
    schedule = _RegistrationTeardownContext(
        installation_id=context.installation_id,
        jobs_file=context.paths.recurring_config_root / "jobs.yaml",
        state_root=context.paths.recurring_state_root,
        native_registration_root=native_registration_root(context, platform),
    )
    _remove_context(schedule, platform)


def remove_context(schedule: ManagedSchedule) -> None:
    _remove_context(schedule, sys.platform)


def _remove_context(
    schedule: ManagedSchedule | _RegistrationTeardownContext, platform: str
) -> None:
    root = schedule.native_registration_root
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    candidates = _teardown_candidates(schedule)
    _write_registration_state(schedule, sorted(candidates), pending=True)
    remaining: set[str] = set()
    if platform.startswith("linux"):
        prefix = f"ai-{registration_token(schedule.installation_id)}"
        session_environment = linux_session_environment()
        inventory = _systemd_unit_inventory(
            prefix, schedule.installation_id, session_environment
        )
        if not inventory.available:
            _teardown_incomplete(schedule, candidates or {"<unit-inventory>"})
        for unit in inventory.entries:
            suffix = ".timer" if unit.endswith(".timer") else ".service"
            name = _context_job(unit, prefix, suffix, schedule.installation_id)
            if name is not None:
                candidates.add(name)
        for timer_path in sorted(root.glob(f"{prefix}*.timer")):
            name = _context_job(
                timer_path.name, prefix, ".timer", schedule.installation_id
            )
            if name is not None:
                candidates.add(name)
        for service_path in sorted(root.glob(f"{prefix}*.service")):
            name = _context_job(
                service_path.name, prefix, ".service", schedule.installation_id
            )
            if name is not None:
                candidates.add(name)
        for name in sorted(candidates):
            service, timer = linux_names(name, schedule.installation_id)
            timer_active, _ = _systemd_unit_state(
                timer, "is-active", session_environment
            )
            service_active, _ = _systemd_unit_state(
                service, "is-active", session_environment
            )
            timer_enabled, _ = _systemd_unit_state(
                timer, "is-enabled", session_environment
            )
            service_enabled, _ = _systemd_unit_state(
                service, "is-enabled", session_environment
            )
            states = (timer_active, service_active, timer_enabled, service_enabled)
            if any(state is None for state in states):
                remaining.add(name)
                continue
            failed = False
            for unit, active, enabled in (
                (timer, timer_active, timer_enabled),
                (service, service_active, service_enabled),
            ):
                if enabled:
                    result = subprocess.run(
                        ["systemctl", "--user", "disable", "--now", unit],
                        capture_output=True,
                        env=session_environment,
                    )
                elif active:
                    result = subprocess.run(
                        ["systemctl", "--user", "stop", unit],
                        capture_output=True,
                        env=session_environment,
                    )
                else:
                    continue
                if result.returncode != 0:
                    failed = True
                    break
            if failed:
                remaining.add(name)
                continue
            if any(states):
                post_states = (
                    _systemd_unit_state(timer, "is-active", session_environment)[0],
                    _systemd_unit_state(service, "is-active", session_environment)[0],
                    _systemd_unit_state(timer, "is-enabled", session_environment)[0],
                    _systemd_unit_state(service, "is-enabled", session_environment)[0],
                )
                if any(state is None or state for state in post_states):
                    remaining.add(name)
                    continue
            _unlink_native(schedule, timer)
            _unlink_native(schedule, service)
        reload_result = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            env=session_environment,
        )
        if reload_result.returncode != 0:
            remaining.update(candidates or {"<daemon-reload>"})
        elif not remaining:
            for name in sorted(candidates):
                service, timer = linux_names(name, schedule.installation_id)
                final_states = (
                    _systemd_unit_state(timer, "is-active", session_environment)[0],
                    _systemd_unit_state(service, "is-active", session_environment)[0],
                    _systemd_unit_state(timer, "is-enabled", session_environment)[0],
                    _systemd_unit_state(service, "is-enabled", session_environment)[0],
                )
                if (
                    any(state is None or state for state in final_states)
                    or (root / timer).exists()
                    or (root / service).exists()
                ):
                    remaining.add(name)
            final_inventory = _systemd_unit_inventory(
                prefix, schedule.installation_id, session_environment
            )
            if not final_inventory.available:
                remaining.update(candidates or {"<unit-inventory>"})
            else:
                for unit in final_inventory.entries:
                    suffix = ".timer" if unit.endswith(".timer") else ".service"
                    name = _context_job(unit, prefix, suffix, schedule.installation_id)
                    if name is not None:
                        remaining.add(name)
    elif platform == "darwin":
        prefix = f"ai-{registration_token(schedule.installation_id)}"
        target = f"gui/{os.getuid()}"
        inventory = _launchd_label_inventory(schedule.installation_id)
        if not inventory.available:
            _teardown_incomplete(schedule, candidates or {"<launchd-inventory>"})
        label_prefix = launchd_label("", schedule.installation_id)
        for label in inventory.entries:
            name = label[len(label_prefix):]
            if name:
                candidates.add(name)
        for path in sorted(root.glob(f"{prefix}*.plist")):
            name = _context_job(path.name, prefix, ".plist", schedule.installation_id)
            if name is not None:
                candidates.add(name)
        for name in sorted(candidates):
            path = root / f"{prefix}{name}.plist"
            service = f"{target}/{launchd_label(name, schedule.installation_id)}"
            active, _ = _launchd_service_state(service)
            if active is None:
                remaining.add(name)
                continue
            if active:
                result = subprocess.run(
                    ["launchctl", "bootout", service], capture_output=True
                )
                post_active, _ = _launchd_service_state(service)
                if result.returncode != 0 or post_active is None or post_active:
                    remaining.add(name)
                    continue
            _unlink_native(schedule, path.name)
            final_active, _ = _launchd_service_state(service)
            if final_active is None or final_active or path.exists():
                remaining.add(name)
        if not remaining:
            final_inventory = _launchd_label_inventory(schedule.installation_id)
            if not final_inventory.available:
                remaining.update(candidates or {"<launchd-inventory>"})
            else:
                for label in final_inventory.entries:
                    name = label[len(label_prefix):]
                    if name:
                        remaining.add(name)
    elif platform == "win32":
        prefix = windows_task_name("", schedule.installation_id)
        inventory = _windows_task_inventory()
        if not inventory.available:
            _teardown_incomplete(schedule, candidates or {"<task-inventory>"})
        existing_tasks = list(inventory.entries)
        selected_tasks: dict[str, str] = {}
        for existing in existing_tasks:
            short = existing.rsplit("\\", 1)[-1]
            name = _context_job(short, prefix, "", schedule.installation_id)
            if name is not None:
                candidates.add(name)
                selected_tasks[name] = existing
        wrapper_prefix = windows_wrapper_name("", schedule.installation_id).removesuffix(".cmd")
        for path in sorted(root.glob(f"{wrapper_prefix}*.cmd")):
            name = _context_job(path.name, wrapper_prefix, ".cmd", schedule.installation_id)
            if name is not None:
                candidates.add(name)
        for name in sorted(candidates):
            existing = selected_tasks.get(name)
            if existing is not None:
                result = subprocess.run(
                    ["schtasks", "/Delete", "/TN", existing, "/F"],
                    capture_output=True,
                )
                if result.returncode != 0:
                    remaining.add(name)
                    continue
                current = _windows_task_inventory()
                if not current.available or any(
                    item.rsplit("\\", 1)[-1]
                    == windows_task_name(name, schedule.installation_id)
                    for item in current.entries
                ):
                    remaining.add(name)
                    continue
            _unlink_native(schedule, windows_wrapper_name(name, schedule.installation_id))
        final_inventory = _windows_task_inventory()
        if not final_inventory.available:
            remaining.update(candidates or {"<task-inventory>"})
        else:
            for existing in final_inventory.entries:
                short = existing.rsplit("\\", 1)[-1]
                name = _context_job(short, prefix, "", schedule.installation_id)
                if name is not None:
                    remaining.add(name)
            for name in candidates:
                if (root / windows_wrapper_name(name, schedule.installation_id)).exists():
                    remaining.add(name)
    else:
        raise RuntimeError(f"unsupported scheduler platform: {platform}")
    if remaining:
        _teardown_incomplete(schedule, remaining)
    if platform.startswith("linux"):
        try:
            _update_sentinel(schedule, remove=True)
            marker = _sentinel_marker(schedule.installation_id)
            if any(
                line.rstrip("\r\n").rstrip().endswith(marker)
                for line in _read_crontab().splitlines(keepends=True)
            ):
                _teardown_incomplete(schedule, candidates or {"<sentinel>"})
        except RuntimeError:
            _teardown_incomplete(schedule, candidates or {"<sentinel>"})
    _unlink_native(schedule, _owner_path(schedule).name)
    _settle_registration_summary(schedule, [])


def status(schedule: ManagedSchedule) -> str:
    if sys.platform.startswith("linux"):
        result = subprocess.run(["systemctl", "--user", "list-timers", f"ai-{registration_token(schedule.installation_id)}*.timer", "--no-pager"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=linux_session_environment())
    elif sys.platform == "darwin":
        chunks = []
        prefix = f"ai-{registration_token(schedule.installation_id)}"
        for path in sorted(schedule.native_registration_root.glob(f"{prefix}*.plist")):
            name = _context_job(path.name, prefix, ".plist", schedule.installation_id)
            if name is not None:
                selected = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{launchd_label(name, schedule.installation_id)}"], capture_output=True, text=True, encoding="utf-8", errors="replace")
                chunks.append(selected.stdout or selected.stderr)
        return "\n".join(chunk.rstrip() for chunk in chunks if chunk)
    else:
        prefix = windows_task_name("", schedule.installation_id)
        return "\n".join(name for name in _windows_existing_tasks() if _context_job(name.rsplit("\\", 1)[-1], prefix, "", schedule.installation_id) is not None)
    return result.stdout or result.stderr


def trigger(schedule: ManagedSchedule, job_name: str) -> bool:
    if sys.platform.startswith("linux"):
        command = ["systemctl", "--user", "start", "--wait", linux_names(job_name, schedule.installation_id)[0]]
    elif sys.platform == "darwin":
        command = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{launchd_label(job_name, schedule.installation_id)}"]
    else:
        command = ["schtasks", "/Run", "/TN", windows_task_name(job_name, schedule.installation_id)]
    kwargs = {"capture_output": True}
    if sys.platform.startswith("linux"):
        kwargs["env"] = linux_session_environment()
    return subprocess.run(command, **kwargs).returncode == 0


__all__ = ["RegistrationNamespaceStatus", "executor_argv", "inspect_registration_namespace", "linux_session_environment", "load_jobs", "remove_context", "remove_installation_context", "render_linux_service", "render_linux_timer", "render_macos_plist", "render_windows_wrapper", "status", "sync", "trigger", "windows_executor_argv", "write_registration_summary"]

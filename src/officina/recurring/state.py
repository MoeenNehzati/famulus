from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

import yaml

from officina.common.atomic_files import atomic_replace_bytes

from .runtime import ManagedSchedule
from .jobs import validate_jobs_payload


class LegacyStateConflict(RuntimeError):
    pass


_LEGACY_AGENT_ENV = "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n"
_DEFAULT_JOBS = Path(__file__).with_name("default_jobs.yaml")


def _validated_jobs(raw: bytes, *, source: Path) -> bytes:
    try:
        payload = yaml.safe_load(raw.decode("utf-8")) or {}
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"malformed recurring jobs at {source}: {exc}") from exc
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError(f"malformed recurring jobs at {source}: expected a jobs list")
    normalized = []
    for original in jobs:
        job = dict(original)
        command = job.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError(f"malformed recurring job at {source}: command is required")
        match = re.match(r"^ASSISTANT_DEFAULT=(claude|codex)\s+(.+)$", command)
        if match:
            backend, command = match.groups()
            if "backend" in job and job["backend"] != backend:
                raise ValueError(f"conflicting inline and structured backend at {source}")
            job["backend"] = backend
            job["command"] = command
        if job.get("backend") not in (None, "claude", "codex"):
            raise ValueError(f"malformed recurring job at {source}: backend must be claude or codex")
        normalized.append(job)
    normalized = validate_jobs_payload({"jobs": normalized})
    return yaml.safe_dump({"jobs": normalized}, sort_keys=False).encode("utf-8")


def _legacy_source(schedule: ManagedSchedule) -> Path | None:
    owner = schedule.native_registration_root / "install-owner.json"
    try:
        payload = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        value = payload.get("source_path") if payload.get("schema_version") == 2 else payload.get("owner")
        if isinstance(value, str) and value:
            selected = Path(value)
            for candidate in (selected, selected / "_rtx"):
                if (candidate / "jobs.yaml").is_file() or (candidate / "logs").is_dir():
                    return candidate
    for registration in schedule.native_registration_root.glob("ai-*"):
        if not registration.is_file():
            continue
        try:
            content = registration.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"([^\r\n\"']+?)/_job_executor\.py", content)
        if match:
            candidate = Path(match.group(1).strip())
            if (candidate / "jobs.yaml").is_file() or (candidate / "logs").is_dir():
                return candidate
    return None


def _validate_records(root: Path) -> None:
    if not root.exists():
        return
    for name in ("latest.json", "running.json"):
        for path in root.rglob(name):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError(f"malformed recurring record at {path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"malformed recurring record at {path}: expected an object")


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _replace_directory(source: Path, target: Path) -> None:
    os.replace(source, target)


def _publish_logs(*, source: Path, target: Path) -> None:
    _validate_records(source)
    if target.exists():
        _validate_records(target)
        if _tree(source) != _tree(target):
            raise LegacyStateConflict(
                f"legacy and canonical recurring histories differ; choose explicitly between {source} and {target}"
            )
        return
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.migrating-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary)
        _validate_records(temporary)
        if _tree(source) != _tree(temporary):
            raise OSError(f"recurring history verification failed for {source}")
        _replace_directory(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def prepare_context_state(
    schedule: ManagedSchedule, *, default_jobs: Path = _DEFAULT_JOBS
) -> None:
    schedule.config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    schedule.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        schedule.config_root.chmod(0o700)
        schedule.state_root.chmod(0o700)
    legacy = _legacy_source(schedule)
    legacy_jobs = legacy / "jobs.yaml" if legacy is not None else None
    if legacy_jobs is not None and legacy_jobs.is_file():
        desired = _validated_jobs(legacy_jobs.read_bytes(), source=legacy_jobs)
        if schedule.jobs_file.exists():
            current = _validated_jobs(schedule.jobs_file.read_bytes(), source=schedule.jobs_file)
            if current != desired:
                raise LegacyStateConflict(
                    f"legacy and canonical recurring jobs differ; choose explicitly between {legacy_jobs} and {schedule.jobs_file}"
                )
        else:
            atomic_replace_bytes(
                schedule.jobs_file, desired, allowed_root=schedule.config_root, mode=0o600
            )
    elif not schedule.jobs_file.exists():
        desired = b"jobs: []\n"
        atomic_replace_bytes(
            schedule.jobs_file, desired, allowed_root=schedule.config_root, mode=0o600
        )
    else:
        _validated_jobs(schedule.jobs_file.read_bytes(), source=schedule.jobs_file)
    legacy_logs = legacy / "logs" if legacy is not None else None
    if legacy_logs is not None and legacy_logs.is_dir():
        _publish_logs(source=legacy_logs, target=schedule.log_root)
    else:
        schedule.log_root.mkdir(mode=0o700, parents=True, exist_ok=True)


def cleanup_legacy_agent_environment(schedule: ManagedSchedule) -> None:
    if not sys_platform_linux():
        return
    environment_file = Path(schedule.environment["HOME"]) / ".config/environment.d/20-ai-agent.conf"
    try:
        if environment_file.read_text(encoding="utf-8") == _LEGACY_AGENT_ENV:
            environment_file.unlink()
    except OSError:
        pass
    import subprocess
    from .native import linux_session_environment

    result = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=linux_session_environment(), check=False,
    )
    if result.returncode == 0 and _LEGACY_AGENT_ENV.rstrip("\n") in result.stdout.splitlines():
        subprocess.run(
            ["systemctl", "--user", "unset-environment", "AI_AGENT_COMMAND_TEMPLATE"],
            capture_output=True, env=linux_session_environment(), check=False,
        )


def sys_platform_linux() -> bool:
    import sys
    return sys.platform.startswith("linux")


__all__ = [
    "LegacyStateConflict",
    "cleanup_legacy_agent_environment",
    "prepare_context_state",
]

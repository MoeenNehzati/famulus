from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from officina.common.atomic_files import atomic_replace_bytes
from officina.common.famulus_paths import resolve_famulus_paths


class RecurringRuntimeError(ValueError):
    pass


class RecurringPrerequisiteError(RecurringRuntimeError):
    pass


_BACKENDS = ("claude", "codex")


@dataclass(frozen=True)
class ManagedSchedule:
    descriptor_path: Path
    owner_id: str
    python: Path
    plugin_root: Path
    jobs_file: Path
    log_root: Path
    config_root: Path
    state_root: Path
    native_registration_root: Path
    default_backend: str
    backend_executables: Mapping[str, Path]
    environment: Mapping[str, str]

def _absolute(path: Path, label: str) -> Path:
    if not str(path) or not path.is_absolute() or "\r" in str(path) or "\n" in str(path):
        raise RecurringRuntimeError(f"{label} must be a CR/LF-free absolute path")
    return path


def _no_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise RecurringRuntimeError(f"{label} contains a symlink component: {current}")


def _roots(environ: Mapping[str, str], platform: str):
    home_name = "USERPROFILE" if platform == "win32" else "HOME"
    home = _absolute(Path(environ.get(home_name, "")), home_name).resolve(strict=False)
    paths = resolve_famulus_paths(platform=platform, home=home, environ=environ)
    native = paths.data_root / "recurring-tasks" / "native"
    if platform != "win32":
        relative = "Library/LaunchAgents" if platform == "darwin" else ".config/systemd/user"
        native = home / relative
    return home, paths, native


def _which(name: str, *, platform: str, environ: Mapping[str, str]) -> str | None:
    candidates = (name,)
    if platform == "win32" and not Path(name).suffix:
        extensions = environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        candidates = tuple(name + extension.lower() for extension in extensions)
    for directory in environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        root = Path(directory.strip('"'))
        for candidate in candidates:
            selected = root / candidate
            if selected.is_file() and os.access(selected, os.X_OK):
                return str(selected)
    return None


def _resolve_executable(
    name: str, environ: Mapping[str, str], *, platform: str
) -> Path:
    selected = _which(name, platform=platform, environ=environ)
    if not selected:
        raise RecurringPrerequisiteError(
            f"selected backend {name!r} is missing from the explicit PATH"
        )
    path = _absolute(Path(selected), f"{name} executable")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RecurringPrerequisiteError(f"selected backend {name!r} is unreadable") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RecurringPrerequisiteError(f"selected backend {name!r} is not executable")
    return resolved


def build_managed_schedule(*, python: Path, plugin_root: Path, environ: Mapping[str, str], platform: str | None = None) -> ManagedSchedule:
    platform = sys.platform if platform is None else platform
    home, paths, native = _roots(environ, platform)
    config = _absolute(paths.recurring_config_root, "config root").resolve(strict=False)
    state = _absolute(paths.recurring_state_root, "state root").resolve(strict=False)
    python = _absolute(python, "selected Python").resolve(strict=False)
    plugin_root = _absolute(plugin_root, "plugin root").resolve(strict=False)
    if not python.is_file() or not plugin_root.is_dir():
        raise RecurringPrerequisiteError("selected Python and plugin root must exist")
    backends = {name: _resolve_executable(name, environ, platform=platform) for name in _BACKENDS}
    environment = {
        "HOME": str(home),
        "PATH": os.pathsep.join(dict.fromkeys(
            [str(python.parent), *(str(path.parent) for path in backends.values())]
        )),
        "PYTHONPATH": str(plugin_root / "src"),
        "CODEX_HOME": environ.get("CODEX_HOME", str(home / ".codex")),
        "CLAUDE_CONFIG_DIR": environ.get("CLAUDE_CONFIG_DIR", str(home / ".claude")),
    }
    if platform == "win32":
        environment.update({
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(paths.data_root.parent),
            "APPDATA": str(paths.config_root.parent),
        })
    return ManagedSchedule(
        descriptor_path=config / "schedule-descriptor.json",
        owner_id=str(config),
        python=python,
        plugin_root=plugin_root,
        jobs_file=config / "jobs.yaml",
        log_root=state / "logs",
        config_root=config,
        state_root=state,
        native_registration_root=native,
        default_backend="claude",
        backend_executables=backends,
        environment=environment,
    )


def _payload(s: ManagedSchedule) -> dict[str, object]:
    return {
        "schema_version": 2,
        "owner_id": s.owner_id,
        "python": str(s.python),
        "plugin_root": str(s.plugin_root),
        "jobs_file": str(s.jobs_file),
        "log_root": str(s.log_root),
        "config_root": str(s.config_root),
        "state_root": str(s.state_root),
        "native_registration_root": str(s.native_registration_root),
        "default_backend": s.default_backend,
        "backend_executables": {name: str(path) for name, path in s.backend_executables.items()},
        "environment": dict(s.environment),
    }


def write_managed_schedule(*, python: Path, plugin_root: Path, environ: Mapping[str, str]) -> ManagedSchedule:
    expected = build_managed_schedule(python=python, plugin_root=plugin_root, environ=environ)
    parent = expected.descriptor_path.parent
    _no_symlink_components(parent, "descriptor parent")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        parent.chmod(0o700)
    raw = (json.dumps(_payload(expected), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_replace_bytes(expected.descriptor_path, raw, allowed_root=parent, mode=0o600)
    return expected


def load_managed_schedule(*, descriptor_path: Path, log_root: Path | None = None) -> ManagedSchedule:
    _no_symlink_components(descriptor_path, "descriptor path")
    try:
        payload = json.loads(descriptor_path.read_text(encoding="utf-8"))
        mode = stat.S_IMODE(descriptor_path.stat().st_mode)
        parent_mode = stat.S_IMODE(descriptor_path.parent.stat().st_mode)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RecurringRuntimeError(f"cannot read schedule descriptor: {exc}") from exc
    required = {
        "schema_version", "owner_id", "python", "plugin_root", "jobs_file",
        "log_root", "config_root", "state_root", "native_registration_root",
        "default_backend", "backend_executables", "environment",
    }
    if set(payload) != required or payload.get("schema_version") != 2:
        raise RecurringRuntimeError("schedule descriptor has an unsupported exact schema")
    if os.name != "nt" and (mode & 0o077 or parent_mode & 0o077):
        raise RecurringRuntimeError("schedule descriptor and directory permissions must be user-only")
    def path(name: str) -> Path:
        return _absolute(Path(str(payload[name])), name).resolve(strict=False)

    schedule = ManagedSchedule(
        descriptor_path=descriptor_path,
        owner_id=str(payload["owner_id"]),
        python=path("python"),
        plugin_root=path("plugin_root"),
        jobs_file=path("jobs_file"),
        log_root=path("log_root"),
        config_root=path("config_root"),
        state_root=path("state_root"),
        native_registration_root=path("native_registration_root"),
        default_backend=str(payload["default_backend"]),
        backend_executables={
            name: _absolute(Path(str(value)), name).resolve(strict=False)
            for name, value in payload["backend_executables"].items()
        },
        environment=dict(payload["environment"]),
    )
    if not (
        schedule.owner_id == str(schedule.config_root)
        and descriptor_path == schedule.config_root / "schedule-descriptor.json"
        and schedule.jobs_file == schedule.config_root / "jobs.yaml"
        and (log_root is None or log_root == schedule.log_root)
        and payload == _payload(schedule)
    ):
        raise RecurringRuntimeError("schedule descriptor is not canonical")
    return schedule


def load_public_schedule(*, environ: Mapping[str, str], platform: str | None = None) -> ManagedSchedule:
    selected_platform = sys.platform if platform is None else platform
    paths = _roots(environ, selected_platform)[1]
    descriptor = paths.recurring_config_root.resolve(strict=False) / "schedule-descriptor.json"
    return load_managed_schedule(descriptor_path=descriptor)


__all__ = ["ManagedSchedule", "RecurringPrerequisiteError", "RecurringRuntimeError", "build_managed_schedule", "load_managed_schedule", "load_public_schedule", "write_managed_schedule"]

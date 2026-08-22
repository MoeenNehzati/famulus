from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from officina.common.atomic_files import atomic_replace_bytes
from officina.install.context import InstallationContext, load_context_from_pointer
from officina.install.runtime_pointer import (
    decode_current_pointer,
    load_deployed_resolver_trusted_roots,
)
from officina.launchers.agent import load_launcher_configuration
from officina.recurring.runtime import (
    ManagedSchedule,
    load_managed_schedule,
    write_managed_schedule,
)

if __package__:
    from ._schedule_backend import ScheduleContext
else:
    from _schedule_backend import ScheduleContext


class ScheduleContextError(ValueError):
    pass


_DESCRIPTOR_NAME = "schedule-descriptor.json"
_BACKENDS = ("claude", "codex")
_FIELDS = {
    "schema_version",
    "installation_id",
    "runtime_root",
    "runtime_resolver",
    "bootstrap_python",
    "launcher_bin",
    "backend_executables",
    "jobs_file",
    "log_root",
    "config_root",
    "state_root",
    "native_registration_root",
    "default_backend",
    "environment",
}
_VALIDATION_TOKEN = object()


@dataclass(frozen=True)
class ScheduleDescriptor:
    schema_version: Literal[1]
    installation_id: str
    runtime_root: Path
    runtime_resolver: Path
    bootstrap_python: Path | None
    launcher_bin: Path
    backend_executables: Mapping[Literal["claude", "codex"], Path]
    jobs_file: Path
    log_root: Path
    config_root: Path
    state_root: Path
    native_registration_root: Path | None
    default_backend: Literal["claude", "codex"]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class _ValidatedScheduleDescriptor(ScheduleDescriptor):
    canonical_path: Path
    _validation_token: object | None = field(
        default=None, init=False, repr=False, compare=False
    )


def _validated_descriptor(
    descriptor: ScheduleDescriptor, *, canonical_path: Path
) -> _ValidatedScheduleDescriptor:
    validated = _ValidatedScheduleDescriptor(
        **descriptor.__dict__, canonical_path=canonical_path
    )
    object.__setattr__(validated, "_validation_token", _VALIDATION_TOKEN)
    return validated


def schedule_descriptor_path(context: InstallationContext) -> Path:
    return context.paths.recurring_config_root / _DESCRIPTOR_NAME


def _absolute(path: Path, *, label: str) -> Path:
    if not str(path) or not path.is_absolute():
        raise ScheduleContextError(f"{label} must be a non-empty absolute path")
    if "\r" in str(path) or "\n" in str(path):
        raise ScheduleContextError(f"{label} must not contain CR or LF")
    return path


def _no_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ScheduleContextError(f"{label} contains a symlink component: {current}")


def _native_registration_root(
    *, context: InstallationContext, platform: str, environ: Mapping[str, str]
) -> Path:
    if platform == "win32":
        return context.paths.recurring_state_root / "task-wrappers"
    home = _posix_account_home()
    if platform == "darwin":
        return home / "Library" / "LaunchAgents"
    return home / ".config" / "systemd" / "user"


def _posix_account_home() -> Path:
    try:
        import pwd

        value = pwd.getpwuid(os.getuid()).pw_dir
    except (AttributeError, ImportError, KeyError, OSError) as exc:
        raise ScheduleContextError(
            "cannot resolve the host account home for native scheduler registration"
        ) from exc
    return _absolute(Path(value), label="host account home")


def _which(name: str, *, platform: str, environ: Mapping[str, str]) -> str | None:
    search_path = environ.get("PATH", "")
    candidates = (name,)
    if platform == "win32" and not Path(name).suffix:
        extensions = environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        candidates = tuple(name + extension.lower() for extension in extensions)
    for directory in search_path.split(os.pathsep):
        if not directory:
            continue
        root = Path(directory.strip('"'))
        for candidate in candidates:
            selected = root / candidate
            if selected.is_file() and os.access(selected, os.X_OK):
                return str(selected)
    return None


def _resolve_executable(
    name: str, *, platform: str, environ: Mapping[str, str]
) -> Path:
    selected = _which(name, platform=platform, environ=environ)
    if not selected:
        raise ScheduleContextError(f"selected backend {name!r} is missing from the explicit PATH")
    path = _absolute(Path(selected), label=f"{name} executable")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ScheduleContextError(f"selected backend {name!r} is not readable: {path}") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ScheduleContextError(f"selected backend {name!r} is not executable: {resolved}")
    return resolved


def _bootstrap_python(*, platform: str, environ: Mapping[str, str]) -> Path | None:
    if platform != "win32":
        return None
    for name in ("python", "py"):
        selected = _which(name, platform=platform, environ=environ)
        if selected:
            return Path(selected).resolve(strict=True)
    raise ScheduleContextError("Windows bootstrap interpreter is missing (tried python and py)")


def _bounded_environment(
    *,
    context: InstallationContext,
    backend_executables: Mapping[str, Path],
    bootstrap_python: Path | None,
    platform: str,
    environ: Mapping[str, str],
    release_id: str,
) -> dict[str, str]:
    home_name = "USERPROFILE" if platform == "win32" else "HOME"
    home = _absolute(Path(environ.get(home_name, "")), label=home_name)
    directories = [context.paths.user_bin]
    for name in _BACKENDS:
        parent = backend_executables[name].parent
        if parent not in directories:
            directories.append(parent)
    if bootstrap_python is not None and bootstrap_python.parent not in directories:
        directories.append(bootstrap_python.parent)
    result = {
        "HOME": str(home),
        "PATH": os.pathsep.join(str(path) for path in directories),
        "CODEX_HOME": str(context.codex_home),
        "CLAUDE_CONFIG_DIR": str(context.claude_home),
        "FAMULUS_ACTIVE_RELEASE": release_id,
    }
    if platform == "win32":
        result["USERPROFILE"] = str(home)
        result["LOCALAPPDATA"] = str(context.paths.data_root.parent)
        result["APPDATA"] = str(context.paths.config_root.parent)
    elif context.mode == "development" and platform != "darwin":
        result["XDG_DATA_HOME"] = str(context.paths.data_root.parent)
        result["XDG_CONFIG_HOME"] = str(context.paths.config_root.parent)
        result["XDG_STATE_HOME"] = str(context.paths.state_root.parent)
    return result


def _authority_snapshot(
    *, runtime_root: Path, environ: Mapping[str, str]
) -> tuple[InstallationContext, object]:
    pointer_path = runtime_root / "current.json"
    for _attempt in range(3):
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(pointer_path, flags)
            with os.fdopen(fd, "rb") as stream:
                before_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(before_stat.st_mode):
                    raise ScheduleContextError("active runtime pointer must be a regular file")
                payload = json.loads(stream.read())
                after_read_stat = os.fstat(stream.fileno())
            if _selector_identity(before_stat) != _selector_identity(after_read_stat):
                continue
            pointer = decode_current_pointer(
                payload,
                runtime_root=runtime_root,
                trusted_interpreter_roots=load_deployed_resolver_trusted_roots(
                    runtime_root=runtime_root
                ),
            )
            context = load_context_from_pointer(
                pointer=pointer,
                runtime_root=runtime_root,
                environ=environ,
            )
            selected_stat = os.stat(pointer_path, follow_symlinks=False)
        except (OSError, UnicodeError, ValueError) as exc:
            if isinstance(exc, ScheduleContextError):
                raise
            raise ScheduleContextError(f"cannot read active runtime pointer: {exc}") from exc
        if _selector_identity(before_stat) == _selector_identity(selected_stat):
            return context, pointer
    raise ScheduleContextError("active runtime pointer changed while building schedule authority")


def _selector_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _expected_descriptor(
    *, runtime_root: Path, environ: Mapping[str, str], platform: str
) -> tuple[InstallationContext, ScheduleDescriptor]:
    runtime_root = _absolute(runtime_root, label="runtime_root")
    context, pointer = _authority_snapshot(runtime_root=runtime_root, environ=environ)
    resolver = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    _no_symlink_components(resolver, label="runtime_resolver")
    if not resolver.is_file():
        raise ScheduleContextError(f"runtime resolver is missing: {resolver}")
    launcher = load_launcher_configuration(config_root=context.paths.config_root)
    backends = {
        name: _resolve_executable(name, platform=platform, environ=environ)
        for name in _BACKENDS
    }
    bootstrap = _bootstrap_python(platform=platform, environ=environ)
    descriptor = ScheduleDescriptor(
        schema_version=1,
        installation_id=context.installation_id,
        runtime_root=runtime_root,
        runtime_resolver=resolver,
        bootstrap_python=bootstrap,
        launcher_bin=context.paths.user_bin,
        backend_executables=backends,
        jobs_file=context.paths.recurring_config_root / "jobs.yaml",
        log_root=context.paths.recurring_state_root / "logs",
        config_root=context.paths.recurring_config_root,
        state_root=context.paths.recurring_state_root,
        native_registration_root=_native_registration_root(
            context=context, platform=platform, environ=environ
        ),
        default_backend=launcher.default_backend,
        environment=_bounded_environment(
            context=context,
            backend_executables=backends,
            bootstrap_python=bootstrap,
            platform=platform,
            environ=environ,
            release_id=pointer.release_id,
        ),
    )
    if pointer.runtime_source.parent.resolve(strict=False) != (
        runtime_root / "releases"
    ).resolve(strict=False):
        raise ScheduleContextError("current pointer runtime source is outside this runtime")
    return context, descriptor


def _payload(descriptor: ScheduleDescriptor) -> dict[str, object]:
    return {
        "schema_version": descriptor.schema_version,
        "installation_id": descriptor.installation_id,
        "runtime_root": str(descriptor.runtime_root),
        "runtime_resolver": str(descriptor.runtime_resolver),
        "bootstrap_python": str(descriptor.bootstrap_python) if descriptor.bootstrap_python else None,
        "launcher_bin": str(descriptor.launcher_bin),
        "backend_executables": {
            name: str(descriptor.backend_executables[name]) for name in _BACKENDS
        },
        "jobs_file": str(descriptor.jobs_file),
        "log_root": str(descriptor.log_root),
        "config_root": str(descriptor.config_root),
        "state_root": str(descriptor.state_root),
        "native_registration_root": (
            str(descriptor.native_registration_root)
            if descriptor.native_registration_root is not None
            else None
        ),
        "default_backend": descriptor.default_backend,
        "environment": dict(descriptor.environment),
    }


def write_schedule_descriptor(
    *,
    runtime_root: Path,
    environ: Mapping[str, str],
) -> ScheduleDescriptor:
    return _from_managed(
        write_managed_schedule(runtime_root=runtime_root, environ=environ)
    )


def _from_managed(schedule: ManagedSchedule) -> ScheduleDescriptor:
    descriptor = ScheduleDescriptor(
        schema_version=1,
        installation_id=schedule.installation_id,
        runtime_root=schedule.runtime_root,
        runtime_resolver=schedule.runtime_resolver,
        bootstrap_python=schedule.bootstrap_python,
        launcher_bin=schedule.launcher_bin or schedule.config_root.parent / "bin",
        backend_executables=schedule.backend_executables,
        jobs_file=schedule.jobs_file,
        log_root=schedule.log_root,
        config_root=schedule.config_root,
        state_root=schedule.state_root,
        native_registration_root=schedule.native_registration_root,
        default_backend=schedule.default_backend,
        environment=schedule.environment,
    )
    return _validated_descriptor(descriptor, canonical_path=schedule.descriptor_path)


def _decode_path(payload: Mapping[str, object], key: str, *, nullable: bool = False) -> Path | None:
    value = payload.get(key)
    if nullable and value is None:
        return None
    if not isinstance(value, str):
        raise ScheduleContextError(f"descriptor {key} must be a path string")
    return _absolute(Path(value), label=key)


def load_schedule_descriptor(
    *,
    path: Path,
    environ: Mapping[str, str],
) -> ScheduleDescriptor:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        runtime_value = payload.get("runtime_root") if isinstance(payload, dict) else None
    except (OSError, UnicodeError, ValueError) as exc:
        raise ScheduleContextError(f"cannot read schedule descriptor: {exc}") from exc
    if not isinstance(runtime_value, str):
        raise ScheduleContextError("descriptor runtime_root must be a path string")
    try:
        managed = load_managed_schedule(
            runtime_root=_absolute(Path(runtime_value), label="runtime_root"),
            descriptor_path=path,
            environ=environ,
        )
    except ValueError as exc:
        raise ScheduleContextError(str(exc)) from exc
    return _from_managed(managed)


def _load_schedule_descriptor(
    *, path: Path, environ: Mapping[str, str], platform: str
) -> ScheduleDescriptor:
    _absolute(path, label="descriptor path")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ScheduleContextError(f"cannot read schedule descriptor: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ScheduleContextError("schedule descriptor must contain the exact fields")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ScheduleContextError("unsupported schedule descriptor schema")
    installation_id = payload.get("installation_id")
    if not isinstance(installation_id, str) or not installation_id:
        raise ScheduleContextError("descriptor installation_id must be a non-empty string")
    for key in (
        "runtime_root",
        "runtime_resolver",
        "launcher_bin",
        "jobs_file",
        "log_root",
        "config_root",
        "state_root",
        "native_registration_root",
    ):
        _decode_path(payload, key, nullable=key == "native_registration_root")
    _decode_path(payload, "bootstrap_python", nullable=True)
    backend_payload = payload.get("backend_executables")
    if not isinstance(backend_payload, dict) or set(backend_payload) != set(_BACKENDS):
        raise ScheduleContextError("descriptor backend_executables must name claude and codex exactly")
    for name in _BACKENDS:
        value = backend_payload[name]
        if not isinstance(value, str):
            raise ScheduleContextError(f"descriptor {name} executable must be a path string")
        _absolute(Path(value), label=f"{name} executable")
    environment_payload = payload.get("environment")
    if not isinstance(environment_payload, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or "\r" in key
        or "\n" in key
        or "\r" in value
        or "\n" in value
        for key, value in environment_payload.items()
    ):
        raise ScheduleContextError("descriptor environment must contain CR/LF-free string pairs")
    runtime_root = _decode_path(payload, "runtime_root")
    assert runtime_root is not None
    context, expected = _expected_descriptor(
        runtime_root=runtime_root, environ=environ, platform=platform
    )
    canonical = schedule_descriptor_path(context)
    if path != canonical:
        raise ScheduleContextError(f"descriptor is not at its canonical context path: {canonical}")
    _no_symlink_components(path, label="descriptor path")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ScheduleContextError(f"cannot stat schedule descriptor: {exc}") from exc
    if os.name != "nt" and mode & 0o077:
        raise ScheduleContextError("schedule descriptor permissions must be user-only")
    if os.name != "nt" and stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ScheduleContextError("schedule descriptor directory permissions must be user-only")
    if payload != _payload(expected):
        if payload.get("installation_id") != expected.installation_id:
            raise ScheduleContextError("descriptor installation_id does not match the active installation")
        raise ScheduleContextError(
            "schedule descriptor does not match active context, pointer, launchers, executables, or platform adapter"
        )
    return _validated_descriptor(expected, canonical_path=canonical)


def build_schedule_context(
    *, descriptor: ScheduleDescriptor, live: bool = True
) -> ScheduleContext:
    if (
        not isinstance(descriptor, _ValidatedScheduleDescriptor)
        or descriptor._validation_token is not _VALIDATION_TOKEN
    ):
        raise ScheduleContextError("schedule descriptor was not validated from its canonical path")
    if live and descriptor.native_registration_root is None:
        raise ScheduleContextError("live scheduling requires an adapter-derived registration root")
    return ScheduleContext(
        skill_dir=Path(__file__).resolve().parent,
        jobs_file=descriptor.jobs_file,
        log_dir=descriptor.log_root,
        unit_dir=descriptor.native_registration_root,
        live=live,
        runtime_resolver=descriptor.runtime_resolver,
        config_root=descriptor.config_root,
        state_root=descriptor.state_root,
        assistant_default=descriptor.default_backend,
        installation_id=descriptor.installation_id,
        backend_executables=descriptor.backend_executables,
        environment=descriptor.environment,
        bootstrap_python=descriptor.bootstrap_python,
    )


def load_schedule_context(
    *, descriptor_path: Path, environ: Mapping[str, str], live: bool = True
) -> ScheduleContext:
    descriptor = load_schedule_descriptor(path=descriptor_path, environ=environ)
    return build_schedule_context(descriptor=descriptor, live=live)


def production_schedule_context(
    *,
    environ: Mapping[str, str] | None = None,
    live: bool = True,
) -> ScheduleContext:
    selected = os.environ if environ is None else environ
    descriptor_value = selected.get("FAMULUS_SCHEDULE_DESCRIPTOR")
    if not descriptor_value:
        raise ScheduleContextError(
            "FAMULUS_SCHEDULE_DESCRIPTOR must name the canonical context descriptor"
        )
    return load_schedule_context(
        descriptor_path=Path(descriptor_value), environ=selected, live=live
    )


def _test_schedule_context(
    *, skill_dir: Path, jobs_file: Path, log_dir: Path, unit_dir: Path
) -> ScheduleContext:
    return ScheduleContext(
        skill_dir=skill_dir,
        jobs_file=jobs_file,
        log_dir=log_dir,
        unit_dir=unit_dir,
        live=False,
    )


def _write_schedule_descriptor_for_test(
    *, runtime_root: Path, environ: Mapping[str, str], platform: str
) -> ScheduleDescriptor:
    context, descriptor = _expected_descriptor(
        runtime_root=runtime_root, environ=environ, platform=platform
    )
    path = schedule_descriptor_path(context)
    _no_symlink_components(path.parent, label="descriptor parent")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    raw = (json.dumps(_payload(descriptor), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_replace_bytes(path, raw, allowed_root=path.parent, mode=0o600)
    return _validated_descriptor(descriptor, canonical_path=path)


def _load_schedule_descriptor_for_test(
    *, path: Path, environ: Mapping[str, str], platform: str
) -> ScheduleDescriptor:
    return _load_schedule_descriptor(path=path, environ=environ, platform=platform)


__all__ = [
    "ScheduleContextError",
    "ScheduleDescriptor",
    "build_schedule_context",
    "load_schedule_context",
    "production_schedule_context",
    "load_schedule_descriptor",
    "schedule_descriptor_path",
    "write_schedule_descriptor",
]

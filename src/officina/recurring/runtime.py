from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from officina.common.atomic_files import atomic_replace_bytes
from officina.install.context import InstallationContext, load_active_context
from officina.install.runtime_pointer import decode_current_pointer, load_deployed_resolver_trusted_roots
from officina.launchers.agent import LauncherConfigurationError, load_launcher_configuration


class RecurringRuntimeError(ValueError):
    pass


class RecurringPrerequisiteError(RecurringRuntimeError):
    pass


_BACKENDS = ("claude", "codex")
_FIELDS = {
    "schema_version", "installation_id", "runtime_root", "runtime_resolver",
    "bootstrap_python", "launcher_bin", "backend_executables", "jobs_file",
    "log_root", "config_root", "state_root", "native_registration_root",
    "default_backend", "environment",
}


@dataclass(frozen=True)
class ManagedSchedule:
    descriptor_path: Path
    runtime_root: Path
    runtime_resolver: Path
    bootstrap_python: Path | None
    installation_id: str
    jobs_file: Path
    log_root: Path
    config_root: Path
    state_root: Path
    native_registration_root: Path
    default_backend: str
    backend_executables: Mapping[str, Path]
    environment: Mapping[str, str]
    launcher_bin: Path | None = None
    launcher_resources: Path | None = None


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


def _posix_account_home() -> Path:
    try:
        import pwd
        return _absolute(Path(pwd.getpwuid(os.getuid()).pw_dir), "host account home")
    except (AttributeError, ImportError, KeyError, OSError) as exc:
        raise RecurringRuntimeError("cannot resolve the host account home") from exc


def _native_root(context: InstallationContext, platform: str) -> Path:
    if platform == "win32":
        return context.paths.recurring_state_root / "task-wrappers"
    home = _posix_account_home()
    if platform == "darwin":
        return home / "Library" / "LaunchAgents"
    return home / ".config" / "systemd" / "user"


def native_registration_root(context: InstallationContext, platform: str) -> Path:
    """Return the recurring owner's native namespace for an explicit context."""
    return _native_root(context, platform)


def _resolve_executable(name: str, environ: Mapping[str, str]) -> Path:
    selected = shutil.which(name, path=environ.get("PATH", ""))
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


def _bootstrap_python(platform: str, environ: Mapping[str, str]) -> Path | None:
    if platform != "win32":
        return None
    for name in ("python", "py"):
        try:
            selected = shutil.which(name, path=environ.get("PATH", ""))
        except OSError as exc:
            raise RecurringPrerequisiteError(
                f"Windows bootstrap interpreter lookup failed for {name!r}"
            ) from exc
        if selected:
            try:
                resolved = Path(selected).resolve(strict=True)
            except OSError as exc:
                raise RecurringPrerequisiteError(
                    f"Windows bootstrap interpreter {name!r} is unreadable"
                ) from exc
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                raise RecurringPrerequisiteError(
                    f"Windows bootstrap interpreter {name!r} is not executable"
                )
            return resolved
    raise RecurringPrerequisiteError("Windows bootstrap interpreter is missing")


def _bounded_environment(
    context: InstallationContext,
    backends: Mapping[str, Path],
    bootstrap: Path | None,
    platform: str,
    environ: Mapping[str, str],
    release_id: str,
) -> dict[str, str]:
    home_name = "USERPROFILE" if platform == "win32" else "HOME"
    home = _absolute(Path(environ.get(home_name, "")), home_name)
    directories = [context.paths.user_bin]
    for name in _BACKENDS:
        if backends[name].parent not in directories:
            directories.append(backends[name].parent)
    if bootstrap is not None and bootstrap.parent not in directories:
        directories.append(bootstrap.parent)
    result = {
        "HOME": str(home), "PATH": os.pathsep.join(str(path) for path in directories),
        "CODEX_HOME": str(context.codex_home), "CLAUDE_CONFIG_DIR": str(context.claude_home),
        "FAMULUS_ACTIVE_RELEASE": release_id,
    }
    if platform == "win32":
        result.update({"USERPROFILE": str(home), "LOCALAPPDATA": str(context.paths.data_root.parent), "APPDATA": str(context.paths.config_root.parent)})
    elif context.mode == "development" and platform != "darwin":
        result.update({"XDG_DATA_HOME": str(context.paths.data_root.parent), "XDG_CONFIG_HOME": str(context.paths.config_root.parent), "XDG_STATE_HOME": str(context.paths.state_root.parent)})
    return result


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _pointer_snapshot(runtime_root: Path, environ: Mapping[str, str]):
    pointer_path = runtime_root / "current.json"
    for _ in range(3):
        try:
            fd = os.open(pointer_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise RecurringRuntimeError("active runtime pointer must be a regular file")
                payload = json.loads(stream.read())
                after = os.fstat(stream.fileno())
            if _identity(before) != _identity(after):
                continue
            pointer = decode_current_pointer(payload, runtime_root=runtime_root, trusted_interpreter_roots=load_deployed_resolver_trusted_roots(runtime_root=runtime_root))
            context = load_active_context(runtime_root=runtime_root, environ=environ)
            selected = os.stat(pointer_path, follow_symlinks=False)
        except (OSError, UnicodeError, ValueError) as exc:
            if isinstance(exc, RecurringRuntimeError):
                raise
            raise RecurringRuntimeError(f"cannot read active runtime pointer: {exc}") from exc
        if _identity(before) == _identity(selected):
            return context, pointer
    raise RecurringRuntimeError("active runtime pointer changed while building schedule authority")


def _expected_schedule(*, runtime_root: Path, environ: Mapping[str, str], platform: str) -> ManagedSchedule:
    runtime_root = _absolute(runtime_root, "runtime_root")
    context, pointer = _pointer_snapshot(runtime_root, environ)
    resolver = runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    _no_symlink_components(resolver, "runtime resolver")
    if not resolver.is_file():
        raise RecurringPrerequisiteError(f"runtime resolver is missing: {resolver}")
    try:
        launcher = load_launcher_configuration(config_root=context.paths.config_root)
    except LauncherConfigurationError as exc:
        raise RecurringPrerequisiteError(
            f"launcher configuration cannot reconstruct recurring authority: {exc}"
        ) from exc
    backends = {name: _resolve_executable(name, environ) for name in _BACKENDS}
    bootstrap = _bootstrap_python(platform, environ)
    if pointer.runtime_source.parent.resolve(strict=False) != (runtime_root / "releases").resolve(strict=False):
        raise RecurringRuntimeError("current pointer runtime source is outside this runtime")
    return ManagedSchedule(
        descriptor_path=context.paths.recurring_config_root / "schedule-descriptor.json",
        runtime_root=runtime_root, runtime_resolver=resolver, bootstrap_python=bootstrap,
        installation_id=context.installation_id,
        jobs_file=context.paths.recurring_config_root / "jobs.yaml",
        log_root=context.paths.recurring_state_root / "logs",
        config_root=context.paths.recurring_config_root, state_root=context.paths.recurring_state_root,
        native_registration_root=_native_root(context, platform), default_backend=launcher.default_backend,
        backend_executables=backends,
        environment=_bounded_environment(context, backends, bootstrap, platform, environ, pointer.release_id),
        launcher_bin=context.paths.user_bin,
        launcher_resources=pointer.launcher_resources,
    )


def _payload(schedule: ManagedSchedule) -> dict[str, object]:
    return {
        "schema_version": 1, "installation_id": schedule.installation_id,
        "runtime_root": str(schedule.runtime_root), "runtime_resolver": str(schedule.runtime_resolver),
        "bootstrap_python": str(schedule.bootstrap_python) if schedule.bootstrap_python else None,
        "launcher_bin": str(schedule.launcher_bin),
        "backend_executables": {name: str(schedule.backend_executables[name]) for name in _BACKENDS},
        "jobs_file": str(schedule.jobs_file), "log_root": str(schedule.log_root),
        "config_root": str(schedule.config_root), "state_root": str(schedule.state_root),
        "native_registration_root": str(schedule.native_registration_root),
        "default_backend": schedule.default_backend, "environment": dict(schedule.environment),
    }


def discover_runtime_root(*, executable: Path | None = None) -> Path:
    selected = _absolute((Path(sys.executable) if executable is None else executable).absolute(), "managed interpreter")
    indices = [index for index, part in enumerate(selected.parts) if part == "releases"]
    if not indices or indices[-1] + 2 >= len(selected.parts):
        raise RecurringRuntimeError("managed interpreter is not beneath runtime_root/releases/<release>")
    return Path(*selected.parts[: indices[-1]])


def resolve_managed_schedule_authority(
    *, runtime_root: Path, environ: Mapping[str, str], platform: str | None = None
) -> ManagedSchedule:
    try:
        return _expected_schedule(
            runtime_root=runtime_root,
            environ=environ,
            platform=sys.platform if platform is None else platform,
        )
    except RecurringPrerequisiteError:
        raise
    except (OSError, RecurringRuntimeError) as exc:
        raise RecurringPrerequisiteError(
            f"managed schedule authority cannot be reconstructed: {exc}"
        ) from exc


def write_managed_schedule(*, runtime_root: Path, environ: Mapping[str, str]) -> ManagedSchedule:
    expected = resolve_managed_schedule_authority(runtime_root=runtime_root, environ=environ)
    parent = expected.descriptor_path.parent
    _no_symlink_components(parent, "descriptor parent")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        parent.chmod(0o700)
    raw = (json.dumps(_payload(expected), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_replace_bytes(expected.descriptor_path, raw, allowed_root=parent, mode=0o600)
    return expected


def load_managed_schedule(
    *, runtime_root: Path, descriptor_path: Path, environ: Mapping[str, str] | None = None,
    log_root: Path | None = None, platform: str | None = None,
) -> ManagedSchedule:
    selected_environment = os.environ if environ is None else environ
    expected = resolve_managed_schedule_authority(
        runtime_root=runtime_root,
        environ=selected_environment,
        platform=platform,
    )
    if descriptor_path != expected.descriptor_path:
        raise RecurringRuntimeError(f"descriptor is not canonical for the active context: {expected.descriptor_path}")
    _no_symlink_components(descriptor_path, "descriptor path")
    try:
        payload = json.loads(descriptor_path.read_text(encoding="utf-8"))
        mode = stat.S_IMODE(descriptor_path.stat().st_mode)
        parent_mode = stat.S_IMODE(descriptor_path.parent.stat().st_mode)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RecurringRuntimeError(f"cannot read schedule descriptor: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _FIELDS or payload.get("schema_version") != 1:
        raise RecurringRuntimeError("schedule descriptor has an unsupported exact schema")
    if os.name != "nt" and (mode & 0o077 or parent_mode & 0o077):
        raise RecurringRuntimeError("schedule descriptor and directory permissions must be user-only")
    if payload != _payload(expected):
        if payload.get("installation_id") != expected.installation_id:
            raise RecurringRuntimeError("descriptor installation_id does not match active installation")
        raise RecurringRuntimeError("schedule descriptor does not match active context, pointer, launchers, executables, environment, or platform adapter")
    if log_root is not None and log_root != expected.log_root:
        raise RecurringRuntimeError("log root override does not match the canonical descriptor")
    return expected


def load_public_schedule(*, runtime_root: Path, environ: Mapping[str, str]) -> ManagedSchedule:
    context = load_active_context(runtime_root=runtime_root, environ=environ)
    return load_managed_schedule(runtime_root=runtime_root, descriptor_path=context.paths.recurring_config_root / "schedule-descriptor.json", environ=environ)


__all__ = ["ManagedSchedule", "RecurringPrerequisiteError", "RecurringRuntimeError", "discover_runtime_root", "load_managed_schedule", "load_public_schedule", "native_registration_root", "resolve_managed_schedule_authority", "write_managed_schedule"]

"""Resolve explicit standard and development installation contexts."""
from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence

from officina.common.famulus_paths import FamulusPaths, resolve_famulus_paths
from officina.install.runtime_pointer import (
    RuntimePointer,
    RuntimePointerError,
    load_current_pointer,
    load_deployed_resolver_trusted_roots,
    load_installed_context_record,
)


class InvalidInstallationContextError(ValueError):
    """An installation context input is absent, relative, or inconsistent."""


class DevelopmentBoundaryError(InvalidInstallationContextError):
    """A development write boundary escapes its checkout or overlaps stable state."""


@dataclass(frozen=True)
class InstallationContext:
    mode: Literal["standard", "development"]
    source_root: Path
    development_root: Path | None
    paths: FamulusPaths
    codex_home: Path
    claude_home: Path
    installation_id: str


def installation_context_home_fields(context: InstallationContext) -> dict[str, str]:
    """Return the backend-home fields stored in an immutable context record."""
    return {
        "codex_home": str(context.codex_home.resolve(strict=False)),
        "claude_home": str(context.claude_home.resolve(strict=False)),
    }


_DEVELOPMENT_ID = re.compile(r"dev-[0-9a-f]{32}\Z")


def _existing_absolute_directory(path: Path, *, label: str) -> Path:
    if not str(path) or not path.is_absolute():
        raise InvalidInstallationContextError(
            f"{label} must be a non-empty absolute path, got {path!r}"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise InvalidInstallationContextError(f"{label} must be an existing directory: {path}") from exc
    if not resolved.is_dir():
        raise InvalidInstallationContextError(f"{label} must be an existing directory: {path}")
    return resolved


def _development_environment(*, platform: str, isolated_home: Path) -> dict[str, str]:
    if platform == "win32":
        return {
            "LOCALAPPDATA": str(isolated_home / "AppData" / "Local"),
            "APPDATA": str(isolated_home / "AppData" / "Roaming"),
        }
    if platform == "darwin":
        return {}
    return {
        "XDG_DATA_HOME": str(isolated_home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(isolated_home / ".config"),
        "XDG_STATE_HOME": str(isolated_home / ".local" / "state"),
    }


def _assistant_home(
    environ: Mapping[str, str], name: str, default: Path
) -> Path:
    value = environ.get(name)
    if value is None:
        return default
    path = Path(value)
    if not value or not path.is_absolute():
        raise InvalidInstallationContextError(f"{name} must be a non-empty absolute path")
    return path


def resolve_stable_roots(
    *, platform: str, home: Path, environ: Mapping[str, str]
) -> tuple[Path, ...]:
    """Derive the normal Famulus roots and assistant homes to protect."""
    paths = resolve_famulus_paths(platform=platform, home=home, environ=environ)
    return (
        paths.data_root,
        paths.config_root,
        paths.state_root,
        paths.user_bin,
        _assistant_home(environ, "CODEX_HOME", home / ".codex"),
        _assistant_home(environ, "CLAUDE_CONFIG_DIR", home / ".claude"),
    )


def resolve_installation_context(
    *,
    mode: Literal["standard", "development"],
    source_root: Path,
    development_root: Path | None,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
    installation_id: str | None = None,
) -> InstallationContext:
    """Resolve immutable installation facts without retaining ambient policy."""
    source = _existing_absolute_directory(source_root, label="source_root")
    if mode == "standard":
        if development_root is not None or installation_id not in (None, "standard"):
            raise InvalidInstallationContextError(
                "standard mode cannot have a development root or non-standard identity"
            )
        paths = resolve_famulus_paths(platform=platform, home=home, environ=environ)
        return InstallationContext(
            mode="standard",
            source_root=source,
            development_root=None,
            paths=paths,
            codex_home=_assistant_home(environ, "CODEX_HOME", home / ".codex"),
            claude_home=_assistant_home(environ, "CLAUDE_CONFIG_DIR", home / ".claude"),
            installation_id="standard",
        )
    if mode != "development":
        raise InvalidInstallationContextError(f"unsupported installation mode: {mode!r}")
    if development_root is None:
        raise InvalidInstallationContextError("development mode requires development_root")
    checkout = _existing_absolute_directory(development_root, label="development_root")
    if source != checkout:
        raise InvalidInstallationContextError(
            "development source_root must be the selected development checkout"
        )
    if installation_id is None or not _DEVELOPMENT_ID.fullmatch(installation_id):
        raise InvalidInstallationContextError("development mode requires a valid immutable installation ID")
    local_root = checkout / ".famulus"
    isolated_home = local_root / "home"
    paths = resolve_famulus_paths(
        platform=platform,
        home=isolated_home,
        environ=_development_environment(platform=platform, isolated_home=isolated_home),
    )
    context = InstallationContext(
        mode="development",
        source_root=source,
        development_root=checkout,
        paths=paths,
        codex_home=local_root / "homes" / "codex",
        claude_home=local_root / "homes" / "claude",
        installation_id=installation_id,
    )
    local_root = validate_development_boundaries(
        context,
        operation="resolve",
        platform=platform,
        home=home,
        environ=environ,
    )
    recorded_id = _read_development_installation_id(local_root)
    if recorded_id != installation_id:
        raise InvalidInstallationContextError(
            "development installation ID does not match .famulus/install-id"
        )
    return context


def _environment_home(*, platform: str, environ: Mapping[str, str]) -> Path:
    name = "USERPROFILE" if platform == "win32" else "HOME"
    value = environ.get(name)
    if not value or not Path(value).is_absolute():
        raise InvalidInstallationContextError(
            f"{name} must be a non-empty absolute path to load the active context"
        )
    return Path(value)


def load_active_context(
    *, runtime_root: Path, environ: Mapping[str, str]
) -> InstallationContext:
    """Load and validate the active installation without mutating it."""
    if not str(runtime_root) or not runtime_root.is_absolute():
        raise InvalidInstallationContextError(
            f"runtime_root must be a non-empty absolute path, got {runtime_root!r}"
        )
    resolved_runtime_root = runtime_root.resolve(strict=False)
    pointer = load_current_pointer(
        runtime_root=resolved_runtime_root,
        trusted_interpreter_roots=load_deployed_resolver_trusted_roots(
            runtime_root=resolved_runtime_root
        ),
    )
    return load_context_from_pointer(
        pointer=pointer,
        runtime_root=resolved_runtime_root,
        environ=environ,
    )


def load_context_from_pointer(
    *, pointer: RuntimePointer, runtime_root: Path, environ: Mapping[str, str]
) -> InstallationContext:
    if not str(runtime_root) or not runtime_root.is_absolute():
        raise InvalidInstallationContextError(
            f"runtime_root must be a non-empty absolute path, got {runtime_root!r}"
        )
    resolved_runtime_root = runtime_root.resolve(strict=False)
    if pointer.installation_context is None or pointer.launcher_resources is None:
        raise RuntimePointerError("active installation requires a schema-3 pointer")
    record = load_installed_context_record(pointer.installation_context)
    platform = sys.platform
    if record.mode == "standard":
        context = resolve_installation_context(
            mode="standard",
            source_root=record.source_root,
            development_root=None,
            platform=platform,
            home=_environment_home(platform=platform, environ=environ),
            environ=environ,
            installation_id="standard",
        )
    else:
        if record.development_root is None:
            raise RuntimePointerError(
                "development installation_context needs development_root"
            )
        source = _existing_absolute_directory(record.source_root, label="source_root")
        checkout = _existing_absolute_directory(
            record.development_root, label="development_root"
        )
        if source != checkout:
            raise InvalidInstallationContextError(
                "development source_root must equal development_root"
            )
        local_root = _validate_local_root(
            checkout, operation="load active context", stable_roots=()
        )
        recorded_id = _read_development_installation_id(local_root)
        if recorded_id != record.installation_id:
            raise InvalidInstallationContextError(
                "development installation ID does not match .famulus/install-id"
            )
        isolated_home = local_root / "home"
        canonical_paths = resolve_famulus_paths(
            platform=platform,
            home=isolated_home,
            environ=_development_environment(
                platform=platform, isolated_home=isolated_home
            ),
        )
        supplied_home = _environment_home(platform=platform, environ=environ)
        if supplied_home.resolve(strict=False) != isolated_home.resolve(strict=False):
            raise InvalidInstallationContextError(
                "supplied environment home does not match the resolved development context"
            )
        if platform == "win32":
            supplied_posix_home = environ.get("HOME")
            if (
                not supplied_posix_home
                or not Path(supplied_posix_home).is_absolute()
                or Path(supplied_posix_home).resolve(strict=False)
                != isolated_home.resolve(strict=False)
            ):
                raise InvalidInstallationContextError(
                    "supplied environment HOME does not match the resolved development context"
                )
        paths = resolve_famulus_paths(
            platform=platform, home=supplied_home, environ=environ
        )
        if paths != canonical_paths:
            raise InvalidInstallationContextError(
                "supplied environment paths do not match the resolved development context"
            )
        context = InstallationContext(
            mode="development",
            source_root=source,
            development_root=checkout,
            paths=paths,
            codex_home=_assistant_home(
                environ, "CODEX_HOME", supplied_home / ".codex"
            ),
            claude_home=_assistant_home(
                environ, "CLAUDE_CONFIG_DIR", supplied_home / ".claude"
            ),
            installation_id=record.installation_id,
        )
    if context.paths.runtime_root.resolve(strict=False) != resolved_runtime_root:
        raise InvalidInstallationContextError(
            "installation_context runtime_root does not match the supplied runtime_root"
        )
    if context.codex_home.resolve(strict=False) != record.codex_home:
        raise InvalidInstallationContextError(
            "installation_context codex_home does not match resolved context"
        )
    if context.claude_home.resolve(strict=False) != record.claude_home:
        raise InvalidInstallationContextError(
            "installation_context claude_home does not match resolved context"
        )
    return context


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _validate_local_root(
    checkout: Path, *, operation: str, stable_roots: Sequence[Path]
) -> Path:
    local_root = checkout / ".famulus"
    resolved_checkout = checkout.resolve(strict=True)
    resolved_local = local_root.resolve(strict=False)
    if resolved_local == resolved_checkout or resolved_checkout not in resolved_local.parents:
        raise DevelopmentBoundaryError(
            f"{operation}: .famulus resolves outside the development checkout: {resolved_local}"
        )
    for stable_root in stable_roots:
        resolved_stable = stable_root.resolve(strict=False)
        if _overlaps(resolved_local, resolved_stable):
            raise DevelopmentBoundaryError(
                f"{operation}: development boundary overlaps stable root {resolved_stable}"
            )
    return resolved_local


def validate_development_boundaries(
    context: InstallationContext,
    *,
    operation: str,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> Path:
    """Recheck real-path containment immediately before a development mutation."""
    if context.mode != "development" or context.development_root is None:
        raise DevelopmentBoundaryError(f"{operation}: a development context is required")
    local_root = _validate_local_root(
        context.development_root,
        operation=operation,
        stable_roots=resolve_stable_roots(
            platform=platform,
            home=home,
            environ=environ,
        ),
    )
    writable = (
        context.paths.data_root,
        context.paths.config_root,
        context.paths.state_root,
        context.paths.user_bin,
        context.codex_home,
        context.claude_home,
    )
    for path in writable:
        resolved = path.resolve(strict=False)
        if resolved != local_root and local_root not in resolved.parents:
            raise DevelopmentBoundaryError(
                f"{operation}: writable path escapes .famulus: {path} -> {resolved}"
            )
    return local_root


def _read_development_installation_id(local_root: Path) -> str:
    identifier_path = local_root / "install-id"
    try:
        resolved_identifier = identifier_path.resolve(strict=True)
    except OSError as exc:
        raise InvalidInstallationContextError(
            f"development installation ID is missing or unreadable: {identifier_path}"
        ) from exc
    if local_root not in resolved_identifier.parents:
        raise DevelopmentBoundaryError(
            f"install-id resolves outside .famulus: {resolved_identifier}"
        )
    identifier = identifier_path.read_text(encoding="utf-8").strip()
    if not _DEVELOPMENT_ID.fullmatch(identifier):
        raise InvalidInstallationContextError(
            f"invalid development installation ID in {identifier_path}"
        )
    return identifier


def load_or_create_development_installation_id(
    checkout: Path,
    *,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> str:
    """Read or atomically create the checkout's immutable scheduler-safe ID."""
    resolved_checkout = _existing_absolute_directory(checkout, label="development_root")
    local_root = _validate_local_root(
        resolved_checkout,
        operation="create installation ID",
        stable_roots=resolve_stable_roots(
            platform=platform,
            home=home,
            environ=environ,
        ),
    )
    local_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    identifier_path = local_root / "install-id"
    if identifier_path.exists() or identifier_path.is_symlink():
        return _read_development_installation_id(local_root)
    identifier = f"dev-{uuid.uuid4().hex}"
    temporary = local_root / f".install-id.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        temporary_created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(identifier + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, identifier_path)
        except FileExistsError:
            return _read_development_installation_id(local_root)
        return identifier
    finally:
        if temporary_created:
            temporary.unlink(missing_ok=True)


__all__ = [
    "DevelopmentBoundaryError",
    "InstallationContext",
    "InvalidInstallationContextError",
    "installation_context_home_fields",
    "load_active_context",
    "load_context_from_pointer",
    "load_or_create_development_installation_id",
    "resolve_installation_context",
    "resolve_stable_roots",
    "validate_development_boundaries",
]

"""Host-neutral, non-Documents Famulus install and state path resolution.

Platform literals live in this ``__init__.py`` aggregation seam (the
convention already used by ``officina.dispatcher.platforms``) so that every
other shared file can stay platform-generic.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

AssistantHost = Literal["claude", "codex"]
PathName = Literal["plugin-data", "logging-path", "setup-status"]
FAMULUS_PATH_FIELDS: Mapping[PathName, str] = MappingProxyType({"plugin-data": "plugin_data", "logging-path": "logging_path", "setup-status": "setup_status"})


class FamulusPathsError(Exception):
    """Base exception for Famulus path resolution failures."""


class InvalidFamulusHomeError(FamulusPathsError, ValueError):
    """Raised when the supplied home directory is not an absolute path."""


class FamulusLocalAppDataMissingError(FamulusPathsError, RuntimeError):
    """Compatibility error retained for callers of the former Windows contract."""


class InvalidFamulusPluginContextError(FamulusPathsError, ValueError):
    """Represent incomplete or invalid plugin provenance.

    Intent
    ------
    Distinguish invalid host-scoped inputs from ordinary path failures.
    Rationale
    ---------
    Callers need one stable error type for rejected plugin launch declarations.
    Pseudocode
    ----------
    - set plugin_context_error = invalid host or data declaration
    Wraps
    -----
    - none"""


class UnknownFamulusPathError(FamulusPathsError, ValueError):
    """Represent a request for an undeclared public path.

    Intent
    ------
    Separate unknown finite path names from unavailable declared paths.
    Rationale
    ---------
    The public getter needs a stable failure for names outside its finite map.
    Pseudocode
    ----------
    - set unknown_path_error = requested name outside the public mapping
    Wraps
    -----
    - none"""


class FamulusPluginContextRequiredError(FamulusPathsError, RuntimeError):
    """Represent an unavailable plugin-only path selection.

    Intent
    ------
    Distinguish absent plugin provenance from an unknown path name.
    Rationale
    ---------
    Declared plugin paths cannot be returned safely without explicit context.
    Pseudocode
    ----------
    - set context_required_error = declared path without plugin provenance
    Wraps
    -----
    - none"""


@dataclass(frozen=True)
class FamulusPaths:
    # Core roots.
    data_root: Path
    config_root: Path
    state_root: Path
    user_bin: Path

    # Shared feature layout, derived from data_root/state_root.
    worker_root: Path

    # Feature-specific config/state subdirectories.
    recurring_config_root: Path
    recurring_state_root: Path
    email_triage_state_root: Path
    assistant_host: AssistantHost | None = None
    plugin_data: Path | None = None
    logging_path: Path | None = None
    setup_status: Path | None = None

    @classmethod
    def get(cls, name: PathName, *, platform: str, home: Path,
            environ: Mapping[str, str]) -> Path:
        """Return one finite public path from explicit resolution inputs.

        Intent
        ------
        Select a declared path while preserving path-resolution failure types.
        Rationale
        ---------
        A finite getter prevents callers from probing arbitrary result fields.
        Pseudocode
        ----------
        - return declared path selected from resolved explicit inputs
        Wraps
        -----
        - none
        InstantiationsFromRepo
        ----------------------
        .UnknownFamulusPathError: {why: {raises: "Carries an undeclared finite path request back to the caller."}}
        .resolve_famulus_paths: {why: {constructs: "Builds the complete explicit path projection used for selection."}}
        .FamulusPluginContextRequiredError: {why: {raises: "Carries absence of required plugin provenance back to the caller."}}
        """
        field = FAMULUS_PATH_FIELDS.get(name)
        if field is None:
            raise UnknownFamulusPathError(f"unknown Famulus path {name!r}")
        paths = resolve_famulus_paths(platform=platform, home=home, environ=environ)
        value = getattr(paths, field)
        if value is None:
            raise FamulusPluginContextRequiredError(f"{name} requires plugin context")
        return value


def _absolute_root(value: str | Path, *, label: str) -> Path:
    path = Path(value)
    if not str(value) or not path.is_absolute():
        raise InvalidFamulusHomeError(f"{label} must be a non-empty absolute path, got {value!r}")
    return path


def _environment_root(environ: Mapping[str, str], name: str) -> Path | None:
    if name not in environ:
        return None
    return _absolute_root(environ[name], label=name)


def _plugin_context(environ: Mapping[str, str]) -> tuple[AssistantHost | None, Path | None]:
    """Validate and return explicit plugin host provenance.

    Intent
    ------
    Accept either a complete supported plugin declaration or no declaration.
    Rationale
    ---------
        Coupled validation prevents partial or ambiguous client-owned state roots.
        Pseudocode
        ----------
        - return complete validated plugin context or no context
    Wraps
    -----
    - none
    InstantiationsFromRepo
    ----------------------
    .InvalidFamulusPluginContextError: {why: {raises: "Carries incomplete or unsupported launch provenance back to callers."}}
    """
    host = environ.get("FAMULUS_HOST")
    raw_data = environ.get("FAMULUS_PLUGIN_DATA")
    if host is None and raw_data is None:
        return None, None
    if host is None or raw_data is None:
        raise InvalidFamulusPluginContextError("plugin host and data must be supplied together")
    if host not in ("claude", "codex"):
        raise InvalidFamulusPluginContextError(f"invalid FAMULUS_HOST {host!r}")
    if not raw_data or not Path(raw_data).is_absolute():
        raise InvalidFamulusPluginContextError("FAMULUS_PLUGIN_DATA must be non-empty and absolute")
    return host, Path(raw_data)


def resolve_famulus_paths(
    *, platform: str, home: Path, environ: Mapping[str, str]
) -> FamulusPaths:
    """Resolve all Famulus paths from explicit inputs only.

    ``environ`` is deliberately mandatory: installation callers must pass the
    environment they selected, and this function never consults ``os.environ``.

    InstantiationsFromRepo
    ----------------------
    ._plugin_context: {why: {constructs: "Builds validated optional plugin provenance for the complete path projection."}}
    """
    if not str(home) or not home.is_absolute():
        raise InvalidFamulusHomeError(f"home must be an absolute path, got {home!r}")
    assistant_host, plugin_data = _plugin_context(environ)
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"):
        _environment_root(environ, name)

    if platform == "darwin":
        base = home / "Library" / "Application Support" / "Famulus"
        data_root = base
        config_root = base / "config"
        state_root = base / "state"
        user_bin = home / ".local" / "bin"
    elif platform == "win32":
        local_app_data = (
            _environment_root(environ, "LOCALAPPDATA") or home / "AppData" / "Local"
        )
        app_data = (
            _environment_root(environ, "APPDATA") or home / "AppData" / "Roaming"
        )
        base = local_app_data / "Famulus"
        data_root = base
        config_root = app_data / "Famulus"
        state_root = base / "state"
        user_bin = base / "bin"
    else:
        xdg_data = _environment_root(environ, "XDG_DATA_HOME")
        data_root = xdg_data / "famulus" if xdg_data else home / ".local" / "share" / "famulus"
        xdg_config = _environment_root(environ, "XDG_CONFIG_HOME")
        config_root = xdg_config / "famulus" if xdg_config else home / ".config" / "famulus"
        xdg_state = _environment_root(environ, "XDG_STATE_HOME")
        state_root = xdg_state / "famulus" if xdg_state else home / ".local" / "state" / "famulus"
        user_bin = home / ".local" / "bin"

    return FamulusPaths(
        data_root=data_root,
        config_root=config_root,
        state_root=state_root,
        user_bin=user_bin,
        worker_root=state_root / "workers",
        recurring_config_root=config_root / "recurring-tasks",
        recurring_state_root=state_root / "recurring-tasks",
        email_triage_state_root=state_root / "email-triage",
        assistant_host=assistant_host,
        plugin_data=plugin_data,
        logging_path=plugin_data / "milestones" if plugin_data else None,
        setup_status=plugin_data / "setup" / "status.json" if plugin_data else None,
    )


__all__ = [
    "AssistantHost",
    "FAMULUS_PATH_FIELDS",
    "FamulusPaths",
    "FamulusPathsError",
    "FamulusPluginContextRequiredError",
    "InvalidFamulusHomeError",
    "InvalidFamulusPluginContextError",
    "FamulusLocalAppDataMissingError",
    "PathName",
    "UnknownFamulusPathError",
    "resolve_famulus_paths",
]

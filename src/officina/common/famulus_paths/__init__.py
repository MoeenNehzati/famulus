"""Host-neutral, non-Documents Famulus install and state path resolution.

Platform literals live in this ``__init__.py`` aggregation seam (the
convention already used by ``officina.dispatcher.platforms``) so that every
other shared file can stay platform-generic.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class FamulusPathsError(Exception):
    """Base exception for Famulus path resolution failures."""


class InvalidFamulusHomeError(FamulusPathsError, ValueError):
    """Raised when the supplied home directory is not an absolute path."""


class FamulusLocalAppDataMissingError(FamulusPathsError, RuntimeError):
    """Compatibility error retained for callers of the former Windows contract."""


@dataclass(frozen=True)
class FamulusPaths:
    # Core roots.
    data_root: Path
    config_root: Path
    state_root: Path
    user_bin: Path

    # Runtime and install layout, derived from data_root/state_root.
    runtime_root: Path
    releases_root: Path
    current_pointer: Path
    install_state_root: Path
    uv_bin: Path
    python_install_dir: Path
    worker_root: Path
    launcher_profile_root: Path

    # Feature-specific config/state subdirectories.
    recurring_config_root: Path
    recurring_state_root: Path
    email_triage_state_root: Path


def _absolute_root(value: str | Path, *, label: str) -> Path:
    path = Path(value)
    if not str(value) or not path.is_absolute():
        raise InvalidFamulusHomeError(f"{label} must be a non-empty absolute path, got {value!r}")
    return path


def _environment_root(environ: Mapping[str, str], name: str) -> Path | None:
    if name not in environ:
        return None
    return _absolute_root(environ[name], label=name)


def resolve_famulus_paths(
    *, platform: str, home: Path, environ: Mapping[str, str]
) -> FamulusPaths:
    """Resolve all Famulus paths from explicit inputs only.

    ``environ`` is deliberately mandatory: installation callers must pass the
    environment they selected, and this function never consults ``os.environ``.
    """
    if not str(home) or not home.is_absolute():
        raise InvalidFamulusHomeError(f"home must be an absolute path, got {home!r}")
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

    runtime_root = data_root / "runtime"
    return FamulusPaths(
        data_root=data_root,
        config_root=config_root,
        state_root=state_root,
        user_bin=user_bin,
        runtime_root=runtime_root,
        releases_root=runtime_root / "releases",
        current_pointer=runtime_root / "current.json",
        install_state_root=state_root / "install",
        uv_bin=data_root / "tools" / "uv",
        python_install_dir=data_root / "python",
        worker_root=state_root / "workers",
        launcher_profile_root=data_root / "launcher-profiles",
        recurring_config_root=config_root / "recurring-tasks",
        recurring_state_root=state_root / "recurring-tasks",
        email_triage_state_root=state_root / "email-triage",
    )


__all__ = [
    "FamulusPaths",
    "FamulusPathsError",
    "InvalidFamulusHomeError",
    "FamulusLocalAppDataMissingError",
    "resolve_famulus_paths",
]

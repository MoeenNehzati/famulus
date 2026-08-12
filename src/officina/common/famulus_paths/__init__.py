"""Host-neutral, non-Documents Famulus install and state path resolution.

Platform literals live in this ``__init__.py`` aggregation seam (the
convention already used by ``officina.dispatcher.platforms``) so that every
other shared file can stay platform-generic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class FamulusPathsError(Exception):
    """Base exception for Famulus path resolution failures."""


class InvalidFamulusHomeError(FamulusPathsError, ValueError):
    """Raised when the supplied home directory is not an absolute path."""


class FamulusLocalAppDataMissingError(FamulusPathsError, RuntimeError):
    """Raised when LOCALAPPDATA is required but unset on Windows."""


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

    # Durable certificate identity, independent of replaceable plugin sources.
    certificate_state_root: Path
    certificate_public_key_root: Path

    # Feature-specific config/state subdirectories.
    recurring_config_root: Path
    recurring_state_root: Path
    email_triage_state_root: Path


def resolve_famulus_paths(*, platform: str, home: Path) -> FamulusPaths:
    if not home.is_absolute():
        raise InvalidFamulusHomeError(f"home must be an absolute path, got {home!r}")

    if platform == "darwin":
        base = home / "Library" / "Application Support" / "Famulus"
        data_root = base
        config_root = base / "config"
        state_root = base / "state"
        user_bin = home / ".local" / "bin"
    elif platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise FamulusLocalAppDataMissingError(
                "LOCALAPPDATA is required to resolve Famulus paths on Windows"
            )
        base = Path(local_app_data) / "Famulus"
        data_root = base
        config_root = base / "config"
        state_root = base / "state"
        user_bin = base / "bin"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        data_root = Path(xdg_data) / "famulus" if xdg_data else home / ".local" / "share" / "famulus"
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        config_root = Path(xdg_config) / "famulus" if xdg_config else home / ".config" / "famulus"
        xdg_state = os.environ.get("XDG_STATE_HOME")
        state_root = Path(xdg_state) / "famulus" if xdg_state else home / ".local" / "state" / "famulus"
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
        certificate_state_root=data_root / "certificates",
        certificate_public_key_root=data_root / "certificates" / "public-keys",
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

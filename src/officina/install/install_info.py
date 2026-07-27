"""Parse and validate the pinned bootstrap/runtime versions in install-info.toml."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib


class InstallInfoError(Exception):
    """Raised when install-info.toml is missing required data or unsupported."""


@dataclass(frozen=True)
class InstallInfo:
    schema_version: int
    uv_version: str
    managed_python: str
    managed_python_supported: str


def load_install_info(path: Path) -> InstallInfo:
    """Load and validate the pinned bootstrap/runtime versions from ``path``."""
    data = tomllib.loads(path.read_text())
    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise InstallInfoError(
            f"unsupported install-info schema_version: {schema_version!r}"
        )
    try:
        return InstallInfo(
            schema_version=schema_version,
            uv_version=data["bootstrap"]["uv_version"],
            managed_python=data["managed_python"]["preferred"],
            managed_python_supported=data["managed_python"]["supported"],
        )
    except KeyError as exc:
        raise InstallInfoError(f"install-info file missing required key: {exc}") from exc


__all__ = ["InstallInfo", "InstallInfoError", "load_install_info"]

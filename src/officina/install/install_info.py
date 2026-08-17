"""Parse and validate the pinned bootstrap/runtime versions in install-info.toml."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import tomllib

import officina.common.toml_io as toml_io


class InstallInfoError(Exception):
    """Raised when install-info.toml is missing required data or unsupported."""


@dataclass(frozen=True)
class InstallInfo:
    schema_version: int
    uv_version: str
    managed_python: str
    managed_python_supported: str


def load_install_info(base: Path) -> InstallInfo:
    """Load and validate the pinned bootstrap/runtime versions from
    ``install-info.toml`` under ``base``."""
    with toml_io.open(base, "install-info.toml") as f:
        data = tomllib.loads(f.read())
    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise InstallInfoError(
            f"unsupported install-info schema_version: {schema_version!r}"
        )
    try:
        info = InstallInfo(
            schema_version=schema_version,
            uv_version=data["bootstrap"]["uv_version"],
            managed_python=data["managed_python"]["preferred"],
            managed_python_supported=data["managed_python"]["supported"],
        )
    except KeyError as exc:
        raise InstallInfoError(f"install-info file missing required key: {exc}") from exc
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", info.managed_python):
        raise InstallInfoError(
            "install-info must pin one exact managed Python patch version"
        )
    if info.managed_python_supported != f"=={info.managed_python}":
        raise InstallInfoError(
            "install-info supported range must equal the exact managed Python patch"
        )
    return info


__all__ = ["InstallInfo", "InstallInfoError", "load_install_info"]

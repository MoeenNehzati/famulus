"""Controlled TOML file access for project-owned runtime code.

Python's stdlib can parse TOML with ``tomllib`` but does not provide a TOML
writer. This module centralizes the text-level TOML access that the project
still needs, so callers do not hand-roll TOML filenames, encodings, or scalar
string escaping at each call site.
"""

from __future__ import annotations

import builtins
import json
import os
import tomllib
from pathlib import Path
from types import TracebackType
from typing import TextIO


class TomlFile:
    """Context manager for UTF-8 TOML files with parse validation on writes."""

    def __init__(self, path: Path, mode: str) -> None:
        self.path = path
        self.mode = mode
        self._file: TextIO | None = None

    def __enter__(self) -> TextIO:
        if _mode_may_write(self.mode):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = builtins.open(self.path, self.mode, encoding="utf-8")
        return self._file

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        assert self._file is not None
        self._file.close()
        if exc_type is None and _mode_may_write(self.mode):
            validate_toml_file(self.path)
        return False


def open(base: Path | str, name: str, mode: str = "r") -> TomlFile:
    """Open a TOML file under ``base`` using UTF-8 text mode.

    ``name`` must be a single TOML filename, not a path. This keeps all TOML
    filename construction inside the controlled TOML boundary.
    """
    if "b" in mode:
        raise ValueError("toml_io.open only supports text modes")
    return TomlFile(Path(base) / _validate_toml_filename(name), mode)


def validate_toml_file(path: Path) -> None:
    """Parse ``path`` as TOML, raising if the file is invalid."""
    tomllib.loads(path.read_text(encoding="utf-8"))


def toml_string(value: str | Path) -> str:
    """Serialize a scalar string value as a TOML-compatible basic string."""
    return json.dumps(str(value), ensure_ascii=False)


def key_value(key: str, value: str | Path) -> str:
    """Return one TOML ``key = value`` line for a scalar string value."""
    if not key.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise ValueError(f"unsupported TOML key: {key!r}")
    return f"{key} = {toml_string(value)}\n"


def profile_config_filename(agent: str) -> str:
    """Return the profile config filename for an agent name."""
    if not agent or "/" in agent or "\\" in agent:
        raise ValueError(f"invalid agent name: {agent!r}")
    return f"{agent}.config.toml"


def iter_profile_configs(directory: Path | str):
    """Yield tracked profile TOML files in a directory."""
    for path in sorted(Path(directory).iterdir()):
        if path.is_file() and path.name.endswith(".config.toml"):
            yield path


def _validate_toml_filename(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("TOML filename must be a string")
    if not name.endswith(".toml"):
        raise ValueError(f"expected a .toml filename: {name!r}")
    if not name or name in {".toml", ".config.toml"}:
        raise ValueError(f"invalid TOML filename: {name!r}")
    if "/" in name or "\\" in name or os.sep in name:
        raise ValueError(f"TOML filename must not contain path separators: {name!r}")
    if os.altsep and os.altsep in name:
        raise ValueError(f"TOML filename must not contain path separators: {name!r}")
    return name


def _mode_may_write(mode: str) -> bool:
    return any(flag in mode for flag in ("w", "a", "x", "+"))

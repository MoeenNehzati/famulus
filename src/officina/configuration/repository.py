"""Strict loading for the repository-owned ``officina.toml`` boundary.

The dispatcher receives an exact absolute configuration path from its launcher.
This module turns that one trusted bootstrap value into confined module roots;
it never searches cwd, parents, environment variables, or the repository.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass
from email.errors import HeaderParseError
from email.headerregistry import Address
from pathlib import Path
from typing import Mapping

from ..common import toml_io


_BARE_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class RepositoryConfigurationError(ValueError):
    """Raised when ``officina.toml`` cannot define safe module roots."""


@dataclass(frozen=True)
class RepositoryConfiguration:
    """Validated repository location and ordered blueprint lookup roots.

    ``config_path`` is the exact launcher-supplied file. ``repository_root`` is
    its parent, and ``module_roots`` contains only confined, existing,
    non-symlink directories in authored lookup order.
    """

    schema_version: int
    config_path: Path
    repository_root: Path
    module_roots: tuple[Path, ...]
    feedback_email: str | None = None


def _require_no_symlink_components(path: Path, *, label: str) -> None:
    """Reject missing or symlinked components without resolving ``path``."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise RepositoryConfigurationError(
                f"{label} does not exist or cannot be inspected: {current}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RepositoryConfigurationError(
                f"{label} contains a symlink component: {current}"
            )


def _load_toml_mapping(config_path: Path) -> Mapping[str, object]:
    """Read the fixed TOML filename and verify the opened regular file."""

    try:
        with toml_io.open(config_path.parent, "officina.toml", "r") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RepositoryConfigurationError(
                    f"repository configuration is not a regular file: {config_path}"
                )
            payload = tomllib.loads(stream.read())
    except RepositoryConfigurationError:
        raise
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RepositoryConfigurationError(
            f"could not read repository configuration {config_path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise RepositoryConfigurationError(
            "repository configuration must be a TOML table"
        )
    return payload


def _relative_root(raw_root: object, *, repository_root: Path) -> Path:
    """Validate one portable relative root and return its absolute path."""

    if not isinstance(raw_root, str):
        raise RepositoryConfigurationError("modules.roots entries must be strings")
    if not raw_root or raw_root.startswith("/"):
        raise RepositoryConfigurationError(
            f"module root must be a nonempty repository-relative path: {raw_root!r}"
        )
    if "\\" in raw_root:
        raise RepositoryConfigurationError(f"unsafe module root path: {raw_root!r}")
    parts = raw_root.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RepositoryConfigurationError(f"unsafe module root path: {raw_root!r}")
    root = repository_root.joinpath(*parts)
    _require_no_symlink_components(root, label="module root")
    if not root.is_dir():
        raise RepositoryConfigurationError(
            f"module root is not a directory: {raw_root!r}"
        )
    return root


def _feedback_email(raw_feedback: object) -> str | None:
    """Validate the optional feedback table and return its single bare address."""

    if raw_feedback is None:
        return None
    if not isinstance(raw_feedback, Mapping):
        raise RepositoryConfigurationError("feedback must be a TOML table")
    unknown = set(raw_feedback) - {"email"}
    if unknown:
        raise RepositoryConfigurationError(f"unknown feedback keys: {sorted(unknown)}")
    raw_email = raw_feedback.get("email")
    if (
        not isinstance(raw_email, str)
        or _BARE_EMAIL_PATTERN.fullmatch(raw_email) is None
    ):
        raise RepositoryConfigurationError(
            "feedback.email must be one nonempty bare email address"
        )
    try:
        address = Address(addr_spec=raw_email)
    except (HeaderParseError, ValueError) as exc:
        raise RepositoryConfigurationError(
            "feedback.email must be one nonempty bare email address"
        ) from exc
    if not address.username or not address.domain or address.addr_spec != raw_email:
        raise RepositoryConfigurationError(
            "feedback.email must be one nonempty bare email address"
        )
    return raw_email


def load_repository_configuration(config_path: Path) -> RepositoryConfiguration:
    """Load one exact absolute ``officina.toml`` without ambient discovery."""

    path = Path(config_path)
    if not path.is_absolute():
        raise RepositoryConfigurationError(
            f"repository configuration path must be absolute: {path}"
        )
    if path.name != toml_io.repository_config_filename():
        raise RepositoryConfigurationError(
            f"unexpected repository configuration filename: {path}"
        )
    path = Path(os.path.abspath(path))
    _require_no_symlink_components(path, label="repository configuration path")
    if not path.is_file():
        raise RepositoryConfigurationError(
            f"repository configuration is not a regular file: {path}"
        )

    payload = _load_toml_mapping(path)
    unknown = set(payload) - {"schema_version", "modules", "feedback"}
    if unknown:
        raise RepositoryConfigurationError(
            f"unknown repository configuration keys: {sorted(unknown)}"
        )
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise RepositoryConfigurationError(
            f"unsupported repository configuration schema_version: {payload.get('schema_version')!r}"
        )
    modules = payload.get("modules")
    if not isinstance(modules, Mapping):
        raise RepositoryConfigurationError("modules must be a TOML table")
    unknown_modules = set(modules) - {"roots"}
    if unknown_modules:
        raise RepositoryConfigurationError(
            f"unknown modules keys: {sorted(unknown_modules)}"
        )
    roots = modules.get("roots")
    if not isinstance(roots, list) or not roots:
        raise RepositoryConfigurationError("modules.roots must be a nonempty array")

    repository_root = path.parent
    module_roots = tuple(
        _relative_root(raw_root, repository_root=repository_root)
        for raw_root in roots
    )
    if len(set(module_roots)) != len(module_roots):
        raise RepositoryConfigurationError("modules.roots contains duplicate paths")
    return RepositoryConfiguration(
        schema_version=1,
        config_path=path,
        repository_root=repository_root,
        module_roots=module_roots,
        feedback_email=_feedback_email(payload.get("feedback")),
    )


__all__ = [
    "RepositoryConfiguration",
    "RepositoryConfigurationError",
    "load_repository_configuration",
]

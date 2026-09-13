"""Fast, unvalidated value fetching across repository blueprints."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import os
from pathlib import Path
import stat

import yaml

from .inventory import (
    JsonValue,
    _EXCLUDED_INFRASTRUCTURE_DIRECTORIES,
    _normalize_json,
    _StrictBlueprintLoader,
)


@dataclass(frozen=True)
class UnverifiedBlueprintValue:
    blueprint_path: Path
    path: tuple[str | int, ...]
    value: JsonValue


def _blueprint_paths(root: Path) -> Iterator[Path]:
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _EXCLUDED_INFRASTRUCTURE_DIRECTORIES
            and not name.startswith(".")
            and not (directory_path / name).is_symlink()
        )
        for name in sorted(file_names):
            if not (
                name == "blueprint.yaml"
                or name.endswith(".blueprint.yaml")
                or directory_path.name == "blueprints" and name.endswith(".yaml")
            ):
                continue
            path = directory_path / name
            try:
                if stat.S_ISREG(path.lstat().st_mode):
                    yield path
            except OSError:
                continue


def quick_fetch_from_all(
    repo_root: Path,
    keys: str | Iterable[str],
) -> tuple[UnverifiedBlueprintValue, ...]:
    """Quickly fetch named values from blueprint-shaped files.

    Unlike ``collect_blueprints`` and ``load_repository_blueprint_graph``, this
    function does not resolve dynamically registered blueprints or reconstruct
    and validate their repository graph. It finds files by naming convention,
    byte-filters them by the requested keys, and parses only the candidates.
    The speed comes from skipping inventory, schema, ownership, routing, and
    authorization checks, so callers must treat every returned value as
    unverified and must not use it to grant authority.
    """

    candidates = (keys,) if isinstance(keys, str) else tuple(keys)
    if not candidates or any(
        not isinstance(key, str) or not key for key in candidates
    ):
        raise ValueError("keys must contain one or more non-empty strings")
    requested = set(candidates)

    matches: list[UnverifiedBlueprintValue] = []

    def visit(
        blueprint_path: Path,
        value: JsonValue,
        path: tuple[str | int, ...],
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, key)
                if key in requested:
                    matches.append(
                        UnverifiedBlueprintValue(blueprint_path, child_path, child)
                    )
                visit(blueprint_path, child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(blueprint_path, child, (*path, index))

    root = Path(repo_root).resolve()
    encoded = tuple(key.encode("utf-8") for key in requested)
    for blueprint_path in _blueprint_paths(root):
        raw = blueprint_path.read_bytes()
        if not any(key in raw for key in encoded):
            continue
        loaded = yaml.load(raw, Loader=_StrictBlueprintLoader)
        if not isinstance(loaded, dict):
            raise ValueError(f"{blueprint_path}: blueprint root must be a mapping")
        declaration = _normalize_json(loaded)
        visit(blueprint_path, declaration, ())
    return tuple(matches)

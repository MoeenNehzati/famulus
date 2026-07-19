from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
from typing import Iterator, Mapping, TypeAlias

import yaml


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class _StrictBlueprintLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _StrictBlueprintLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    seen: set[object] = set()
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "mapping key must be a string",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        seen.add(key)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictBlueprintLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


@dataclass(frozen=True)
class BlueprintDocument:
    path: Path
    relative_path: Path
    owner_root: Path
    declaration: Mapping[str, JsonValue]
    node_type: str | None
    node_id: str | None


@dataclass(frozen=True)
class BlueprintInventoryIssue:
    relative_path: Path
    message: str


@dataclass(frozen=True)
class BlueprintInventoryResult:
    documents: tuple[BlueprintDocument, ...]
    issues: tuple[BlueprintInventoryIssue, ...]


class BlueprintInventoryError(ValueError):
    def __init__(self, issues: tuple[BlueprintInventoryIssue, ...]) -> None:
        self.issues = issues
        details = "; ".join(
            f"{issue.relative_path.as_posix()}: {issue.message}" for issue in issues
        )
        super().__init__(f"blueprint inventory failed: {details}")


def _normalize_json(value: object, *, location: str = "$") -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-JSON number at {location}")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [
            _normalize_json(child, location=f"{location}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"mapping key must be a string at {location}")
            normalized[key] = _normalize_json(child, location=f"{location}.{key}")
        return normalized
    raise ValueError(
        f"non-JSON value at {location}: {type(value).__name__}"
    )


def _blueprint_paths(repo_root: Path) -> tuple[Path, ...]:
    candidates: set[Path] = set()
    excluded_directories = {
        ".git",
        ".pytest_cache",
        "__pycache__",
        "tmp",
        "logs",
        ".health",
        ".certificates",
    }

    def regular_file(path: Path) -> bool:
        try:
            return stat.S_ISREG(path.lstat().st_mode)
        except OSError:
            return False

    def hidden_sidecars(root: Path) -> None:
        if not root.is_dir() or root.is_symlink():
            return
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in excluded_directories
                and not (directory_path / name).is_symlink()
            )
            for name in sorted(file_names):
                if not (name.startswith(".") and name.endswith(".blueprint.yaml")):
                    continue
                path = directory_path / name
                if regular_file(path):
                    candidates.add(path)

    skills_root = repo_root / "skills"
    if skills_root.is_dir() and not skills_root.is_symlink():
        for skill_root in sorted(skills_root.iterdir()):
            if not skill_root.is_dir() or skill_root.is_symlink():
                continue
            root_blueprint = skill_root / "blueprint.yaml"
            if regular_file(root_blueprint):
                candidates.add(root_blueprint)
            hidden_sidecars(skill_root)
    hidden_sidecars(repo_root / "references")
    return tuple(sorted(candidates, key=lambda path: path.relative_to(repo_root).as_posix()))


def _read_regular_no_follow(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"selected blueprint is not a regular file: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        if fd >= 0:
            os.close(fd)


def _owner_root(repo_root: Path, path: Path) -> Path:
    relative = path.relative_to(repo_root)
    if len(relative.parts) >= 2 and relative.parts[0] == "skills":
        return repo_root / "skills" / relative.parts[1]
    return repo_root


def collect_blueprints(
    repo_root: Path, *, skip_parse_errors: bool = False
) -> BlueprintInventoryResult:
    repo_root = Path(repo_root).resolve()
    documents: list[BlueprintDocument] = []
    issues: list[BlueprintInventoryIssue] = []
    for path in _blueprint_paths(repo_root):
        relative = path.relative_to(repo_root)
        try:
            raw = _read_regular_no_follow(path)
            loaded = yaml.load(raw, Loader=_StrictBlueprintLoader)
            if not isinstance(loaded, dict):
                raise ValueError("document root must be a mapping")
            declaration = _normalize_json(loaded)
            assert isinstance(declaration, dict)
            node_type = declaration.get("node_type") or declaration.get(
                "blueprint_type"
            )
            node_id = declaration.get("id")
            documents.append(
                BlueprintDocument(
                    path=path,
                    relative_path=relative,
                    owner_root=_owner_root(repo_root, path),
                    declaration=declaration,
                    node_type=node_type if isinstance(node_type, str) else None,
                    node_id=node_id if isinstance(node_id, str) else None,
                )
            )
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            issues.append(BlueprintInventoryIssue(relative, str(exc)))
    result = BlueprintInventoryResult(tuple(documents), tuple(issues))
    if issues and not skip_parse_errors:
        raise BlueprintInventoryError(result.issues)
    return result


def iter_blueprints(repo_root: Path) -> Iterator[BlueprintDocument]:
    return iter(collect_blueprints(repo_root).documents)

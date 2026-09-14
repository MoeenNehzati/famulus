from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
from typing import AbstractSet, Iterator, Mapping, TypeAlias

import yaml

from ..common.atomic_files import AtomicWriteError, read_regular_file_bytes
from ..git.provenance import git_ignored_paths


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


_SafeBlueprintLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _StrictBlueprintLoader(_SafeBlueprintLoader):
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
    module_root: Path
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


_EXCLUDED_INFRASTRUCTURE_DIRECTORIES = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "_build",
        "build",
        "dist",
        "node_modules",
        "tmp",
        "logs",
        ".health",
        ".certificates",
        ".certificate-history",
    }
)


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _ignored_paths(repo_root: Path) -> frozenset[Path]:
    try:
        git_marker_mode = (repo_root / ".git").lstat().st_mode
    except FileNotFoundError:
        return frozenset()
    if not (stat.S_ISDIR(git_marker_mode) or stat.S_ISREG(git_marker_mode)):
        return frozenset()
    return frozenset(repo_root / path for path in git_ignored_paths(repo_root))


def _ignored_path(path: Path, ignored_paths: AbstractSet[Path]) -> bool:
    return path in ignored_paths or any(
        parent in ignored_paths for parent in path.parents
    )


def _excluded_directory(
    path: Path,
    *,
    ignored_paths: AbstractSet[Path],
) -> bool:
    return (
        path.name in _EXCLUDED_INFRASTRUCTURE_DIRECTORIES
        or path.is_symlink()
        or _ignored_path(path, ignored_paths)
    )


def _canonical_module_roots(
    repo_root: Path,
    ignored_paths: AbstractSet[Path],
    *,
    issues: list[BlueprintInventoryIssue] | None = None,
    reject_nested_repositories: bool = False,
    exclude_host_system_skills: bool = False,
) -> tuple[Path, ...]:
    roots: set[Path] = set()
    for directory, directory_names, file_names in os.walk(
        repo_root, followlinks=False
    ):
        directory_path = Path(directory)
        if (
            reject_nested_repositories
            and directory_path != repo_root
            and ".git" in {*directory_names, *file_names}
        ):
            if issues is not None:
                issues.append(
                    BlueprintInventoryIssue(
                        (directory_path / ".git").relative_to(repo_root),
                        "nested source-control repository is not allowed",
                    )
                )
            directory_names[:] = []
            continue
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not _excluded_directory(
                directory_path / name,
                ignored_paths=ignored_paths,
            )
            and not (
                exclude_host_system_skills
                and directory_path / name == repo_root / "skills" / ".system"
            )
        )
        if "blueprint.yaml" in file_names:
            marker = directory_path / "blueprint.yaml"
            if _regular_file(marker) and not _ignored_path(marker, ignored_paths):
                roots.add(directory_path)
    return tuple(sorted(roots))


def _blueprint_paths(
    repo_root: Path,
    module_roots: tuple[Path, ...],
    ignored_paths: AbstractSet[Path],
) -> tuple[Path, ...]:
    candidates: set[Path] = set()

    for module_root in module_roots:
        marker = module_root / "blueprint.yaml"
        if _regular_file(marker) and not _ignored_path(marker, ignored_paths):
            candidates.add(marker)
        blueprints_root = module_root / "blueprints"
        if blueprints_root.is_dir() and not blueprints_root.is_symlink():
            for source_blueprint in sorted(blueprints_root.glob("*.yaml")):
                if _regular_file(source_blueprint) and not _ignored_path(
                    source_blueprint, ignored_paths
                ):
                    candidates.add(source_blueprint)
    return tuple(sorted(candidates, key=lambda path: path.relative_to(repo_root).as_posix()))


def _module_root(
    repo_root: Path, path: Path, module_roots: tuple[Path, ...]
) -> Path:
    roots = {root: root for root in module_roots}
    return next((roots[parent] for parent in (path, *path.parents) if parent in roots), repo_root)


def _nearest_module_parent(
    module_root: Path,
    module_roots: tuple[Path, ...],
) -> Path | None:
    roots = {root: root for root in module_roots}
    return next((roots[parent] for parent in module_root.parents if parent in roots), None)


def _reconcile_topology(
    repo_root: Path,
    module_roots: tuple[Path, ...],
    marker_documents: Mapping[Path, BlueprintDocument],
    issues: list[BlueprintInventoryIssue],
) -> None:
    """Validate direct identity from physical nesting and child registration."""

    physical_parents = {
        root: _nearest_module_parent(root, module_roots)
        for root in module_roots
    }
    valid = {
        root: document
        for root, document in marker_documents.items()
        if document.declaration.get("schema_version") == 6
        and document.declaration.get("node_type") == "module"
    }
    registered: set[Path] = set()
    for module_root, document in sorted(valid.items()):
        parent_root = physical_parents[module_root]
        expected_id = (
            module_root.name
            if parent_root is None
            else f"{valid[parent_root].node_id}.{module_root.name}"
            if parent_root in valid and valid[parent_root].node_id is not None
            else None
        )
        if expected_id is not None and document.node_id != expected_id:
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    f"module id {document.node_id!r} must match direct identity {expected_id!r}",
                )
            )
        children = document.declaration.get("children")
        if not isinstance(children, dict):
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    "version 6 module children must be an explicit mapping",
                )
            )
            continue
        for segment, registration in children.items():
            if not isinstance(segment, str) or registration != {}:
                issues.append(
                    BlueprintInventoryIssue(
                        document.relative_path,
                        f"child {segment!r} must be an empty local-segment registration",
                    )
                )
                continue
            child_root = module_root / segment
            child = valid.get(child_root)
            expected_child_id = f"{document.node_id}.{segment}"
            if child is None or child.node_id != expected_child_id:
                issues.append(
                    BlueprintInventoryIssue(
                        document.relative_path,
                        f"child segment {segment!r} does not identify {expected_child_id!r}",
                    )
                )
                continue
            if physical_parents.get(child_root) != module_root:
                issues.append(
                    BlueprintInventoryIssue(
                        child.relative_path,
                        "registration must come from the nearest physical parent",
                    )
                )
                continue
            if child_root in registered:
                issues.append(
                    BlueprintInventoryIssue(
                        child.relative_path,
                        "nested module is registered more than once",
                    )
                )
            registered.add(child_root)
    for module_root, parent_root in sorted(physical_parents.items()):
        if parent_root is not None and module_root not in registered:
            issues.append(
                BlueprintInventoryIssue(
                    module_root.relative_to(repo_root) / "blueprint.yaml",
                    "unregistered nested module marker was not consumed exactly once",
                )
            )


def collect_blueprints(
    repo_root: Path,
    *,
    skip_parse_errors: bool = False,
) -> BlueprintInventoryResult:
    repo_root = Path(repo_root).resolve()
    documents: list[BlueprintDocument] = []
    issues: list[BlueprintInventoryIssue] = []
    ignored_paths = _ignored_paths(repo_root)
    module_roots = _canonical_module_roots(
        repo_root,
        ignored_paths,
        issues=issues,
        reject_nested_repositories=True,
        exclude_host_system_skills=True,
    )
    for path in _blueprint_paths(repo_root, module_roots, ignored_paths):
        relative = path.relative_to(repo_root)
        try:
            raw = read_regular_file_bytes(
                path,
                allowed_root=repo_root,
            ).decode("utf-8")
            loaded = yaml.load(raw, Loader=_StrictBlueprintLoader)
            if not isinstance(loaded, dict):
                raise ValueError("document root must be a mapping")
            declaration = _normalize_json(loaded)
            assert isinstance(declaration, dict)
            node_type = declaration.get("node_type")
            node_id = declaration.get("id")
            documents.append(
                BlueprintDocument(
                    path=path,
                    relative_path=relative,
                    module_root=_module_root(repo_root, path, module_roots),
                    declaration=declaration,
                    node_type=node_type if isinstance(node_type, str) else None,
                    node_id=node_id if isinstance(node_id, str) else None,
                )
            )
        except (
            AtomicWriteError,
            OSError,
            UnicodeError,
            ValueError,
            yaml.YAMLError,
        ) as exc:
            issues.append(BlueprintInventoryIssue(relative, str(exc)))
    marker_documents = [
        document
        for document in documents
        if document.path == document.module_root / "blueprint.yaml"
    ]
    modules_by_id: dict[str, BlueprintDocument] = {}
    for document in marker_documents:
        declaration = document.declaration
        if (
            declaration.get("schema_version") != 6
            or declaration.get("node_type") != "module"
        ):
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    "canonical module marker must declare schema_version "
                    "6 and node_type module",
                )
            )
            continue
        module_id = declaration.get("id")
        if not isinstance(module_id, str):
            continue
        previous = modules_by_id.get(module_id)
        if previous is not None:
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    f"duplicate module id {module_id!r}: "
                    f"{previous.relative_path.as_posix()} and "
                    f"{document.relative_path.as_posix()}",
                )
            )
        else:
            modules_by_id[module_id] = document
    _reconcile_topology(
        repo_root,
        module_roots,
        {
            document.module_root: document
            for document in marker_documents
        },
        issues,
    )
    issues.sort(key=lambda issue: (issue.relative_path.as_posix(), issue.message))
    documents.sort(key=lambda document: document.relative_path.as_posix())
    result = BlueprintInventoryResult(tuple(documents), tuple(issues))
    if issues and not skip_parse_errors:
        raise BlueprintInventoryError(result.issues)
    return result


def iter_blueprints(repo_root: Path) -> Iterator[BlueprintDocument]:
    return iter(collect_blueprints(repo_root).documents)

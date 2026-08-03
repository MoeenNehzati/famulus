from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
from typing import AbstractSet, Iterator, Mapping, TypeAlias

import yaml

from .atomic_files import AtomicWriteError, read_regular_file_bytes
from .git_provenance import git_ignored_paths


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
    owners = [root for root in module_roots if path.is_relative_to(root)]
    if owners:
        return max(owners, key=lambda root: len(root.parts))
    return repo_root


@dataclass(frozen=True)
class _Registration:
    parent_root: Path
    child_root: Path
    child_id: str


def _nearest_module_parent(
    module_root: Path,
    module_roots: tuple[Path, ...],
) -> Path | None:
    parents = [
        candidate
        for candidate in module_roots
        if candidate != module_root and module_root.is_relative_to(candidate)
    ]
    if not parents:
        return None
    return max(parents, key=lambda path: len(path.parts))


def _module_label(
    module_root: Path,
    marker_documents: Mapping[Path, BlueprintDocument],
    repo_root: Path,
) -> str:
    document = marker_documents.get(module_root)
    if document is not None and document.node_id is not None:
        return document.node_id
    return module_root.relative_to(repo_root).as_posix()


def _child_marker_target(
    module_root: Path,
    locator_path: str,
) -> tuple[Path | None, str | None]:
    if (
        not locator_path
        or "\\" in locator_path
        or "\0" in locator_path
        or ":" in locator_path
    ):
        return None, "child locator path is not a canonical relative path"
    relative = PurePosixPath(locator_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != locator_path
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.name != "blueprint.yaml"
    ):
        return None, "child locator path is not a canonical relative blueprint.yaml path"
    return module_root.joinpath(*relative.parts), None


def _path_contains_symlink(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        return True
    current = repo_root
    for component in relative.parts:
        current = current / component
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return False


def _discovery_is_skill(declaration: Mapping[str, JsonValue]) -> bool:
    discovery = declaration.get("discovery")
    return (
        isinstance(discovery, dict)
        and discovery.get("mechanism") == "skill"
    )


def _registration_cycle_issues(
    registrations: tuple[_Registration, ...],
    marker_documents: Mapping[Path, BlueprintDocument],
    repo_root: Path,
) -> tuple[BlueprintInventoryIssue, ...]:
    adjacency: dict[Path, set[Path]] = {}
    for registration in registrations:
        adjacency.setdefault(registration.parent_root, set()).add(
            registration.child_root
        )

    state: dict[Path, int] = {}
    stack: list[Path] = []
    reported: set[tuple[Path, ...]] = set()
    issues: list[BlueprintInventoryIssue] = []

    def visit(module_root: Path) -> None:
        state[module_root] = 1
        stack.append(module_root)
        for child_root in sorted(adjacency.get(module_root, ())):
            child_state = state.get(child_root, 0)
            if child_state == 0:
                visit(child_root)
                continue
            if child_state != 1:
                continue
            cycle_start = stack.index(child_root)
            cycle = tuple(stack[cycle_start:] + [child_root])
            canonical = tuple(sorted(set(cycle)))
            if canonical in reported:
                continue
            reported.add(canonical)
            labels = [
                _module_label(root, marker_documents, repo_root)
                for root in cycle
            ]
            document = marker_documents.get(module_root)
            relative_path = (
                document.relative_path
                if document is not None
                else module_root.relative_to(repo_root) / "blueprint.yaml"
            )
            issues.append(
                BlueprintInventoryIssue(
                    relative_path,
                    f"registration cycle: {' -> '.join(labels)}",
                )
            )
        stack.pop()
        state[module_root] = 2

    nodes = set(adjacency)
    for children in adjacency.values():
        nodes.update(children)
    for module_root in sorted(nodes):
        if state.get(module_root, 0) == 0:
            visit(module_root)
    return tuple(issues)


def _reconcile_v5_topology(
    repo_root: Path,
    module_roots: tuple[Path, ...],
    marker_documents: Mapping[Path, BlueprintDocument],
    ignored_paths: AbstractSet[Path],
    issues: list[BlueprintInventoryIssue],
) -> None:
    physical_parents = {
        root: _nearest_module_parent(root, module_roots)
        for root in module_roots
    }
    valid_markers = {
        root: document
        for root, document in marker_documents.items()
        if document.declaration.get("schema_version") == 5
        and document.declaration.get("node_type") == "module"
    }

    managed_skills: set[Path] = set()
    skills_root = repo_root / "skills"
    for module_root, document in valid_markers.items():
        top_level = physical_parents[module_root] is None
        direct_skills_child = module_root.parent == skills_root
        has_skill_gateway = _regular_file(module_root / "SKILL.md")
        declares_skill = _discovery_is_skill(document.declaration)
        id_matches = document.node_id == module_root.name
        has_skill_signal = (
            direct_skills_child or has_skill_gateway or declares_skill
        )
        accepted = (
            top_level
            and direct_skills_child
            and has_skill_gateway
            and declares_skill
            and id_matches
        )
        if accepted:
            managed_skills.add(module_root)
        elif has_skill_signal:
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    "partial repository-managed skill: require a top-level module "
                    "directly below skills/ with a regular SKILL.md, matching "
                    "directory/module id, and discovery.mechanism skill",
                )
            )

    for module_root, document in valid_markers.items():
        module_id = document.node_id
        if module_id is None:
            continue
        if module_root.name != "_rtx":
            if module_id != module_root.name:
                issues.append(
                    BlueprintInventoryIssue(
                        document.relative_path,
                        f"module id {module_id!r} must match its directory",
                    )
                )
            continue
        parent_root = physical_parents[module_root]
        if (
            parent_root is None
            or parent_root not in managed_skills
            or module_root != parent_root / "_rtx"
        ):
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    "_rtx module-root exception is reserved for the direct code "
                    "child of a repository-managed skill",
                )
            )
            continue
        parent_id = valid_markers[parent_root].node_id
        assert parent_id is not None
        expected_id = f"{parent_id}-rtx"
        if module_id != expected_id:
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    f"code child id must be {expected_id!r}",
                )
            )

    marker_paths = {
        module_root / "blueprint.yaml": module_root
        for module_root in module_roots
    }
    registrations: list[_Registration] = []
    for parent_root, parent_document in valid_markers.items():
        children = parent_document.declaration.get("children")
        if not isinstance(children, dict):
            issues.append(
                BlueprintInventoryIssue(
                    parent_document.relative_path,
                    "version 5 module children must be an explicit mapping",
                )
            )
            continue
        for child_id, raw_locator in children.items():
            if not isinstance(raw_locator, dict):
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator must be a mapping",
                    )
                )
                continue
            if set(raw_locator) != {"base", "path"}:
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator must contain only base and path",
                    )
                )
                continue
            if raw_locator.get("base") != "module-root":
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator base must be module-root",
                    )
                )
                continue
            locator_path = raw_locator.get("path")
            if not isinstance(locator_path, str):
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator path must be a string",
                    )
                )
                continue
            target, diagnostic = _child_marker_target(parent_root, locator_path)
            if target is None:
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} {diagnostic}",
                    )
                )
                continue
            if _ignored_path(target, ignored_paths):
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator points into an ignored path",
                    )
                )
                continue
            if _path_contains_symlink(target, repo_root):
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator contains a symbolic link",
                    )
                )
                continue
            child_root = marker_paths.get(target)
            if child_root is None:
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator does not identify a collected "
                        "canonical module marker",
                    )
                )
                continue
            child_document = valid_markers.get(child_root)
            if child_document is None:
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child {child_id!r} locator does not identify a valid "
                        "version 5 module marker",
                    )
                )
                continue
            if child_document.node_id != child_id:
                issues.append(
                    BlueprintInventoryIssue(
                        parent_document.relative_path,
                        f"child registration id {child_id!r} does not match marker "
                        f"id {child_document.node_id!r}",
                    )
                )
                continue
            registrations.append(
                _Registration(
                    parent_root=parent_root,
                    child_root=child_root,
                    child_id=child_id,
                )
            )

    registration_tuple = tuple(registrations)
    issues.extend(
        _registration_cycle_issues(
            registration_tuple,
            marker_documents,
            repo_root,
        )
    )
    registrations_by_child: dict[Path, list[_Registration]] = {}
    for registration in registrations:
        registrations_by_child.setdefault(registration.child_root, []).append(
            registration
        )

    valid_children: dict[Path, set[Path]] = {}
    for child_root, child_registrations in registrations_by_child.items():
        if len(child_registrations) > 1:
            parents = ", ".join(
                _module_label(
                    registration.parent_root,
                    marker_documents,
                    repo_root,
                )
                for registration in sorted(
                    child_registrations,
                    key=lambda item: item.parent_root,
                )
            )
            issues.append(
                BlueprintInventoryIssue(
                    child_root.relative_to(repo_root) / "blueprint.yaml",
                    f"nested module is registered by multiple parents: {parents}",
                )
            )
            continue
        registration = child_registrations[0]
        nearest_parent = physical_parents[child_root]
        if registration.parent_root != nearest_parent:
            if nearest_parent is None:
                message = (
                    "registered child is not physically contained by its "
                    "authored parent"
                )
            else:
                nearest_label = _module_label(
                    nearest_parent,
                    marker_documents,
                    repo_root,
                )
                authored_label = _module_label(
                    registration.parent_root,
                    marker_documents,
                    repo_root,
                )
                message = (
                    f"registration must come from nearest physical parent "
                    f"{nearest_label!r}, not {authored_label!r}"
                )
            issues.append(
                BlueprintInventoryIssue(
                    child_root.relative_to(repo_root) / "blueprint.yaml",
                    message,
                )
            )
            continue
        if child_root != registration.parent_root:
            valid_children.setdefault(registration.parent_root, set()).add(
                child_root
            )

    reachable = {
        root for root, parent in physical_parents.items() if parent is None
    }
    pending = list(sorted(reachable, reverse=True))
    while pending:
        parent_root = pending.pop()
        for child_root in sorted(valid_children.get(parent_root, ())):
            if child_root in reachable:
                continue
            reachable.add(child_root)
            pending.append(child_root)
    for module_root, physical_parent in physical_parents.items():
        if physical_parent is not None and module_root not in reachable:
            issues.append(
                BlueprintInventoryIssue(
                    module_root.relative_to(repo_root) / "blueprint.yaml",
                    "unregistered nested module marker was not consumed exactly once",
                )
            )

    for skill_root in sorted(managed_skills):
        skill_document = valid_markers[skill_root]
        skill_id = skill_document.node_id
        assert skill_id is not None
        code_root = skill_root / "_rtx"
        code_document = valid_markers.get(code_root)
        if code_document is None:
            if code_root.exists():
                issues.append(
                    BlueprintInventoryIssue(
                        skill_document.relative_path,
                        "existing _rtx implementation directory must contain "
                        "a valid blueprint.yaml",
                    )
                )
            continue
        if "discovery" in code_document.declaration:
            issues.append(
                BlueprintInventoryIssue(
                    code_document.relative_path,
                    "repository-managed skill code child must not declare discovery",
                )
            )
        gateway = code_document.declaration.get("gateway")
        if not isinstance(gateway, dict) or gateway.get("path") != "__init__.py":
            issues.append(
                BlueprintInventoryIssue(
                    code_document.relative_path,
                    "repository-managed skill code child gateway must be __init__.py",
                )
            )
        if not _regular_file(code_root / "__init__.py"):
            issues.append(
                BlueprintInventoryIssue(
                    code_document.relative_path,
                    "repository-managed skill code child requires a regular "
                    "_rtx/__init__.py",
                )
            )
        nested_below_code = [
            root
            for root in module_roots
            if root != code_root and root.is_relative_to(code_root)
        ]
        for nested_root in nested_below_code:
            issues.append(
                BlueprintInventoryIssue(
                    nested_root.relative_to(repo_root) / "blueprint.yaml",
                    "version 5 does not allow nested modules below _rtx",
                )
            )


def collect_blueprints(
    repo_root: Path,
    *,
    expected_schema_version: int = 5,
    skip_parse_errors: bool = False,
) -> BlueprintInventoryResult:
    if expected_schema_version not in {4, 5}:
        raise ValueError("expected_schema_version must be 4 or 5")
    repo_root = Path(repo_root).resolve()
    documents: list[BlueprintDocument] = []
    issues: list[BlueprintInventoryIssue] = []
    ignored_paths = _ignored_paths(repo_root)
    module_roots = _canonical_module_roots(
        repo_root,
        ignored_paths,
        issues=issues,
        reject_nested_repositories=expected_schema_version == 5,
        exclude_host_system_skills=expected_schema_version == 5,
    )
    if expected_schema_version == 4:
        for index, root in enumerate(module_roots):
            for possible_parent in module_roots[:index]:
                if root.is_relative_to(possible_parent):
                    issues.append(
                        BlueprintInventoryIssue(
                            root.relative_to(repo_root) / "blueprint.yaml",
                            "nested module roots are not allowed: "
                            f"{possible_parent.relative_to(repo_root).as_posix()} "
                            f"and {root.relative_to(repo_root).as_posix()}",
                        )
                    )
                    break
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
            declaration.get("schema_version") != expected_schema_version
            or declaration.get("node_type") != "module"
        ):
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    "canonical module marker must declare schema_version "
                    f"{expected_schema_version} and node_type module",
                )
            )
            continue
        module_id = declaration.get("id")
        if (
            expected_schema_version == 4
            and isinstance(module_id, str)
            and module_id != document.module_root.name
        ):
            issues.append(
                BlueprintInventoryIssue(
                    document.relative_path,
                    f"module id {module_id!r} must match its directory",
                )
            )
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
    if expected_schema_version == 5:
        _reconcile_v5_topology(
            repo_root,
            module_roots,
            {
                document.module_root: document
                for document in marker_documents
            },
            ignored_paths,
            issues,
        )
    issues.sort(key=lambda issue: (issue.relative_path.as_posix(), issue.message))
    documents.sort(key=lambda document: document.relative_path.as_posix())
    result = BlueprintInventoryResult(tuple(documents), tuple(issues))
    if issues and not skip_parse_errors:
        raise BlueprintInventoryError(result.issues)
    return result


def iter_blueprints(
    repo_root: Path,
    *,
    expected_schema_version: int = 5,
) -> Iterator[BlueprintDocument]:
    return iter(
        collect_blueprints(
            repo_root,
            expected_schema_version=expected_schema_version,
        ).documents
    )

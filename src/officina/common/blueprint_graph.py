"""Load legacy and typed skill blueprints into one graph representation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

import jsonschema
import yaml


class BlueprintGraphError(ValueError):
    """Raised when blueprint files cannot form a coherent graph."""


class BlueprintSchemaError(BlueprintGraphError):
    """Raised when one typed graph node fails its concrete JSON Schema."""

    def __init__(self, blueprint_path: Path, json_path: str, message: str) -> None:
        self.blueprint_path = blueprint_path
        self.json_path = json_path
        self.schema_message = message
        super().__init__(f"{blueprint_path}: schema error at {json_path}: {message}")


@dataclass(frozen=True)
class BlueprintNode:
    node_id: str
    node_type: str
    version: int
    skill_root: Path
    blueprint_path: Path
    gateway_path: Path | None
    declaration: dict[str, Any]
    virtual: bool = False
    embedded: bool = False

    @property
    def blueprint_type(self) -> str:
        """Return the normalized node type for compatibility with v2 consumers."""

        return self.node_type

    @property
    def binding_path(self) -> Path | None:
        """Return the normalized gateway for compatibility with v2 consumers."""

        return self.gateway_path


@dataclass(frozen=True)
class BlueprintEdge:
    relation: str
    source_id: str
    target_id: str
    required_version: int
    target_blueprint_path: Path | None = None


@dataclass(frozen=True)
class SkillBlueprintGraph:
    skill_root: Path
    root: BlueprintNode
    nodes: dict[str, BlueprintNode]
    edges: tuple[BlueprintEdge, ...]
    root_node_ids: tuple[str, ...] = ()


class RuntimeFileBinding:
    """An opened regular file whose validation is bound to later use."""

    def __init__(self, path: Path, fd: int, mode: int) -> None:
        self.path = path
        self.fd = fd
        self.mode = mode

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def read_bytes(self) -> bytes:
        if self.fd < 0:
            raise BlueprintGraphError(f"{self.path}: runtime input binding is closed")
        os.lseek(self.fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(self.fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)

    def proc_path(self) -> str:
        if self.fd < 0 or not Path("/proc/self/fd").is_dir():
            raise BlueprintGraphError(
                f"{self.path}: descriptor-backed execution is unavailable on this host"
            )
        return f"/proc/self/fd/{self.fd}"

    def is_effectively_executable(self) -> bool:
        if os.access not in os.supports_effective_ids:
            raise BlueprintGraphError(
                f"{self.path}: effective-ID executable checks are unavailable on this host"
            )
        return os.access(self.proc_path(), os.X_OK, effective_ids=True)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


_TYPED_SCHEMA_FILES = {
    (2, "skill"): "v2/skill.schema.json",
    (2, "llm-interface"): "v2/llm-interface.schema.json",
    (2, "machine-interface"): "v2/machine-interface.schema.json",
    (2, "behavior-source"): "v2/behavior-source.schema.json",
    (3, "skill"): "skill.schema.json",
    (3, "llm-interface"): "llm-interface.schema.json",
    (3, "machine-interface"): "machine-interface.schema.json",
    (3, "behavior-source"): "behavior-source.schema.json",
}


def _is_typed_declaration(declaration: dict[str, Any]) -> bool:
    return declaration.get("schema_version") in {2, 3} or any(
        key in declaration for key in ("blueprint_type", "node_type")
    )


def declaration_node_type(declaration: dict[str, Any]) -> str | None:
    """Return one typed declaration's version-normalized node type."""

    key = "node_type" if declaration.get("schema_version") == 3 else "blueprint_type"
    value = declaration.get(key)
    return value if isinstance(value, str) else None


def declaration_gateway(declaration: dict[str, Any]) -> dict[str, Any] | None:
    """Return one typed declaration's version-normalized gateway mapping."""

    key = "gateway" if declaration.get("schema_version") == 3 else "binding"
    value = declaration.get(key)
    return value if isinstance(value, dict) else None


def node_owner_namespace(node: BlueprintNode, repo_root: Path) -> str:
    """Return the namespace determined by a node's canonical sidecar location."""

    blueprint = Path(os.path.abspath(node.blueprint_path))
    references = Path(os.path.abspath(repo_root / "references"))
    if blueprint.is_relative_to(references):
        return "references"
    skills = Path(os.path.abspath(repo_root / "skills"))
    relative = blueprint.relative_to(skills)
    return relative.parts[0]


def edge_key(edge: BlueprintEdge) -> tuple[str, str, str, int, str | None]:
    """Return the canonical identity of one graph relationship."""

    return (
        edge.relation,
        edge.source_id,
        edge.target_id,
        edge.required_version,
        edge.target_blueprint_path.as_posix() if edge.target_blueprint_path else None,
    )


def postorder_node_ids(graph: SkillBlueprintGraph) -> tuple[str, ...]:
    """Return the graph's reachable nodes in deterministic dependency postorder."""

    children: dict[str, list[str]] = {node_id: [] for node_id in graph.nodes}
    for edge in graph.edges:
        children[edge.source_id].append(edge.target_id)
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for child_id in sorted(children[node_id]):
            visit(child_id)
        ordered.append(node_id)

    root_node_ids = graph.root_node_ids or (graph.root.node_id,)
    for root_node_id in sorted(root_node_ids):
        visit(root_node_id)
    return tuple(ordered)


def _descriptor_safe_open_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
    )


def descriptor_safe_open_supported() -> bool:
    """Return whether typed runtime inputs can be opened without path races."""

    return _descriptor_safe_open_supported()


def _runtime_relative_path(
    path: Path,
    owner_root: Path,
    repo_root: Path,
) -> tuple[Path, Path]:
    repo_absolute = Path(os.path.abspath(repo_root))
    owner_absolute = Path(os.path.abspath(owner_root))
    path_absolute = Path(os.path.abspath(path))
    try:
        path_absolute.relative_to(owner_absolute)
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{path}: runtime input must be under its owning root {owner_root}"
        ) from exc
    try:
        relative = path_absolute.relative_to(repo_absolute)
    except ValueError as exc:
        raise BlueprintGraphError(f"{path}: runtime input must be under {repo_root}") from exc
    if not relative.parts:
        raise BlueprintGraphError(f"{path}: runtime input must name a file")
    return path_absolute, relative


def _open_runtime_descriptor(
    path: Path,
    owner_root: Path,
    repo_root: Path,
    *,
    directory: bool = False,
    path_only: bool = False,
) -> RuntimeFileBinding:
    if not _descriptor_safe_open_supported():
        raise BlueprintGraphError(
            f"{path}: descriptor-safe no-follow file access is unavailable on this host"
        )
    path_absolute, relative = _runtime_relative_path(path, owner_root, repo_root)
    if path_only:
        if not hasattr(os, "O_PATH"):
            raise BlueprintGraphError(
                f"{path}: descriptor-bound executable access is unavailable on this host"
            )
        file_flags = os.O_PATH | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    else:
        file_flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
    directory_flags = file_flags | os.O_DIRECTORY
    current_fd = -1
    try:
        current_fd = os.open(Path(os.path.abspath(repo_root)), directory_flags)
        for index, component in enumerate(relative.parts):
            is_final = index == len(relative.parts) - 1
            flags = directory_flags if not is_final or directory else file_flags
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        metadata = os.fstat(current_fd)
        valid_type = (
            stat.S_ISDIR(metadata.st_mode)
            if directory
            else stat.S_ISREG(metadata.st_mode)
        )
        if not valid_type:
            if stat.S_ISLNK(metadata.st_mode):
                raise BlueprintGraphError(
                    f"{path}: runtime input contains a symlink component"
                )
            noun = "directory" if directory else "regular file"
            raise BlueprintGraphError(f"{path}: runtime input must be a {noun}")
        binding = RuntimeFileBinding(path_absolute, current_fd, metadata.st_mode)
        current_fd = -1
        return binding
    except BlueprintGraphError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            detail = "contains a symlink component"
        elif isinstance(exc, FileNotFoundError):
            detail = "does not exist"
        elif isinstance(exc, NotADirectoryError):
            detail = "has a path component that is a symlink or is not a directory"
        else:
            detail = f"cannot be opened safely: {exc.strerror or exc}"
        raise BlueprintGraphError(f"{path}: runtime input {detail}") from exc
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def open_runtime_file(
    path: Path,
    owner_root: Path,
    repo_root: Path,
    *,
    executable: bool = False,
) -> RuntimeFileBinding:
    """Open a contained regular file without following any path symlink."""

    binding = _open_runtime_descriptor(
        path,
        owner_root,
        repo_root,
        path_only=executable,
    )
    if executable:
        try:
            effective = binding.is_effectively_executable()
        except BlueprintGraphError:
            binding.close()
            raise
        if not effective:
            binding.close()
            raise BlueprintGraphError(f"{path}: runtime input is not executable")
    return binding


def open_runtime_python_package(
    package_root: Path,
    owner_root: Path,
    repo_root: Path,
) -> tuple[RuntimeFileBinding, ...]:
    """Open every Python source in a package tree through retained directories."""

    package_root = Path(os.path.abspath(package_root))
    root_binding = _open_runtime_descriptor(
        package_root,
        owner_root,
        repo_root,
        directory=True,
    )
    bindings: list[RuntimeFileBinding] = []
    directory_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )

    def visit(directory_fd: int, relative_dir: Path) -> None:
        for name in sorted(os.listdir(directory_fd)):
            relative = relative_dir / name
            child_path = package_root / relative
            try:
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise BlueprintGraphError(
                        f"{child_path}: Python package contains a symlink component"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                    try:
                        visit(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif name.endswith(".py"):
                    child_fd = os.open(name, file_flags, dir_fd=directory_fd)
                    child_metadata = os.fstat(child_fd)
                    if not stat.S_ISREG(child_metadata.st_mode):
                        os.close(child_fd)
                        raise BlueprintGraphError(
                            f"{child_path}: Python package source must be a regular file"
                        )
                    bindings.append(
                        RuntimeFileBinding(child_path, child_fd, child_metadata.st_mode)
                    )
            except BlueprintGraphError:
                raise
            except OSError as exc:
                raise BlueprintGraphError(
                    f"{child_path}: cannot snapshot Python package safely: {exc}"
                ) from exc

    try:
        visit(root_binding.fd, Path())
        return tuple(bindings)
    except Exception:
        for binding in bindings:
            binding.close()
        raise
    finally:
        root_binding.close()


def _load_mapping(path: Path, owner_root: Path, repo_root: Path) -> dict[str, Any]:
    binding: RuntimeFileBinding | None = None
    try:
        binding = open_runtime_file(path, owner_root, repo_root)
        value = yaml.safe_load(binding.read_bytes().decode("utf-8")) or {}
    except BlueprintGraphError:
        raise
    except (UnicodeError, yaml.YAMLError) as exc:
        raise BlueprintGraphError(f"{path}: cannot load blueprint YAML: {exc}") from exc
    finally:
        if binding is not None:
            binding.close()
    if not isinstance(value, dict):
        raise BlueprintGraphError(f"{path}: blueprint top level must be a mapping")
    return value


def _positive_version(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BlueprintGraphError(f"{context}: version must be a positive integer")
    return value


def _resolve_locator(
    skill_root: Path,
    locator: object,
    context: str,
    repo_root: Path,
) -> Path:
    if not isinstance(locator, dict):
        raise BlueprintGraphError(f"{context}: blueprint locator must be a mapping")
    base = locator.get("base")
    raw_path = locator.get("path")
    if base not in {"skill-root", "repository-root"}:
        raise BlueprintGraphError(f"{context}: unsupported blueprint locator base {base!r}")
    if not isinstance(raw_path, str) or not raw_path:
        raise BlueprintGraphError(f"{context}: blueprint locator path must be non-empty")
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise BlueprintGraphError(
            f"{context}: locator path must be relative without parent traversal"
        )
    root = skill_root if base == "skill-root" else repo_root
    candidate = root / relative_path
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{context}: locator must resolve under {base}"
        ) from exc
    return candidate


def _gateway_path(skill_root: Path, declaration: dict[str, Any]) -> Path | None:
    gateway = declaration_gateway(declaration)
    if gateway is not None:
        path = gateway.get("path")
        if isinstance(path, str) and path:
            return skill_root / path

    file_path = declaration.get("file")
    if isinstance(file_path, str) and file_path:
        return skill_root / file_path

    invocation = declaration.get("invocation")
    if isinstance(invocation, dict):
        entrypoint = invocation.get("entrypoint")
        if isinstance(entrypoint, str) and entrypoint:
            return skill_root / entrypoint.split(":", 1)[0]
    return None


def _owner_root_for_sidecar(path: Path, repo_root: Path) -> Path:
    blueprint = Path(os.path.abspath(path))
    references = Path(os.path.abspath(repo_root / "references"))
    if blueprint.is_relative_to(references):
        return repo_root
    skills_root = Path(os.path.abspath(repo_root / "skills"))
    try:
        relative = blueprint.relative_to(skills_root)
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{path}: canonical sidecar must be under skills/ or references/"
        ) from exc
    if len(relative.parts) < 2:
        raise BlueprintGraphError(
            f"{path}: canonical sidecar must be inside a skill directory"
        )
    return skills_root / relative.parts[0]


def _validate_node_owner_namespace(node: BlueprintNode, repo_root: Path) -> None:
    if node.blueprint_type == "skill":
        return
    namespace = {
        "llm-interface": "llm",
        "machine-interface": "machine",
        "behavior-source": "source",
    }[node.blueprint_type]
    expected_prefix = f"{node_owner_namespace(node, repo_root)}.{namespace}."
    if not node.node_id.startswith(expected_prefix):
        raise BlueprintGraphError(
            f"{node.blueprint_path}: {node.blueprint_type} id must use "
            f"`.{namespace}.` namespace in `{expected_prefix}` owner namespace; "
            f"got {node.node_id!r}"
        )


def _reject_duplicate_authored_edges(
    root: BlueprintNode,
    edges: list[BlueprintEdge],
) -> None:
    seen: set[tuple[str, str, str, int, str | None]] = set()
    for edge in edges:
        key = edge_key(edge)
        if key in seen:
            raise BlueprintGraphError(
                f"{root.blueprint_path}: duplicate authored relationship "
                f"{edge.relation} {edge.source_id} -> {edge.target_id}"
            )
        seen.add(key)


def _legacy_graph(skill_root: Path, blueprint_path: Path, declaration: dict[str, Any]) -> SkillBlueprintGraph:
    skill_id = skill_root.name
    interfaces = declaration.get("interfaces")
    if not isinstance(interfaces, dict):
        interfaces = {}
    llm = interfaces.get("llm")
    default = llm.get("default") if isinstance(llm, dict) else None
    root_version = default.get("version", 1) if isinstance(default, dict) else 1
    if not isinstance(root_version, int) or isinstance(root_version, bool) or root_version < 1:
        root_version = 1

    root = BlueprintNode(
        node_id=skill_id,
        node_type="skill",
        version=root_version,
        skill_root=skill_root,
        blueprint_path=blueprint_path,
        gateway_path=None,
        declaration=declaration,
    )
    nodes = {skill_id: root}
    edges: list[BlueprintEdge] = []

    for namespace, blueprint_type in (("machine", "machine-interface"), ("llm", "llm-interface")):
        specifications = interfaces.get(namespace)
        if not isinstance(specifications, dict):
            continue
        for local_name in sorted(specifications):
            specification = specifications[local_name]
            if not isinstance(specification, dict):
                continue
            node_id = f"{skill_id}.{namespace}.{local_name}"
            version = _positive_version(specification.get("version"), node_id)
            node = BlueprintNode(
                node_id=node_id,
                node_type=blueprint_type,
                version=version,
                skill_root=skill_root,
                blueprint_path=blueprint_path,
                gateway_path=_gateway_path(skill_root, specification),
                declaration=specification,
                virtual=True,
            )
            nodes[node_id] = node
            edges.append(BlueprintEdge("declares-interface", skill_id, node_id, version))

    for node in list(nodes.values()):
        if not node.virtual:
            continue
        raw_uses = node.declaration.get("uses_interfaces", [])
        if not isinstance(raw_uses, list):
            continue
        for index, entry in enumerate(raw_uses):
            if not isinstance(entry, dict):
                continue
            target_id = entry.get("interface")
            if not isinstance(target_id, str) or not target_id:
                continue
            version = _positive_version(
                entry.get("version"),
                f"{blueprint_path}:{node.node_id}.uses_interfaces[{index}]",
            )
            edges.append(
                BlueprintEdge("uses-interface", node.node_id, target_id, version)
            )

    _reject_duplicate_authored_edges(root, edges)
    return SkillBlueprintGraph(skill_root, root, nodes, tuple(edges))


def _typed_graph(
    skill_root: Path,
    blueprint_path: Path,
    declaration: dict[str, Any],
    schema_root: Path | None = None,
    selected_interface_ids: frozenset[str] | None = None,
) -> SkillBlueprintGraph:
    validators: dict[str, jsonschema.Draft7Validator] = {}
    if schema_root is not None:
        root_errors = _typed_declaration_schema_errors(
            blueprint_path,
            declaration,
            schema_root,
            validators,
            expected_blueprint_type="skill",
        )
        if root_errors:
            raise root_errors[0]
    skill_id = declaration.get("id")
    if not isinstance(skill_id, str) or not skill_id:
        raise BlueprintGraphError(f"{blueprint_path}: typed skill blueprint requires a non-empty id")
    if declaration_node_type(declaration) != "skill":
        raise BlueprintGraphError(f"{blueprint_path}: canonical root node type must be skill")
    if skill_id != skill_root.name:
        raise BlueprintGraphError(
            f"{blueprint_path}: skill id {skill_id!r} must match directory name {skill_root.name!r}"
        )

    inline_default = declaration.get("default_interface")
    inline_default_id = f"{skill_id}.llm.default"
    if inline_default is not None and not isinstance(inline_default, dict):
        raise BlueprintGraphError(f"{blueprint_path}: default_interface must be a mapping")
    raw_interfaces = declaration.get("interfaces")
    if not isinstance(raw_interfaces, list):
        raise BlueprintGraphError(f"{blueprint_path}: typed interfaces must be a list")
    sidecar_default = any(
        isinstance(entry, dict) and entry.get("interface") == inline_default_id
        for entry in raw_interfaces
    )
    if inline_default is not None and sidecar_default:
        raise BlueprintGraphError(
            f"{blueprint_path}: define exactly one default interface representation"
        )
    if inline_default is None and not sidecar_default and schema_root is not None:
        raise BlueprintGraphError(
            f"{blueprint_path}: define exactly one default interface representation"
        )
    root_version = (
        _positive_version(inline_default.get("version"), f"{blueprint_path}:default_interface")
        if inline_default is not None
        else 1
    )
    root = BlueprintNode(
        node_id=skill_id,
        node_type="skill",
        version=root_version,
        skill_root=skill_root,
        blueprint_path=blueprint_path,
        gateway_path=(
            _gateway_path(skill_root, declaration)
            if declaration.get("schema_version") == 3
            else skill_root / "SKILL.md" if inline_default is not None else None
        ),
        declaration=declaration,
    )
    nodes: dict[str, BlueprintNode] = {skill_id: root}
    paths_by_id: dict[str, Path] = {skill_id: blueprint_path}
    edges: list[BlueprintEdge] = []
    repo_root = skill_root.parent.parent

    def load_node(
        path: Path,
        expected_id: str,
        expected_version: int,
    ) -> BlueprintNode:
        existing_path = paths_by_id.get(expected_id)
        if existing_path is not None:
            if existing_path != path:
                raise BlueprintGraphError(
                    f"duplicate node id {expected_id!r}: {existing_path} and {path}"
                )
            return nodes[expected_id]
        node_skill_root = _owner_root_for_sidecar(path, repo_root)
        try:
            node_declaration = _load_mapping(path, node_skill_root, repo_root)
        except BlueprintGraphError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                raise BlueprintGraphError(
                    f"{path}: missing subordinate blueprint for {expected_id}"
                ) from exc
            raise
        if schema_root is not None:
            expected_type = _blueprint_type_for_node_id(expected_id)
            node_errors = _typed_declaration_schema_errors(
                path,
                node_declaration,
                schema_root,
                validators,
                expected_blueprint_type=expected_type,
            )
            if node_errors:
                raise node_errors[0]
        node_id = node_declaration.get("id")
        if node_id != expected_id:
            raise BlueprintGraphError(
                f"{path}: node id {node_id!r} does not match edge target {expected_id!r}"
            )
        version = _positive_version(node_declaration.get("version"), str(path))
        if version != expected_version:
            raise BlueprintGraphError(
                f"{path}: node version {version} does not match pinned version {expected_version}"
            )
        blueprint_type = declaration_node_type(node_declaration)
        if blueprint_type not in {"llm-interface", "machine-interface", "behavior-source"}:
            raise BlueprintGraphError(f"{path}: unsupported node type {blueprint_type!r}")

        node = BlueprintNode(
            node_id=node_id,
            node_type=blueprint_type,
            version=version,
            skill_root=node_skill_root,
            blueprint_path=path,
            gateway_path=_gateway_path(node_skill_root, node_declaration),
            declaration=node_declaration,
        )
        _validate_node_owner_namespace(node, repo_root)
        paths_by_id[node_id] = path
        nodes[node_id] = node

        for relation, field, id_field in (
            ("uses-interface", "uses_interfaces", "interface"),
            ("uses-behavior-source", "behavior_sources", "source"),
            ("uses-behavior-source", "uses_behavior_sources", "source"),
        ):
            raw_entries = node_declaration.get(field, [])
            if not isinstance(raw_entries, list):
                continue
            for index, entry in enumerate(raw_entries):
                if not isinstance(entry, dict):
                    continue
                target_id = entry.get(id_field)
                if not isinstance(target_id, str) or not target_id:
                    continue
                target_version = _positive_version(
                    entry.get("version"), f"{path}:{field}[{index}]"
                )
                target_path: Path | None = None
                if "blueprint" in entry:
                    target_path = _resolve_locator(
                        node_skill_root,
                        entry["blueprint"],
                        f"{path}:{field}[{index}]",
                        repo_root,
                    )
                edges.append(
                    BlueprintEdge(
                        relation,
                        node_id,
                        target_id,
                        target_version,
                        target_path,
                    )
                )
                if target_path is not None:
                    load_node(target_path, target_id, target_version)
        return node

    if (
        inline_default is not None
        and (selected_interface_ids is None or inline_default_id in selected_interface_ids)
    ):
        version = _positive_version(
            inline_default.get("version"), f"{blueprint_path}:default_interface"
        )
        if declaration.get("schema_version") == 3:
            embedded_declaration = {
                "schema_version": 3,
                "node_type": "llm-interface",
                "id": inline_default_id,
                "gateway": deepcopy(declaration["gateway"]),
                "content": deepcopy(declaration["content"]),
                **deepcopy(inline_default),
            }
        else:
            embedded_declaration = {
                "schema_version": 2,
                "blueprint_type": "llm-interface",
                "id": inline_default_id,
                "binding": {"kind": "instruction-file", "path": "SKILL.md"},
                **deepcopy(inline_default),
            }
        embedded = BlueprintNode(
            node_id=inline_default_id,
            node_type="llm-interface",
            version=version,
            skill_root=skill_root,
            blueprint_path=blueprint_path,
            gateway_path=skill_root / "SKILL.md",
            declaration=embedded_declaration,
            embedded=True,
        )
        nodes[inline_default_id] = embedded
        paths_by_id[inline_default_id] = blueprint_path
        edges.append(
            BlueprintEdge("declares-interface", skill_id, inline_default_id, version)
        )
        for relation, field, id_field in (
            ("uses-interface", "uses_interfaces", "interface"),
            ("uses-behavior-source", "behavior_sources", "source"),
        ):
            raw_entries = embedded_declaration.get(field, [])
            if not isinstance(raw_entries, list):
                continue
            for index, entry in enumerate(raw_entries):
                if not isinstance(entry, dict):
                    continue
                target_id = entry.get(id_field)
                if not isinstance(target_id, str) or not target_id:
                    continue
                target_version = _positive_version(
                    entry.get("version"), f"{blueprint_path}:default_interface.{field}[{index}]"
                )
                target_path = None
                if "blueprint" in entry:
                    target_path = _resolve_locator(
                        skill_root,
                        entry["blueprint"],
                        f"{blueprint_path}:default_interface.{field}[{index}]",
                        repo_root,
                    )
                edges.append(
                    BlueprintEdge(
                        relation,
                        inline_default_id,
                        target_id,
                        target_version,
                        target_path,
                    )
                )
                if target_path is not None:
                    load_node(target_path, target_id, target_version)
    for index, entry in enumerate(raw_interfaces):
        if not isinstance(entry, dict):
            raise BlueprintGraphError(f"{blueprint_path}:interfaces[{index}] must be a mapping")
        node_id = entry.get("interface")
        if not isinstance(node_id, str) or not node_id:
            raise BlueprintGraphError(f"{blueprint_path}:interfaces[{index}] requires interface")
        if selected_interface_ids is not None and node_id not in selected_interface_ids:
            continue
        version = _positive_version(entry.get("version"), f"{blueprint_path}:interfaces[{index}]")
        target_path = _resolve_locator(
            skill_root,
            entry.get("blueprint"),
            f"{blueprint_path}:interfaces[{index}]",
            repo_root,
        )
        edges.append(BlueprintEdge("declares-interface", skill_id, node_id, version, target_path))
        load_node(target_path, node_id, version)

    _reject_duplicate_authored_edges(root, edges)
    _reject_cycles(nodes, edges)
    graph = SkillBlueprintGraph(skill_root, root, nodes, tuple(edges))
    _validate_typed_layout(graph)
    return graph


def _root_binding_locator_entries(
    graph: SkillBlueprintGraph,
    binding_path: Path,
) -> dict[str, Path]:
    raw_interfaces = graph.root.declaration.get("interfaces", [])
    if not isinstance(raw_interfaces, list):
        return {}
    repo_root = graph.skill_root.parent.parent
    matches: dict[str, Path] = {}
    for index, entry in enumerate(raw_interfaces):
        if not isinstance(entry, dict):
            continue
        node_id = entry.get("interface")
        if not isinstance(node_id, str) or not node_id:
            continue
        sidecar_path = _resolve_locator(
            graph.skill_root,
            entry.get("blueprint"),
            f"{graph.root.blueprint_path}:interfaces[{index}]",
            repo_root,
        )
        local_name = node_id.rsplit(".", 1)[-1]
        owner_qualified_name = node_id.removeprefix(f"{graph.root.node_id}.")
        candidate_paths = {
            binding_path.with_name(f".{binding_path.name}.blueprint.yaml"),
            binding_path.with_name(
                f".{binding_path.name}.{local_name}.blueprint.yaml"
            ),
            binding_path.with_name(
                f".{binding_path.name}.{owner_qualified_name}.blueprint.yaml"
            ),
        }
        if sidecar_path in candidate_paths:
            matches[node_id] = sidecar_path
    return matches


def _expected_sidecar_path(
    graph: SkillBlueprintGraph,
    binding_path: Path,
    node_id: str,
    binding_node_ids: set[str],
) -> tuple[Path, str]:
    if len(binding_node_ids) == 1:
        return (
            binding_path.with_name(f".{binding_path.name}.blueprint.yaml"),
            "sidecar name must match its bound file",
        )
    local_name = node_id.rsplit(".", 1)[-1]
    local_names = [candidate.rsplit(".", 1)[-1] for candidate in binding_node_ids]
    qualifier = local_name
    if local_names.count(local_name) > 1:
        qualifier = node_id.removeprefix(f"{graph.root.node_id}.")
    return (
        binding_path.with_name(
            f".{binding_path.name}.{qualifier}.blueprint.yaml"
        ),
        "shared binding requires qualified sidecar",
    )


def _validate_typed_layout(graph: SkillBlueprintGraph) -> None:
    version_two_bound_nodes: dict[Path, list[BlueprintNode]] = {}
    version_three_gateways: dict[Path, BlueprintNode] = {}
    version_three_content_owners: dict[Path, BlueprintNode] = {}
    repo_root = graph.skill_root.parent.parent
    for node in graph.nodes.values():
        schema_version = node.declaration.get("schema_version")
        if node.node_type == "skill" and schema_version != 3:
            continue
        gateway_path = node.gateway_path
        gateway = declaration_gateway(node.declaration)
        gateway_kind = (
            gateway.get("kind") if isinstance(gateway, dict) else None
        )
        raw_gateway_path = (
            gateway.get("path") if isinstance(gateway, dict) else None
        )
        if isinstance(raw_gateway_path, str):
            relative_gateway = Path(raw_gateway_path)
            if relative_gateway.is_absolute() or ".." in relative_gateway.parts:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: gateway path must be relative without parent traversal"
                )
        if (
            node.node_id == f"{graph.root.node_id}.llm.default"
            and gateway_path != graph.skill_root / "SKILL.md"
        ):
            raise BlueprintGraphError(
                f"{node.blueprint_path}: default LLM interface gateway must be SKILL.md"
            )
        if gateway_path is None:
            raise BlueprintGraphError(
                f"{node.blueprint_path}: gateway must be an existing regular file"
            )
        if (
            gateway_path.name.endswith(".blueprint.yaml")
            or gateway_path.name.endswith(".health.json")
            or "pooled-blueprint-review" in gateway_path.name
        ):
            raise BlueprintGraphError(
                f"{node.blueprint_path}: gateway cannot be a blueprint or health artifact"
            )
        if gateway_kind in {"python-entrypoint", "command-file"}:
            directory = "_rtx" if gateway_kind == "python-entrypoint" else "_cx"
            try:
                Path(os.path.abspath(gateway_path)).relative_to(
                    Path(os.path.abspath(node.skill_root / directory))
                )
            except ValueError as exc:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: {gateway_kind} gateway must be under {directory}"
                ) from exc
            try:
                gateway_path.resolve().relative_to(
                    (node.skill_root / directory).resolve()
                )
            except ValueError as exc:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: {gateway_kind} gateway must resolve under {directory}"
                ) from exc
        try:
            gateway_handle = open_runtime_file(
                gateway_path,
                node.skill_root,
                repo_root,
                executable=gateway_kind == "command-file",
            )
        except BlueprintGraphError as exc:
            if gateway_kind == "command-file" and "not executable" in str(exc):
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: command file must be executable: {raw_gateway_path}"
                ) from exc
            if "symlink" in str(exc):
                raise
            raise BlueprintGraphError(
                f"{node.blueprint_path}: gateway must be an existing regular file: {exc}"
            ) from exc
        gateway_handle.close()
        if node.embedded:
            continue

        if schema_version == 3:
            canonical_gateway = Path(os.path.abspath(gateway_path))
            previous_entry_owner = version_three_gateways.get(canonical_gateway)
            if previous_entry_owner is not None:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: version 3 gateway {gateway_path} "
                    f"is shared by {previous_entry_owner.node_id} and {node.node_id}"
                )
            version_three_gateways[canonical_gateway] = node
            for content_path in resolved_node_content_paths(node, repo_root):
                canonical_content = Path(os.path.abspath(content_path))
                previous_owner = version_three_content_owners.get(canonical_content)
                if previous_owner is not None:
                    raise BlueprintGraphError(
                        f"{node.blueprint_path}: content file {content_path} is owned by both "
                        f"{previous_owner.node_id} and {node.node_id}"
                    )
                version_three_content_owners[canonical_content] = node
            if node.node_type != "skill":
                expected = gateway_path.with_name(
                    f".{gateway_path.name}.blueprint.yaml"
                )
                if node.blueprint_path != expected:
                    raise BlueprintGraphError(
                        f"{node.blueprint_path}: sidecar name must match its gateway; "
                        f"expected {expected}"
                    )
        else:
            version_two_bound_nodes.setdefault(gateway_path, []).append(node)

    for binding_path, nodes in version_two_bound_nodes.items():
        locator_entries = _root_binding_locator_entries(graph, binding_path)
        binding_node_ids = {node.node_id for node in nodes} | set(locator_entries)
        authored_paths = {
            node.node_id: node.blueprint_path
            for node in nodes
        }
        authored_paths.update(locator_entries)
        for node_id, authored_path in authored_paths.items():
            expected, message = _expected_sidecar_path(
                graph,
                binding_path,
                node_id,
                binding_node_ids,
            )
            if authored_path != expected:
                raise BlueprintGraphError(
                    f"{authored_path}: {message}; expected {expected}"
                )


def _reject_cycles(nodes: dict[str, BlueprintNode], edges: list[BlueprintEdge]) -> None:
    children: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        if edge.target_id in nodes:
            children[edge.source_id].append(edge.target_id)
    for values in children.values():
        values.sort()

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise BlueprintGraphError(f"blueprint graph cycle includes {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child_id in children[node_id]:
            visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(nodes):
        visit(node_id)


def load_skill_blueprint_graph(
    skill_root: Path,
    schema_root: Path | None = None,
    *,
    selected_interface_ids: frozenset[str] | None = None,
) -> SkillBlueprintGraph:
    """Load one skill's canonical root and all reachable local sidecars."""

    skill_root = Path(skill_root)
    blueprint_path = skill_root / "blueprint.yaml"
    declaration = _load_mapping(blueprint_path, skill_root, skill_root.parent.parent)
    if _is_typed_declaration(declaration):
        return _typed_graph(
            skill_root,
            blueprint_path,
            declaration,
            schema_root,
            selected_interface_ids,
        )
    return _legacy_graph(skill_root, blueprint_path, declaration)


def _blueprint_type_for_node_id(node_id: str) -> str:
    for marker, blueprint_type in (
        (".llm.", "llm-interface"),
        (".machine.", "machine-interface"),
        (".source.", "behavior-source"),
    ):
        if marker in node_id:
            return blueprint_type
    raise BlueprintGraphError(f"cannot determine concrete blueprint type for {node_id!r}")


def _json_error_path(error: jsonschema.ValidationError) -> str:
    parts = list(error.absolute_path)
    if error.validator == "required":
        match = re.match(r"'([^']+)' is a required property", error.message)
        if match is not None:
            parts.append(match.group(1))
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _validate_concrete_node_schemas(
    graph: SkillBlueprintGraph,
    schema_root: Path,
) -> None:
    validators: dict[str, jsonschema.Draft7Validator] = {}
    for node in sorted(graph.nodes.values(), key=lambda item: str(item.blueprint_path)):
        if node.virtual or node.embedded:
            continue
        errors = _typed_declaration_schema_errors(
            node.blueprint_path,
            node.declaration,
            schema_root,
            validators,
        )
        if errors:
            raise errors[0]


def _typed_declaration_schema_errors(
    blueprint_path: Path,
    declaration: dict[str, Any],
    schema_root: Path,
    validators: dict[str, jsonschema.Draft7Validator],
    *,
    expected_blueprint_type: str | None = None,
) -> tuple[BlueprintSchemaError, ...]:
    node_type = expected_blueprint_type or declaration_node_type(declaration)
    schema_version = declaration.get("schema_version")
    selected_version = schema_version
    if selected_version not in {2, 3}:
        selected_version = 3 if "node_type" in declaration else 2
    try:
        schema_name = _TYPED_SCHEMA_FILES[(selected_version, node_type)]
    except KeyError as exc:
        raise BlueprintGraphError(
            f"{blueprint_path}: unsupported typed node type {node_type!r} "
            f"for schema version {schema_version!r}"
        ) from exc
    validator = validators.get(schema_name)
    if validator is None:
        validator = _load_schema_validator(schema_root / schema_name)
        validators[schema_name] = validator
    try:
        errors = sorted(
            validator.iter_errors(declaration),
            key=lambda error: (_json_error_path(error), error.message),
        )
    except Exception as exc:
        schema_path = Path(schema_root) / schema_name
        raise BlueprintSchemaError(
            blueprint_path,
            "$",
            f"cannot resolve concrete schema {schema_path}: {exc}",
        ) from exc
    return tuple(
        BlueprintSchemaError(
            blueprint_path,
            _json_error_path(error),
            error.message,
        )
        for error in errors
    )


def _load_schema_validator(schema_path: Path) -> jsonschema.Draft7Validator:
    """Read a concrete schema bundle through one no-follow directory handle."""

    schema_path = Path(os.path.abspath(schema_path))
    schema_root = (
        schema_path.parent.parent
        if schema_path.parent.name == "v2"
        else schema_path.parent
    )
    repo_root = schema_root.parent.parent
    directories: list[RuntimeFileBinding] = []
    try:
        documents: dict[str, dict[str, Any]] = {}
        file_flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
        for relative_directory in (Path("."), Path("v2")):
            directory_path = schema_root / relative_directory
            if relative_directory != Path(".") and not directory_path.exists():
                continue
            directory = _open_runtime_descriptor(
                directory_path,
                repo_root,
                repo_root,
                directory=True,
            )
            directories.append(directory)
            for name in sorted(
                name
                for name in os.listdir(directory.fd)
                if name.endswith(".schema.json")
            ):
                relative_name = (
                    name
                    if relative_directory == Path(".")
                    else (relative_directory / name).as_posix()
                )
                child_path = schema_root / relative_name
                child_fd = -1
                try:
                    child_fd = os.open(name, file_flags, dir_fd=directory.fd)
                    metadata = os.fstat(child_fd)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise OSError(f"schema is not a regular file: {child_path}")
                    child = RuntimeFileBinding(child_path, child_fd, metadata.st_mode)
                    child_fd = -1
                    try:
                        document = json.loads(child.read_bytes().decode("utf-8"))
                    finally:
                        child.close()
                    if not isinstance(document, dict):
                        raise TypeError("schema top level must be a mapping")
                    documents[relative_name] = document
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
                    raise BlueprintSchemaError(
                        child_path,
                        "$",
                        f"cannot load schema: {exc}",
                    ) from exc
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
        selected_name = schema_path.relative_to(schema_root).as_posix()
        try:
            selected = documents[selected_name]
        except KeyError as exc:
            raise BlueprintSchemaError(
                schema_path,
                "$",
                "cannot load schema: file does not exist",
            ) from exc
        store: dict[str, dict[str, Any]] = {}
        for name, document in documents.items():
            store[name] = document
            store[(schema_root / name).as_uri()] = document
            schema_id = document.get("$id")
            if isinstance(schema_id, str):
                store[schema_id] = document
        resolver = jsonschema.RefResolver(
            base_uri=schema_path.as_uri(),
            referrer=selected,
            store=store,
        )
        return jsonschema.Draft7Validator(selected, resolver=resolver)
    except BlueprintSchemaError:
        raise
    except (BlueprintGraphError, OSError) as exc:
        raise BlueprintSchemaError(
            schema_path,
            "$",
            f"cannot load schema bundle: {exc}",
        ) from exc
    finally:
        for directory in directories:
            directory.close()
def typed_declaration_schema_errors(
    blueprint_path: Path,
    declaration: dict[str, Any],
    schema_root: Path,
) -> tuple[BlueprintSchemaError, ...]:
    """Return concrete-schema errors for one authored typed declaration."""

    return _typed_declaration_schema_errors(
        blueprint_path,
        declaration,
        Path(schema_root),
        {},
    )


def _is_forbidden_content_artifact(path: Path) -> bool:
    name = path.name
    return (
        name == "blueprint.yaml"
        or name.endswith(".blueprint.yaml")
        or name.endswith(".health.json")
        or name.endswith(".audit.json")
        or "pooled-blueprint-review" in name
    )


def _regular_files_beneath(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not (directory_path / name).is_symlink()
        )
        for name in sorted(file_names):
            path = directory_path / name
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                files.append(path)
    return tuple(sorted(files))


def resolved_node_content_paths(
    node: BlueprintNode,
    repo_root: Path,
) -> tuple[Path, ...]:
    """Resolve the regular files exclusively owned by one authored node."""

    repo_root = Path(os.path.abspath(repo_root))
    owner_root = Path(os.path.abspath(node.skill_root))
    try:
        owner_root.relative_to(repo_root)
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content ownership root must be inside the repository"
        ) from exc

    if node.declaration.get("schema_version") != 3:
        paths: set[Path] = set()
        if node.gateway_path is not None:
            paths.add(node.gateway_path)
        declared_inputs_value = node.declaration.get("local_hash_inputs", [])
        if not isinstance(declared_inputs_value, list):
            raise BlueprintGraphError(
                f"{node.blueprint_path}: local_hash_inputs must be a list"
            )
        declared_inputs = list(declared_inputs_value)
        default_interface = node.declaration.get("default_interface")
        if node.node_type == "skill" and isinstance(default_interface, dict):
            inline_inputs = default_interface.get("local_hash_inputs", [])
            if not isinstance(inline_inputs, list):
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: default_interface.local_hash_inputs must be a list"
                )
            declared_inputs.extend(inline_inputs)
        for declared in declared_inputs:
            if not isinstance(declared, str) or not declared:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: local_hash_inputs entries must be non-empty strings"
                )
            relative = Path(declared)
            if relative.is_absolute() or ".." in relative.parts:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: local_hash_input {declared!r} must be "
                    "owner-relative without parent traversal"
                )
            paths.add(owner_root / relative)
        return tuple(sorted(paths))

    raw_patterns = node.declaration.get("content")
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content must be a non-empty list of regex patterns"
        )
    candidates = _regular_files_beneath(owner_root)
    relative_candidates = {
        path: path.relative_to(owner_root).as_posix() for path in candidates
    }
    matched_paths: set[Path] = set()
    for raw_pattern in raw_patterns:
        if not isinstance(raw_pattern, str) or not raw_pattern:
            raise BlueprintGraphError(
                f"{node.blueprint_path}: content patterns must be non-empty strings"
            )
        try:
            pattern = re.compile(raw_pattern)
        except re.error as exc:
            raise BlueprintGraphError(
                f"{node.blueprint_path}: invalid content regex {raw_pattern!r}: {exc}"
            ) from exc
        matches = {
            path
            for path, relative in relative_candidates.items()
            if pattern.fullmatch(relative) is not None
        }
        if not matches:
            raise BlueprintGraphError(
                f"{node.blueprint_path}: content pattern {raw_pattern!r} matched no files"
            )
        matched_paths.update(matches)

    for path in sorted(matched_paths):
        if _is_forbidden_content_artifact(path):
            raise BlueprintGraphError(
                f"{node.blueprint_path}: content cannot include a blueprint or health artifact: {path}"
            )
    if node.gateway_path is None or node.gateway_path not in matched_paths:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: gateway must be included in content"
        )
    return tuple(sorted(matched_paths))


def authored_node_input_paths(
    node: BlueprintNode,
    repo_root: Path | None = None,
) -> tuple[Path, ...]:
    """Return the authored blueprint and resolved content files for one node."""

    if repo_root is None:
        owner_root = Path(os.path.abspath(node.skill_root))
        blueprint_path = Path(os.path.abspath(node.blueprint_path))
        if blueprint_path.is_relative_to(owner_root / "references"):
            repo_root = owner_root
        elif owner_root.parent.name == "skills":
            repo_root = owner_root.parent.parent
        else:
            raise BlueprintGraphError(
                f"{node.blueprint_path}: cannot infer repository root from node ownership"
            )
    return tuple(
        sorted({node.blueprint_path, *resolved_node_content_paths(node, repo_root)})
    )


def validate_runtime_file_path(
    path: Path,
    owner_root: Path,
    repo_root: Path,
) -> Path:
    """Validate one lexical runtime file through a no-follow descriptor walk."""

    binding = open_runtime_file(path, owner_root, repo_root)
    try:
        return binding.path
    finally:
        binding.close()


def _validate_runtime_files(graph: SkillBlueprintGraph) -> None:
    repo_root = graph.skill_root.parent.parent
    for node in graph.nodes.values():
        for path in authored_node_input_paths(node, repo_root):
            validate_runtime_file_path(path, node.skill_root, repo_root)
        binding = declaration_gateway(node.declaration)
        if (
            isinstance(binding, dict)
            and binding.get("kind") == "command-file"
            and node.binding_path is not None
        ):
            executable = open_runtime_file(
                node.binding_path,
                node.skill_root,
                repo_root,
                executable=True,
            )
            executable.close()


def _relationship_matrix(schema_root: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    metadata_path = schema_root / "schema-meta.json"
    binding: RuntimeFileBinding | None = None
    try:
        repo_root = Path(schema_root).parent.parent
        binding = open_runtime_file(metadata_path, repo_root, repo_root)
        metadata = json.loads(binding.read_bytes().decode("utf-8"))
        raw_matrix = metadata["x-famulus"]["relationship_matrix"]
    except (
        BlueprintGraphError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise BlueprintGraphError(
            f"{metadata_path}: cannot load relationship matrix: {exc}"
        ) from exc
    finally:
        if binding is not None:
            binding.close()
    if not isinstance(raw_matrix, dict):
        raise BlueprintGraphError(f"{metadata_path}: relationship matrix must be a mapping")
    matrix: dict[str, dict[str, tuple[str, ...]]] = {}
    for source_type, relations in raw_matrix.items():
        if not isinstance(source_type, str) or not isinstance(relations, dict):
            raise BlueprintGraphError(f"{metadata_path}: invalid relationship matrix entry")
        matrix[source_type] = {}
        for relation, target_types in relations.items():
            if not isinstance(relation, str) or not isinstance(target_types, list) or not all(
                isinstance(target_type, str) for target_type in target_types
            ):
                raise BlueprintGraphError(
                    f"{metadata_path}: invalid relationship matrix targets"
                )
            matrix[source_type][relation] = tuple(target_types)
    return matrix


def relationship_target_types(
    schema_root: Path,
    source_type: str,
    relation: str,
) -> tuple[str, ...]:
    """Return target node types allowed by schema-meta for one relationship."""

    return _relationship_matrix(Path(schema_root)).get(source_type, {}).get(relation, ())


def _node_owner_id(node: BlueprintNode, repo_root: Path) -> str:
    return node_owner_namespace(node, repo_root)


def graph_contract_errors(
    graph: SkillBlueprintGraph,
    schema_root: Path,
) -> list[str]:
    """Return matrix, identity, version, visibility, access, and cycle errors."""

    try:
        matrix = _relationship_matrix(Path(schema_root))
    except BlueprintGraphError as exc:
        return [str(exc)]
    repo_root = graph.skill_root.parent.parent
    errors: list[str] = []
    for edge in graph.edges:
        source = graph.nodes.get(edge.source_id)
        target = graph.nodes.get(edge.target_id)
        context_path = source.blueprint_path if source is not None else edge.source_id
        context = f"{context_path}: {edge.source_id} {edge.relation}"
        if source is None:
            errors.append(f"{context}: source node is unknown")
            continue
        if target is None:
            noun = "behavior source" if edge.relation == "uses-behavior-source" else "interface"
            errors.append(f"{context} targets unknown {noun} `{edge.target_id}`")
            continue
        allowed_targets = matrix.get(source.blueprint_type, {}).get(edge.relation, ())
        if target.blueprint_type not in allowed_targets:
            errors.append(
                f"{context} targets `{target.node_id}` ({target.blueprint_type}); "
                "relationship matrix forbids this source, relation, and target type"
            )
            continue
        if target.version != edge.required_version:
            errors.append(
                f"{context} pins `{target.node_id}` version {edge.required_version}, "
                f"but target version is {target.version}"
            )
        if edge.relation == "uses-interface":
            source_support = source.declaration.get("platform_support")
            target_support = target.declaration.get("platform_support")
            if isinstance(source_support, dict) and isinstance(target_support, dict):
                for platform, supported in source_support.items():
                    if (
                        supported is True
                        and target_support.get(platform) is not True
                    ):
                        errors.append(
                            f"{context} targets `{target.node_id}`, which does not support "
                            f"required platform `{platform}`"
                        )
        source_owner = _node_owner_id(source, repo_root)
        target_owner = _node_owner_id(target, repo_root)
        if edge.relation == "declares-interface" and target_owner != source.node_id:
            errors.append(f"{context} target must belong to skill `{source.node_id}`")
        if (
            edge.relation == "uses-behavior-source"
            and target_owner != "references"
            and target_owner != source_owner
        ):
            errors.append(
                f"{context} targets `{target.node_id}`; behavior source outside declaring "
                "skill or repository references"
            )
        if edge.relation != "uses-interface" or source_owner == target_owner:
            continue
        allow_all = target.declaration.get("allow_all_skills") is True
        allowed_callers = target.declaration.get("allowed_callers", [])
        if not isinstance(allowed_callers, list):
            errors.append(
                f"{target.blueprint_path}: {target.node_id}.allowed_callers: expected list"
            )
        elif not allow_all and source_owner not in allowed_callers:
            errors.append(
                f"{context} targets `{target.node_id}`, but `{source_owner}` "
                "is not allowed by target access control"
            )
    try:
        _reject_cycles(graph.nodes, list(graph.edges))
    except BlueprintGraphError as exc:
        errors.append(str(exc))
    return errors


def validate_graph_contract(graph: SkillBlueprintGraph, schema_root: Path) -> None:
    """Raise for the first shared typed graph contract violation."""

    errors = graph_contract_errors(graph, schema_root)
    if errors:
        raise BlueprintGraphError(errors[0])


def load_validated_skill_blueprint_graph(
    skill_root: Path,
    schema_root: Path,
) -> SkillBlueprintGraph:
    """Load one typed skill closure and enforce install-local runtime contracts."""

    schema_root = Path(schema_root)
    local_graph = load_skill_blueprint_graph(skill_root, schema_root)
    if not _is_typed_declaration(local_graph.root.declaration):
        return local_graph
    repo_root = local_graph.skill_root.parent.parent
    graph = load_reachable_repository_skill_graph(
        repo_root,
        local_graph.root.node_id,
        schema_root=schema_root,
    )
    _validate_concrete_node_schemas(graph, schema_root)
    _validate_runtime_files(graph)
    validate_graph_contract(graph, Path(schema_root))
    return graph


def load_repository_blueprint_graphs(repo_root: Path) -> dict[str, SkillBlueprintGraph]:
    """Load every skill root with a canonical blueprint in a repository."""

    skills_root = Path(repo_root) / "skills"
    graphs: dict[str, SkillBlueprintGraph] = {}
    if not skills_root.is_dir():
        return graphs
    for blueprint_path in sorted(skills_root.glob("*/blueprint.yaml")):
        graph = load_skill_blueprint_graph(blueprint_path.parent)
        if graph.root.node_id in graphs:
            raise BlueprintGraphError(f"duplicate skill id {graph.root.node_id!r}")
        graphs[graph.root.node_id] = graph
    return graphs


def _owner_skill_id(node_id: str) -> str | None:
    for marker in (".llm.", ".machine.", ".source."):
        owner, separator, _local_name = node_id.partition(marker)
        if separator and owner:
            return owner
    return None


def load_reachable_repository_skill_graph(
    repo_root: Path,
    root_skill_id: str,
    *,
    schema_root: Path | None = None,
) -> SkillBlueprintGraph:
    """Load only the skill graphs needed by one root's interface closure."""

    root = Path(repo_root)
    graphs: dict[str, SkillBlueprintGraph] = {}
    loading: set[str] = set()
    all_interfaces: set[str] = {root_skill_id}
    selected_interfaces: dict[str, set[str]] = {}

    def load_owner(skill_id: str, interface_id: str | None = None) -> None:
        if interface_id is not None and skill_id not in all_interfaces:
            selected_interfaces.setdefault(skill_id, set()).add(interface_id)
        selected = (
            None
            if skill_id in all_interfaces
            else frozenset(selected_interfaces.get(skill_id, set()))
        )
        existing = graphs.get(skill_id)
        if (
            existing is not None
            and (selected is None or all(node_id in existing.nodes for node_id in selected))
        ):
            return
        if skill_id in loading:
            return
        loading.add(skill_id)
        try:
            graph = load_skill_blueprint_graph(
                root / "skills" / skill_id,
                schema_root,
                selected_interface_ids=selected,
            )
            if graph.root.node_id != skill_id:
                raise BlueprintGraphError(
                    f"skill directory {skill_id!r} declares root id {graph.root.node_id!r}"
                )
            graphs[skill_id] = graph
        finally:
            loading.remove(skill_id)

    load_owner(root_skill_id)

    def loaded_node(node_id: str) -> BlueprintNode | None:
        for graph in graphs.values():
            node = graph.nodes.get(node_id)
            if node is not None:
                return node
        return None

    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        node = loaded_node(node_id)
        if node is None:
            return
        visited.add(node_id)
        edges = sorted(
            (
                edge
                for graph in graphs.values()
                for edge in graph.edges
                if edge.source_id == node_id
            ),
            key=edge_key,
        )
        for edge in edges:
            if edge.relation == "uses-interface" and loaded_node(edge.target_id) is None:
                owner = _owner_skill_id(edge.target_id)
                if owner is None:
                    raise BlueprintGraphError(
                        f"{edge.source_id}: cannot determine owner of {edge.target_id!r}"
                    )
                load_owner(owner, edge.target_id)
            if loaded_node(edge.target_id) is not None:
                visit(edge.target_id)

    visit(root_skill_id)
    return resolve_repository_skill_graph(graphs, root_skill_id)


def resolve_repository_skill_graph(
    graphs: dict[str, SkillBlueprintGraph],
    root_skill_id: str | set[str],
) -> SkillBlueprintGraph:
    """Resolve one root's reachable local and cross-skill downstream nodes."""

    root_skill_ids = (root_skill_id,) if isinstance(root_skill_id, str) else tuple(sorted(root_skill_id))
    if not root_skill_ids:
        raise BlueprintGraphError("repository graph resolution requires at least one root skill id")
    try:
        root_graph = graphs[root_skill_ids[0]]
    except KeyError as exc:
        raise BlueprintGraphError(f"unknown root skill id {root_skill_ids[0]!r}") from exc

    global_nodes: dict[str, BlueprintNode] = {}
    owner_root_ids: dict[Path, str] = {}
    edges_by_source: dict[str, dict[tuple[str, str, str, int, str | None], BlueprintEdge]] = {}
    for graph in graphs.values():
        owner_root_ids[graph.skill_root] = graph.root.node_id
        for node_id, node in graph.nodes.items():
            existing = global_nodes.get(node_id)
            if existing is not None and existing.blueprint_path != node.blueprint_path:
                raise BlueprintGraphError(
                    f"duplicate node id {node_id!r}: {existing.blueprint_path} and "
                    f"{node.blueprint_path}"
                )
            global_nodes[node_id] = node
        for edge in graph.edges:
            edges_by_source.setdefault(edge.source_id, {}).setdefault(edge_key(edge), edge)

    reachable_nodes: dict[str, BlueprintNode] = {}
    reachable_edges: dict[tuple[str, str, str, int, str | None], BlueprintEdge] = {}

    def visit(node_id: str) -> None:
        if node_id in reachable_nodes:
            return
        try:
            node = global_nodes[node_id]
        except KeyError as exc:
            raise BlueprintGraphError(f"unresolved downstream node {node_id!r}") from exc
        reachable_nodes[node_id] = node
        if node.embedded:
            owner_root_id = owner_root_ids.get(node.skill_root)
            if owner_root_id is None:
                raise BlueprintGraphError(
                    f"{node.node_id}: embedded interface has no owning skill root"
                )
            visit(owner_root_id)
        for edge in sorted(
            edges_by_source.get(node_id, {}).values(),
            key=edge_key,
        ):
            target = global_nodes.get(edge.target_id)
            if target is None:
                raise BlueprintGraphError(
                    f"{edge.source_id}: unresolved downstream node {edge.target_id!r}"
                )
            if target.version != edge.required_version:
                raise BlueprintGraphError(
                    f"{edge.source_id}: pins {edge.target_id} version "
                    f"{edge.required_version}, but target version is {target.version}"
                )
            reachable_edges.setdefault(edge_key(edge), edge)
            visit(edge.target_id)

    for skill_id in root_skill_ids:
        visit(skill_id)
    _reject_cycles(reachable_nodes, list(reachable_edges.values()))
    resolved = SkillBlueprintGraph(
        root_graph.skill_root,
        root_graph.root,
        reachable_nodes,
        tuple(reachable_edges.values()),
        root_skill_ids,
    )
    repo_root = root_graph.skill_root.parent.parent
    content_owners: dict[Path, BlueprintNode] = {}
    gateway_owners: dict[Path, BlueprintNode] = {}
    for node in sorted(resolved.nodes.values(), key=lambda item: item.node_id):
        if node.embedded or node.declaration.get("schema_version") != 3:
            continue
        if node.gateway_path is not None:
            gateway = Path(os.path.abspath(node.gateway_path))
            previous_entry_owner = gateway_owners.get(gateway)
            if previous_entry_owner is not None:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: version 3 gateway {node.gateway_path} "
                    f"is shared by {previous_entry_owner.node_id} and {node.node_id}"
                )
            gateway_owners[gateway] = node
        for path in resolved_node_content_paths(node, repo_root):
            canonical = Path(os.path.abspath(path))
            previous_owner = content_owners.get(canonical)
            if previous_owner is not None:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: content file {path} is owned by both "
                    f"{previous_owner.node_id} and {node.node_id}"
                )
            content_owners[canonical] = node
    return resolved


def expanded_legacy_blueprint(graph: SkillBlueprintGraph) -> dict[str, Any]:
    """Project a typed graph into the legacy nested view used during migration."""

    if not _is_typed_declaration(graph.root.declaration):
        return deepcopy(graph.root.declaration)

    root_fields = {
        key: deepcopy(value)
        for key, value in graph.root.declaration.items()
        if key not in {
            "schema_version",
            "blueprint_type",
            "node_type",
            "id",
            "gateway",
            "content",
            "default_interface",
            "interfaces",
        }
    }
    interfaces: dict[str, dict[str, Any]] = {"machine": {}, "llm": {}}
    for edge in graph.edges:
        if edge.relation != "declares-interface" or edge.source_id != graph.root.node_id:
            continue
        node = graph.nodes[edge.target_id]
        namespace = "machine" if node.blueprint_type == "machine-interface" else "llm"
        local_name = node.node_id.rsplit(".", 1)[-1]
        specification = {
            key: deepcopy(value)
            for key, value in node.declaration.items()
            if key not in {
                "schema_version",
                "blueprint_type",
                "node_type",
                "id",
                "binding",
                "gateway",
                "content",
                "behavior_sources",
            }
        }
        behavior_sources: list[dict[str, Any]] = []
        for source_entry in node.declaration.get("behavior_sources", []):
            if not isinstance(source_entry, dict):
                continue
            source_id = source_entry.get("source")
            source_node = graph.nodes.get(source_id) if isinstance(source_id, str) else None
            if source_node is None:
                continue
            source_gateway = declaration_gateway(source_node.declaration) or {}
            behavior_sources.append(
                {
                    "path": source_gateway.get("path"),
                    "content": (
                        source_node.declaration.get("semantic_type")
                        if source_node.declaration.get("schema_version") == 3
                        else source_node.declaration.get("content")
                    ),
                    "format": source_node.declaration.get("format"),
                    "reason": source_entry.get("reason"),
                }
            )

        binding = declaration_gateway(node.declaration) or {}
        if namespace == "machine":
            if binding.get("kind") == "python-entrypoint":
                specification["invocation"] = {
                    "kind": "python_machine_interface",
                    "entrypoint": f"{binding.get('path')}:{binding.get('symbol')}",
                    "args_prefix": deepcopy(binding.get("args_prefix", [])),
                    "behavior_sources": behavior_sources,
                }
            elif binding.get("kind") == "command-file":
                specification["invocation"] = {
                    "kind": "command_file",
                    "path": binding.get("path"),
                    "args_prefix": deepcopy(binding.get("args_prefix", [])),
                    "behavior_sources": behavior_sources,
                }
        else:
            path = binding.get("path")
            specification["binding"] = {
                "kind": "skill_file" if path == "SKILL.md" else "markdown_file",
                "path": path,
            }
            specification["behavior_sources"] = behavior_sources
        interfaces[namespace][local_name] = specification

    root_fields["interfaces"] = interfaces
    return root_fields

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
from typing import Any, Mapping

import jsonschema
import yaml

from .blueprint_inventory import JsonValue, iter_blueprints as iter_inventory_blueprints


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

    @property
    def module_root(self) -> Path:
        """Return the physical module root for this node."""

        return self.skill_root


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


@dataclass(frozen=True)
class InterfaceExport:
    interface_id: str
    version: int
    local_name: str
    module_node_id: str
    declaration: Mapping[str, JsonValue]
    source_node_id: str | None = None
    source_interface_id: str | None = None
    export_declaration: Mapping[str, JsonValue] | None = None


# Temporary pre-v4 source compatibility. Both names denote the same DTO.
MachineInterfaceExport = InterfaceExport


@dataclass(frozen=True)
class ExportDependencyEdge:
    source_export_id: str
    target_interface_id: str
    target_version: int


@dataclass(frozen=True)
class HelperEdge:
    source_export_id: str
    local_helper_id: str
    target_interface_id: str
    target_version: int
    binding: Mapping[str, JsonValue]


@dataclass(frozen=True)
class CertificationEdge:
    relation: str
    source_node_id: str
    target_node_id: str
    target_version: int | None = None

    @property
    def source_module_id(self) -> str:
        """Return the legacy v3 source-module field."""

        return self.source_node_id


# Temporary pre-v4 source compatibility. Both names denote the same edge type.
ModuleCertificationEdge = CertificationEdge


@dataclass(frozen=True, init=False)
class RepositoryBlueprintGraph:
    nodes: Mapping[str, BlueprintNode]
    node_edges: tuple[BlueprintEdge, ...]
    exports: Mapping[str, InterfaceExport]
    export_edges: tuple[ExportDependencyEdge, ...]
    helper_edges: tuple[HelperEdge, ...]
    certification_edges: tuple[CertificationEdge, ...]
    module_sources: Mapping[str, tuple[str, ...]]
    direct_file_owners: Mapping[Path, str]

    def __init__(
        self,
        *,
        nodes: Mapping[str, BlueprintNode],
        node_edges: tuple[BlueprintEdge, ...],
        exports: Mapping[str, InterfaceExport] | None = None,
        machine_exports: Mapping[str, InterfaceExport] | None = None,
        export_edges: tuple[ExportDependencyEdge, ...],
        helper_edges: tuple[HelperEdge, ...],
        certification_edges: tuple[CertificationEdge, ...],
        module_sources: Mapping[str, tuple[str, ...]] | None = None,
        direct_file_owners: Mapping[Path, str] | None = None,
    ) -> None:
        if exports is not None and machine_exports is not None:
            raise TypeError("specify exports or machine_exports, not both")
        resolved_exports = exports if exports is not None else machine_exports
        if resolved_exports is None:
            resolved_exports = {}
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "node_edges", node_edges)
        object.__setattr__(self, "exports", resolved_exports)
        object.__setattr__(self, "export_edges", export_edges)
        object.__setattr__(self, "helper_edges", helper_edges)
        object.__setattr__(self, "certification_edges", certification_edges)
        object.__setattr__(self, "module_sources", module_sources or {})
        object.__setattr__(self, "direct_file_owners", direct_file_owners or {})

    @property
    def machine_exports(self) -> Mapping[str, InterfaceExport]:
        """Return the pre-v4 name for the canonical export mapping."""

        return self.exports


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
    (3, "machine-module"): "machine-module.schema.json",
    (3, "behavior-source"): "behavior-source.schema.json",
    (4, "module"): "module.schema.json",
    (4, "behavioral_source"): "behavioral-source.schema.json",
}


def _is_typed_declaration(declaration: dict[str, Any]) -> bool:
    return declaration.get("schema_version") in {2, 3, 4} or any(
        key in declaration for key in ("blueprint_type", "node_type")
    )


def declaration_node_type(declaration: dict[str, Any]) -> str | None:
    """Return one typed declaration's version-normalized node type."""

    key = (
        "node_type"
        if declaration.get("schema_version") in {3, 4}
        else "blueprint_type"
    )
    value = declaration.get(key)
    return value if isinstance(value, str) else None


def declaration_gateway(declaration: dict[str, Any]) -> dict[str, Any] | None:
    """Return one typed declaration's version-normalized gateway mapping."""

    key = (
        "gateway"
        if declaration.get("schema_version") in {3, 4}
        else "binding"
    )
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
    if base not in {"skill-root", "module-root", "repository-root"}:
        raise BlueprintGraphError(f"{context}: unsupported blueprint locator base {base!r}")
    if not isinstance(raw_path, str) or not raw_path:
        raise BlueprintGraphError(f"{context}: blueprint locator path must be non-empty")
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise BlueprintGraphError(
            f"{context}: locator path must be relative without parent traversal"
        )
    root = skill_root if base in {"skill-root", "module-root"} else repo_root
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
    if selected_version not in {2, 3, 4}:
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

    if node.declaration.get("schema_version") not in {3, 4}:
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


def _target_node_from_document(document: Any) -> BlueprintNode:
    declaration = dict(document.declaration)
    node_id = declaration.get("id")
    node_type = declaration.get("node_type")
    if not isinstance(node_id, str) or not node_id:
        raise BlueprintGraphError(f"{document.path}: target v3 blueprint requires a non-empty id")
    if not isinstance(node_type, str):
        raise BlueprintGraphError(f"{document.path}: target v3 blueprint requires node_type")
    raw_version = declaration.get("version")
    if node_type == "skill":
        default = declaration.get("default_interface")
        raw_version = default.get("version", 1) if isinstance(default, dict) else 1
    version = _positive_version(raw_version, str(document.path))
    gateway = declaration.get("gateway")
    gateway_path = None
    if isinstance(gateway, dict) and isinstance(gateway.get("path"), str):
        gateway_path = document.owner_root / gateway["path"]
    return BlueprintNode(
        node_id=node_id,
        node_type=node_type,
        version=version,
        skill_root=document.owner_root,
        blueprint_path=document.path,
        gateway_path=gateway_path,
        declaration=declaration,
    )


def _target_relationship_entries(
    declaration: Mapping[str, Any],
) -> tuple[tuple[str, str, int], ...]:
    entries: list[tuple[str, str, int]] = []
    for relation, field, id_field in (
        ("uses-interface", "uses_interfaces", "interface"),
        ("uses-behavior-source", "behavior_sources", "source"),
        ("uses-behavior-source", "uses_behavior_sources", "source"),
    ):
        raw_entries = declaration.get(field, [])
        if not isinstance(raw_entries, list):
            raise BlueprintGraphError(f"{field} must be a list")
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                raise BlueprintGraphError(f"{field}[{index}] must be a mapping")
            target = entry.get(id_field)
            if not isinstance(target, str) or not target:
                raise BlueprintGraphError(f"{field}[{index}] requires {id_field}")
            version = _positive_version(entry.get("version"), f"{field}[{index}]")
            entries.append((relation, target, version))
    return tuple(entries)


def _reject_export_cycles(
    exports: Mapping[str, InterfaceExport],
    edges: tuple[ExportDependencyEdge, ...],
) -> None:
    children: dict[str, list[str]] = {interface_id: [] for interface_id in exports}
    for edge in edges:
        children[edge.source_export_id].append(edge.target_interface_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(interface_id: str) -> None:
        if interface_id in visiting:
            start = visiting.index(interface_id)
            cycle = visiting[start:] + [interface_id]
            raise BlueprintGraphError(
                "runtime export dependency cycle: " + " -> ".join(cycle)
            )
        if interface_id in visited:
            return
        visiting.append(interface_id)
        for target in sorted(children[interface_id]):
            visit(target)
        visiting.pop()
        visited.add(interface_id)

    for interface_id in sorted(exports):
        visit(interface_id)


def _reject_module_cycles(
    module_ids: set[str], edges: tuple[ModuleCertificationEdge, ...]
) -> None:
    children: dict[str, list[str]] = {module_id: [] for module_id in module_ids}
    for edge in edges:
        if edge.target_node_id in module_ids:
            children[edge.source_node_id].append(edge.target_node_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(module_id: str) -> None:
        if module_id in visiting:
            start = visiting.index(module_id)
            cycle = visiting[start:] + [module_id]
            raise BlueprintGraphError(
                "module certification dependency cycle: " + " -> ".join(cycle)
            )
        if module_id in visited:
            return
        visiting.append(module_id)
        for target in sorted(children[module_id]):
            visit(target)
        visiting.pop()
        visited.add(module_id)

    for module_id in sorted(module_ids):
        visit(module_id)


def _validate_repository_content_ownership(
    nodes: Mapping[str, BlueprintNode],
    repo_root: Path,
) -> None:
    content_owners: dict[Path, BlueprintNode] = {}
    gateway_owners: dict[Path, BlueprintNode] = {}
    for node in sorted(nodes.values(), key=lambda item: item.node_id):
        if node.embedded:
            continue
        if node.gateway_path is not None:
            gateway = Path(os.path.abspath(node.gateway_path))
            previous_gateway_owner = gateway_owners.get(gateway)
            if previous_gateway_owner is not None:
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: version 3 gateway {node.gateway_path} "
                    f"is shared by {previous_gateway_owner.node_id} and {node.node_id}"
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


def _require_platform_compatibility(
    source: BlueprintNode,
    target: BlueprintNode,
    *,
    context: str,
) -> None:
    source_support = source.declaration.get("platform_support")
    target_support = target.declaration.get("platform_support")
    if not isinstance(source_support, Mapping) or not isinstance(
        target_support, Mapping
    ):
        return
    for platform, supported in source_support.items():
        if supported is True and target_support.get(platform) is not True:
            raise BlueprintGraphError(
                f"{context}: target {target.node_id} does not support required "
                f"platform {platform!r}"
            )


def _reject_certification_cycles(
    node_ids: set[str], edges: tuple[CertificationEdge, ...]
) -> None:
    children: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source_node_id in children and edge.target_node_id in children:
            children[edge.source_node_id].append(edge.target_node_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            start = visiting.index(node_id)
            cycle = visiting[start:] + [node_id]
            raise BlueprintGraphError(
                "certification dependency cycle: " + " -> ".join(cycle)
            )
        if node_id in visited:
            return
        visiting.append(node_id)
        for target_id in sorted(children[node_id]):
            visit(target_id)
        visiting.pop()
        visited.add(node_id)

    for node_id in sorted(node_ids):
        visit(node_id)


def _load_v4_repository_blueprint_graph(
    root: Path,
    documents: tuple[Any, ...],
    *,
    schema_root: Path | None,
) -> RepositoryBlueprintGraph:
    """Load the unified module/behavioral-source repository graph."""

    validators: dict[str, jsonschema.Draft7Validator] = {}
    nodes: dict[str, BlueprintNode] = {}
    documents_by_path: dict[Path, Any] = {}
    for document in documents:
        if schema_root is not None:
            errors = _typed_declaration_schema_errors(
                document.path,
                dict(document.declaration),
                schema_root,
                validators,
            )
            if errors:
                raise errors[0]
        node = _target_node_from_document(document)
        existing = nodes.get(node.node_id)
        if existing is not None:
            raise BlueprintGraphError(
                f"duplicate node id {node.node_id!r}: "
                f"{existing.blueprint_path} and {node.blueprint_path}"
            )
        nodes[node.node_id] = node
        documents_by_path[Path(os.path.abspath(document.path))] = document

    modules = {
        node.node_id: node
        for node in nodes.values()
        if node.node_type == "module"
    }
    sources = {
        node.node_id: node
        for node in nodes.values()
        if node.node_type == "behavioral_source"
    }
    if not modules:
        raise BlueprintGraphError("version 4 repository graph requires at least one module")
    if len(modules) + len(sources) != len(nodes):
        raise BlueprintGraphError("version 4 repository graph permits only module and behavioral_source nodes")

    module_sources: dict[str, tuple[str, ...]] = {}
    source_modules: dict[str, str] = {}
    for module_id, module in sorted(modules.items()):
        if module.skill_root.name != module_id:
            raise BlueprintGraphError(
                f"{module.blueprint_path}: module id {module_id!r} must match its directory"
            )
        raw_sources = module.declaration.get("sources")
        if not isinstance(raw_sources, dict):
            raise BlueprintGraphError(f"{module.blueprint_path}: sources must be a mapping")
        contained: list[str] = []
        for source_id, entry in sorted(raw_sources.items()):
            if not isinstance(source_id, str) or not isinstance(entry, dict):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: invalid source containment entry"
                )
            expected_prefix = f"{module_id}.source."
            if not source_id.startswith(expected_prefix):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: source {source_id!r} must use "
                    f"module namespace {expected_prefix!r}"
                )
            locator_path = _resolve_locator(
                module.skill_root,
                entry.get("blueprint"),
                f"{module.blueprint_path}:sources.{source_id}",
                root,
            )
            source = sources.get(source_id)
            if source is None:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: unresolved contained source {source_id!r}"
                )
            if Path(os.path.abspath(locator_path)) != Path(
                os.path.abspath(source.blueprint_path)
            ):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: source {source_id!r} locator does not "
                    "identify its canonical blueprint"
                )
            previous_module = source_modules.get(source_id)
            if previous_module is not None:
                raise BlueprintGraphError(
                    f"source {source_id!r} is contained by both {previous_module} and {module_id}"
                )
            if source.skill_root != module.skill_root:
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: contained source must be inside module {module_id}"
                )
            source_modules[source_id] = module_id
            contained.append(source_id)
        module_sources[module_id] = tuple(contained)
    orphan_sources = sorted(set(sources) - set(source_modules))
    if orphan_sources:
        raise BlueprintGraphError(
            "behavioral sources must be contained by exactly one module: "
            + ", ".join(orphan_sources)
        )

    source_interfaces: dict[str, tuple[BlueprintNode, Mapping[str, JsonValue]]] = {}
    for source_id, source in sorted(sources.items()):
        raw_interfaces = source.declaration.get("interfaces")
        if not isinstance(raw_interfaces, dict):
            raise BlueprintGraphError(f"{source.blueprint_path}: interfaces must be a mapping")
        for interface_id, declaration in sorted(raw_interfaces.items()):
            if not isinstance(interface_id, str) or not isinstance(declaration, dict):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: invalid source interface declaration"
                )
            expected_prefix = f"{source_id}.interface."
            if not interface_id.startswith(expected_prefix):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: interface {interface_id!r} must use "
                    f"source namespace {expected_prefix!r}"
                )
            if interface_id in source_interfaces:
                raise BlueprintGraphError(f"duplicate source interface {interface_id!r}")
            source_interfaces[interface_id] = (source, declaration)

    exports: dict[str, InterfaceExport] = {}
    for module_id, module in sorted(modules.items()):
        raw_exports = module.declaration.get("exports")
        if not isinstance(raw_exports, dict):
            raise BlueprintGraphError(f"{module.blueprint_path}: exports must be a mapping")
        for export_id, export_declaration in sorted(raw_exports.items()):
            if not isinstance(export_id, str) or not isinstance(export_declaration, dict):
                raise BlueprintGraphError(f"{module.blueprint_path}: invalid export declaration")
            expected_prefix = f"{module_id}.interface."
            if not export_id.startswith(expected_prefix):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} must use "
                    f"module namespace {expected_prefix!r}"
                )
            source_interface_id = export_declaration.get("source_interface")
            if not isinstance(source_interface_id, str):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} requires source_interface"
                )
            try:
                source, interface_declaration = source_interfaces[source_interface_id]
            except KeyError as exc:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} targets unknown "
                    f"source interface {source_interface_id!r}"
                ) from exc
            if source_modules[source.node_id] != module_id:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} must bind a contained "
                    "source interface"
                )
            exports[export_id] = InterfaceExport(
                interface_id=export_id,
                version=_positive_version(
                    interface_declaration.get("version"), source_interface_id
                ),
                local_name=export_id.rsplit(".interface.", 1)[-1],
                module_node_id=module_id,
                declaration=interface_declaration,
                source_node_id=source.node_id,
                source_interface_id=source_interface_id,
                export_declaration=export_declaration,
            )

    node_edges: list[BlueprintEdge] = []
    certification_edges: list[CertificationEdge] = []
    interface_uses_by_source: dict[str, tuple[tuple[str, int], ...]] = {}
    for module_id, source_ids in sorted(module_sources.items()):
        for source_id in source_ids:
            source = sources[source_id]
            node_edges.append(
                BlueprintEdge(
                    "contains-source", module_id, source_id, source.version, source.blueprint_path
                )
            )
            certification_edges.append(
                CertificationEdge("contains-source", module_id, source_id, source.version)
            )

    for source_id, source in sorted(sources.items()):
        raw_dependencies = source.declaration.get("dependencies", [])
        if not isinstance(raw_dependencies, list):
            raise BlueprintGraphError(f"{source.blueprint_path}: dependencies must be a list")
        for index, dependency in enumerate(raw_dependencies):
            if not isinstance(dependency, dict):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: dependencies[{index}] must be a mapping"
                )
            target_id = dependency.get("source")
            if not isinstance(target_id, str) or target_id not in sources:
                raise BlueprintGraphError(
                    f"{source.node_id}: unresolved behavioral source {target_id!r}"
                )
            target = sources[target_id]
            version = _positive_version(
                dependency.get("version"), f"{source.node_id}.dependencies[{index}]"
            )
            if target.version != version:
                raise BlueprintGraphError(
                    f"{source.node_id}: pins {target_id} version {version}, but target "
                    f"version is {target.version}"
                )
            locator_path = _resolve_locator(
                source.skill_root,
                dependency.get("blueprint"),
                f"{source.blueprint_path}:dependencies[{index}]",
                root,
            )
            if Path(os.path.abspath(locator_path)) != Path(os.path.abspath(target.blueprint_path)):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: dependency locator for {target_id!r} "
                    "does not identify its canonical blueprint"
                )
            node_edges.append(
                BlueprintEdge("uses-source", source_id, target_id, version, target.blueprint_path)
            )
            certification_edges.append(
                CertificationEdge("uses-source", source_id, target_id, version)
            )

        raw_uses = source.declaration.get("uses_interfaces", [])
        if not isinstance(raw_uses, list):
            raise BlueprintGraphError(f"{source.blueprint_path}: uses_interfaces must be a list")
        uses: list[tuple[str, int]] = []
        for index, use in enumerate(raw_uses):
            if not isinstance(use, dict):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: uses_interfaces[{index}] must be a mapping"
                )
            target_id = use.get("interface")
            version = _positive_version(
                use.get("version"), f"{source.node_id}.uses_interfaces[{index}]"
            )
            if not isinstance(target_id, str):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: uses_interfaces[{index}] requires interface"
                )
            if target_id in source_interfaces:
                target_source, target_declaration = source_interfaces[target_id]
                if source_modules[target_source.node_id] != source_modules[source_id]:
                    raise BlueprintGraphError(
                        f"{source.node_id}: private interface {target_id!r} cannot be used cross-module"
                    )
                actual_version = _positive_version(target_declaration.get("version"), target_id)
                if actual_version != version:
                    raise BlueprintGraphError(
                        f"{source.node_id}: pins {target_id} version {version}, but target "
                        f"version is {actual_version}"
                    )
                relation = "uses-private-interface"
                target_node_id = target_source.node_id
            elif target_id in exports:
                export = exports[target_id]
                if export.version != version:
                    raise BlueprintGraphError(
                        f"{source.node_id}: pins {target_id} version {version}, but target "
                        f"version is {export.version}"
                    )
                caller_module = source_modules[source_id]
                access = (
                    export.export_declaration.get("access")
                    if isinstance(export.export_declaration, Mapping)
                    else None
                )
                if not isinstance(access, Mapping):
                    raise BlueprintGraphError(f"{target_id}: export access is missing")
                allowed = access.get("allowed_callers", [])
                if (
                    caller_module != export.module_node_id
                    and access.get("allow_all_modules") is not True
                    and caller_module not in allowed
                ):
                    raise BlueprintGraphError(
                        f"{source.node_id}: caller module {caller_module!r} is not allowed "
                        f"by {target_id}"
                    )
                relation = "uses-export"
                target_node_id = export.module_node_id
                target_module = modules[export.module_node_id]
                _require_platform_compatibility(
                    source,
                    sources[export.source_node_id],
                    context=source.node_id,
                )
                _ = target_module
            else:
                raise BlueprintGraphError(
                    f"{source.node_id}: unresolved interface {target_id!r}"
                )
            uses.append((target_id, version))
            node_edges.append(BlueprintEdge(relation, source_id, target_id, version))
            certification_edges.append(
                CertificationEdge(relation, source_id, target_node_id, version)
            )
        interface_uses_by_source[source_id] = tuple(uses)

    export_edges: list[ExportDependencyEdge] = []
    helper_edges: list[HelperEdge] = []
    for export_id, export in sorted(exports.items()):
        assert export.source_node_id is not None
        direct_uses = set(interface_uses_by_source[export.source_node_id])
        for target_id, version in sorted(direct_uses):
            if target_id in exports:
                export_edges.append(ExportDependencyEdge(export_id, target_id, version))
        contract = export.declaration.get("contract")
        raw_helpers = contract.get("helpers", []) if isinstance(contract, Mapping) else []
        if not isinstance(raw_helpers, list):
            raise BlueprintGraphError(f"{export_id}: contract.helpers must be a list")
        for index, helper in enumerate(raw_helpers):
            if not isinstance(helper, dict):
                raise BlueprintGraphError(f"{export_id}: helpers[{index}] must be a mapping")
            helper_id = helper.get("id")
            target_id = helper.get("interface")
            version = _positive_version(helper.get("version"), f"{export_id}.helpers[{index}]")
            if not isinstance(helper_id, str) or not isinstance(target_id, str):
                raise BlueprintGraphError(f"{export_id}: helper requires id and interface")
            if (target_id, version) not in direct_uses:
                raise BlueprintGraphError(
                    f"{export_id}: helper {helper_id!r} target must be in the "
                    "source's effective direct interface set"
                )
            helper_edges.append(HelperEdge(export_id, helper_id, target_id, version, helper))

    module_content: dict[str, set[Path]] = {}
    source_content: dict[str, set[Path]] = {}
    blueprint_paths = {Path(os.path.abspath(node.blueprint_path)) for node in nodes.values()}
    for module_id, module in sorted(modules.items()):
        paths = {Path(os.path.abspath(path)) for path in resolved_node_content_paths(module, root)}
        if paths & blueprint_paths:
            raise BlueprintGraphError(
                f"{module.blueprint_path}: content cannot include blueprint files"
            )
        module_content[module_id] = paths
    for source_id, source in sorted(sources.items()):
        paths = {Path(os.path.abspath(path)) for path in resolved_node_content_paths(source, root)}
        if paths & blueprint_paths:
            raise BlueprintGraphError(
                f"{source.blueprint_path}: content cannot include blueprint files"
            )
        source_content[source_id] = paths

    direct_file_owners: dict[Path, str] = {}
    for module_id, source_ids in sorted(module_sources.items()):
        seen_source_paths: dict[Path, str] = {}
        for source_id in source_ids:
            missing = source_content[source_id] - module_content[module_id]
            if missing:
                raise BlueprintGraphError(
                    f"{sources[source_id].blueprint_path}: source content must be contained "
                    f"by module {module_id}: {sorted(str(path) for path in missing)}"
                )
            for path in sorted(source_content[source_id]):
                previous = seen_source_paths.get(path)
                if previous is not None:
                    raise BlueprintGraphError(
                        f"{sources[source_id].blueprint_path}: sibling sources {previous} "
                        f"and {source_id} overlap at {path}"
                    )
                seen_source_paths[path] = source_id
                direct_file_owners[path] = source_id
        for path in sorted(module_content[module_id] - set(seen_source_paths)):
            direct_file_owners[path] = module_id

    edge_keys: set[tuple[str, str, str, int | None]] = set()
    unique_certification_edges: list[CertificationEdge] = []
    for edge in certification_edges:
        key = (edge.relation, edge.source_node_id, edge.target_node_id, edge.target_version)
        if key not in edge_keys:
            edge_keys.add(key)
            unique_certification_edges.append(edge)
    certification_edge_tuple = tuple(
        sorted(
            unique_certification_edges,
            key=lambda edge: (
                edge.source_node_id,
                edge.relation,
                edge.target_node_id,
                edge.target_version or 0,
            ),
        )
    )
    _reject_certification_cycles(set(nodes), certification_edge_tuple)
    export_edge_tuple = tuple(
        sorted(
            export_edges,
            key=lambda edge: (
                edge.source_export_id,
                edge.target_interface_id,
                edge.target_version,
            ),
        )
    )
    _reject_export_cycles(exports, export_edge_tuple)
    return RepositoryBlueprintGraph(
        nodes=dict(sorted(nodes.items())),
        node_edges=tuple(sorted(node_edges, key=edge_key)),
        exports=dict(sorted(exports.items())),
        export_edges=export_edge_tuple,
        helper_edges=tuple(
            sorted(helper_edges, key=lambda edge: (edge.source_export_id, edge.local_helper_id))
        ),
        certification_edges=certification_edge_tuple,
        module_sources=module_sources,
        direct_file_owners=direct_file_owners,
    )


def load_repository_blueprint_graph(
    repo_root: Path,
    *,
    schema_root: Path | None = None,
) -> RepositoryBlueprintGraph:
    """Normalize the complete repository inventory into one graph."""

    root = Path(repo_root).resolve()
    documents = tuple(iter_inventory_blueprints(root))
    v4_documents = tuple(
        document
        for document in documents
        if document.declaration.get("schema_version") == 4
    )
    target_documents = tuple(
        document
        for document in documents
        if document.declaration.get("schema_version") == 3
        and document.node_type in {"skill", "llm-interface", "behavior-source", "machine-module"}
    )
    selected_schema_root = Path(schema_root) if schema_root is not None else None
    if selected_schema_root is None:
        candidate = root / "references" / "blueprint"
        if (candidate / "module.schema.json").is_file() or (
            candidate / "machine-module.schema.json"
        ).is_file():
            selected_schema_root = candidate
    if v4_documents:
        if selected_schema_root is None:
            selected_schema_root = (
                Path(__file__).resolve().parents[3]
                / "references"
                / "blueprint"
            )
        if target_documents:
            raise BlueprintGraphError(
                "repository graph cannot mix version 4 and pre-v4 target nodes"
            )
        return _load_v4_repository_blueprint_graph(
            root,
            v4_documents,
            schema_root=selected_schema_root,
        )
    if selected_schema_root is not None:
        validators: dict[str, jsonschema.Draft7Validator] = {}
        for document in target_documents:
            errors = _typed_declaration_schema_errors(
                document.path,
                dict(document.declaration),
                selected_schema_root,
                validators,
            )
            if errors:
                raise errors[0]
    nodes: dict[str, BlueprintNode] = {}
    for document in target_documents:
        node = _target_node_from_document(document)
        existing = nodes.get(node.node_id)
        if existing is not None:
            raise BlueprintGraphError(
                f"duplicate node id {node.node_id!r}: {existing.blueprint_path} and {node.blueprint_path}"
            )
        if node.node_type == "machine-module":
            owner = node.skill_root.name
            if not node.node_id.startswith(f"{owner}.machine-module."):
                raise BlueprintGraphError(
                    f"{node.blueprint_path}: machine-module id must use owner namespace "
                    f"{owner!r}"
                )
        nodes[node.node_id] = node

    for skill in tuple(
        sorted(
            (node for node in nodes.values() if node.node_type == "skill"),
            key=lambda item: item.node_id,
        )
    ):
        default = skill.declaration.get("default_interface")
        if not isinstance(default, dict):
            continue
        default_id = f"{skill.node_id}.llm.default"
        if default_id in nodes:
            raise BlueprintGraphError(
                f"{skill.blueprint_path}: inline and sidecar default interfaces conflict"
            )
        embedded_declaration = {
            "schema_version": 3,
            "node_type": "llm-interface",
            "id": default_id,
            "gateway": deepcopy(skill.declaration.get("gateway", {})),
            "content": deepcopy(skill.declaration.get("content", [])),
            **deepcopy(default),
        }
        nodes[default_id] = BlueprintNode(
            node_id=default_id,
            node_type="llm-interface",
            version=_positive_version(default.get("version"), f"{skill.blueprint_path}:default_interface"),
            skill_root=skill.skill_root,
            blueprint_path=skill.blueprint_path,
            gateway_path=skill.gateway_path,
            declaration=embedded_declaration,
            embedded=True,
        )

    machine_exports: dict[str, InterfaceExport] = {}
    module_shared_edges: dict[str, tuple[tuple[str, int], ...]] = {}
    export_local_edges: dict[str, tuple[tuple[str, int], ...]] = {}
    helper_edges: list[HelperEdge] = []
    for module in sorted(
        (node for node in nodes.values() if node.node_type == "machine-module"),
        key=lambda item: item.node_id,
    ):
        raw_interfaces = module.declaration.get("interfaces")
        if not isinstance(raw_interfaces, dict) or not raw_interfaces:
            raise BlueprintGraphError(f"{module.blueprint_path}: interfaces must be a nonempty mapping")
        shared = tuple(
            (target, version)
            for relation, target, version in _target_relationship_entries(
                {"uses_interfaces": module.declaration.get("uses_interfaces", [])}
            )
            if relation == "uses-interface"
        )
        module_shared_edges[module.node_id] = shared
        owner = module.skill_root.name
        for local_name in sorted(raw_interfaces):
            declaration = raw_interfaces[local_name]
            if not isinstance(declaration, dict):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: interfaces.{local_name} must be a mapping"
                )
            interface_id = declaration.get("id")
            if not isinstance(interface_id, str) or not interface_id:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: interfaces.{local_name}.id is required"
                )
            if not interface_id.startswith(f"{owner}.machine."):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export id {interface_id!r} must use owner namespace"
                )
            if interface_id in machine_exports:
                prior = machine_exports[interface_id]
                raise BlueprintGraphError(
                    f"duplicate public export id {interface_id!r} in "
                    f"{prior.module_node_id} and {module.node_id}"
                )
            export = InterfaceExport(
                interface_id=interface_id,
                version=_positive_version(
                    declaration.get("version"),
                    f"{module.blueprint_path}:interfaces.{local_name}",
                ),
                local_name=local_name,
                module_node_id=module.node_id,
                declaration=declaration,
            )
            machine_exports[interface_id] = export
            local = tuple(
                (target, version)
                for relation, target, version in _target_relationship_entries(
                    {"uses_interfaces": declaration.get("uses_interfaces", [])}
                )
                if relation == "uses-interface"
            )
            export_local_edges[interface_id] = local
            raw_helpers = declaration.get("helpers", [])
            if not isinstance(raw_helpers, list):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: interfaces.{local_name}.helpers must be a list"
                )
            for index, helper in enumerate(raw_helpers):
                if not isinstance(helper, dict):
                    raise BlueprintGraphError(
                        f"{module.blueprint_path}: interfaces.{local_name}.helpers[{index}] must be a mapping"
                    )
                helper_id = helper.get("id")
                target = helper.get("interface")
                if not isinstance(helper_id, str) or not isinstance(target, str):
                    raise BlueprintGraphError(
                        f"{module.blueprint_path}: helper requires id and interface"
                    )
                helper_edges.append(
                    HelperEdge(
                        interface_id,
                        helper_id,
                        target,
                        _positive_version(helper.get("version"), f"helper {helper_id}"),
                        helper,
                    )
                )

    export_edges: list[ExportDependencyEdge] = []
    for interface_id, export in sorted(machine_exports.items()):
        declared = module_shared_edges[export.module_node_id] + export_local_edges[interface_id]
        seen: set[tuple[str, int]] = set()
        for target_id, target_version in declared:
            if (target_id, target_version) in seen:
                continue
            seen.add((target_id, target_version))
            target = machine_exports.get(target_id)
            if target is None:
                raise BlueprintGraphError(
                    f"{interface_id}: unresolved machine export {target_id!r}"
                )
            if target.version != target_version:
                raise BlueprintGraphError(
                    f"{interface_id}: pins {target_id} version {target_version}, "
                    f"but target version {target.version} is declared"
                )
            target_decl = target.declaration
            caller_skill = nodes[export.module_node_id].skill_root.name
            allowed = target_decl.get("allowed_callers", [])
            if target_decl.get("allow_all_skills") is not True and caller_skill not in allowed:
                raise BlueprintGraphError(
                    f"{interface_id}: caller {caller_skill!r} is not allowed by {target_id}"
                )
            _require_platform_compatibility(
                nodes[export.module_node_id],
                nodes[target.module_node_id],
                context=interface_id,
            )
            export_edges.append(
                ExportDependencyEdge(interface_id, target_id, target_version)
            )

    authority = {
        interface_id: {
            edge.target_interface_id
            for edge in export_edges
            if edge.source_export_id == interface_id
        }
        for interface_id in machine_exports
    }
    for helper in helper_edges:
        target = machine_exports.get(helper.target_interface_id)
        if target is None:
            raise BlueprintGraphError(
                f"{helper.source_export_id}: helper {helper.local_helper_id!r} targets "
                f"unknown export {helper.target_interface_id!r}"
            )
        if target.version != helper.target_version:
            raise BlueprintGraphError(
                f"{helper.source_export_id}: helper {helper.local_helper_id!r} pins "
                f"{helper.target_interface_id} version {helper.target_version}, but target "
                f"version is {target.version}"
            )
        if helper.target_interface_id not in authority[helper.source_export_id]:
            raise BlueprintGraphError(
                f"{helper.source_export_id}: helper {helper.local_helper_id!r} target must "
                "be in the export's effective direct tool set"
            )

    node_edges: list[BlueprintEdge] = []
    for node in sorted(nodes.values(), key=lambda item: item.node_id):
        if node.node_type == "skill":
            raw_interfaces = node.declaration.get("interfaces", [])
            if isinstance(raw_interfaces, list):
                for entry in raw_interfaces:
                    if isinstance(entry, dict) and isinstance(entry.get("interface"), str):
                        node_edges.append(
                            BlueprintEdge(
                                "declares-interface",
                                node.node_id,
                                entry["interface"],
                                _positive_version(entry.get("version"), f"{node.node_id}.interfaces"),
                            )
                        )
            continue
        for relation, target_id, version in _target_relationship_entries(node.declaration):
            if relation == "uses-interface":
                target_export = machine_exports.get(target_id)
                target_node = nodes.get(target_id)
                if target_export is None and (
                    target_node is None or target_node.node_type != "llm-interface"
                ):
                    raise BlueprintGraphError(f"{node.node_id}: unresolved interface {target_id!r}")
                actual_version = (
                    target_export.version if target_export is not None else target_node.version
                )
                if actual_version != version:
                    raise BlueprintGraphError(
                        f"{node.node_id}: pins {target_id} version {version}, but target "
                        f"version is {actual_version}"
                    )
                target_declaration = (
                    target_export.declaration
                    if target_export is not None
                    else target_node.declaration
                )
                target_owner = (
                    nodes[target_export.module_node_id].skill_root.name
                    if target_export is not None
                    else target_node.skill_root.name
                )
                caller_skill = node.skill_root.name
                if target_owner != caller_skill:
                    allowed = target_declaration.get("allowed_callers", [])
                    if (
                        target_declaration.get("allow_all_skills") is not True
                        and caller_skill not in allowed
                    ):
                        raise BlueprintGraphError(
                            f"{node.node_id}: caller {caller_skill!r} is not allowed "
                            f"by {target_id}"
                        )
            else:
                target_node = nodes.get(target_id)
                if target_node is None:
                    raise BlueprintGraphError(
                        f"{node.node_id}: unresolved behavior source {target_id!r}"
                    )
                if target_node.version != version:
                    raise BlueprintGraphError(
                        f"{node.node_id}: pins {target_id} version {version}, but target "
                        f"version is {target_node.version}"
                    )
            node_edges.append(BlueprintEdge(relation, node.node_id, target_id, version))

    cert_keys: set[tuple[str, str]] = set()
    for edge in node_edges:
        source = nodes.get(edge.source_id)
        if source is not None and source.node_type == "machine-module":
            target_export = machine_exports.get(edge.target_id)
            target_id = (
                target_export.module_node_id if target_export is not None else edge.target_id
            )
            if target_id != source.node_id:
                cert_keys.add((source.node_id, target_id))
    for edge in export_edges:
        source_module = machine_exports[edge.source_export_id].module_node_id
        target_module = machine_exports[edge.target_interface_id].module_node_id
        if source_module != target_module:
            cert_keys.add((source_module, target_module))
    certification_edges = tuple(
        CertificationEdge("uses-module", source, target)
        for source, target in sorted(cert_keys)
    )
    export_edge_tuple = tuple(
        sorted(
            export_edges,
            key=lambda edge: (
                edge.source_export_id,
                edge.target_interface_id,
                edge.target_version,
            ),
        )
    )
    _reject_export_cycles(machine_exports, export_edge_tuple)
    _reject_module_cycles(
        {node.node_id for node in nodes.values() if node.node_type == "machine-module"},
        certification_edges,
    )
    _validate_repository_content_ownership(nodes, root)
    return RepositoryBlueprintGraph(
        nodes=dict(sorted(nodes.items())),
        node_edges=tuple(sorted(node_edges, key=edge_key)),
        exports=dict(sorted(machine_exports.items())),
        export_edges=export_edge_tuple,
        helper_edges=tuple(
            sorted(
                helper_edges,
                key=lambda edge: (edge.source_export_id, edge.local_helper_id),
            )
        ),
        certification_edges=certification_edges,
    )


def resolve_machine_export(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
    version: int | None = None,
) -> tuple[BlueprintNode, MachineInterfaceExport]:
    """Resolve one callable public export to its owning module."""

    if interface_id in graph.nodes and graph.nodes[interface_id].node_type == "machine-module":
        raise BlueprintGraphError(f"module id {interface_id!r} is not callable")
    try:
        export = graph.machine_exports[interface_id]
    except KeyError as exc:
        raise BlueprintGraphError(f"unknown machine export {interface_id!r}") from exc
    if version is not None and export.version != version:
        raise BlueprintGraphError(
            f"{interface_id}: requested version {version}, but target version is {export.version}"
        )
    return graph.nodes[export.module_node_id], export


def resolve_export(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
    version: int | None = None,
) -> tuple[BlueprintNode, BlueprintNode, InterfaceExport]:
    """Resolve one v4 public export to its module and behavioral source."""

    if interface_id in graph.nodes and graph.nodes[interface_id].node_type == "module":
        raise BlueprintGraphError(f"module id {interface_id!r} is not callable")
    try:
        export = graph.exports[interface_id]
    except KeyError as exc:
        raise BlueprintGraphError(f"unknown export {interface_id!r}") from exc
    if version is not None and export.version != version:
        raise BlueprintGraphError(
            f"{interface_id}: requested version {version}, but target version is {export.version}"
        )
    if export.source_node_id is None:
        raise BlueprintGraphError(
            f"{interface_id}: pre-v4 export has no behavioral-source binding"
        )
    source = graph.nodes.get(export.source_node_id)
    if source is None or source.node_type != "behavioral_source":
        raise BlueprintGraphError(
            f"{interface_id}: export source {export.source_node_id!r} is unavailable"
        )
    return graph.nodes[export.module_node_id], source, export


def runtime_authority_for_export(
    graph: RepositoryBlueprintGraph, interface_id: str
) -> tuple[str, ...]:
    """Return only the selected export's direct machine-tool authority."""

    resolve_machine_export(graph, interface_id)
    return tuple(
        sorted(
            edge.target_interface_id
            for edge in graph.export_edges
            if edge.source_export_id == interface_id
        )
    )


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

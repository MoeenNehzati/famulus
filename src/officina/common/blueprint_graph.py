"""Load the repository's version 4 module and behavioral-source graph."""

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping

import jsonschema
import yaml

from .atomic_files import read_regular_file_bytes
from .blueprint_inventory import (
    BlueprintDocument,
    JsonValue,
    _normalize_json,
    _StrictBlueprintLoader,
    iter_blueprints as iter_inventory_blueprints,
)


class BlueprintGraphError(ValueError):
    """Raised when blueprint files cannot form a coherent repository graph."""


class BlueprintSchemaError(BlueprintGraphError):
    """Raised when a graph node fails its concrete JSON Schema."""

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

@dataclass(frozen=True)
class BlueprintEdge:
    relation: str
    source_id: str
    target_id: str
    required_version: int
    target_blueprint_path: Path | None = None


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


@dataclass(frozen=True)
class RepositoryBlueprintGraph:
    nodes: Mapping[str, BlueprintNode]
    node_edges: tuple[BlueprintEdge, ...]
    exports: Mapping[str, InterfaceExport]
    export_edges: tuple[ExportDependencyEdge, ...]
    helper_edges: tuple[HelperEdge, ...]
    certification_edges: tuple[CertificationEdge, ...]
    module_sources: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    direct_file_owners: Mapping[Path, str] = field(default_factory=dict)


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


_SCHEMA_FILES = {
    "module": "module.schema.json",
    "behavioral_source": "behavioral-source.schema.json",
}


def _edge_key(edge: BlueprintEdge) -> tuple[str, str, str, int, str | None]:
    """Return the canonical identity of one graph relationship."""

    return (
        edge.relation,
        edge.source_id,
        edge.target_id,
        edge.required_version,
        (
            edge.target_blueprint_path.as_posix()
            if edge.target_blueprint_path
            else None
        ),
    )


def _descriptor_safe_open_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
    )


def descriptor_safe_open_supported() -> bool:
    """Return whether runtime inputs can be opened without path races."""

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
            final = index == len(relative.parts) - 1
            flags = directory_flags if not final or directory else file_flags
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
                metadata = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
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
                        RuntimeFileBinding(
                            child_path,
                            child_fd,
                            child_metadata.st_mode,
                        )
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


def _positive_version(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BlueprintGraphError(f"{context}: version must be a positive integer")
    return value


def _resolve_locator(
    module_root: Path,
    locator: object,
    context: str,
    repo_root: Path,
) -> Path:
    if not isinstance(locator, dict):
        raise BlueprintGraphError(f"{context}: blueprint locator must be a mapping")
    base = locator.get("base")
    raw_path = locator.get("path")
    if base not in {"module-root", "repository-root"}:
        raise BlueprintGraphError(
            f"{context}: unsupported blueprint locator base {base!r}"
        )
    if not isinstance(raw_path, str) or not raw_path:
        raise BlueprintGraphError(
            f"{context}: blueprint locator path must be non-empty"
        )
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise BlueprintGraphError(
            f"{context}: locator path must be relative without parent traversal"
        )
    root = module_root if base == "module-root" else repo_root
    candidate = root / relative_path
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{context}: locator must resolve under {base}"
        ) from exc
    return candidate


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


def _load_schema_validator(schema_path: Path) -> jsonschema.Draft7Validator:
    """Read a concrete v4 schema bundle through the shared confined reader."""

    schema_path = Path(os.path.abspath(schema_path))
    schema_root = schema_path.parent
    repo_root = schema_root.parent.parent
    try:
        documents: dict[str, dict[str, Any]] = {}
        for name in sorted(
            name
            for name in os.listdir(schema_root)
            if name.endswith(".schema.json")
        ):
            child_path = schema_root / name
            try:
                document = json.loads(
                    read_regular_file_bytes(
                        child_path,
                        allowed_root=repo_root,
                        allow_non_atomic=False,
                    ).decode("utf-8")
                )
                if not isinstance(document, dict):
                    raise TypeError("schema top level must be a mapping")
                documents[name] = document
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
                raise BlueprintSchemaError(
                    child_path,
                    "$",
                    f"cannot load schema: {exc}",
                ) from exc
        try:
            selected = documents[schema_path.name]
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


def _declaration_schema_errors(
    blueprint_path: Path,
    declaration: dict[str, Any],
    schema_root: Path,
    validators: dict[str, jsonschema.Draft7Validator],
) -> tuple[BlueprintSchemaError, ...]:
    schema_version = declaration.get("schema_version")
    node_type = declaration.get("node_type")
    if schema_version != 4:
        raise BlueprintGraphError(
            f"{blueprint_path}: repository graph requires schema_version 4"
        )
    try:
        schema_name = _SCHEMA_FILES[node_type]
    except (KeyError, TypeError) as exc:
        raise BlueprintGraphError(
            f"{blueprint_path}: unsupported typed node type {node_type!r} "
            "for schema version 4"
        ) from exc
    validator = validators.get(schema_name)
    if validator is None:
        validator = _load_schema_validator(Path(schema_root) / schema_name)
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


def _is_forbidden_content_artifact(path: Path) -> bool:
    name = path.name
    return (
        name == "blueprint.yaml"
        or name.endswith(".blueprint.yaml")
        or ".certificates" in path.parts
    )


def _regular_files_beneath(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
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
    """Resolve the regular files exclusively owned by one v4 node."""

    if node.declaration.get("schema_version") != 4:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content resolution requires schema_version 4"
        )
    repo_root = Path(os.path.abspath(repo_root))
    owner_root = Path(os.path.abspath(node.skill_root))
    try:
        owner_root.relative_to(repo_root)
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content ownership root must be inside the repository"
        ) from exc

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
                f"{node.blueprint_path}: invalid content regex "
                f"{raw_pattern!r}: {exc}"
            ) from exc
        matches = {
            path
            for path, relative in relative_candidates.items()
            if pattern.fullmatch(relative) is not None
        }
        if not matches:
            raise BlueprintGraphError(
                f"{node.blueprint_path}: content pattern "
                f"{raw_pattern!r} matched no files"
            )
        matched_paths.update(matches)

    for path in sorted(matched_paths):
        if _is_forbidden_content_artifact(path):
            raise BlueprintGraphError(
                f"{node.blueprint_path}: content cannot include a blueprint "
                f"or certification artifact: {path}"
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
                f"{node.blueprint_path}: cannot infer repository root "
                "from node ownership"
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


def _v4_node_from_document(document: Any) -> BlueprintNode:
    declaration = dict(document.declaration)
    node_id = declaration.get("id")
    node_type = declaration.get("node_type")
    if not isinstance(node_id, str) or not node_id:
        raise BlueprintGraphError(
            f"{document.path}: v4 blueprint requires a non-empty id"
        )
    if node_type not in _SCHEMA_FILES:
        raise BlueprintGraphError(
            f"{document.path}: unsupported typed node type {node_type!r}"
        )
    gateway = declaration.get("gateway")
    gateway_path = None
    if isinstance(gateway, dict) and isinstance(gateway.get("path"), str):
        gateway_path = document.owner_root / gateway["path"]
    return BlueprintNode(
        node_id=node_id,
        node_type=node_type,
        version=_positive_version(declaration.get("version"), str(document.path)),
        skill_root=document.owner_root,
        blueprint_path=document.path,
        gateway_path=gateway_path,
        declaration=declaration,
    )


def load_module_blueprint(
    repo_root: Path,
    module_root: Path,
    *,
    schema_root: Path | None = None,
) -> BlueprintNode:
    """Load and validate one exact v4 module marker without scanning siblings."""

    repository = Path(os.path.abspath(repo_root))
    module = Path(os.path.abspath(module_root))
    try:
        module.relative_to(repository)
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{module_root}: module root must be inside repository {repo_root}"
        ) from exc

    marker = module / "blueprint.yaml"
    binding: RuntimeFileBinding | None = None
    try:
        binding = open_runtime_file(marker, module, repository)
        loaded = yaml.load(
            binding.read_bytes().decode("utf-8"),
            Loader=_StrictBlueprintLoader,
        )
        if not isinstance(loaded, dict):
            raise ValueError("document root must be a mapping")
        declaration = _normalize_json(loaded)
        assert isinstance(declaration, dict)
    except BlueprintGraphError:
        raise
    except (UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise BlueprintGraphError(
            f"{marker}: cannot load module blueprint: {exc}"
        ) from exc
    finally:
        if binding is not None:
            binding.close()

    node_type = declaration.get("node_type")
    node_id = declaration.get("id")
    document = BlueprintDocument(
        path=marker,
        relative_path=marker.relative_to(repository),
        owner_root=module,
        declaration=declaration,
        node_type=node_type if isinstance(node_type, str) else None,
        node_id=node_id if isinstance(node_id, str) else None,
    )

    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else repository / "references" / "blueprint"
    )
    if not (selected_schema_root / "module.schema.json").is_file():
        selected_schema_root = (
            Path(__file__).resolve().parents[3]
            / "references"
            / "blueprint"
        )
    errors = _declaration_schema_errors(
        marker,
        dict(declaration),
        selected_schema_root,
        {},
    )
    if errors:
        raise errors[0]

    node = _v4_node_from_document(document)
    if node.node_type != "module":
        raise BlueprintGraphError(f"{marker}: exact module marker must declare node_type module")
    if node.node_id != module.name:
        raise BlueprintGraphError(
            f"{marker}: module id {node.node_id!r} must match its directory"
        )
    for path in authored_node_input_paths(node, repository):
        validate_runtime_file_path(path, module, repository)
    return node


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


def _require_platform_compatibility(
    source: BlueprintNode,
    target: BlueprintNode,
    *,
    context: str,
) -> None:
    source_support = source.declaration.get("platform_support")
    target_support = target.declaration.get("platform_support")
    if not isinstance(source_support, Mapping) or not isinstance(
        target_support,
        Mapping,
    ):
        return
    for platform, supported in source_support.items():
        if supported is True and target_support.get(platform) is not True:
            raise BlueprintGraphError(
                f"{context}: target {target.node_id} does not support "
                f"required platform {platform!r}"
            )


def _reject_certification_cycles(
    node_ids: set[str],
    edges: tuple[CertificationEdge, ...],
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


def _v4_local_ids(
    values: object,
    *,
    context: str,
) -> tuple[set[str], list[Mapping[str, Any]]]:
    entries = values if isinstance(values, list) else []
    identifiers: set[str] = set()
    mappings: list[Mapping[str, Any]] = []
    for value in entries:
        if not isinstance(value, Mapping):
            continue
        mappings.append(value)
        identifier = value.get("id")
        if not isinstance(identifier, str):
            continue
        if identifier in identifiers:
            raise BlueprintGraphError(
                f"{context}: duplicate local id {identifier!r}"
            )
        identifiers.add(identifier)
    return identifiers, mappings


def _require_v4_local_ref(
    value: object,
    valid: set[str],
    *,
    context: str,
    kind: str,
) -> None:
    if isinstance(value, str) and value not in valid:
        raise BlueprintGraphError(f"{context}: unknown {kind} {value!r}")


def _walk_v4_contract(
    value: object,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], str, object]]:
    found: list[tuple[tuple[str, ...], str, object]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            field = str(key)
            found.append((path, field, child))
            found.extend(_walk_v4_contract(child, (*path, field)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_v4_contract(child, (*path, str(index))))
    return found


def _validate_v4_internal_path(value: object, *, context: str) -> None:
    if not isinstance(value, str):
        return
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or re.match(r"^[A-Za-z]:[/\\]", value) is not None
    ):
        raise BlueprintGraphError(
            f"{context}: path must be relative without parent traversal"
        )


def _v4_authority_claims(
    modules: Mapping[str, BlueprintNode],
) -> tuple[tuple[str, str, str, re.Pattern[str] | None], ...]:
    claims: list[tuple[str, str, str, re.Pattern[str] | None]] = []
    for module_id, module in sorted(modules.items()):
        authority = module.declaration.get("authority")
        raw_claims = (
            authority.get("owns_filesystem")
            if isinstance(authority, Mapping)
            else None
        )
        if not isinstance(raw_claims, list):
            continue
        for index, claim in enumerate(raw_claims):
            if not isinstance(claim, Mapping):
                continue
            match = claim.get("match")
            path = claim.get("path")
            if not isinstance(match, str) or not isinstance(path, str):
                continue
            compiled: re.Pattern[str] | None = None
            if match == "regex":
                try:
                    compiled = re.compile(path)
                except re.error as exc:
                    raise BlueprintGraphError(
                        f"{module.blueprint_path}: "
                        f"authority.owns_filesystem[{index}] "
                        f"has invalid regex {path!r}: {exc}"
                    ) from exc
            claims.append((module_id, match, path, compiled))
    return tuple(claims)


def _validate_v4_interface_contract(
    interface_id: str,
    declaration: Mapping[str, Any],
    *,
    module_id: str,
    authority_claims: tuple[
        tuple[str, str, str, re.Pattern[str] | None],
        ...,
    ],
) -> None:
    contract = declaration.get("contract")
    if not isinstance(contract, Mapping):
        return
    arguments = contract.get("arguments")
    argument_ids = set(arguments) if isinstance(arguments, Mapping) else set()
    precondition_ids, preconditions = _v4_local_ids(
        contract.get("preconditions"),
        context=f"{interface_id}.preconditions",
    )
    output_ids, outputs = _v4_local_ids(
        contract.get("outputs"),
        context=f"{interface_id}.outputs",
    )
    outcome_ids, outcomes = _v4_local_ids(
        contract.get("outcomes"),
        context=f"{interface_id}.outcomes",
    )
    helper_ids, helpers = _v4_local_ids(
        contract.get("helpers"),
        context=f"{interface_id}.helpers",
    )
    execution = contract.get("execution")
    effect_ids, effects = _v4_local_ids(
        execution.get("effects") if isinstance(execution, Mapping) else [],
        context=f"{interface_id}.execution.effects",
    )
    direct_io = contract.get("direct_io")
    io_entries: list[tuple[str, Mapping[str, Any]]] = []
    io_ids: set[str] = set()
    write_ids: set[str] = set()
    effect_io_ids: set[str] = set()
    if isinstance(direct_io, Mapping):
        for section in ("reads", "writes", "network"):
            entries = direct_io.get(section)
            if not isinstance(entries, list):
                continue
            section_ids: set[str] = set()
            for index, entry in enumerate(entries):
                if not isinstance(entry, Mapping):
                    continue
                io_id = entry.get("id")
                if isinstance(io_id, str):
                    if io_id in section_ids:
                        raise BlueprintGraphError(
                            f"{interface_id}.contract.direct_io.{section}: "
                            f"duplicate local id {io_id!r}"
                        )
                    section_ids.add(io_id)
                    io_ids.add(io_id)
                    if section == "writes":
                        write_ids.add(io_id)
                    if section in {"writes", "network"}:
                        effect_io_ids.add(io_id)
                io_entries.append((f"{section}[{index}]", entry))

    reference_fields = {
        "argument_ref": (argument_ids, "argument"),
        "page_argument_ref": (argument_ids, "argument"),
        "expected_from_argument": (argument_ids, "argument"),
        "direct_io_ref": (io_ids, "direct-I/O"),
        "helper_ref": (helper_ids, "helper"),
        "values_from_helper": (helper_ids, "helper"),
        "output_ref": (output_ids, "output"),
        "cursor_output_ref": (output_ids, "output"),
        "outcome_ref": (outcome_ids, "outcome"),
        "unmet_outcome": (outcome_ids, "outcome"),
        "unattended_outcome": (outcome_ids, "outcome"),
        "startup_failure_outcome": (outcome_ids, "outcome"),
    }
    for path, field, value in _walk_v4_contract(contract):
        selected = reference_fields.get(field)
        if (
            selected is None
            or not isinstance(value, str)
            or field == "output_ref"
            and "helpers" in path
        ):
            continue
        valid, label = selected
        _require_v4_local_ref(
            value,
            valid,
            context=f"{interface_id}.{'.'.join((*path, field))}",
            kind=label,
        )

    for output in outputs:
        output_id = output.get("id")
        ref = output.get("direct_io_ref")
        if isinstance(ref, str) and ref in io_ids and ref not in write_ids:
            raise BlueprintGraphError(
                f"{interface_id}: output {output_id!r} direct-I/O "
                f"{ref!r} must be a write"
            )

    for outcome in outcomes:
        outcome_id = outcome.get("id")
        for output_ref in outcome.get("outputs", []):
            _require_v4_local_ref(
                output_ref,
                output_ids,
                context=f"{interface_id}: outcome {outcome_id!r}",
                kind="output",
            )
        for effect_ref in outcome.get("effects", []):
            _require_v4_local_ref(
                effect_ref,
                effect_ids,
                context=f"{interface_id}: outcome {outcome_id!r}",
                kind="effect",
            )

    for helper in helpers:
        helper_id = helper.get("id")
        route = helper.get("route")
        if isinstance(route, Mapping):
            route_targets = {
                "argument-enum": (argument_ids, "argument"),
                "precondition": (precondition_ids, "precondition"),
                "output": (output_ids, "output"),
            }
            target = route_targets.get(route.get("kind"))
            if target is not None:
                valid, label = target
                _require_v4_local_ref(
                    route.get("target"),
                    valid,
                    context=f"{interface_id}: helper {helper_id!r} route",
                    kind=label,
                )
        for field in ("empty", "failure"):
            behavior = helper.get(field)
            if isinstance(behavior, Mapping):
                _require_v4_local_ref(
                    behavior.get("outcome"),
                    outcome_ids,
                    context=f"{interface_id}: helper {helper_id!r} {field}",
                    kind="outcome",
                )

    if isinstance(execution, Mapping):
        for effect in effects:
            effect_id = effect.get("id")
            effect_ref = effect.get("direct_io_ref")
            if (
                isinstance(effect_ref, str)
                and effect_ref in io_ids
                and effect_ref not in effect_io_ids
            ):
                raise BlueprintGraphError(
                    f"{interface_id}: effect {effect_id!r} direct-I/O "
                    f"{effect_ref!r} must be a write or network action"
                )
            for outcome_ref in effect.get("may_occur_in_outcomes", []):
                _require_v4_local_ref(
                    outcome_ref,
                    outcome_ids,
                    context=f"{interface_id}: effect {effect_id!r}",
                    kind="outcome",
                )

    outcome_effects = {
        (str(outcome.get("id")), effect_ref)
        for outcome in outcomes
        for effect_ref in outcome.get("effects", [])
    }
    effect_outcomes = {
        (outcome_ref, str(effect.get("id")))
        for effect in effects
        for outcome_ref in effect.get("may_occur_in_outcomes", [])
    }
    if not outcome_effects <= effect_outcomes:
        raise BlueprintGraphError(
            f"{interface_id}: outcome effects must be permitted by their effect"
        )
    outcome_classes = {
        str(outcome.get("id")): outcome.get("class") for outcome in outcomes
    }
    undeclared_success_effects = {
        pair
        for pair in effect_outcomes - outcome_effects
        if outcome_classes.get(pair[0]) in {"success", "no-op"}
    }
    if undeclared_success_effects:
        raise BlueprintGraphError(
            f"{interface_id}: successful outcome/effect references "
            "must be exact inverses"
        )

    filesystem_media = {
        "local-filesystem",
        "repository-filesystem",
        "home-filesystem",
        "temporary-filesystem",
    }
    for entry_context, entry in io_entries:
        path = entry.get("path")
        _validate_v4_internal_path(
            path,
            context=f"{interface_id}.contract.direct_io.{entry_context}",
        )
        if (
            entry_context.startswith("writes[")
            and entry.get("medium") in filesystem_media
            and entry.get("path_match", "exact") == "exact"
            and isinstance(path, str)
        ):
            for owner_id, match, owned_path, pattern in authority_claims:
                if owner_id == module_id:
                    continue
                claimed = (
                    path == owned_path
                    if match == "exact"
                    else pattern is not None
                    and pattern.fullmatch(path) is not None
                )
                if claimed:
                    raise BlueprintGraphError(
                        f"{interface_id}: module {module_id!r} write "
                        f"{path!r} is owned by {owner_id}"
                    )


def _load_v4_repository_blueprint_graph(
    root: Path,
    documents: tuple[Any, ...],
    *,
    schema_root: Path,
) -> RepositoryBlueprintGraph:
    validators: dict[str, jsonschema.Draft7Validator] = {}
    nodes: dict[str, BlueprintNode] = {}
    for document in documents:
        errors = _declaration_schema_errors(
            document.path,
            dict(document.declaration),
            schema_root,
            validators,
        )
        if errors:
            raise errors[0]
        node = _v4_node_from_document(document)
        existing = nodes.get(node.node_id)
        if existing is not None:
            raise BlueprintGraphError(
                f"duplicate node id {node.node_id!r}: "
                f"{existing.blueprint_path} and {node.blueprint_path}"
            )
        nodes[node.node_id] = node

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
        raise BlueprintGraphError(
            "version 4 repository graph requires at least one module"
        )
    if len(modules) + len(sources) != len(nodes):
        raise BlueprintGraphError(
            "version 4 repository graph permits only module "
            "and behavioral_source nodes"
        )

    module_sources: dict[str, tuple[str, ...]] = {}
    source_modules: dict[str, str] = {}
    for module_id, module in sorted(modules.items()):
        if module.skill_root.name != module_id:
            raise BlueprintGraphError(
                f"{module.blueprint_path}: module id {module_id!r} "
                "must match its directory"
            )
        raw_sources = module.declaration.get("sources")
        if not isinstance(raw_sources, dict):
            raise BlueprintGraphError(
                f"{module.blueprint_path}: sources must be a mapping"
            )
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
                    f"{module.blueprint_path}: unresolved contained "
                    f"source {source_id!r}"
                )
            if Path(os.path.abspath(locator_path)) != Path(
                os.path.abspath(source.blueprint_path)
            ):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: source {source_id!r} locator "
                    "does not identify its canonical blueprint"
                )
            previous_module = source_modules.get(source_id)
            if previous_module is not None:
                raise BlueprintGraphError(
                    f"source {source_id!r} is contained by both "
                    f"{previous_module} and {module_id}"
                )
            if source.skill_root != module.skill_root:
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: contained source must be "
                    f"inside module {module_id}"
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

    authority_claims = _v4_authority_claims(modules)
    source_interfaces: dict[
        str,
        tuple[BlueprintNode, Mapping[str, JsonValue]],
    ] = {}
    for source_id, source in sorted(sources.items()):
        raw_interfaces = source.declaration.get("interfaces")
        if not isinstance(raw_interfaces, dict):
            raise BlueprintGraphError(
                f"{source.blueprint_path}: interfaces must be a mapping"
            )
        for interface_id, declaration in sorted(raw_interfaces.items()):
            if not isinstance(interface_id, str) or not isinstance(
                declaration,
                dict,
            ):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: "
                    "invalid source interface declaration"
                )
            expected_prefix = f"{source_id}.interface."
            if not interface_id.startswith(expected_prefix):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: interface {interface_id!r} "
                    f"must use source namespace {expected_prefix!r}"
                )
            if interface_id in source_interfaces:
                raise BlueprintGraphError(
                    f"duplicate source interface {interface_id!r}"
                )
            _validate_v4_interface_contract(
                interface_id,
                declaration,
                module_id=source_modules[source_id],
                authority_claims=authority_claims,
            )
            source_interfaces[interface_id] = (source, declaration)

    exports: dict[str, InterfaceExport] = {}
    for module_id, module in sorted(modules.items()):
        raw_exports = module.declaration.get("exports")
        if not isinstance(raw_exports, dict):
            raise BlueprintGraphError(
                f"{module.blueprint_path}: exports must be a mapping"
            )
        for export_id, export_declaration in sorted(raw_exports.items()):
            if not isinstance(export_id, str) or not isinstance(
                export_declaration,
                dict,
            ):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: invalid export declaration"
                )
            expected_prefix = f"{module_id}.interface."
            if not export_id.startswith(expected_prefix):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} must use "
                    f"module namespace {expected_prefix!r}"
                )
            if export_id in exports:
                raise BlueprintGraphError(f"duplicate export {export_id!r}")
            source_interface_id = export_declaration.get("source_interface")
            if not isinstance(source_interface_id, str):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} "
                    "requires source_interface"
                )
            try:
                source, interface_declaration = source_interfaces[
                    source_interface_id
                ]
            except KeyError as exc:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} targets "
                    f"unknown source interface {source_interface_id!r}"
                ) from exc
            if source_modules[source.node_id] != module_id:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} "
                    "must bind a contained source interface"
                )
            exports[export_id] = InterfaceExport(
                interface_id=export_id,
                version=_positive_version(
                    interface_declaration.get("version"),
                    source_interface_id,
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
                    "contains-source",
                    module_id,
                    source_id,
                    source.version,
                    source.blueprint_path,
                )
            )

    for source_id, source in sorted(sources.items()):
        raw_dependencies = source.declaration.get("dependencies", [])
        if not isinstance(raw_dependencies, list):
            raise BlueprintGraphError(
                f"{source.blueprint_path}: dependencies must be a list"
            )
        for index, dependency in enumerate(raw_dependencies):
            if not isinstance(dependency, dict):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: "
                    f"dependencies[{index}] must be a mapping"
                )
            target_id = dependency.get("source")
            if not isinstance(target_id, str) or target_id not in sources:
                raise BlueprintGraphError(
                    f"{source.node_id}: unresolved behavioral source {target_id!r}"
                )
            target = sources[target_id]
            version = _positive_version(
                dependency.get("version"),
                f"{source.node_id}.dependencies[{index}]",
            )
            if target.version != version:
                raise BlueprintGraphError(
                    f"{source.node_id}: pins {target_id} version {version}, "
                    f"but target version is {target.version}"
                )
            locator_path = _resolve_locator(
                source.skill_root,
                dependency.get("blueprint"),
                f"{source.blueprint_path}:dependencies[{index}]",
                root,
            )
            if Path(os.path.abspath(locator_path)) != Path(
                os.path.abspath(target.blueprint_path)
            ):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: dependency locator for "
                    f"{target_id!r} does not identify its canonical blueprint"
                )
            node_edges.append(
                BlueprintEdge(
                    "uses-source",
                    source_id,
                    target_id,
                    version,
                    target.blueprint_path,
                )
            )
            certification_edges.append(
                CertificationEdge(
                    "uses-source",
                    source_id,
                    target_id,
                    version,
                )
            )

        raw_uses = source.declaration.get("uses_interfaces", [])
        if not isinstance(raw_uses, list):
            raise BlueprintGraphError(
                f"{source.blueprint_path}: uses_interfaces must be a list"
            )
        uses: list[tuple[str, int]] = []
        for index, use in enumerate(raw_uses):
            if not isinstance(use, dict):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: "
                    f"uses_interfaces[{index}] must be a mapping"
                )
            target_id = use.get("interface")
            version = _positive_version(
                use.get("version"),
                f"{source.node_id}.uses_interfaces[{index}]",
            )
            if not isinstance(target_id, str):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: "
                    f"uses_interfaces[{index}] requires interface"
                )
            if target_id in source_interfaces:
                target_source, target_declaration = source_interfaces[target_id]
                if source_modules[target_source.node_id] != source_modules[source_id]:
                    raise BlueprintGraphError(
                        f"{source.node_id}: private interface {target_id!r} "
                        "cannot be used cross-module"
                    )
                actual_version = _positive_version(
                    target_declaration.get("version"),
                    target_id,
                )
                if actual_version != version:
                    raise BlueprintGraphError(
                        f"{source.node_id}: pins {target_id} version {version}, "
                        f"but target version is {actual_version}"
                    )
                relation = "uses-private-interface"
                target_node_id = target_source.node_id
            elif target_id in exports:
                export = exports[target_id]
                if export.version != version:
                    raise BlueprintGraphError(
                        f"{source.node_id}: pins {target_id} version {version}, "
                        f"but target version is {export.version}"
                    )
                caller_module = source_modules[source_id]
                access = (
                    export.export_declaration.get("access")
                    if isinstance(export.export_declaration, Mapping)
                    else None
                )
                if not isinstance(access, Mapping):
                    raise BlueprintGraphError(
                        f"{target_id}: export access is missing"
                    )
                allowed = access.get("allowed_callers", [])
                if (
                    caller_module != export.module_node_id
                    and access.get("allow_all_modules") is not True
                    and caller_module not in allowed
                ):
                    raise BlueprintGraphError(
                        f"{source.node_id}: caller module "
                        f"{caller_module!r} is not allowed by {target_id}"
                    )
                relation = "uses-export"
                target_node_id = export.source_node_id
                assert target_node_id is not None
                _require_platform_compatibility(
                    source,
                    sources[target_node_id],
                    context=source.node_id,
                )
            else:
                raise BlueprintGraphError(
                    f"{source.node_id}: unresolved interface {target_id!r}"
                )
            uses.append((target_id, version))
            node_edges.append(
                BlueprintEdge(
                    relation,
                    source_id,
                    target_id,
                    version,
                )
            )
            certification_target = sources[target_node_id]
            certification_edges.append(
                CertificationEdge(
                    relation,
                    source_id,
                    target_node_id,
                    certification_target.version,
                )
            )
        interface_uses_by_source[source_id] = tuple(uses)

    export_edges: list[ExportDependencyEdge] = []
    helper_edges: list[HelperEdge] = []
    for export_id, export in sorted(exports.items()):
        assert export.source_node_id is not None
        direct_uses = set(interface_uses_by_source[export.source_node_id])
        for target_id, version in sorted(direct_uses):
            if target_id in exports:
                export_edges.append(
                    ExportDependencyEdge(export_id, target_id, version)
                )
        contract = export.declaration.get("contract")
        raw_helpers = (
            contract.get("helpers", [])
            if isinstance(contract, Mapping)
            else []
        )
        if not isinstance(raw_helpers, list):
            raise BlueprintGraphError(
                f"{export_id}: contract.helpers must be a list"
            )
        for index, helper in enumerate(raw_helpers):
            if not isinstance(helper, dict):
                raise BlueprintGraphError(
                    f"{export_id}: helpers[{index}] must be a mapping"
                )
            helper_id = helper.get("id")
            target_id = helper.get("interface")
            version = _positive_version(
                helper.get("version"),
                f"{export_id}.helpers[{index}]",
            )
            if not isinstance(helper_id, str) or not isinstance(target_id, str):
                raise BlueprintGraphError(
                    f"{export_id}: helper requires id and interface"
                )
            if (target_id, version) not in direct_uses:
                raise BlueprintGraphError(
                    f"{export_id}: helper {helper_id!r} target must be "
                    "in the source's effective direct interface set"
                )
            helper_edges.append(
                HelperEdge(
                    export_id,
                    helper_id,
                    target_id,
                    version,
                    helper,
                )
            )

    module_content: dict[str, set[Path]] = {}
    source_content: dict[str, set[Path]] = {}
    blueprint_paths = {
        Path(os.path.abspath(node.blueprint_path)) for node in nodes.values()
    }
    for module_id, module in sorted(modules.items()):
        paths = {
            Path(os.path.abspath(path))
            for path in resolved_node_content_paths(module, root)
        }
        if paths & blueprint_paths:
            raise BlueprintGraphError(
                f"{module.blueprint_path}: content cannot include blueprint files"
            )
        module_content[module_id] = paths
    for source_id, source in sorted(sources.items()):
        paths = {
            Path(os.path.abspath(path))
            for path in resolved_node_content_paths(source, root)
        }
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
                    f"{sources[source_id].blueprint_path}: source content "
                    f"must be contained by module {module_id}: "
                    f"{sorted(str(path) for path in missing)}"
                )
            for path in sorted(source_content[source_id]):
                previous = seen_source_paths.get(path)
                if previous is not None:
                    raise BlueprintGraphError(
                        f"{sources[source_id].blueprint_path}: sibling sources "
                        f"{previous} and {source_id} overlap at {path}"
                    )
                seen_source_paths[path] = source_id
                direct_file_owners[path] = source_id
        for path in sorted(module_content[module_id] - set(seen_source_paths)):
            direct_file_owners[path] = module_id

    seen_certification_edges: set[tuple[str, str, str, int | None]] = set()
    unique_certification_edges: list[CertificationEdge] = []
    for edge in certification_edges:
        key = (
            edge.relation,
            edge.source_node_id,
            edge.target_node_id,
            edge.target_version,
        )
        if key not in seen_certification_edges:
            seen_certification_edges.add(key)
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
        node_edges=tuple(sorted(node_edges, key=_edge_key)),
        exports=dict(sorted(exports.items())),
        export_edges=export_edge_tuple,
        helper_edges=tuple(
            sorted(
                helper_edges,
                key=lambda edge: (
                    edge.source_export_id,
                    edge.local_helper_id,
                ),
            )
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
    """Load the complete v4 repository inventory into one graph."""

    root = Path(repo_root).resolve()
    documents = tuple(iter_inventory_blueprints(root))
    for document in documents:
        if document.declaration.get("schema_version") != 4:
            raise BlueprintGraphError(
                f"{document.path}: repository graph requires schema_version 4"
            )

    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else root / "references" / "blueprint"
    )
    if not (selected_schema_root / "module.schema.json").is_file():
        selected_schema_root = (
            Path(__file__).resolve().parents[3]
            / "references"
            / "blueprint"
        )
    return _load_v4_repository_blueprint_graph(
        root,
        documents,
        schema_root=selected_schema_root,
    )


def resolve_export(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
    version: int | None = None,
) -> tuple[BlueprintNode, BlueprintNode, InterfaceExport]:
    """Resolve one public export to its module and behavioral source."""

    if interface_id in graph.nodes and graph.nodes[interface_id].node_type == "module":
        raise BlueprintGraphError(f"module id {interface_id!r} is not callable")
    try:
        export = graph.exports[interface_id]
    except KeyError as exc:
        raise BlueprintGraphError(f"unknown export {interface_id!r}") from exc
    if version is not None and export.version != version:
        raise BlueprintGraphError(
            f"{interface_id}: requested version {version}, "
            f"but target version is {export.version}"
        )
    if export.source_node_id is None:
        raise BlueprintGraphError(
            f"{interface_id}: export has no behavioral-source binding"
        )
    source = graph.nodes.get(export.source_node_id)
    if source is None or source.node_type != "behavioral_source":
        raise BlueprintGraphError(
            f"{interface_id}: export source "
            f"{export.source_node_id!r} is unavailable"
        )
    return graph.nodes[export.module_node_id], source, export


def runtime_authority_for_export(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
) -> tuple[str, ...]:
    """Return the selected export's direct callable-interface authority."""

    resolve_export(graph, interface_id)
    return tuple(
        sorted(
            edge.target_interface_id
            for edge in graph.export_edges
            if edge.source_export_id == interface_id
        )
    )

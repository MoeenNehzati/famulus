"""Load versioned module and behavioral-source repository graphs."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable, Mapping, Sequence

import jsonschema
import yaml

from ..common.atomic_files import AtomicWriteError, read_regular_file_bytes
from .inventory import (
    BlueprintDocument,
    BlueprintInventoryError,
    JsonValue,
    _normalize_json,
    _StrictBlueprintLoader,
    collect_blueprints,
    iter_blueprints as iter_inventory_blueprints,
)
from officina.configuration.configured_schema import (
    ConfiguredSchemaError,
    configured_validator,
    schema_requires_configuration,
)
from ..common.repository_paths import (
    RepositoryPathError,
    equivalent_root_relative_path,
    repository_relative_path,
)


class BlueprintGraphError(ValueError):
    """Raised when blueprint files cannot form a coherent repository graph.

    Intent
    ------
    Keep exception context and invariants together as a BlueprintGraphError contract derived from ValueError.

    Rationale
    ---------
    Callers use this type to carry exception context and invariants across validation and graph assembly while retaining the semantics provided by ValueError.

    Pseudocode
    ----------
    - set blueprintgrapherror_contract = declared fields and invariants

    Wraps
    -----
    - none
    """


class BlueprintSchemaError(BlueprintGraphError):
    """Raised when a graph node fails its concrete JSON Schema.

    Intent
    ------
    Keep exception context and invariants together as a BlueprintSchemaError contract derived from BlueprintGraphError.

    Rationale
    ---------
    Callers use this type to carry exception context and invariants across validation and graph assembly while retaining the semantics provided by BlueprintGraphError.

    Pseudocode
    ----------
    - set blueprintschemaerror_contract = declared fields and invariants

    Wraps
    -----
    - none
    """

    def __init__(self, blueprint_path: Path, json_path: str, message: str) -> None:
        """Transform blueprint path, json path, message into the init result used by the blueprint graph.

        Intent
        ------
        Use blueprint path, json path, message to transform blueprint path, json path, message into the init result used by the blueprint graph.

        Rationale
        ---------
        The operation combines blueprint path, json path, message through __init__, super and a single state transition, making the resulting init behavior explicit across 0 conditional branches.

        Pseudocode
        ----------
        - set init_inputs = blueprint path, json path, message
        - return none

        Wraps
        -----
        - none
        """
        self.blueprint_path = blueprint_path
        self.json_path = json_path
        self.schema_message = message
        super().__init__(f"{blueprint_path}: schema error at {json_path}: {message}")


@dataclass(frozen=True)
class BlueprintNode:
    """Store blueprintnode state.

    Intent
    ------
    Keep node_id, node_type, version, module_root, blueprint_path together as a BlueprintNode contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry node_id, node_type, version, module_root, blueprint_path across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set blueprintnode_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    node_id: str
    node_type: str
    version: int
    module_root: Path
    blueprint_path: Path
    gateway_path: Path | None
    declaration: dict[str, Any]

@dataclass(frozen=True)
class BlueprintEdge:
    """Store blueprintedge state.

    Intent
    ------
    Keep relation, source_id, target_id, required_version, target_blueprint_path together as a BlueprintEdge contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry relation, source_id, target_id, required_version, target_blueprint_path across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set blueprintedge_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    relation: str
    source_id: str
    target_id: str
    required_version: int
    target_blueprint_path: Path | None = None


@dataclass(frozen=True)
class InterfaceExport:
    """Store interfaceexport state.

    Intent
    ------
    Keep interface_id, version, local_name, module_node_id, declaration together as a InterfaceExport contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry interface_id, version, local_name, module_node_id, declaration across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set interfaceexport_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    interface_id: str
    version: int
    local_name: str
    module_node_id: str
    declaration: Mapping[str, JsonValue]
    source_node_id: str | None = None
    source_interface_id: str | None = None
    export_declaration: Mapping[str, JsonValue] | None = None
    terminal_interface_id: str | None = None
    terminal_module_node_id: str | None = None


@dataclass(frozen=True)
class RoutedInterface:
    """Store routedinterface state.

    Intent
    ------
    Keep route_owner_id, child_module_id, interface_id, version, terminal_module_id together as a RoutedInterface contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry route_owner_id, child_module_id, interface_id, version, terminal_module_id across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set routedinterface_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    route_owner_id: str
    child_module_id: str
    interface_id: str
    version: int
    terminal_module_id: str
    terminal_module_version: int


@dataclass(frozen=True)
class NamespaceRoute:
    """Store namespaceroute state.

    Intent
    ------
    Keep route_owner_id, child_module_id, child_version, declaration, materialized_interfaces together as a NamespaceRoute contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry route_owner_id, child_module_id, child_version, declaration, materialized_interfaces across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set namespaceroute_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    route_owner_id: str
    child_module_id: str
    child_version: int
    declaration: Mapping[str, JsonValue]
    materialized_interfaces: tuple[RoutedInterface, ...]


@dataclass(frozen=True)
class ExportDependencyEdge:
    """Store exportdependencyedge state.

    Intent
    ------
    Keep source_export_id, target_interface_id, target_version together as a ExportDependencyEdge contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry source_export_id, target_interface_id, target_version across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set exportdependencyedge_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    source_export_id: str
    target_interface_id: str
    target_version: int


@dataclass(frozen=True)
class HelperEdge:
    """Store helperedge state.

    Intent
    ------
    Keep source_export_id, local_helper_id, target_interface_id, target_version, binding together as a HelperEdge contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry source_export_id, local_helper_id, target_interface_id, target_version, binding across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set helperedge_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    source_export_id: str
    local_helper_id: str
    target_interface_id: str
    target_version: int
    binding: Mapping[str, JsonValue]


@dataclass(frozen=True)
class CertificationEdge:
    """Store certificationedge state.

    Intent
    ------
    Keep relation, source_node_id, target_node_id, target_version together as a CertificationEdge contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry relation, source_node_id, target_node_id, target_version across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set certificationedge_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    relation: str
    source_node_id: str
    target_node_id: str
    target_version: int | None = None


@dataclass(frozen=True)
class RepositoryBlueprintGraph:
    """Store repositoryblueprintgraph state.

    Intent
    ------
    Keep nodes, node_edges, exports, export_edges, helper_edges together as a RepositoryBlueprintGraph contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry nodes, node_edges, exports, export_edges, helper_edges across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set repositoryblueprintgraph_contract = declared fields and invariants

    Wraps
    -----
    - none
    """
    nodes: Mapping[str, BlueprintNode]
    node_edges: tuple[BlueprintEdge, ...]
    exports: Mapping[str, InterfaceExport]
    export_edges: tuple[ExportDependencyEdge, ...]
    helper_edges: tuple[HelperEdge, ...]
    certification_edges: tuple[CertificationEdge, ...]
    module_sources: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    direct_file_owners: Mapping[Path, str] = field(default_factory=dict)
    schema_version: int = 4
    source_modules: Mapping[str, str] = field(default_factory=dict)
    source_interfaces: Mapping[str, InterfaceExport] = field(default_factory=dict)
    module_parents: Mapping[str, str | None] = field(default_factory=dict)
    module_children: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    module_local_segments: Mapping[str, str] = field(default_factory=dict)
    module_ancestry: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    namespace_routes: Mapping[tuple[str, str], NamespaceRoute] = field(
        default_factory=dict
    )
    routed_interfaces: tuple[RoutedInterface, ...] = ()
    interface_content_paths: Mapping[str, tuple[Path, ...]] = field(
        default_factory=dict
    )
    interface_uses: Mapping[str, tuple[tuple[str, int], ...]] = field(
        default_factory=dict
    )
    setup_requirements: Mapping[str, tuple[tuple[str, int], ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class BlueprintDiagnostic:
    """One non-fatal repository blueprint defect outside a dispatch closure.

    Intent
    ------
    Keep code, message, path together as a BlueprintDiagnostic contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry code, message, path across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set blueprintdiagnostic_contract = declared fields and invariants

    Wraps
    -----
    - none
    """

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class DispatchBlueprintGraph:
    """A canonical graph sufficient for one dispatch plus unrelated warnings.

    Intent
    ------
    Keep graph, diagnostics together as a DispatchBlueprintGraph contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry graph, diagnostics across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set dispatchblueprintgraph_contract = declared fields and invariants

    Wraps
    -----
    - none
    """

    graph: RepositoryBlueprintGraph
    diagnostics: tuple[BlueprintDiagnostic, ...] = ()


class RuntimeFileBinding:
    """An opened regular file whose validation is bound to later use.

    Intent
    ------
    Keep exception context and invariants together as a RuntimeFileBinding contract derived from an immutable record boundary.

    Rationale
    ---------
    Callers use this type to carry exception context and invariants across validation and graph assembly while retaining the semantics provided by an immutable record boundary.

    Pseudocode
    ----------
    - set runtimefilebinding_contract = declared fields and invariants

    Wraps
    -----
    - none
    """

    def __init__(self, path: Path, fd: int, mode: int) -> None:
        """Transform path, fd, mode into the init result used by the blueprint graph.

        Intent
        ------
        Use path, fd, mode to transform path, fd, mode into the init result used by the blueprint graph.

        Rationale
        ---------
        The operation combines path, fd, mode through local state and a single state transition, making the resulting init behavior explicit across 0 conditional branches.

        Pseudocode
        ----------
        - set init_inputs = path, fd, mode
        - return none

        Wraps
        -----
        - none
        """
        self.path = path
        self.fd = fd
        self.mode = mode

    def close(self) -> None:
        """Transform declared fields into the close result used by the blueprint graph.

        Intent
        ------
        Use declared fields to transform declared fields into the close result used by the blueprint graph.

        Rationale
        ---------
        The operation combines declared fields through close and a single state transition, making the resulting close behavior explicit across 1 conditional branches.

        Pseudocode
        ----------
        - set close_inputs = declared fields
        - return none

        Wraps
        -----
        - none
        """
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def read_bytes(self) -> bytes:
        """Transform declared fields into the read bytes result used by the blueprint graph.

        Intent
        ------
        Use declared fields to transform declared fields into the read bytes result used by the blueprint graph.

        Rationale
        ---------
        The operation combines declared fields through lseek, join, BlueprintGraphError and ordered iteration, bounded failure checks, an explicit return value, making the resulting read bytes behavior explicit across 1 conditional branches.

        Pseudocode
        ----------
        - set read_bytes_inputs = declared fields
        - if read_bytes_inputs violate blueprint invariants:
          - raise blueprint graph error
        - for item in read_bytes_inputs:
          - set validated_item = item
        - return read bytes value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming declared fields into the read bytes value."
        """
        if self.fd < 0:
            raise BlueprintGraphError(f"{self.path}: runtime input binding is closed")
        os.lseek(self.fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(self.fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)

    def proc_path(self) -> str:
        """Transform declared fields into the proc path result used by the blueprint graph.

        Intent
        ------
        Use declared fields to transform declared fields into the proc path result used by the blueprint graph.

        Rationale
        ---------
        The operation combines declared fields through BlueprintGraphError, is_dir, Path and bounded failure checks, an explicit return value, making the resulting proc path behavior explicit across 1 conditional branches.

        Pseudocode
        ----------
        - set proc_path_inputs = declared fields
        - if proc_path_inputs violate blueprint invariants:
          - raise blueprint graph error
        - return proc path value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming declared fields into the proc path value."
        """
        if self.fd < 0 or not Path("/proc/self/fd").is_dir():
            raise BlueprintGraphError(
                f"{self.path}: descriptor-backed execution is unavailable on this host"
            )
        return f"/proc/self/fd/{self.fd}"

    def is_effectively_executable(self) -> bool:
        """Transform declared fields into the is effectively executable result used by the blueprint graph.

        Intent
        ------
        Use declared fields to transform declared fields into the is effectively executable result used by the blueprint graph.

        Rationale
        ---------
        The operation combines declared fields through access, BlueprintGraphError, proc_path and bounded failure checks, an explicit return value, making the resulting is effectively executable behavior explicit across 1 conditional branches.

        Pseudocode
        ----------
        - set is_effectively_executable_inputs = declared fields
        - if is_effectively_executable_inputs violate blueprint invariants:
          - raise blueprint graph error
        - return is effectively executable value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming declared fields into the is effectively executable value."
        """
        if os.access not in os.supports_effective_ids:
            raise BlueprintGraphError(
                f"{self.path}: effective-ID executable checks are unavailable on this host"
            )
        return os.access(self.proc_path(), os.X_OK, effective_ids=True)

    def __del__(self) -> None:
        """Transform declared fields into the del result used by the blueprint graph.

        Intent
        ------
        Use declared fields to transform declared fields into the del result used by the blueprint graph.

        Rationale
        ---------
        The operation combines declared fields through close and a single state transition, making the resulting del behavior explicit across 0 conditional branches.

        Pseudocode
        ----------
        - set del_inputs = declared fields
        - return none

        Wraps
        -----
        - none
        """
        try:
            self.close()
        except OSError:
            pass


_SCHEMA_FILES = {
    "module": "module.schema.json",
    "behavioral_source": "behavioral-source.schema.json",
}


def _edge_key(edge: BlueprintEdge) -> tuple[str, str, str, int, str | None]:
    """Return the canonical identity of one graph relationship.

    Intent
    ------
    Use edge to return the canonical identity of one graph relationship.

    Rationale
    ---------
    The operation combines edge through as_posix and an explicit return value, making the resulting edge key behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set edge_key_inputs = edge
    - return edge key value

    Wraps
    -----
    - none
    """

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
    """Transform declared fields into the descriptor safe open supported result used by the blueprint graph.

    Intent
    ------
    Use declared fields to transform declared fields into the descriptor safe open supported result used by the blueprint graph.

    Rationale
    ---------
    The operation combines declared fields through hasattr and an explicit return value, making the resulting descriptor safe open supported behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set descriptor_safe_open_supported_inputs = declared fields
    - return descriptor safe open supported value

    Wraps
    -----
    - none
    """
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
    )


def descriptor_safe_open_supported() -> bool:
    """Return whether runtime inputs can be opened without path races.

    Intent
    ------
    Use declared fields to return whether runtime inputs can be opened without path races.

    Rationale
    ---------
    The operation combines declared fields through _descriptor_safe_open_supported and an explicit return value, making the resulting descriptor safe open supported behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set descriptor_safe_open_supported_inputs = declared fields
    - return descriptor safe open supported value

    Wraps
    -----
    - ._descriptor_safe_open_supported -> preprocess: forwards validated arguments; postprocess: returns the wrapped value unchanged; fixed_arguments: none
    """

    return _descriptor_safe_open_supported()


def _graph_repository_relative_path(path: Path, repo_root: Path) -> Path:
    """Transform path, repo root into the graph repository relative path result used by the blueprint graph.

    Intent
    ------
    Use path, repo root to transform path, repo root into the graph repository relative path result used by the blueprint graph.

    Rationale
    ---------
    The operation combines path, repo root through repository_relative_path, BlueprintGraphError and bounded failure checks, an explicit return value, making the resulting graph repository relative path behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set graph_repository_relative_path_inputs = path, repo root
    - if graph_repository_relative_path_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return graph repository relative path value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .repository_paths.repository_relative_path:
      why:
        constructs: "Supplies dependency position 1, repository relative path, while transforming path, repo root into the graph repository relative path value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming path, repo root into the graph repository relative path value."
    """
    try:
        return repository_relative_path(path, repo_root)
    except RepositoryPathError as exc:
        raise BlueprintGraphError(
            f"{path}: runtime input must be under {repo_root}"
        ) from exc


def _runtime_relative_path(
    path: Path,
    owner_root: Path,
    repo_root: Path,
) -> tuple[Path, Path]:
    """Transform path, owner root, repo root into the runtime relative path result used by the blueprint graph.

    Intent
    ------
    Use path, owner root, repo root to transform path, owner root, repo root into the runtime relative path result used by the blueprint graph.

    Rationale
    ---------
    The operation combines path, owner root, repo root through Path, _graph_repository_relative_path, abspath and bounded failure checks, an explicit return value, making the resulting runtime relative path behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set runtime_relative_path_inputs = path, owner root, repo root
    - if runtime_relative_path_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return runtime relative path value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .repository_paths.equivalent_root_relative_path:
      why:
        computes: "Supplies dependency position 1, equivalent root relative path, while transforming path, owner root, repo root into the runtime relative path value."

    InstantiationsFromRepo
    ----------------------
    ._graph_repository_relative_path:
      why:
        constructs: "Supplies dependency position 1,  graph repository relative path, while transforming path, owner root, repo root into the runtime relative path value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming path, owner root, repo root into the runtime relative path value."
    """
    repo_absolute = Path(os.path.abspath(repo_root))
    owner_absolute = Path(os.path.abspath(owner_root))
    path_absolute = Path(os.path.abspath(path))
    try:
        equivalent_root_relative_path(path_absolute, owner_absolute)
    except RepositoryPathError as exc:
        raise BlueprintGraphError(
            f"{path}: runtime input must be under its owning root {owner_root}"
        ) from exc
    relative = _graph_repository_relative_path(path_absolute, repo_absolute)
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
    """Open runtime descriptor.

    Intent
    ------
    Use path, owner root, repo root, directory to open runtime descriptor.

    Rationale
    ---------
    The operation combines path, owner root, repo root, directory through _runtime_relative_path, _descriptor_safe_open_supported, BlueprintGraphError and ordered iteration, bounded failure checks, an explicit return value, making the resulting open runtime descriptor behavior explicit across 8 conditional branches.

    Pseudocode
    ----------
    - set open_runtime_descriptor_inputs = path, owner root, repo root, directory
    - if open_runtime_descriptor_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in open_runtime_descriptor_inputs:
      - set validated_item = item
    - return open runtime descriptor value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._descriptor_safe_open_supported:
      why:
        computes: "Supplies dependency position 1,  descriptor safe open supported, while transforming path, owner root, repo root, directory into the open runtime descriptor value."

    InstantiationsFromRepo
    ----------------------
    .RuntimeFileBinding:
      why:
        constructs: "Supplies dependency position 1, RuntimeFileBinding, while transforming path, owner root, repo root, directory into the open runtime descriptor value."
    ._runtime_relative_path:
      why:
        constructs: "Supplies dependency position 2,  runtime relative path, while transforming path, owner root, repo root, directory into the open runtime descriptor value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 3, BlueprintGraphError, while transforming path, owner root, repo root, directory into the open runtime descriptor value."
    """
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
    """Open a contained regular file without following any path symlink.

    Intent
    ------
    Use path, owner root, repo root, executable to open a contained regular file without following any path symlink.

    Rationale
    ---------
    The operation combines path, owner root, repo root, executable through _open_runtime_descriptor, is_effectively_executable, close and bounded failure checks, an explicit return value, making the resulting open runtime file behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set open_runtime_file_inputs = path, owner root, repo root, executable
    - if open_runtime_file_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return open runtime file value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming path, owner root, repo root, executable into the open runtime file value."
    ._open_runtime_descriptor:
      why:
        constructs: "Supplies dependency position 2,  open runtime descriptor, while transforming path, owner root, repo root, executable into the open runtime file value."
    """

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
    """Open every Python source in a package tree through retained directories.

    Intent
    ------
    Use package root, owner root, repo root to open every python source in a package tree through retained directories.

    Rationale
    ---------
    The operation combines package root, owner root, repo root through Path, _open_runtime_descriptor, abspath and ordered iteration, bounded failure checks, an explicit return value, making the resulting open runtime python package behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set open_runtime_python_package_inputs = package root, owner root, repo root
    - if open_runtime_python_package_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in open_runtime_python_package_inputs:
      - set validated_item = item
    - return open runtime python package value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming package root, owner root, repo root into the open runtime python package value."
    .RuntimeFileBinding:
      why:
        constructs: "Supplies dependency position 2, RuntimeFileBinding, while transforming package root, owner root, repo root into the open runtime python package value."
    ._open_runtime_descriptor:
      why:
        constructs: "Supplies dependency position 3,  open runtime descriptor, while transforming package root, owner root, repo root into the open runtime python package value."
    """

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
        """Transform directory fd, relative dir into the visit result used by the blueprint graph.

        Intent
        ------
        Use directory fd, relative dir to transform directory fd, relative dir into the visit result used by the blueprint graph.

        Rationale
        ---------
        The operation combines directory fd, relative dir through sorted, listdir, stat and ordered iteration, bounded failure checks, making the resulting visit behavior explicit across 4 conditional branches.

        Pseudocode
        ----------
        - set visit_inputs = directory fd, relative dir
        - if visit_inputs violate blueprint invariants:
          - raise blueprint graph error
        - for item in visit_inputs:
          - set validated_item = item
        - return none

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .RuntimeFileBinding:
          why:
            constructs: "Supplies dependency position 1, RuntimeFileBinding, while transforming directory fd, relative dir into the visit value."
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming directory fd, relative dir into the visit value."
        """
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


def snapshot_runtime_python_package(
    package_root: Path,
    owner_root: Path,
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> tuple[tuple[Path, bytes], ...]:
    """Snapshot confined Python sources through the shared native reader.

    Intent
    ------
    Use package root, owner root, repo root, allow non atomic to snapshot confined python sources through the shared native reader.

    Rationale
    ---------
    The operation combines package root, owner root, repo root, allow non atomic through _runtime_relative_path, Path, tuple and ordered iteration, bounded failure checks, an explicit return value, making the resulting snapshot runtime python package behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set snapshot_runtime_python_package_inputs = package root, owner root, repo root, allow non atomic
    - if snapshot_runtime_python_package_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in snapshot_runtime_python_package_inputs:
      - set validated_item = item
    - return snapshot runtime python package value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .atomic_files.read_regular_file_bytes:
      why:
        constructs: "Supplies dependency position 1, read regular file bytes, while transforming package root, owner root, repo root, allow non atomic into the snapshot runtime python package value."
    ._runtime_relative_path:
      why:
        constructs: "Supplies dependency position 2,  runtime relative path, while transforming package root, owner root, repo root, allow non atomic into the snapshot runtime python package value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 3, BlueprintGraphError, while transforming package root, owner root, repo root, allow non atomic into the snapshot runtime python package value."
    """

    package_absolute, _relative = _runtime_relative_path(
        package_root,
        owner_root,
        repo_root,
    )
    owner_absolute = Path(os.path.abspath(owner_root))
    snapshots: list[tuple[Path, bytes]] = []

    def raise_walk_error(error: OSError) -> None:
        """Transform error into the raise walk error result used by the blueprint graph.

        Intent
        ------
        Use error to transform error into the raise walk error result used by the blueprint graph.

        Rationale
        ---------
        The operation combines error through local state and bounded failure checks, making the resulting raise walk error behavior explicit across 0 conditional branches.

        Pseudocode
        ----------
        - set raise_walk_error_inputs = error
        - if raise_walk_error_inputs violate blueprint invariants:
          - raise blueprint graph error
        - return none

        Wraps
        -----
        - none
        """
        raise error

    try:
        for directory, directory_names, file_names in os.walk(
            package_absolute,
            followlinks=False,
            onerror=raise_walk_error,
        ):
            directory_path = Path(directory)
            for name in (*directory_names, *file_names):
                child_path = directory_path / name
                metadata = child_path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or (
                    getattr(metadata, "st_file_attributes", 0) & 0x400
                ):
                    raise BlueprintGraphError(
                        f"{child_path}: Python package contains a symbolic link "
                        "or reparse point component"
                    )
            directory_names[:] = sorted(directory_names)
            for name in sorted(file_names):
                if not name.endswith(".py"):
                    continue
                source_path = directory_path / name
                snapshots.append(
                    (
                        source_path,
                        read_regular_file_bytes(
                            source_path,
                            allowed_root=owner_absolute,
                            allow_non_atomic=allow_non_atomic,
                        ),
                    )
                )
    except (AtomicWriteError, OSError) as exc:
        raise BlueprintGraphError(
            f"{package_root}: cannot snapshot Python package safely: {exc}"
        ) from exc
    return tuple(snapshots)


_RUNTIME_PYTHON_PACKAGE_SNAPSHOT_FORMAT = "officina-python-package-snapshot"
_RUNTIME_PYTHON_PACKAGE_SNAPSHOT_VERSION = 1


def _runtime_python_snapshot_path(value: object) -> str:
    """Transform value into the runtime python snapshot path result used by the blueprint graph.

    Intent
    ------
    Use value to transform value into the runtime python snapshot path result used by the blueprint graph.

    Rationale
    ---------
    The operation combines value through PurePosixPath, BlueprintGraphError, is_absolute and bounded failure checks, an explicit return value, making the resulting runtime python snapshot path behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set runtime_python_snapshot_path_inputs = value
    - if runtime_python_snapshot_path_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return runtime python snapshot path value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming value into the runtime python snapshot path value."
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BlueprintGraphError(
            "invalid package snapshot: source path must be a non-empty string"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or len(path.parts) < 2
        or "." in path.parts
        or ".." in path.parts
        or path.suffix != ".py"
    ):
        raise BlueprintGraphError(
            f"invalid package snapshot: invalid Python source path {value!r}"
        )
    return value


def encode_runtime_python_package_snapshot(
    snapshots: tuple[tuple[Path, bytes], ...],
    owner_root: Path,
) -> bytes:
    """Encode one deterministic, path-confined Python package snapshot.

    Intent
    ------
    Use snapshots, owner root to encode one deterministic, path-confined python package snapshot.

    Rationale
    ---------
    The operation combines snapshots, owner root through sort, encode, _runtime_python_snapshot_path and ordered iteration, bounded failure checks, an explicit return value, making the resulting encode runtime python package snapshot behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set encode_runtime_python_package_snapshot_inputs = snapshots, owner root
    - if encode_runtime_python_package_snapshot_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in encode_runtime_python_package_snapshot_inputs:
      - set validated_item = item
    - return encode runtime python package snapshot value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._graph_repository_relative_path:
      why:
        computes: "Supplies dependency position 1,  graph repository relative path, while transforming snapshots, owner root into the encode runtime python package snapshot value."

    InstantiationsFromRepo
    ----------------------
    ._runtime_python_snapshot_path:
      why:
        constructs: "Supplies dependency position 1,  runtime python snapshot path, while transforming snapshots, owner root into the encode runtime python package snapshot value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming snapshots, owner root into the encode runtime python package snapshot value."
    """

    records: list[dict[str, str]] = []
    for source_path, source in snapshots:
        if not isinstance(source, bytes):
            raise BlueprintGraphError(
                "invalid package snapshot: source content must be bytes"
            )
        logical_path = _runtime_python_snapshot_path(
            _graph_repository_relative_path(source_path, owner_root).as_posix()
        )
        records.append(
            {
                "path": logical_path,
                "source": base64.b64encode(source).decode("ascii"),
            }
        )
    records.sort(key=lambda record: record["path"])
    paths = [record["path"] for record in records]
    if not paths or len(paths) != len(set(paths)):
        raise BlueprintGraphError(
            "invalid package snapshot: source paths must be non-empty and unique"
        )
    roots = {PurePosixPath(path).parts[0] for path in paths}
    if len(roots) != 1:
        raise BlueprintGraphError(
            "invalid package snapshot: sources must share one package root"
        )
    document = {
        "files": records,
        "format": _RUNTIME_PYTHON_PACKAGE_SNAPSHOT_FORMAT,
        "version": _RUNTIME_PYTHON_PACKAGE_SNAPSHOT_VERSION,
    }
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def decode_runtime_python_package_snapshot(
    payload: bytes,
) -> tuple[tuple[str, bytes], ...]:
    """Strictly decode a deterministic Python package snapshot.

    Intent
    ------
    Use payload to strictly decode a deterministic python package snapshot.

    Rationale
    ---------
    The operation combines payload through tuple, loads, BlueprintGraphError and ordered iteration, bounded failure checks, an explicit return value, making the resulting decode runtime python package snapshot behavior explicit across 11 conditional branches.

    Pseudocode
    ----------
    - set decode_runtime_python_package_snapshot_inputs = payload
    - if decode_runtime_python_package_snapshot_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in decode_runtime_python_package_snapshot_inputs:
      - set validated_item = item
    - return decode runtime python package snapshot value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._runtime_python_snapshot_path:
      why:
        constructs: "Supplies dependency position 1,  runtime python snapshot path, while transforming payload into the decode runtime python package snapshot value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming payload into the decode runtime python package snapshot value."
    """

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Reject invalid duplicate keys.

        Intent
        ------
        Use pairs to reject invalid duplicate keys.

        Rationale
        ---------
        The operation combines pairs through ValueError and ordered iteration, bounded failure checks, an explicit return value, making the resulting reject duplicate keys behavior explicit across 1 conditional branches.

        Pseudocode
        ----------
        - set reject_duplicate_keys_inputs = pairs
        - if reject_duplicate_keys_inputs violate blueprint invariants:
          - raise blueprint graph error
        - for item in reject_duplicate_keys_inputs:
          - set validated_item = item
        - return reject duplicate keys value

        Wraps
        -----
        - none
        """
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BlueprintGraphError(
            f"invalid package snapshot: malformed JSON: {exc}"
        ) from exc
    if not isinstance(document, dict) or set(document) != {
        "files",
        "format",
        "version",
    }:
        raise BlueprintGraphError(
            "invalid package snapshot: document fields are not exact"
        )
    if document["format"] != _RUNTIME_PYTHON_PACKAGE_SNAPSHOT_FORMAT:
        raise BlueprintGraphError(
            "invalid package snapshot: unsupported format"
        )
    version = document["version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != _RUNTIME_PYTHON_PACKAGE_SNAPSHOT_VERSION
    ):
        raise BlueprintGraphError(
            "invalid package snapshot: unsupported version"
        )
    files = document["files"]
    if not isinstance(files, list) or not files:
        raise BlueprintGraphError(
            "invalid package snapshot: files must be a non-empty list"
        )

    decoded: list[tuple[str, bytes]] = []
    previous_path: str | None = None
    package_root: str | None = None
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "source"}:
            raise BlueprintGraphError(
                "invalid package snapshot: source fields are not exact"
            )
        logical_path = _runtime_python_snapshot_path(record["path"])
        if previous_path is not None and logical_path <= previous_path:
            raise BlueprintGraphError(
                "invalid package snapshot: source paths must be sorted and unique"
            )
        previous_path = logical_path
        current_root = PurePosixPath(logical_path).parts[0]
        if package_root is None:
            package_root = current_root
        elif current_root != package_root:
            raise BlueprintGraphError(
                "invalid package snapshot: sources must share one package root"
            )
        encoded_source = record["source"]
        if not isinstance(encoded_source, str):
            raise BlueprintGraphError(
                "invalid package snapshot: source content must be base64 text"
            )
        try:
            source = base64.b64decode(encoded_source, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BlueprintGraphError(
                "invalid package snapshot: source content is not valid base64"
            ) from exc
        if base64.b64encode(source).decode("ascii") != encoded_source:
            raise BlueprintGraphError(
                "invalid package snapshot: source content is not canonical base64"
            )
        decoded.append((logical_path, source))
    return tuple(decoded)


def _positive_version(value: object, context: str) -> int:
    """Transform value, context into the positive version result used by the blueprint graph.

    Intent
    ------
    Use value, context to transform value, context into the positive version result used by the blueprint graph.

    Rationale
    ---------
    The operation combines value, context through isinstance, BlueprintGraphError and bounded failure checks, an explicit return value, making the resulting positive version behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set positive_version_inputs = value, context
    - if positive_version_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return positive version value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming value, context into the positive version value."
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BlueprintGraphError(f"{context}: version must be a positive integer")
    return value


def _resolve_locator(
    module_root: Path,
    locator: object,
    context: str,
    repo_root: Path,
) -> Path:
    """Resolve locator for blueprint graph operations.

    Intent
    ------
    Use module root, locator, context, repo root to resolve locator for blueprint graph operations.

    Rationale
    ---------
    The operation combines module root, locator, context, repo root through get, Path, isinstance and bounded failure checks, an explicit return value, making the resulting resolve locator behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set resolve_locator_inputs = module root, locator, context, repo root
    - if resolve_locator_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return resolve locator value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming module root, locator, context, repo root into the resolve locator value."
    """
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
    """Transform error into the json error path result used by the blueprint graph.

    Intent
    ------
    Use error to transform error into the json error path result used by the blueprint graph.

    Rationale
    ---------
    The operation combines error through list, match, append and ordered iteration, an explicit return value, making the resulting json error path behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set json_error_path_inputs = error
    - for item in json_error_path_inputs:
      - set validated_item = item
    - return json error path value

    Wraps
    -----
    - none
    """
    parts = list(error.absolute_path)
    if error.validator == "required":
        match = re.match(r"'([^']+)' is a required property", error.message)
        if match is not None:
            parts.append(match.group(1))
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _load_schema_validator(schema_path: Path) -> jsonschema.protocols.Validator:
    """Load a concrete blueprint schema with ordinary local-reference resolution.

    Intent
    ------
    Use schema path to load a concrete blueprint schema with ordinary local-reference resolution.

    Rationale
    ---------
    The operation combines schema path through Path, abspath, is_file and bounded failure checks, an explicit return value, making the resulting load schema validator behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set load_schema_validator_inputs = schema path
    - if load_schema_validator_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return load schema validator value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.configuration.configured_schema.schema_requires_configuration:
      why:
        computes: "Supplies dependency position 1, schema requires configuration, while transforming schema path into the load schema validator value."

    InstantiationsFromRepo
    ----------------------
    officina.configuration.configured_schema.configured_validator:
      why:
        constructs: "Supplies dependency position 1, configured validator, while transforming schema path into the load schema validator value."
    officina.configuration.configured_schema.ConfiguredSchemaError:
      why:
        constructs: "Supplies dependency position 2, ConfiguredSchemaError, while transforming schema path into the load schema validator value."
    .BlueprintSchemaError:
      why:
        constructs: "Supplies dependency position 3, BlueprintSchemaError, while transforming schema path into the load schema validator value."
    """

    schema_path = Path(os.path.abspath(schema_path))
    config_path = schema_path.parent / "config.yaml"
    try:
        if config_path.is_file():
            return configured_validator(
                schema_path,
                config_path=config_path,
                allowed_schema_root=schema_path.parent,
            )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if schema_requires_configuration(schema):
            raise ConfiguredSchemaError(
                f"{schema_path}: schema uses x-officina-config but sibling "
                "config.yaml is missing"
            )
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
        resolver = jsonschema.RefResolver(
            base_uri=schema_path.as_uri(),
            referrer=schema,
        )
        return validator_class(schema, resolver=resolver)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        jsonschema.SchemaError,
        ConfiguredSchemaError,
    ) as exc:
        raise BlueprintSchemaError(
            schema_path,
            "$",
            f"cannot load schema: {exc}",
        ) from exc


def _declaration_schema_errors(
    blueprint_path: Path,
    declaration: dict[str, Any],
    schema_root: Path,
    validators: dict[str, jsonschema.protocols.Validator],
    *,
    expected_schema_version: int = 6,
) -> tuple[BlueprintSchemaError, ...]:
    """Transform blueprint path, declaration, schema root, validators into the declaration schema errors result used by the blueprint graph.

    Intent
    ------
    Use blueprint path, declaration, schema root, validators to transform blueprint path, declaration, schema root, validators into the declaration schema errors result used by the blueprint graph.

    Rationale
    ---------
    The operation combines blueprint path, declaration, schema root, validators through get, tuple, BlueprintGraphError and bounded failure checks, an explicit return value, making the resulting declaration schema errors behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set declaration_schema_errors_inputs = blueprint path, declaration, schema root, validators
    - if declaration_schema_errors_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return declaration schema errors value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._json_error_path:
      why:
        computes: "Supplies dependency position 1,  json error path, while transforming blueprint path, declaration, schema root, validators into the declaration schema errors value."

    InstantiationsFromRepo
    ----------------------
    ._load_schema_validator:
      why:
        constructs: "Supplies dependency position 1,  load schema validator, while transforming blueprint path, declaration, schema root, validators into the declaration schema errors value."
    .BlueprintSchemaError:
      why:
        constructs: "Supplies dependency position 2, BlueprintSchemaError, while transforming blueprint path, declaration, schema root, validators into the declaration schema errors value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 3, BlueprintGraphError, while transforming blueprint path, declaration, schema root, validators into the declaration schema errors value."
    """
    schema_version = declaration.get("schema_version")
    node_type = declaration.get("node_type")
    if schema_version != expected_schema_version:
        raise BlueprintGraphError(
            f"{blueprint_path}: repository graph requires schema_version "
            f"{expected_schema_version}"
        )
    try:
        schema_name = _SCHEMA_FILES[node_type]
    except (KeyError, TypeError) as exc:
        raise BlueprintGraphError(
            f"{blueprint_path}: unsupported typed node type {node_type!r} "
            f"for schema version {expected_schema_version}"
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
    """Transform path into the is forbidden content artifact result used by the blueprint graph.

    Intent
    ------
    Use path to transform path into the is forbidden content artifact result used by the blueprint graph.

    Rationale
    ---------
    The operation combines path through endswith and an explicit return value, making the resulting is forbidden content artifact behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set is_forbidden_content_artifact_inputs = path
    - return is forbidden content artifact value

    Wraps
    -----
    - none
    """
    name = path.name
    return (
        name == "blueprint.yaml"
        or name.endswith(".blueprint.yaml")
        or ".certificates" in path.parts
    )


def _regular_files_beneath(root: Path) -> tuple[Path, ...]:
    """Transform root into the regular files beneath result used by the blueprint graph.

    Intent
    ------
    Use root to transform root into the regular files beneath result used by the blueprint graph.

    Rationale
    ---------
    The operation combines root through walk, tuple, Path and ordered iteration, an explicit return value, making the resulting regular files beneath behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set regular_files_beneath_inputs = root
    - for item in regular_files_beneath_inputs:
      - set validated_item = item
    - return regular files beneath value

    Wraps
    -----
    - none
    """
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
    *,
    excluded_module_roots: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    """Resolve regular files in one node's module-local ownership scope.

    Intent
    ------
    Use node, repo root, excluded module roots to resolve regular files in one node's module-local ownership scope.

    Rationale
    ---------
    The operation combines node, repo root, excluded module roots through Path, get, tuple and ordered iteration, bounded failure checks, an explicit return value, making the resulting resolved node content paths behavior explicit across 6 conditional branches.

    Pseudocode
    ----------
    - set resolved_node_content_paths_inputs = node, repo root, excluded module roots
    - if resolved_node_content_paths_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in resolved_node_content_paths_inputs:
      - set validated_item = item
    - return resolved node content paths value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._regular_files_beneath:
      why:
        computes: "Supplies dependency position 1,  regular files beneath, while transforming node, repo root, excluded module roots into the resolved node content paths value."
    ._is_forbidden_content_artifact:
      why:
        computes: "Supplies dependency position 2,  is forbidden content artifact, while transforming node, repo root, excluded module roots into the resolved node content paths value."
    .repository_paths.equivalent_root_relative_path:
      why:
        computes: "Supplies dependency position 3, equivalent root relative path, while transforming node, repo root, excluded module roots into the resolved node content paths value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming node, repo root, excluded module roots into the resolved node content paths value."
    """

    if node.declaration.get("schema_version") not in {4, 5, 6}:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content resolution requires schema_version 4, 5, or 6"
        )
    repo_root = Path(os.path.abspath(repo_root))
    owner_root = Path(os.path.abspath(node.module_root))
    try:
        equivalent_root_relative_path(owner_root, repo_root)
    except RepositoryPathError as exc:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content ownership root must be inside the repository"
        ) from exc

    raw_patterns = node.declaration.get("content")
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content must be a non-empty list of regex patterns"
        )
    excluded = tuple(Path(os.path.abspath(path)) for path in excluded_module_roots)
    candidates = tuple(
        path
        for path in _regular_files_beneath(owner_root)
        if not any(path.is_relative_to(child_root) for child_root in excluded)
        and "__pycache__" not in path.relative_to(owner_root).parts
        and not path.name.endswith(".pyc")
    )
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
    """Return the authored blueprint and resolved content files for one node.

    Intent
    ------
    Use node, repo root to return the authored blueprint and resolved content files for one node.

    Rationale
    ---------
    The operation combines node, repo root through tuple, Path, is_relative_to and bounded failure checks, an explicit return value, making the resulting authored node input paths behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set authored_node_input_paths_inputs = node, repo root
    - if authored_node_input_paths_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return authored node input paths value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolved_node_content_paths:
      why:
        computes: "Supplies dependency position 1, resolved node content paths, while transforming node, repo root into the authored node input paths value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming node, repo root into the authored node input paths value."
    """

    if repo_root is None:
        owner_root = Path(os.path.abspath(node.module_root))
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
    """Validate one confined regular runtime file on the current platform.

    Intent
    ------
    Use path, owner root, repo root to validate one confined regular runtime file on the current platform.

    Rationale
    ---------
    The operation combines path, owner root, repo root through _runtime_relative_path, read_regular_file_bytes, BlueprintGraphError and bounded failure checks, an explicit return value, making the resulting validate runtime file path behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set validate_runtime_file_path_inputs = path, owner root, repo root
    - if validate_runtime_file_path_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return validate runtime file path value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .atomic_files.read_regular_file_bytes:
      why:
        computes: "Supplies dependency position 1, read regular file bytes, while transforming path, owner root, repo root into the validate runtime file path value."

    InstantiationsFromRepo
    ----------------------
    ._runtime_relative_path:
      why:
        constructs: "Supplies dependency position 1,  runtime relative path, while transforming path, owner root, repo root into the validate runtime file path value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming path, owner root, repo root into the validate runtime file path value."
    """

    path_absolute, _relative = _runtime_relative_path(
        path,
        owner_root,
        repo_root,
    )
    try:
        read_regular_file_bytes(
            path_absolute,
            allowed_root=Path(os.path.abspath(owner_root)),
            allow_non_atomic=False,
        )
    except (AtomicWriteError, OSError) as exc:
        raise BlueprintGraphError(f"{path}: {exc}") from exc
    return path_absolute


def _node_from_document(
    document: Any,
    *,
    expected_schema_version: int,
) -> BlueprintNode:
    """Transform document, expected schema version into the node from document result used by the blueprint graph.

    Intent
    ------
    Use document, expected schema version to transform document, expected schema version into the node from document result used by the blueprint graph.

    Rationale
    ---------
    The operation combines document, expected schema version through dict, get, BlueprintNode and bounded failure checks, an explicit return value, making the resulting node from document behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set node_from_document_inputs = document, expected schema version
    - if node_from_document_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return node from document value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Supplies dependency position 1,  positive version, while transforming document, expected schema version into the node from document value."
    .BlueprintNode:
      why:
        constructs: "Supplies dependency position 2, BlueprintNode, while transforming document, expected schema version into the node from document value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 3, BlueprintGraphError, while transforming document, expected schema version into the node from document value."
    """
    declaration = dict(document.declaration)
    node_id = declaration.get("id")
    node_type = declaration.get("node_type")
    if not isinstance(node_id, str) or not node_id:
        raise BlueprintGraphError(
            f"{document.path}: version {expected_schema_version} blueprint "
            "requires a non-empty id"
        )
    if node_type not in _SCHEMA_FILES:
        raise BlueprintGraphError(
            f"{document.path}: unsupported typed node type {node_type!r}"
        )
    gateway = declaration.get("gateway")
    gateway_path = None
    if isinstance(gateway, dict) and isinstance(gateway.get("path"), str):
        gateway_path = document.module_root / gateway["path"]
    return BlueprintNode(
        node_id=node_id,
        node_type=node_type,
        version=_positive_version(declaration.get("version"), str(document.path)),
        module_root=document.module_root,
        blueprint_path=document.path,
        gateway_path=gateway_path,
        declaration=declaration,
    )


def _v4_node_from_document(document: Any) -> BlueprintNode:
    """Transform document into the v4 node from document result used by the blueprint graph.

    Intent
    ------
    Use document to transform document into the v4 node from document result used by the blueprint graph.

    Rationale
    ---------
    The operation combines document through _node_from_document and an explicit return value, making the resulting v4 node from document behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set v4_node_from_document_inputs = document
    - return v4 node from document value

    Wraps
    -----
    - ._node_from_document -> preprocess: forwards validated arguments; postprocess: returns the wrapped value unchanged; fixed_arguments: none
    """
    return _node_from_document(document, expected_schema_version=4)


def _load_module_blueprint(
    repo_root: Path,
    module_root: Path,
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 4,
    validators: dict[str, jsonschema.protocols.Validator],
) -> BlueprintNode:
    """Load one exact module marker with caller-owned schema validators.

    Intent
    ------
    Use repo root, module root, schema root, expected schema version to load one exact module marker with caller-owned schema validators.

    Rationale
    ---------
    The operation combines repo root, module root, schema root, expected schema version through Path, get, BlueprintDocument and ordered iteration, bounded failure checks, an explicit return value, making the resulting load module blueprint behavior explicit across 6 conditional branches.

    Pseudocode
    ----------
    - set load_module_blueprint_inputs = repo root, module root, schema root, expected schema version
    - if load_module_blueprint_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in load_module_blueprint_inputs:
      - set validated_item = item
    - return load module blueprint value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .authored_node_input_paths:
      why:
        computes: "Supplies dependency position 1, authored node input paths, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    .atomic_files.read_regular_file_bytes:
      why:
        computes: "Supplies dependency position 2, read regular file bytes, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    .validate_runtime_file_path:
      why:
        computes: "Supplies dependency position 3, validate runtime file path, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    ._node_from_document:
      why:
        constructs: "Supplies dependency position 2,  node from document, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    .inventory.BlueprintDocument:
      why:
        constructs: "Supplies dependency position 3, BlueprintDocument, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    .inventory._normalize_json:
      why:
        constructs: "Supplies dependency position 4,  normalize json, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    ._declaration_schema_errors:
      why:
        constructs: "Supplies dependency position 5,  declaration schema errors, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    """

    if expected_schema_version not in {4, 5, 6}:
        raise ValueError("expected_schema_version must be 4, 5, or 6")

    repository = Path(os.path.abspath(repo_root))
    module = Path(os.path.abspath(module_root))
    try:
        module.relative_to(repository)
    except ValueError as exc:
        raise BlueprintGraphError(
            f"{module_root}: module root must be inside repository {repo_root}"
        ) from exc

    marker = module / "blueprint.yaml"
    try:
        loaded = yaml.load(
            read_regular_file_bytes(
                marker,
                allowed_root=module,
                allow_non_atomic=False,
            ).decode("utf-8"),
            Loader=_StrictBlueprintLoader,
        )
        if not isinstance(loaded, dict):
            raise ValueError("document root must be a mapping")
        declaration = _normalize_json(loaded)
        assert isinstance(declaration, dict)
    except BlueprintGraphError:
        raise
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise BlueprintGraphError(
            f"{marker}: cannot load module blueprint: {exc}"
        ) from exc

    node_type = declaration.get("node_type")
    node_id = declaration.get("id")
    document = BlueprintDocument(
        path=marker,
        relative_path=marker.relative_to(repository),
        module_root=module,
        declaration=declaration,
        node_type=node_type if isinstance(node_type, str) else None,
        node_id=node_id if isinstance(node_id, str) else None,
    )

    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else repository / "references" / "blueprint-schema"
    )
    if not (selected_schema_root / "module.schema.json").is_file():
        selected_schema_root = (
            Path(__file__).resolve().parents[3]
            / "references"
            / "blueprint-schema"
        )
    errors = _declaration_schema_errors(
        marker,
        dict(declaration),
        selected_schema_root,
        validators,
        expected_schema_version=expected_schema_version,
    )
    if errors:
        raise errors[0]

    node = _node_from_document(
        document,
        expected_schema_version=expected_schema_version,
    )
    if node.node_type != "module":
        raise BlueprintGraphError(f"{marker}: exact module marker must declare node_type module")
    if (
        node.node_id != module.name
        if expected_schema_version < 6
        else node.node_id.rsplit(".", 1)[-1] != module.name
    ):
        raise BlueprintGraphError(
            f"{marker}: module id {node.node_id!r} must match its directory"
        )
    for path in authored_node_input_paths(node, repository):
        validate_runtime_file_path(path, module, repository)
    return node


def prepare_module_blueprint_loader(
    repo_root: Path,
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 4,
) -> Callable[[Path], BlueprintNode]:
    """Prepare an exact module loader with one bounded schema-validator cache.

    Intent
    ------
    Use repo root, schema root, expected schema version to prepare an exact module loader with one bounded schema-validator cache.

    Rationale
    ---------
    The operation combines repo root, schema root, expected schema version through _load_module_blueprint and an explicit return value, making the resulting prepare module blueprint loader behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set prepare_module_blueprint_loader_inputs = repo root, schema root, expected schema version
    - return prepare module blueprint loader value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._load_module_blueprint:
      why:
        constructs: "Supplies dependency position 1,  load module blueprint, while transforming repo root, schema root, expected schema version into the prepare module blueprint loader value."
    """

    validators: dict[str, jsonschema.protocols.Validator] = {}

    def load(module_root: Path) -> BlueprintNode:
        """Load one module through the prepared repository schema context.

        Intent
        ------
        Use module root to load one module through the prepared repository schema context.

        Rationale
        ---------
        The operation combines module root through _load_module_blueprint and an explicit return value, making the resulting load behavior explicit across 0 conditional branches.

        Pseudocode
        ----------
        - set load_inputs = module root
        - return load value

        Wraps
        -----
        - ._load_module_blueprint -> preprocess: forwards validated arguments; postprocess: returns the wrapped value unchanged; fixed_arguments: none
        """

        return _load_module_blueprint(
            repo_root,
            module_root,
            schema_root=schema_root,
            expected_schema_version=expected_schema_version,
            validators=validators,
        )

    return load


def load_module_blueprints(
    repo_root: Path,
    module_roots: Sequence[Path],
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 4,
) -> tuple[BlueprintNode, ...]:
    """Load ordered exact module markers through one prepared schema context.

    Intent
    ------
    Use repo root, module roots, schema root, expected schema version to load ordered exact module markers through one prepared schema context.

    Rationale
    ---------
    The operation combines repo root, module roots, schema root, expected schema version through prepare_module_blueprint_loader, tuple, load and an explicit return value, making the resulting load module blueprints behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set load_module_blueprints_inputs = repo root, module roots, schema root, expected schema version
    - return load module blueprints value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .prepare_module_blueprint_loader:
      why:
        constructs: "Supplies dependency position 1, prepare module blueprint loader, while transforming repo root, module roots, schema root, expected schema version into the load module blueprints value."
    """

    load = prepare_module_blueprint_loader(
        repo_root,
        schema_root=schema_root,
        expected_schema_version=expected_schema_version,
    )
    return tuple(load(module_root) for module_root in module_roots)


def load_module_blueprint(
    repo_root: Path,
    module_root: Path,
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 4,
) -> BlueprintNode:
    """Load one exact module marker through an isolated schema context.

    Intent
    ------
    Use repo root, module root, schema root, expected schema version to load one exact module marker through an isolated schema context.

    Rationale
    ---------
    The operation combines repo root, module root, schema root, expected schema version through prepare_module_blueprint_loader, load and an explicit return value, making the resulting load module blueprint behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set load_module_blueprint_inputs = repo root, module root, schema root, expected schema version
    - return load module blueprint value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .prepare_module_blueprint_loader:
      why:
        constructs: "Supplies dependency position 1, prepare module blueprint loader, while transforming repo root, module root, schema root, expected schema version into the load module blueprint value."
    """

    load = prepare_module_blueprint_loader(
        repo_root,
        schema_root=schema_root,
        expected_schema_version=expected_schema_version,
    )
    return load(module_root)


def _reject_export_cycles(
    exports: Mapping[str, InterfaceExport],
    edges: tuple[ExportDependencyEdge, ...],
) -> None:
    """Reject invalid export cycles.

    Intent
    ------
    Use exports, edges to reject invalid export cycles.

    Rationale
    ---------
    The operation combines exports, edges through set, sorted, append and ordered iteration, bounded failure checks, an explicit return value, making the resulting reject export cycles behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set reject_export_cycles_inputs = exports, edges
    - if reject_export_cycles_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in reject_export_cycles_inputs:
      - set validated_item = item
    - return reject export cycles value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming exports, edges into the reject export cycles value."
    """
    children: dict[str, list[str]] = {interface_id: [] for interface_id in exports}
    for edge in edges:
        children[edge.source_export_id].append(edge.target_interface_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(interface_id: str) -> None:
        """Transform interface id into the visit result used by the blueprint graph.

        Intent
        ------
        Use interface id to transform interface id into the visit result used by the blueprint graph.

        Rationale
        ---------
        The operation combines interface id through append, sorted, pop and ordered iteration, bounded failure checks, an explicit return value, making the resulting visit behavior explicit across 2 conditional branches.

        Pseudocode
        ----------
        - set visit_inputs = interface id
        - if visit_inputs violate blueprint invariants:
          - raise blueprint graph error
        - for item in visit_inputs:
          - set validated_item = item
        - return visit value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming interface id into the visit value."
        """
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
    """Require valid platform compatibility.

    Intent
    ------
    Use source, target, context to require valid platform compatibility.

    Rationale
    ---------
    The operation combines source, target, context through get, items, isinstance and ordered iteration, bounded failure checks, an explicit return value, making the resulting require platform compatibility behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set require_platform_compatibility_inputs = source, target, context
    - if require_platform_compatibility_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in require_platform_compatibility_inputs:
      - set validated_item = item
    - return require platform compatibility value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming source, target, context into the require platform compatibility value."
    """
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
    """Reject invalid certification cycles.

    Intent
    ------
    Use node ids, edges to reject invalid certification cycles.

    Rationale
    ---------
    The operation combines node ids, edges through set, sorted, append and ordered iteration, bounded failure checks, an explicit return value, making the resulting reject certification cycles behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set reject_certification_cycles_inputs = node ids, edges
    - if reject_certification_cycles_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in reject_certification_cycles_inputs:
      - set validated_item = item
    - return reject certification cycles value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming node ids, edges into the reject certification cycles value."
    """
    children: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source_node_id in children and edge.target_node_id in children:
            children[edge.source_node_id].append(edge.target_node_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        """Transform node id into the visit result used by the blueprint graph.

        Intent
        ------
        Use node id to transform node id into the visit result used by the blueprint graph.

        Rationale
        ---------
        The operation combines node id through append, sorted, pop and ordered iteration, bounded failure checks, an explicit return value, making the resulting visit behavior explicit across 2 conditional branches.

        Pseudocode
        ----------
        - set visit_inputs = node id
        - if visit_inputs violate blueprint invariants:
          - raise blueprint graph error
        - for item in visit_inputs:
          - set validated_item = item
        - return visit value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming node id into the visit value."
        """
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
    """Transform values, context into the v4 local ids result used by the blueprint graph.

    Intent
    ------
    Use values, context to transform values, context into the v4 local ids result used by the blueprint graph.

    Rationale
    ---------
    The operation combines values, context through set, isinstance, append and ordered iteration, bounded failure checks, an explicit return value, making the resulting v4 local ids behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set v4_local_ids_inputs = values, context
    - if v4_local_ids_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v4_local_ids_inputs:
      - set validated_item = item
    - return v4 local ids value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming values, context into the v4 local ids value."
    """
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
    """Require valid v4 local ref.

    Intent
    ------
    Use value, valid, context, kind to require valid v4 local ref.

    Rationale
    ---------
    The operation combines value, valid, context, kind through isinstance, BlueprintGraphError and bounded failure checks, making the resulting require v4 local ref behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set require_v4_local_ref_inputs = value, valid, context, kind
    - if require_v4_local_ref_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return none

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming value, valid, context, kind into the require v4 local ref value."
    """
    if isinstance(value, str) and value not in valid:
        raise BlueprintGraphError(f"{context}: unknown {kind} {value!r}")


def _walk_v4_contract(
    value: object,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], str, object]]:
    """Transform value, path into the walk v4 contract result used by the blueprint graph.

    Intent
    ------
    Use value, path to transform value, path into the walk v4 contract result used by the blueprint graph.

    Rationale
    ---------
    The operation combines value, path through isinstance, items, str and ordered iteration, an explicit return value, making the resulting walk v4 contract behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set walk_v4_contract_inputs = value, path
    - for item in walk_v4_contract_inputs:
      - set validated_item = item
    - return walk v4 contract value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._walk_v4_contract:
      why:
        constructs: "Supplies dependency position 1,  walk v4 contract, while transforming value, path into the walk v4 contract value."
    """
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
    """Validate v4 internal path.

    Intent
    ------
    Use value, context to validate v4 internal path.

    Rationale
    ---------
    The operation combines value, context through PurePosixPath, isinstance, is_absolute and bounded failure checks, an explicit return value, making the resulting validate v4 internal path behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set validate_v4_internal_path_inputs = value, context
    - if validate_v4_internal_path_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return validate v4 internal path value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming value, context into the validate v4 internal path value."
    """
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
    """Transform modules into the v4 authority claims result used by the blueprint graph.

    Intent
    ------
    Use modules to transform modules into the v4 authority claims result used by the blueprint graph.

    Rationale
    ---------
    The operation combines modules through sorted, tuple, items and ordered iteration, bounded failure checks, an explicit return value, making the resulting v4 authority claims behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set v4_authority_claims_inputs = modules
    - if v4_authority_claims_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v4_authority_claims_inputs:
      - set validated_item = item
    - return v4 authority claims value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming modules into the v4 authority claims value."
    """
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
    """Validate v4 interface contract.

    Intent
    ------
    Use interface id, declaration, module id, authority claims to validate v4 interface contract.

    Rationale
    ---------
    The operation combines interface id, declaration, module id, authority claims through get, _v4_local_ids, set and ordered iteration, bounded failure checks, an explicit return value, making the resulting validate v4 interface contract behavior explicit across 20 conditional branches.

    Pseudocode
    ----------
    - set validate_v4_interface_contract_inputs = interface id, declaration, module id, authority claims
    - if validate_v4_interface_contract_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in validate_v4_interface_contract_inputs:
      - set validated_item = item
    - return validate v4 interface contract value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._walk_v4_contract:
      why:
        computes: "Supplies dependency position 1,  walk v4 contract, while transforming interface id, declaration, module id, authority claims into the validate v4 interface contract value."
    ._validate_v4_internal_path:
      why:
        computes: "Supplies dependency position 2,  validate v4 internal path, while transforming interface id, declaration, module id, authority claims into the validate v4 interface contract value."
    ._require_v4_local_ref:
      why:
        computes: "Supplies dependency position 3,  require v4 local ref, while transforming interface id, declaration, module id, authority claims into the validate v4 interface contract value."

    InstantiationsFromRepo
    ----------------------
    ._v4_local_ids:
      why:
        constructs: "Supplies dependency position 1,  v4 local ids, while transforming interface id, declaration, module id, authority claims into the validate v4 interface contract value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming interface id, declaration, module id, authority claims into the validate v4 interface contract value."
    """
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


def _build_source_relationships(
    root: Path,
    *,
    sources: Mapping[str, BlueprintNode],
    node_edges: list[BlueprintEdge],
    certification_edges: list[CertificationEdge],
    dependency_root: Callable[[BlueprintNode], Path],
    resolve_interface_use: Callable[
        [str, BlueprintNode, str, int],
        tuple[str, str],
    ],
) -> dict[str, tuple[tuple[str, int], ...]]:
    """Build source relationships.

    Intent
    ------
    Use root, sources, node edges, certification edges to build source relationships.

    Rationale
    ---------
    The operation combines root, sources, node edges, certification edges through sorted, items, get and ordered iteration, bounded failure checks, an explicit return value, making the resulting build source relationships behavior explicit across 8 conditional branches.

    Pseudocode
    ----------
    - set build_source_relationships_inputs = root, sources, node edges, certification edges
    - if build_source_relationships_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in build_source_relationships_inputs:
      - set validated_item = item
    - return build source relationships value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Supplies dependency position 1,  positive version, while transforming root, sources, node edges, certification edges into the build source relationships value."
    .BlueprintEdge:
      why:
        constructs: "Supplies dependency position 2, BlueprintEdge, while transforming root, sources, node edges, certification edges into the build source relationships value."
    ._resolve_locator:
      why:
        constructs: "Supplies dependency position 3,  resolve locator, while transforming root, sources, node edges, certification edges into the build source relationships value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 4, BlueprintGraphError, while transforming root, sources, node edges, certification edges into the build source relationships value."
    .CertificationEdge:
      why:
        constructs: "Supplies dependency position 5, CertificationEdge, while transforming root, sources, node edges, certification edges into the build source relationships value."
    """
    interface_uses_by_source: dict[str, tuple[tuple[str, int], ...]] = {}
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
                    f"{source_id}: unresolved behavioral source {target_id!r}"
                )
            target = sources[target_id]
            version = _positive_version(
                dependency.get("version"),
                f"{source_id}.dependencies[{index}]",
            )
            if target.version != version:
                raise BlueprintGraphError(
                    f"{source_id}: pins {target_id} version {version}, "
                    f"but target version is {target.version}"
                )
            locator = _resolve_locator(
                dependency_root(source),
                dependency.get("blueprint"),
                f"{source.blueprint_path}:dependencies[{index}]",
                root,
            )
            if Path(os.path.abspath(locator)) != Path(
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
            interface_id = use.get("interface")
            version = _positive_version(
                use.get("version"),
                f"{source_id}.uses_interfaces[{index}]",
            )
            if not isinstance(interface_id, str):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: "
                    f"uses_interfaces[{index}] requires interface"
                )
            relation, target_source_id = resolve_interface_use(
                source_id,
                source,
                interface_id,
                version,
            )
            uses.append((interface_id, version))
            node_edges.append(
                BlueprintEdge(
                    relation,
                    source_id,
                    interface_id,
                    version,
                )
            )
            target_source = sources[target_source_id]
            certification_edges.append(
                CertificationEdge(
                    relation,
                    source_id,
                    target_source_id,
                    target_source.version,
                )
            )
        interface_uses_by_source[source_id] = tuple(uses)
    return interface_uses_by_source


def _build_export_relationships(
    exports: Mapping[str, InterfaceExport],
    interface_uses_by_source: Mapping[str, tuple[tuple[str, int], ...]],
) -> tuple[list[ExportDependencyEdge], list[HelperEdge]]:
    """Build export relationships.

    Intent
    ------
    Use exports, interface uses by source to build export relationships.

    Rationale
    ---------
    The operation combines exports, interface uses by source through sorted, items, set and ordered iteration, bounded failure checks, an explicit return value, making the resulting build export relationships behavior explicit across 5 conditional branches.

    Pseudocode
    ----------
    - set build_export_relationships_inputs = exports, interface uses by source
    - if build_export_relationships_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in build_export_relationships_inputs:
      - set validated_item = item
    - return build export relationships value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Supplies dependency position 1,  positive version, while transforming exports, interface uses by source into the build export relationships value."
    .ExportDependencyEdge:
      why:
        constructs: "Supplies dependency position 2, ExportDependencyEdge, while transforming exports, interface uses by source into the build export relationships value."
    .HelperEdge:
      why:
        constructs: "Supplies dependency position 3, HelperEdge, while transforming exports, interface uses by source into the build export relationships value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 4, BlueprintGraphError, while transforming exports, interface uses by source into the build export relationships value."
    """
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
    return export_edges, helper_edges


def _load_v4_repository_blueprint_graph(
    root: Path,
    documents: tuple[Any, ...],
    *,
    schema_root: Path,
) -> RepositoryBlueprintGraph:
    """Load v4 repository blueprint graph.

    Intent
    ------
    Use root, documents, schema root to load v4 repository blueprint graph.

    Rationale
    ---------
    The operation combines root, documents, schema root through sorted, _v4_authority_claims, _build_source_relationships and ordered iteration, bounded failure checks, an explicit return value, making the resulting load v4 repository blueprint graph behavior explicit across 35 conditional branches.

    Pseudocode
    ----------
    - set load_v4_repository_blueprint_graph_inputs = root, documents, schema root
    - if load_v4_repository_blueprint_graph_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in load_v4_repository_blueprint_graph_inputs:
      - set validated_item = item
    - return load v4 repository blueprint graph value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._reject_certification_cycles:
      why:
        computes: "Supplies dependency position 1,  reject certification cycles, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._require_platform_compatibility:
      why:
        computes: "Supplies dependency position 2,  require platform compatibility, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._reject_export_cycles:
      why:
        computes: "Supplies dependency position 3,  reject export cycles, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._validate_v4_interface_contract:
      why:
        computes: "Supplies dependency position 4,  validate v4 interface contract, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    .resolved_node_content_paths:
      why:
        computes: "Supplies dependency position 5, resolved node content paths, while transforming root, documents, schema root into the load v4 repository blueprint graph value."

    InstantiationsFromRepo
    ----------------------
    ._v4_node_from_document:
      why:
        constructs: "Supplies dependency position 1,  v4 node from document, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._declaration_schema_errors:
      why:
        constructs: "Supplies dependency position 2,  declaration schema errors, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._positive_version:
      why:
        constructs: "Supplies dependency position 3,  positive version, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._build_export_relationships:
      why:
        constructs: "Supplies dependency position 4,  build export relationships, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    .BlueprintEdge:
      why:
        constructs: "Supplies dependency position 5, BlueprintEdge, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._resolve_locator:
      why:
        constructs: "Supplies dependency position 6,  resolve locator, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    .InterfaceExport:
      why:
        constructs: "Supplies dependency position 7, InterfaceExport, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 8, BlueprintGraphError, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._build_source_relationships:
      why:
        constructs: "Supplies dependency position 9,  build source relationships, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    ._v4_authority_claims:
      why:
        constructs: "Supplies dependency position 10,  v4 authority claims, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    .RepositoryBlueprintGraph:
      why:
        constructs: "Supplies dependency position 11, RepositoryBlueprintGraph, while transforming root, documents, schema root into the load v4 repository blueprint graph value."
    """
    validators: dict[str, jsonschema.protocols.Validator] = {}
    nodes: dict[str, BlueprintNode] = {}
    for document in documents:
        errors = _declaration_schema_errors(
            document.path,
            dict(document.declaration),
            schema_root,
            validators,
            expected_schema_version=4,
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
        if module.module_root.name != module_id:
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
                module.module_root,
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
            if source.module_root != module.module_root:
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

    def resolve_v4_interface_use(
        source_id: str,
        source: BlueprintNode,
        interface_id: str,
        version: int,
    ) -> tuple[str, str]:
        """Resolve v4 interface use.

        Intent
        ------
        Use source id, source, interface id, version to resolve v4 interface use.

        Rationale
        ---------
        The operation combines source id, source, interface id, version through BlueprintGraphError, _positive_version, get and bounded failure checks, an explicit return value, making the resulting resolve v4 interface use behavior explicit across 7 conditional branches.

        Pseudocode
        ----------
        - set resolve_v4_interface_use_inputs = source id, source, interface id, version
        - if resolve_v4_interface_use_inputs violate blueprint invariants:
          - raise blueprint graph error
        - return resolve v4 interface use value

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._require_platform_compatibility:
          why:
            computes: "Supplies dependency position 1,  require platform compatibility, while transforming source id, source, interface id, version into the resolve v4 interface use value."

        InstantiationsFromRepo
        ----------------------
        ._positive_version:
          why:
            constructs: "Supplies dependency position 1,  positive version, while transforming source id, source, interface id, version into the resolve v4 interface use value."
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming source id, source, interface id, version into the resolve v4 interface use value."
        """
        if interface_id in source_interfaces:
            target_source, target_declaration = source_interfaces[interface_id]
            if source_modules[target_source.node_id] != source_modules[source_id]:
                raise BlueprintGraphError(
                    f"{source.node_id}: private interface {interface_id!r} "
                    "cannot be used cross-module"
                )
            actual_version = _positive_version(
                target_declaration.get("version"),
                interface_id,
            )
            if actual_version != version:
                raise BlueprintGraphError(
                    f"{source.node_id}: pins {interface_id} version {version}, "
                    f"but target version is {actual_version}"
                )
            return "uses-private-interface", target_source.node_id
        if interface_id in exports:
            export = exports[interface_id]
            if export.version != version:
                raise BlueprintGraphError(
                    f"{source.node_id}: pins {interface_id} version {version}, "
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
                    f"{interface_id}: export access is missing"
                )
            allowed = access.get("allowed_callers", [])
            if (
                caller_module != export.module_node_id
                and access.get("allow_all_modules") is not True
                and caller_module not in allowed
            ):
                raise BlueprintGraphError(
                    f"{source.node_id}: caller module "
                    f"{caller_module!r} is not allowed by {interface_id}"
                )
            target_source_id = export.source_node_id
            assert target_source_id is not None
            _require_platform_compatibility(
                source,
                sources[target_source_id],
                context=source.node_id,
            )
            return "uses-export", target_source_id
        raise BlueprintGraphError(
            f"{source.node_id}: unresolved interface {interface_id!r}"
        )

    interface_uses_by_source = _build_source_relationships(
        root,
        sources=sources,
        node_edges=node_edges,
        certification_edges=certification_edges,
        dependency_root=lambda source: source.module_root,
        resolve_interface_use=resolve_v4_interface_use,
    )
    export_edges, helper_edges = _build_export_relationships(
        exports,
        interface_uses_by_source,
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


def _v5_topology(
    root: Path,
    modules: Mapping[str, BlueprintNode],
) -> tuple[
    dict[str, str | None],
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, tuple[str, ...]],
]:
    """Transform root, modules into the v5 topology result used by the blueprint graph.

    Intent
    ------
    Use root, modules to transform root, modules into the v5 topology result used by the blueprint graph.

    Rationale
    ---------
    The operation combines root, modules through sorted, Path, items and ordered iteration, bounded failure checks, an explicit return value, making the resulting v5 topology behavior explicit across 7 conditional branches.

    Pseudocode
    ----------
    - set v5_topology_inputs = root, modules
    - if v5_topology_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v5_topology_inputs:
      - set validated_item = item
    - return v5 topology value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolve_locator:
      why:
        computes: "Supplies dependency position 1,  resolve locator, while transforming root, modules into the v5 topology value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming root, modules into the v5 topology value."
    """
    modules_by_marker = {
        Path(os.path.abspath(module.blueprint_path)): module_id
        for module_id, module in modules.items()
    }
    parents: dict[str, str | None] = {module_id: None for module_id in modules}
    children: dict[str, list[str]] = {module_id: [] for module_id in modules}
    local_segments: dict[str, str] = {}
    for parent_id, parent in sorted(modules.items()):
        raw_children = parent.declaration.get("children")
        if not isinstance(raw_children, dict):
            raise BlueprintGraphError(
                f"{parent.blueprint_path}: children must be a mapping"
            )
        seen_segments: set[str] = set()
        for child_id, locator in sorted(raw_children.items()):
            if not isinstance(child_id, str):
                raise BlueprintGraphError(
                    f"{parent.blueprint_path}: child id must be a string"
                )
            marker = Path(
                os.path.abspath(
                    _resolve_locator(
                        parent.module_root,
                        locator,
                        f"{parent.blueprint_path}:children.{child_id}",
                        root,
                    )
                )
            )
            actual_child_id = modules_by_marker.get(marker)
            if actual_child_id != child_id:
                raise BlueprintGraphError(
                    f"{parent.blueprint_path}: child {child_id!r} locator "
                    "does not identify its canonical module marker"
                )
            if parents[child_id] is not None:
                raise BlueprintGraphError(
                    f"{modules[child_id].blueprint_path}: duplicate registered parent"
                )
            child_root = modules[child_id].module_root
            try:
                child_root.relative_to(parent.module_root)
            except ValueError as exc:
                raise BlueprintGraphError(
                    f"{parent.blueprint_path}: child {child_id!r} is not contained"
                ) from exc
            segment = child_root.name
            if segment in seen_segments:
                raise BlueprintGraphError(
                    f"{parent.blueprint_path}: duplicate local child segment "
                    f"{segment!r}"
                )
            seen_segments.add(segment)
            parents[child_id] = parent_id
            children[parent_id].append(child_id)
            local_segments[child_id] = segment

    ancestry: dict[str, tuple[str, ...]] = {}
    visiting: list[str] = []

    def resolve_ancestry(module_id: str) -> tuple[str, ...]:
        """Resolve ancestry for blueprint graph operations.

        Intent
        ------
        Use module id to resolve ancestry for blueprint graph operations.

        Rationale
        ---------
        The operation combines module id through get, append, pop and bounded failure checks, an explicit return value, making the resulting resolve ancestry behavior explicit across 2 conditional branches.

        Pseudocode
        ----------
        - set resolve_ancestry_inputs = module id
        - if resolve_ancestry_inputs violate blueprint invariants:
          - raise blueprint graph error
        - return resolve ancestry value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming module id into the resolve ancestry value."
        """
        existing = ancestry.get(module_id)
        if existing is not None:
            return existing
        if module_id in visiting:
            start = visiting.index(module_id)
            raise BlueprintGraphError(
                "module registration cycle: "
                + " -> ".join(visiting[start:] + [module_id])
            )
        visiting.append(module_id)
        parent_id = parents[module_id]
        value = (
            (module_id,)
            if parent_id is None
            else (*resolve_ancestry(parent_id), module_id)
        )
        visiting.pop()
        ancestry[module_id] = value
        return value

    for module_id in sorted(modules):
        resolve_ancestry(module_id)
    return (
        dict(sorted(parents.items())),
        {
            module_id: tuple(sorted(child_ids))
            for module_id, child_ids in sorted(children.items())
        },
        dict(sorted(local_segments.items())),
        dict(sorted(ancestry.items())),
    )


def _v6_topology(
    modules: Mapping[str, BlueprintNode],
) -> tuple[
    dict[str, str | None],
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, tuple[str, ...]],
]:
    """Derive v6 topology from dotted identity and empty child registrations."""

    parents: dict[str, str | None] = {}
    children: dict[str, list[str]] = {module_id: [] for module_id in modules}
    local_segments: dict[str, str] = {}
    for module_id, module in sorted(modules.items()):
        parent_id, separator, segment = module_id.rpartition(".")
        parents[module_id] = parent_id if separator else None
        if separator:
            parent = modules.get(parent_id)
            if parent is None:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: missing parent module {parent_id!r}"
                )
            if module.module_root != parent.module_root / segment:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: module path must match dotted identity {module_id!r}"
                )
            registrations = parent.declaration.get("children")
            if not isinstance(registrations, Mapping) or registrations.get(segment) != {}:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: parent {parent_id!r} must register child segment {segment!r}"
                )
            children[parent_id].append(module_id)
            local_segments[module_id] = segment
        elif module.module_root.name != module_id:
            raise BlueprintGraphError(
                f"{module.blueprint_path}: top-level module id must match directory name"
            )
    for parent_id, parent in sorted(modules.items()):
        registrations = parent.declaration.get("children")
        if not isinstance(registrations, Mapping):
            raise BlueprintGraphError(f"{parent.blueprint_path}: children must be a mapping")
        expected = {local_segments[child_id] for child_id in children[parent_id]}
        if set(registrations) != expected:
            raise BlueprintGraphError(
                f"{parent.blueprint_path}: child registrations do not match direct descendants"
            )
    ancestry = {
        module_id: tuple(
            ".".join(module_id.split(".")[:index])
            for index in range(1, len(module_id.split(".")) + 1)
        )
        for module_id in modules
    }
    return (
        dict(sorted(parents.items())),
        {key: tuple(sorted(value)) for key, value in sorted(children.items())},
        dict(sorted(local_segments.items())),
        dict(sorted(ancestry.items())),
    )


def _v5_sources(
    root: Path,
    modules: Mapping[str, BlueprintNode],
    sources: Mapping[str, BlueprintNode],
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Transform root, modules, sources into the v5 sources result used by the blueprint graph.

    Intent
    ------
    Use root, modules, sources to transform root, modules, sources into the v5 sources result used by the blueprint graph.

    Rationale
    ---------
    The operation combines root, modules, sources through sorted, Path, items and ordered iteration, bounded failure checks, an explicit return value, making the resulting v5 sources behavior explicit across 7 conditional branches.

    Pseudocode
    ----------
    - set v5_sources_inputs = root, modules, sources
    - if v5_sources_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v5_sources_inputs:
      - set validated_item = item
    - return v5 sources value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolve_locator:
      why:
        computes: "Supplies dependency position 1,  resolve locator, while transforming root, modules, sources into the v5 sources value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming root, modules, sources into the v5 sources value."
    """
    sources_by_marker = {
        Path(os.path.abspath(source.blueprint_path)): source_id
        for source_id, source in sources.items()
    }
    module_sources: dict[str, tuple[str, ...]] = {}
    source_modules: dict[str, str] = {}
    for module_id, module in sorted(modules.items()):
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
            marker = Path(
                os.path.abspath(
                    _resolve_locator(
                        module.module_root,
                        entry.get("blueprint"),
                        f"{module.blueprint_path}:sources.{source_id}",
                        root,
                    )
                )
            )
            if sources_by_marker.get(marker) != source_id:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: source {source_id!r} locator "
                    "does not identify its canonical blueprint"
                )
            source = sources[source_id]
            if source.module_root != module.module_root:
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: contained source must be "
                    f"owned by module {module_id}"
                )
            if source_id in source_modules:
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: source is contained more than once"
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
    return module_sources, source_modules


def _v5_authority_claims_overlap(
    first: tuple[str, re.Pattern[str] | None],
    second: tuple[str, re.Pattern[str] | None],
) -> bool:
    """Transform first, second into the v5 authority claims overlap result used by the blueprint graph.

    Intent
    ------
    Use first, second to transform first, second into the v5 authority claims overlap result used by the blueprint graph.

    Rationale
    ---------
    The operation combines first, second through fullmatch and an explicit return value, making the resulting v5 authority claims overlap behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set v5_authority_claims_overlap_inputs = first, second
    - return v5 authority claims overlap value

    Wraps
    -----
    - none
    """
    first_match, first_pattern = first
    second_match, second_pattern = second
    if first_pattern is None and second_pattern is None:
        return first_match == second_match
    if first_pattern is None:
        return (
            second_pattern is not None
            and second_pattern.fullmatch(first_match) is not None
        )
    if second_pattern is None:
        return first_pattern.fullmatch(second_match) is not None
    # General Python-regex intersection is not decidable here. Nested
    # authority must fail closed when disjointness cannot be proved.
    return True


def _validate_v5_nested_authority(
    modules: Mapping[str, BlueprintNode],
    module_ancestry: Mapping[str, tuple[str, ...]],
) -> None:
    """Validate v5 nested authority.

    Intent
    ------
    Use modules, module ancestry to validate v5 nested authority.

    Rationale
    ---------
    The operation combines modules, module ancestry through _v4_authority_claims, sorted, append and ordered iteration, bounded failure checks, making the resulting validate v5 nested authority behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set validate_v5_nested_authority_inputs = modules, module ancestry
    - if validate_v5_nested_authority_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in validate_v5_nested_authority_inputs:
      - set validated_item = item
    - return none

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._v5_authority_claims_overlap:
      why:
        computes: "Supplies dependency position 1,  v5 authority claims overlap, while transforming modules, module ancestry into the validate v5 nested authority value."
    ._v4_authority_claims:
      why:
        computes: "Supplies dependency position 2,  v4 authority claims, while transforming modules, module ancestry into the validate v5 nested authority value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming modules, module ancestry into the validate v5 nested authority value."
    """
    mutable_claims: dict[
        str,
        list[tuple[str, re.Pattern[str] | None]],
    ] = {module_id: [] for module_id in modules}
    for module_id, _match, path, pattern in _v4_authority_claims(modules):
        mutable_claims[module_id].append((path, pattern))
    claims_by_module = {
        module_id: tuple(claims)
        for module_id, claims in mutable_claims.items()
    }

    for descendant_id, ancestry in sorted(module_ancestry.items()):
        for ancestor_id in ancestry[:-1]:
            for ancestor_claim in claims_by_module[ancestor_id]:
                for descendant_claim in claims_by_module[descendant_id]:
                    if not _v5_authority_claims_overlap(
                        ancestor_claim,
                        descendant_claim,
                    ):
                        continue
                    raise BlueprintGraphError(
                        "filesystem authority overlap between ancestor module "
                        f"{ancestor_id!r} and descendant module "
                        f"{descendant_id!r}: {ancestor_claim[0]!r} and "
                        f"{descendant_claim[0]!r}"
                    )


def _validate_v5_managed_skill_code_boundaries(
    modules: Mapping[str, BlueprintNode],
    sources: Mapping[str, BlueprintNode],
    module_sources: Mapping[str, tuple[str, ...]],
) -> None:
    """Keep executable behavior inside each managed skill's `_rtx` child.

    Intent
    ------
    Use modules, sources, module sources to keep executable behavior inside each managed skill's `_rtx` child.

    Rationale
    ---------
    The operation combines modules, sources, module sources through sorted, items, get and ordered iteration, bounded failure checks, making the resulting validate v5 managed skill code boundaries behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set validate_v5_managed_skill_code_boundaries_inputs = modules, sources, module sources
    - if validate_v5_managed_skill_code_boundaries_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in validate_v5_managed_skill_code_boundaries_inputs:
      - set validated_item = item
    - return none

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming modules, sources, module sources into the validate v5 managed skill code boundaries value."
    """

    for module_id, module in sorted(modules.items()):
        discovery = module.declaration.get("discovery")
        if not (
            isinstance(discovery, Mapping)
            and discovery.get("mechanism") == "skill"
            and module.module_root.parent.name == "skills"
        ):
            continue
        for source_id in module_sources.get(module_id, ()):
            source = sources[source_id]
            gateway = source.declaration.get("gateway")
            language = (
                gateway.get("language")
                if isinstance(gateway, Mapping)
                else None
            )
            if not isinstance(language, str) or not language.startswith(
                "Markdown"
            ):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: repository-managed skill "
                    "parents may contain only Markdown instruction sources; "
                    "move executable behavior to the registered _rtx child"
                )
            interfaces = source.declaration.get("interfaces")
            if not isinstance(interfaces, Mapping):
                continue
            for interface_id, declaration in interfaces.items():
                if (
                    isinstance(declaration, Mapping)
                    and "process_binding" in declaration
                ):
                    raise BlueprintGraphError(
                        f"{source.blueprint_path}: parent interface "
                        f"{interface_id!r} cannot declare a process binding; "
                        "export the _rtx child interface through a facade"
                    )


def _v5_interfaces_and_exports(
    modules: Mapping[str, BlueprintNode],
    sources: Mapping[str, BlueprintNode],
    source_modules: Mapping[str, str],
    module_children: Mapping[str, tuple[str, ...]],
    module_local_segments: Mapping[str, str],
    *,
    allow_facades: bool = True,
) -> tuple[dict[str, InterfaceExport], dict[str, InterfaceExport]]:
    """Transform modules, sources, source modules, module children into the v5 interfaces and exports result used by the blueprint graph.

    Intent
    ------
    Use modules, sources, source modules, module children to transform modules, sources, source modules, module children into the v5 interfaces and exports result used by the blueprint graph.

    Rationale
    ---------
    The operation combines modules, sources, source modules, module children through _v4_authority_claims, sorted, items and ordered iteration, bounded failure checks, an explicit return value, making the resulting v5 interfaces and exports behavior explicit across 16 conditional branches.

    Pseudocode
    ----------
    - set v5_interfaces_and_exports_inputs = modules, sources, source modules, module children
    - if v5_interfaces_and_exports_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v5_interfaces_and_exports_inputs:
      - set validated_item = item
    - return v5 interfaces and exports value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_v4_interface_contract:
      why:
        computes: "Supplies dependency position 1,  validate v4 interface contract, while transforming modules, sources, source modules, module children into the v5 interfaces and exports value."

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Supplies dependency position 1,  positive version, while transforming modules, sources, source modules, module children into the v5 interfaces and exports value."
    ._v4_authority_claims:
      why:
        constructs: "Supplies dependency position 2,  v4 authority claims, while transforming modules, sources, source modules, module children into the v5 interfaces and exports value."
    .InterfaceExport:
      why:
        constructs: "Supplies dependency position 3, InterfaceExport, while transforming modules, sources, source modules, module children into the v5 interfaces and exports value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 4, BlueprintGraphError, while transforming modules, sources, source modules, module children into the v5 interfaces and exports value."
    """
    authority_claims = _v4_authority_claims(modules)
    source_interfaces: dict[str, InterfaceExport] = {}
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
                    f"{source.blueprint_path}: invalid source interface declaration"
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
            source_interfaces[interface_id] = InterfaceExport(
                interface_id=interface_id,
                version=_positive_version(
                    declaration.get("version"),
                    interface_id,
                ),
                local_name=interface_id.rsplit(".interface.", 1)[-1],
                module_node_id=source_modules[source_id],
                declaration=declaration,
                source_node_id=source_id,
                source_interface_id=interface_id,
            )

    exports: dict[str, InterfaceExport] = {}
    facade_declarations: list[
        tuple[str, str, Mapping[str, JsonValue]]
    ] = []
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
            if not export_id.startswith(f"{module_id}.interface."):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} must use "
                    f"module namespace {module_id!r}"
                )
            if export_id in exports:
                raise BlueprintGraphError(f"duplicate export {export_id!r}")
            source_interface_id = export_declaration.get("source_interface")
            if isinstance(source_interface_id, str):
                source_interface = source_interfaces.get(source_interface_id)
                if source_interface is None:
                    raise BlueprintGraphError(
                        f"{module.blueprint_path}: export {export_id!r} targets "
                        f"unknown source interface {source_interface_id!r}"
                    )
                if source_interface.module_node_id != module_id:
                    raise BlueprintGraphError(
                        f"{module.blueprint_path}: export {export_id!r} "
                        "must bind a contained source interface"
                    )
                exports[export_id] = InterfaceExport(
                    interface_id=export_id,
                    version=source_interface.version,
                    local_name=export_id.rsplit(".interface.", 1)[-1],
                    module_node_id=module_id,
                    declaration=source_interface.declaration,
                    source_node_id=source_interface.source_node_id,
                    source_interface_id=source_interface_id,
                    export_declaration=export_declaration,
                    terminal_interface_id=export_id,
                    terminal_module_node_id=module_id,
                )
                continue
            facade = export_declaration.get("facade_interface")
            if not allow_facades:
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} must bind a contained source interface"
                )
            if not isinstance(facade, Mapping):
                raise BlueprintGraphError(
                    f"{module.blueprint_path}: export {export_id!r} must be "
                    "a source export or facade"
                )
            facade_declarations.append(
                (module_id, export_id, export_declaration)
            )

    for module_id, export_id, export_declaration in facade_declarations:
        facade = export_declaration["facade_interface"]
        assert isinstance(facade, Mapping)
        target_id = facade.get("interface")
        if not isinstance(target_id, str):
            raise BlueprintGraphError(
                f"{export_id}: facade target interface must be a string"
            )
        target = exports.get(target_id)
        if target is None:
            raise BlueprintGraphError(
                f"{export_id}: facade targets unknown or nonterminal export "
                f"{target_id!r}"
            )
        target_version = _positive_version(
            facade.get("version"),
            f"{export_id}.facade_interface",
        )
        if target.version != target_version:
            raise BlueprintGraphError(
                f"{export_id}: facade pins {target_id} version {target_version}, "
                f"but target version is {target.version}"
            )
        child_id = target.module_node_id
        if (
            child_id not in module_children.get(module_id, ())
            or module_local_segments.get(child_id) != "_rtx"
            or target.terminal_interface_id != target.interface_id
        ):
            raise BlueprintGraphError(
                f"{export_id}: facade must target one direct _rtx child export"
            )
        exports[export_id] = InterfaceExport(
            interface_id=export_id,
            version=target.version,
            local_name=export_id.rsplit(".interface.", 1)[-1],
            module_node_id=module_id,
            declaration=target.declaration,
            source_node_id=target.source_node_id,
            source_interface_id=target.source_interface_id,
            export_declaration=export_declaration,
            terminal_interface_id=target.interface_id,
            terminal_module_node_id=target.module_node_id,
        )
    return (
        dict(sorted(source_interfaces.items())),
        dict(sorted(exports.items())),
    )


def _setup_requirements(
    exports: Mapping[str, InterfaceExport],
) -> dict[str, tuple[tuple[str, int], ...]]:
    """Validate and return explicit public setup-interface prerequisites."""

    requirements: dict[str, tuple[tuple[str, int], ...]] = {}
    for export_id, export in sorted(exports.items()):
        declaration = export.export_declaration or {}
        raw = declaration.get("setup_requires_setup_of")
        is_setup = export_id.endswith(".interface.setup")
        if is_setup and raw is None:
            raise BlueprintGraphError(
                f"{export_id}: setup interface must declare setup_requires_setup_of"
            )
        if not is_setup and raw is not None:
            raise BlueprintGraphError(
                f"{export_id}: only setup interfaces may declare "
                "setup_requires_setup_of"
            )
        if not is_setup:
            continue
        if not isinstance(raw, list):
            raise BlueprintGraphError(
                f"{export_id}: setup_requires_setup_of must be a list"
            )
        parsed: list[tuple[str, int]] = []
        for index, entry in enumerate(raw):
            if not isinstance(entry, Mapping):
                raise BlueprintGraphError(
                    f"{export_id}: setup_requires_setup_of[{index}] must be a mapping"
                )
            target = entry.get("interface")
            version = entry.get("version")
            if (
                not isinstance(target, str)
                or type(version) is not int
                or version < 1
            ):
                raise BlueprintGraphError(
                    f"{export_id}: invalid setup prerequisite at index {index}"
                )
            parsed.append((target, version))
        if len(parsed) != len(set(parsed)):
            raise BlueprintGraphError(
                f"{export_id}: setup_requires_setup_of contains a duplicate"
            )
        requirements[export_id] = tuple(parsed)

    for export_id, entries in requirements.items():
        for target_id, version in entries:
            target = exports.get(target_id)
            if target_id not in requirements or target is None:
                raise BlueprintGraphError(
                    f"{export_id}: setup prerequisite {target_id!r} is not a "
                    "public setup interface"
                )
            if target.version != version:
                raise BlueprintGraphError(
                    f"{export_id}: setup prerequisite {target_id!r} pins version "
                    f"{version}, but target version is {target.version}"
                )

    graph = RepositoryBlueprintGraph(
        nodes={},
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        setup_requirements=requirements,
    )
    for export_id in requirements:
        setup_order(graph, export_id)
    return requirements


def setup_order(
    graph: RepositoryBlueprintGraph,
    root_setup_interface: str,
) -> tuple[str, ...]:
    """Return explicit setup prerequisites before their dependent interface."""

    requirements = graph.setup_requirements
    if root_setup_interface not in requirements:
        raise BlueprintGraphError(
            f"{root_setup_interface!r} is not a public setup interface"
        )
    state: dict[str, int] = {}
    order: list[str] = []
    stack: list[tuple[str, bool]] = [(root_setup_interface, False)]
    while stack:
        interface_id, exiting = stack.pop()
        status = state.get(interface_id, 0)
        if exiting:
            if status != 2:
                state[interface_id] = 2
                order.append(interface_id)
            continue
        if status == 2:
            continue
        if status == 1:
            raise BlueprintGraphError(
                f"setup dependency cycle reaches {interface_id}"
            )
        state[interface_id] = 1
        stack.append((interface_id, True))
        dependencies = requirements.get(interface_id)
        if dependencies is None:
            raise BlueprintGraphError(
                f"setup prerequisite {interface_id!r} is not declared"
            )
        for dependency_id, _version in reversed(sorted(dependencies)):
            if state.get(dependency_id) == 1:
                raise BlueprintGraphError(
                    f"setup dependency cycle reaches {dependency_id}"
                )
            if state.get(dependency_id) != 2:
                stack.append((dependency_id, False))
    return tuple(order)


def _v5_namespace_routes(
    modules: Mapping[str, BlueprintNode],
    exports: Mapping[str, InterfaceExport],
    module_children: Mapping[str, tuple[str, ...]],
    *,
    direct_segments: bool = False,
) -> tuple[
    dict[tuple[str, str], NamespaceRoute],
    tuple[RoutedInterface, ...],
]:
    """Transform modules, exports, module children into the v5 namespace routes result used by the blueprint graph.

    Intent
    ------
    Use modules, exports, module children to transform modules, exports, module children into the v5 namespace routes result used by the blueprint graph.

    Rationale
    ---------
    The operation combines modules, exports, module children through items, sorted, tuple and ordered iteration, bounded failure checks, an explicit return value, making the resulting v5 namespace routes behavior explicit across 13 conditional branches.

    Pseudocode
    ----------
    - set v5_namespace_routes_inputs = modules, exports, module children
    - if v5_namespace_routes_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v5_namespace_routes_inputs:
      - set validated_item = item
    - return v5 namespace routes value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .RoutedInterface:
      why:
        constructs: "Supplies dependency position 1, RoutedInterface, while transforming modules, exports, module children into the v5 namespace routes value."
    ._positive_version:
      why:
        constructs: "Supplies dependency position 2,  positive version, while transforming modules, exports, module children into the v5 namespace routes value."
    .NamespaceRoute:
      why:
        constructs: "Supplies dependency position 3, NamespaceRoute, while transforming modules, exports, module children into the v5 namespace routes value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 4, BlueprintGraphError, while transforming modules, exports, module children into the v5 namespace routes value."
    """
    direct_exports: dict[str, dict[str, InterfaceExport]] = {
        module_id: {} for module_id in modules
    }
    for interface_id, export in exports.items():
        direct_exports[export.module_node_id][interface_id] = export

    routes: dict[tuple[str, str], NamespaceRoute] = {}
    outward_surfaces: dict[str, dict[str, InterfaceExport]] = {}
    visiting: list[str] = []

    def outward_surface(module_id: str) -> dict[str, InterfaceExport]:
        """Transform module id into the outward surface result used by the blueprint graph.

        Intent
        ------
        Use module id to transform module id into the outward surface result used by the blueprint graph.

        Rationale
        ---------
        The operation combines module id through get, append, dict and ordered iteration, bounded failure checks, an explicit return value, making the resulting outward surface behavior explicit across 13 conditional branches.

        Pseudocode
        ----------
        - set outward_surface_inputs = module id
        - if outward_surface_inputs violate blueprint invariants:
          - raise blueprint graph error
        - for item in outward_surface_inputs:
          - set validated_item = item
        - return outward surface value

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .RoutedInterface:
          why:
            constructs: "Supplies dependency position 1, RoutedInterface, while transforming module id into the outward surface value."
        ._positive_version:
          why:
            constructs: "Supplies dependency position 2,  positive version, while transforming module id into the outward surface value."
        .NamespaceRoute:
          why:
            constructs: "Supplies dependency position 3, NamespaceRoute, while transforming module id into the outward surface value."
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 4, BlueprintGraphError, while transforming module id into the outward surface value."
        """
        existing = outward_surfaces.get(module_id)
        if existing is not None:
            return existing
        if module_id in visiting:
            start = visiting.index(module_id)
            raise BlueprintGraphError(
                "namespace route cycle: "
                + " -> ".join(visiting[start:] + [module_id])
            )
        visiting.append(module_id)
        surface = dict(direct_exports[module_id])
        raw_routes = modules[module_id].declaration.get("namespace_exports")
        if not isinstance(raw_routes, dict):
            raise BlueprintGraphError(
                f"{modules[module_id].blueprint_path}: "
                "namespace_exports must be a mapping"
            )
        for raw_child_id, declaration in sorted(raw_routes.items()):
            child_id = (
                f"{module_id}.{raw_child_id}"
                if direct_segments
                else raw_child_id
            )
            if (
                not isinstance(child_id, str)
                or not isinstance(declaration, dict)
                or child_id not in module_children.get(module_id, ())
            ):
                raise BlueprintGraphError(
                    f"{modules[module_id].blueprint_path}: namespace export "
                    f"{child_id!r} must target a registered direct child"
                )
            child_version = _positive_version(
                declaration.get("version"),
                f"{modules[module_id].blueprint_path}:"
                f"namespace_exports.{child_id}",
            )
            if modules[child_id].version != child_version:
                raise BlueprintGraphError(
                    f"{module_id}: namespace route pins {child_id} version "
                    f"{child_version}, but target version is "
                    f"{modules[child_id].version}"
                )
            child_surface = outward_surface(child_id)
            raw_surface = declaration.get("surface")
            if not isinstance(raw_surface, Mapping):
                raise BlueprintGraphError(
                    f"{module_id}: namespace route {child_id} has invalid surface"
                )
            if direct_segments and "all" in raw_surface:
                raise BlueprintGraphError(
                    f"{module_id}: namespace route {child_id} requires explicit only surface"
                )
            if raw_surface.get("all") is True:
                selected = dict(child_surface)
            else:
                only = raw_surface.get("only")
                if not isinstance(only, Mapping):
                    raise BlueprintGraphError(
                        f"{module_id}: namespace route {child_id} "
                        "must select all or only"
                    )
                selected = {}
                for interface_id, raw_version in sorted(only.items()):
                    if not isinstance(interface_id, str):
                        raise BlueprintGraphError(
                            f"{module_id}: routed interface id must be a string"
                        )
                    export = child_surface.get(interface_id)
                    version = _positive_version(
                        raw_version,
                        f"{module_id}:namespace_exports.{child_id}."
                        f"surface.only.{interface_id}",
                    )
                    if export is None:
                        raise BlueprintGraphError(
                            f"{module_id}: namespace route {child_id} cannot "
                            f"expose private or unrouted interface {interface_id!r}"
                        )
                    if export.version != version:
                        raise BlueprintGraphError(
                            f"{module_id}: namespace route pins {interface_id} "
                            f"version {version}, but target version is "
                            f"{export.version}"
                        )
                    selected[interface_id] = export
            interface_access = declaration.get("interface_access", {})
            if not isinstance(interface_access, Mapping) or not set(
                interface_access
            ) <= set(selected):
                raise BlueprintGraphError(
                    f"{module_id}: interface_access must name only materialized "
                    f"interfaces of route {child_id}"
                )

            materialized = tuple(
                RoutedInterface(
                    route_owner_id=module_id,
                    child_module_id=child_id,
                    interface_id=interface_id,
                    version=export.version,
                    terminal_module_id=(
                        export.terminal_module_node_id
                        or export.module_node_id
                    ),
                    terminal_module_version=modules[
                        export.terminal_module_node_id
                        or export.module_node_id
                    ].version,
                )
                for interface_id, export in sorted(selected.items())
            )
            routes[(module_id, child_id)] = NamespaceRoute(
                route_owner_id=module_id,
                child_module_id=child_id,
                child_version=child_version,
                declaration=declaration,
                materialized_interfaces=materialized,
            )
            for interface_id, export in selected.items():
                if interface_id in surface:
                    raise BlueprintGraphError(
                        f"{module_id}: duplicate outward interface "
                        f"{interface_id!r}"
                    )
                surface[interface_id] = export
        visiting.pop()
        outward_surfaces[module_id] = surface
        return surface

    for module_id in sorted(modules):
        outward_surface(module_id)
    routed = tuple(
        sorted(
            (
                item
                for route in routes.values()
                for item in route.materialized_interfaces
            ),
            key=lambda item: (
                item.route_owner_id,
                item.child_module_id,
                item.interface_id,
                item.version,
            ),
        )
    )
    return dict(sorted(routes.items())), routed


def _v5_content_ownership(
    root: Path,
    nodes: Mapping[str, BlueprintNode],
    modules: Mapping[str, BlueprintNode],
    sources: Mapping[str, BlueprintNode],
    module_sources: Mapping[str, tuple[str, ...]],
    module_children: Mapping[str, tuple[str, ...]],
) -> dict[Path, str]:
    """Transform root, nodes, modules, sources into the v5 content ownership result used by the blueprint graph.

    Intent
    ------
    Use root, nodes, modules, sources to transform root, nodes, modules, sources into the v5 content ownership result used by the blueprint graph.

    Rationale
    ---------
    The operation combines root, nodes, modules, sources through sorted, Path, items and ordered iteration, bounded failure checks, an explicit return value, making the resulting v5 content ownership behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set v5_content_ownership_inputs = root, nodes, modules, sources
    - if v5_content_ownership_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in v5_content_ownership_inputs:
      - set validated_item = item
    - return v5 content ownership value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolved_node_content_paths:
      why:
        computes: "Supplies dependency position 1, resolved node content paths, while transforming root, nodes, modules, sources into the v5 content ownership value."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming root, nodes, modules, sources into the v5 content ownership value."
    """
    blueprint_paths = {
        Path(os.path.abspath(node.blueprint_path)) for node in nodes.values()
    }
    module_content: dict[str, set[Path]] = {}
    source_content: dict[str, set[Path]] = {}
    for module_id, module in sorted(modules.items()):
        excluded_roots = tuple(
            modules[child_id].module_root
            for child_id in module_children.get(module_id, ())
        )
        paths = {
            Path(os.path.abspath(path))
            for path in resolved_node_content_paths(
                module,
                root,
                excluded_module_roots=excluded_roots,
            )
        }
        if paths & blueprint_paths:
            raise BlueprintGraphError(
                f"{module.blueprint_path}: content cannot include blueprint files"
            )
        module_content[module_id] = paths
        for source_id in module_sources[module_id]:
            source = sources[source_id]
            source_paths = {
                Path(os.path.abspath(path))
                for path in resolved_node_content_paths(
                    source,
                    root,
                    excluded_module_roots=excluded_roots,
                )
            }
            if source_paths & blueprint_paths:
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: content cannot include blueprint files"
                )
            source_content[source_id] = source_paths

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
    return direct_file_owners


def _v6_interface_facets(
    root: Path,
    *,
    modules: Mapping[str, BlueprintNode],
    sources: Mapping[str, BlueprintNode],
    source_modules: Mapping[str, str],
    module_children: Mapping[str, tuple[str, ...]],
    interface_uses_by_source: Mapping[str, tuple[tuple[str, int], ...]],
) -> tuple[
    dict[str, tuple[Path, ...]],
    dict[str, tuple[tuple[str, int], ...]],
]:
    """Resolve explicit v6 interface facets inside source-owned envelopes.

    Intent
    ------
    Materialize each source-interface content and interface-use subset from
    authored v6 declarations without transferring node ownership or authority
    to the interface.

    Rationale
    ---------
    Certification needs one canonical graph-owned partition to attribute file
    and dependency drift. Reusing node content resolution preserves path,
    gateway, forbidden-artifact, child-module, and regular-file invariants.

    Pseudocode
    ----------
    - for each behavioral source:
      - resolve its complete content and interface-use envelopes
      - for each declared interface:
        - resolve interface content with the source gateway and ownership root
        - reject content outside the source envelope
        - reject interface uses outside the source envelope
    - return canonical content and use mappings sorted by interface identity

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolved_node_content_paths:
      why:
        computes: "Applies the canonical node content grammar and gateway requirement to each interface subset."

    InstantiationsFromRepo
    ----------------------
    .BlueprintNode:
      why:
        constructs: "Carries the source identity and gateway with one interface content declaration through canonical resolution."
    .BlueprintGraphError:
      why:
        raises: "Rejects interface facets that escape their source-owned content or declared interface-use envelope."
    """

    content_paths: dict[str, tuple[Path, ...]] = {}
    interface_uses: dict[str, tuple[tuple[str, int], ...]] = {}
    for source_id, source in sorted(sources.items()):
        module_id = source_modules[source_id]
        excluded_roots = tuple(
            modules[child_id].module_root
            for child_id in module_children.get(module_id, ())
        )
        source_content = set(
            resolved_node_content_paths(
                source,
                root,
                excluded_module_roots=excluded_roots,
            )
        )
        source_uses = set(interface_uses_by_source[source_id])
        raw_interfaces = source.declaration.get("interfaces")
        if not isinstance(raw_interfaces, Mapping):
            raise BlueprintGraphError(
                f"{source.blueprint_path}: interfaces must be a mapping"
            )
        for interface_id, declaration in sorted(raw_interfaces.items()):
            if not isinstance(interface_id, str) or not isinstance(
                declaration,
                Mapping,
            ):
                raise BlueprintGraphError(
                    f"{source.blueprint_path}: invalid source interface declaration"
                )
            raw_content = declaration.get("content")
            facet_declaration = dict(source.declaration)
            facet_declaration["content"] = raw_content
            facet_node = BlueprintNode(
                node_id=source.node_id,
                node_type=source.node_type,
                version=source.version,
                module_root=source.module_root,
                blueprint_path=source.blueprint_path,
                gateway_path=source.gateway_path,
                declaration=facet_declaration,
            )
            resolved = resolved_node_content_paths(
                facet_node,
                root,
                excluded_module_roots=excluded_roots,
            )
            if not resolved or not set(resolved) <= source_content:
                raise BlueprintGraphError(
                    f"{interface_id}: content must be a non-empty subset of "
                    f"source {source_id} content"
                )

            raw_uses = declaration.get("uses_interfaces")
            if not isinstance(raw_uses, list):
                raise BlueprintGraphError(
                    f"{interface_id}: uses_interfaces must be a list"
                )
            uses: list[tuple[str, int]] = []
            for index, use in enumerate(raw_uses):
                if not isinstance(use, Mapping):
                    raise BlueprintGraphError(
                        f"{interface_id}: uses_interfaces[{index}] must be a mapping"
                    )
                target_id = use.get("interface")
                version = use.get("version")
                if (
                    not isinstance(target_id, str)
                    or not isinstance(version, int)
                    or isinstance(version, bool)
                ):
                    raise BlueprintGraphError(
                        f"{interface_id}: invalid uses_interfaces[{index}]"
                    )
                uses.append((target_id, version))
            if not set(uses) <= source_uses:
                raise BlueprintGraphError(
                    f"{interface_id}: uses_interfaces must be a subset of "
                    f"source {source_id} uses_interfaces"
                )
            content_paths[interface_id] = tuple(sorted(resolved))
            interface_uses[interface_id] = tuple(sorted(uses))
    return dict(sorted(content_paths.items())), dict(sorted(interface_uses.items()))


def _unique_certification_edges(
    edges: list[CertificationEdge],
) -> tuple[CertificationEdge, ...]:
    """Transform edges into the unique certification edges result used by the blueprint graph.

    Intent
    ------
    Use edges to transform edges into the unique certification edges result used by the blueprint graph.

    Rationale
    ---------
    The operation combines edges through tuple, sorted, values and an explicit return value, making the resulting unique certification edges behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set unique_certification_edges_inputs = edges
    - return unique certification edges value

    Wraps
    -----
    - none
    """
    unique = {
        (
            edge.relation,
            edge.source_node_id,
            edge.target_node_id,
            edge.target_version,
        ): edge
        for edge in edges
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda edge: (
                edge.source_node_id,
                edge.relation,
                edge.target_node_id,
                edge.target_version or 0,
            ),
        )
    )


def _load_v5_repository_blueprint_graph(
    root: Path,
    documents: tuple[Any, ...],
    *,
    schema_root: Path,
    schema_version: int = 5,
) -> RepositoryBlueprintGraph:
    """Load v5 repository blueprint graph.

    Intent
    ------
    Use root, documents, schema root to load v5 repository blueprint graph.

    Rationale
    ---------
    The operation combines root, documents, schema root through _v5_topology, _validate_v5_nested_authority, _v5_sources and ordered iteration, bounded failure checks, an explicit return value, making the resulting load v5 repository blueprint graph behavior explicit across 11 conditional branches.

    Pseudocode
    ----------
    - set load_v5_repository_blueprint_graph_inputs = root, documents, schema root
    - if load_v5_repository_blueprint_graph_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in load_v5_repository_blueprint_graph_inputs:
      - set validated_item = item
    - return load v5 repository blueprint graph value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_v5_managed_skill_code_boundaries:
      why:
        computes: "Supplies dependency position 1,  validate v5 managed skill code boundaries, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._reject_certification_cycles:
      why:
        computes: "Supplies dependency position 2,  reject certification cycles, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._validate_v5_nested_authority:
      why:
        computes: "Supplies dependency position 3,  validate v5 nested authority, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._require_platform_compatibility:
      why:
        computes: "Supplies dependency position 4,  require platform compatibility, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._reject_export_cycles:
      why:
        computes: "Supplies dependency position 5,  reject export cycles, while transforming root, documents, schema root into the load v5 repository blueprint graph value."

    InstantiationsFromRepo
    ----------------------
    ._unique_certification_edges:
      why:
        constructs: "Supplies dependency position 1,  unique certification edges, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._declaration_schema_errors:
      why:
        constructs: "Supplies dependency position 2,  declaration schema errors, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._v5_content_ownership:
      why:
        constructs: "Supplies dependency position 3,  v5 content ownership, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._node_from_document:
      why:
        constructs: "Supplies dependency position 4,  node from document, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._v5_interfaces_and_exports:
      why:
        constructs: "Supplies dependency position 5,  v5 interfaces and exports, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._build_export_relationships:
      why:
        constructs: "Supplies dependency position 6,  build export relationships, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    .BlueprintEdge:
      why:
        constructs: "Supplies dependency position 7, BlueprintEdge, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._v5_topology:
      why:
        constructs: "Supplies dependency position 8,  v5 topology, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._v5_namespace_routes:
      why:
        constructs: "Supplies dependency position 9,  v5 namespace routes, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 10, BlueprintGraphError, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._v5_sources:
      why:
        constructs: "Supplies dependency position 11,  v5 sources, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    .CertificationEdge:
      why:
        constructs: "Supplies dependency position 12, CertificationEdge, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    ._build_source_relationships:
      why:
        constructs: "Supplies dependency position 13,  build source relationships, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    .RepositoryBlueprintGraph:
      why:
        constructs: "Supplies dependency position 14, RepositoryBlueprintGraph, while transforming root, documents, schema root into the load v5 repository blueprint graph value."
    """
    validators: dict[str, jsonschema.protocols.Validator] = {}
    nodes: dict[str, BlueprintNode] = {}
    for document in documents:
        errors = _declaration_schema_errors(
            document.path,
            dict(document.declaration),
            schema_root,
            validators,
            expected_schema_version=schema_version,
        )
        if errors:
            raise errors[0]
        node = _node_from_document(document, expected_schema_version=schema_version)
        previous = nodes.get(node.node_id)
        if previous is not None:
            raise BlueprintGraphError(
                f"duplicate node id {node.node_id!r}: "
                f"{previous.blueprint_path} and {node.blueprint_path}"
            )
        nodes[node.node_id] = node

    modules = {
        node_id: node
        for node_id, node in nodes.items()
        if node.node_type == "module"
    }
    sources = {
        node_id: node
        for node_id, node in nodes.items()
        if node.node_type == "behavioral_source"
    }
    if not modules:
        raise BlueprintGraphError(
            f"version {schema_version} repository graph requires at least one module"
        )
    if len(modules) + len(sources) != len(nodes):
        raise BlueprintGraphError(
            f"version {schema_version} repository graph permits only module and "
            "behavioral_source nodes"
        )

    (
        module_parents,
        module_children,
        module_local_segments,
        module_ancestry,
    ) = (
        _v6_topology(modules)
        if schema_version == 6
        else _v5_topology(root, modules)
    )
    _validate_v5_nested_authority(modules, module_ancestry)
    module_sources, source_modules = _v5_sources(
        root,
        modules,
        sources,
    )
    if schema_version == 5:
        _validate_v5_managed_skill_code_boundaries(
            modules,
            sources,
            module_sources,
        )
    source_interfaces, exports = _v5_interfaces_and_exports(
        modules,
        sources,
        source_modules,
        module_children,
        module_local_segments,
        allow_facades=schema_version == 5,
    )
    setup_requirements = _setup_requirements(exports) if schema_version == 6 else {}
    namespace_routes, routed_interfaces = _v5_namespace_routes(
        modules,
        exports,
        module_children,
        direct_segments=schema_version == 6,
    )

    node_edges: list[BlueprintEdge] = []
    certification_edges: list[CertificationEdge] = []
    for child_id, parent_id in sorted(module_parents.items()):
        if parent_id is None:
            continue
        child = modules[child_id]
        node_edges.append(
            BlueprintEdge(
                "contains-module",
                parent_id,
                child_id,
                child.version,
                child.blueprint_path,
            )
        )
    for module_id, source_ids in sorted(module_sources.items()):
        for source_id in source_ids:
            source = sources[source_id]
            edge = BlueprintEdge(
                "contains-source",
                module_id,
                source_id,
                source.version,
                source.blueprint_path,
            )
            node_edges.append(edge)
            certification_edges.append(
                CertificationEdge(
                    "contains-source",
                    module_id,
                    source_id,
                    source.version,
                )
            )
    for route in namespace_routes.values():
        child = modules[route.child_module_id]
        node_edges.append(
            BlueprintEdge(
                "routes-child-namespace",
                route.route_owner_id,
                route.child_module_id,
                child.version,
                child.blueprint_path,
            )
        )
        certification_edges.append(
            CertificationEdge(
                "routes-child-namespace",
                route.route_owner_id,
                route.child_module_id,
                child.version,
            )
        )
        terminal_ids = {
            item.terminal_module_id for item in route.materialized_interfaces
        }
        for terminal_id in sorted(terminal_ids):
            terminal = modules[terminal_id]
            node_edges.append(
                BlueprintEdge(
                    "routes-terminal-module",
                    route.route_owner_id,
                    terminal_id,
                    terminal.version,
                    terminal.blueprint_path,
                )
            )
            certification_edges.append(
                CertificationEdge(
                    "routes-terminal-module",
                    route.route_owner_id,
                    terminal_id,
                    terminal.version,
                )
            )
    for export in exports.values():
        terminal_id = export.terminal_interface_id or export.interface_id
        if terminal_id == export.interface_id:
            continue
        terminal_module_id = export.terminal_module_node_id
        source_id = export.source_node_id
        assert terminal_module_id is not None and source_id is not None
        terminal_module = modules[terminal_module_id]
        source = sources[source_id]
        node_edges.extend(
            (
                BlueprintEdge(
                    "facades-child-export",
                    export.module_node_id,
                    terminal_module_id,
                    terminal_module.version,
                    terminal_module.blueprint_path,
                ),
                BlueprintEdge(
                    "facades-implementing-source",
                    export.module_node_id,
                    source_id,
                    source.version,
                    source.blueprint_path,
                ),
            )
        )
        certification_edges.extend(
            (
                CertificationEdge(
                    "facades-child-export",
                    export.module_node_id,
                    terminal_module_id,
                    terminal_module.version,
                ),
                CertificationEdge(
                    "facades-implementing-source",
                    export.module_node_id,
                    source_id,
                    source.version,
                ),
            )
        )

    provisional = RepositoryBlueprintGraph(
        nodes=dict(sorted(nodes.items())),
        node_edges=tuple(sorted(node_edges, key=_edge_key)),
        exports=exports,
        export_edges=(),
        helper_edges=(),
        certification_edges=_unique_certification_edges(certification_edges),
        module_sources=module_sources,
        schema_version=schema_version,
        source_modules=source_modules,
        source_interfaces=source_interfaces,
        module_parents=module_parents,
        module_children=module_children,
        module_local_segments=module_local_segments,
        module_ancestry=module_ancestry,
        namespace_routes=namespace_routes,
        routed_interfaces=routed_interfaces,
    )
    from .authorization import (  # noqa: PLC0415
        _ResolutionFailure,
        _validate_authorization_declarations,
        AuthorizationRequest,
        resolve_interface_authorization,
    )

    try:
        _validate_authorization_declarations(provisional)
    except _ResolutionFailure as exc:
        raise BlueprintGraphError(exc.diagnostic) from exc

    def resolve_v5_interface_use(
        source_id: str,
        source: BlueprintNode,
        interface_id: str,
        version: int,
    ) -> tuple[str, str]:
        """Resolve v5 interface use.

        Intent
        ------
        Use source id, source, interface id, version to resolve v5 interface use.

        Rationale
        ---------
        The operation combines source id, source, interface id, version through get, BlueprintGraphError, resolve_interface_authorization and bounded failure checks, an explicit return value, making the resulting resolve v5 interface use behavior explicit across 5 conditional branches.

        Pseudocode
        ----------
        - set resolve_v5_interface_use_inputs = source id, source, interface id, version
        - if resolve_v5_interface_use_inputs violate blueprint invariants:
          - raise blueprint graph error
        - return resolve v5 interface use value

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._require_platform_compatibility:
          why:
            computes: "Supplies dependency position 1,  require platform compatibility, while transforming source id, source, interface id, version into the resolve v5 interface use value."

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming source id, source, interface id, version into the resolve v5 interface use value."
        """
        private = source_interfaces.get(interface_id)
        if private is not None:
            if private.module_node_id != source_modules[source_id]:
                raise BlueprintGraphError(
                    f"{source_id}: private interface {interface_id!r} "
                    "cannot be used cross-module"
                )
            if private.version != version:
                raise BlueprintGraphError(
                    f"{source_id}: pins {interface_id} version {version}, "
                    f"but target version is {private.version}"
                )
            target_source_id = private.source_node_id
            assert target_source_id is not None
            return "uses-private-interface", target_source_id
        if interface_id in exports:
            result = resolve_interface_authorization(
                provisional,
                AuthorizationRequest(
                    caller_module_id=source_modules[source_id],
                    caller_source_id=source_id,
                    interface_id=interface_id,
                    version=version,
                ),
            )
            if not result.allowed:
                raise BlueprintGraphError(
                    f"{source_id}: {result.diagnostic}"
                )
            target_source_id = result.implementing_source_id
            assert target_source_id is not None
            _require_platform_compatibility(
                source,
                sources[target_source_id],
                context=source_id,
            )
            return "uses-export", target_source_id
        raise BlueprintGraphError(
            f"{source_id}: unresolved interface {interface_id!r}"
        )

    interface_uses_by_source = _build_source_relationships(
        root,
        sources=sources,
        node_edges=node_edges,
        certification_edges=certification_edges,
        dependency_root=lambda source: source.module_root,
        resolve_interface_use=resolve_v5_interface_use,
    )
    export_edges, helper_edges = _build_export_relationships(
        exports,
        interface_uses_by_source,
    )

    direct_file_owners = _v5_content_ownership(
        root,
        nodes,
        modules,
        sources,
        module_sources,
        module_children,
    )
    if schema_version == 6:
        interface_content_paths, interface_uses = _v6_interface_facets(
            root,
            modules=modules,
            sources=sources,
            source_modules=source_modules,
            module_children=module_children,
            interface_uses_by_source=interface_uses_by_source,
        )
    else:
        interface_content_paths = {}
        interface_uses = {}
    certification_edge_tuple = _unique_certification_edges(
        certification_edges
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
        exports=exports,
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
        schema_version=schema_version,
        source_modules=source_modules,
        source_interfaces=source_interfaces,
        module_parents=module_parents,
        module_children=module_children,
        module_local_segments=module_local_segments,
        module_ancestry=module_ancestry,
        namespace_routes=namespace_routes,
        routed_interfaces=routed_interfaces,
        interface_content_paths=interface_content_paths,
        interface_uses=interface_uses,
        setup_requirements=setup_requirements,
    )


def _declared_interface_references(value: JsonValue) -> tuple[str, ...]:
    """Return interface identifiers conservatively referenced by a declaration.

    Intent
    ------
    Use value to return interface identifiers conservatively referenced by a declaration.

    Rationale
    ---------
    The operation combines value through set, visit, tuple and ordered iteration, an explicit return value, making the resulting declared interface references behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set declared_interface_references_inputs = value
    - for item in declared_interface_references_inputs:
      - set validated_item = item
    - return declared interface references value

    Wraps
    -----
    - none
    """

    found: set[str] = set()

    def visit(item: JsonValue) -> None:
        """Transform item into the visit result used by the blueprint graph.

        Intent
        ------
        Use item to transform item into the visit result used by the blueprint graph.

        Rationale
        ---------
        The operation combines item through isinstance, get, values and ordered iteration, an explicit return value, making the resulting visit behavior explicit across 3 conditional branches.

        Pseudocode
        ----------
        - set visit_inputs = item
        - for item in visit_inputs:
          - set validated_item = item
        - return visit value

        Wraps
        -----
        - none
        """
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        interface_id = item.get("interface")
        if isinstance(interface_id, str) and ".interface." in interface_id:
            found.add(interface_id)
        for child in item.values():
            visit(child)

    visit(value)
    return tuple(sorted(found))


def _declared_absolute_caller_references(value: JsonValue) -> tuple[str, ...]:
    """Return absolute module IDs named by access-policy caller lists.

    Intent
    ------
    Use value to return absolute module ids named by access-policy caller lists.

    Rationale
    ---------
    The operation combines value through set, visit, tuple and ordered iteration, an explicit return value, making the resulting declared absolute caller references behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set declared_absolute_caller_references_inputs = value
    - for item in declared_absolute_caller_references_inputs:
      - set validated_item = item
    - return declared absolute caller references value

    Wraps
    -----
    - none
    """

    found: set[str] = set()

    def visit(item: JsonValue) -> None:
        """Transform item into the visit result used by the blueprint graph.

        Intent
        ------
        Use item to transform item into the visit result used by the blueprint graph.

        Rationale
        ---------
        The operation combines item through isinstance, get, values and ordered iteration, an explicit return value, making the resulting visit behavior explicit across 3 conditional branches.

        Pseudocode
        ----------
        - set visit_inputs = item
        - for item in visit_inputs:
          - set validated_item = item
        - return visit value

        Wraps
        -----
        - none
        """
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        raw_callers = item.get("allowed_callers")
        if isinstance(raw_callers, list):
            found.update(
                caller
                for caller in raw_callers
                if isinstance(caller, str) and not caller.startswith(".")
            )
        for child in item.values():
            visit(child)

    visit(value)
    return tuple(sorted(found))


def _declared_source_dependencies(value: JsonValue) -> tuple[str, ...]:
    """Return behavioral-source IDs named by direct dependency declarations.

    Intent
    ------
    Use value to return behavioral-source ids named by direct dependency declarations.

    Rationale
    ---------
    The operation combines value through get, tuple, isinstance and an explicit return value, making the resulting declared source dependencies behavior explicit across 2 conditional branches.

    Pseudocode
    ----------
    - set declared_source_dependencies_inputs = value
    - return declared source dependencies value

    Wraps
    -----
    - none
    """

    if not isinstance(value, dict):
        return ()
    raw_dependencies = value.get("dependencies", [])
    if not isinstance(raw_dependencies, list):
        return ()
    return tuple(
        sorted(
            {
                source_id
                for dependency in raw_dependencies
                if isinstance(dependency, dict)
                and isinstance((source_id := dependency.get("source")), str)
            }
        )
    )


def _dispatch_document_closure(
    documents: tuple[BlueprintDocument, ...],
    *,
    caller_module_id: str,
    interface_id: str,
) -> tuple[tuple[BlueprintDocument, ...], frozenset[Path]]:
    """Select a conservative module-family closure for one dispatch request.

    Intent
    ------
    Use documents, caller module id, interface id to select a conservative module-family closure for one dispatch request.

    Rationale
    ---------
    The operation combines documents, caller module id, interface id through get, sorted, frozenset and ordered iteration, bounded failure checks, an explicit return value, making the resulting dispatch document closure behavior explicit across 16 conditional branches.

    Pseudocode
    ----------
    - set dispatch_document_closure_inputs = documents, caller module id, interface id
    - if dispatch_document_closure_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in dispatch_document_closure_inputs:
      - set validated_item = item
    - return dispatch document closure value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._declared_interface_references:
      why:
        computes: "Supplies dependency position 1,  declared interface references, while transforming documents, caller module id, interface id into the dispatch document closure value."
    ._declared_source_dependencies:
      why:
        computes: "Supplies dependency position 2,  declared source dependencies, while transforming documents, caller module id, interface id into the dispatch document closure value."

    InstantiationsFromRepo
    ----------------------
    ._declared_absolute_caller_references:
      why:
        constructs: "Supplies dependency position 1,  declared absolute caller references, while transforming documents, caller module id, interface id into the dispatch document closure value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 2, BlueprintGraphError, while transforming documents, caller module id, interface id into the dispatch document closure value."
    """

    nodes: dict[str, BlueprintDocument] = {}
    module_documents: dict[str, BlueprintDocument] = {}
    export_owners: dict[str, str] = {}
    module_ids_by_root: dict[Path, str] = {}
    children: dict[str, set[str]] = {}
    parents: dict[str, str] = {}
    for document in documents:
        node_id = document.node_id
        if node_id is not None:
            previous = nodes.get(node_id)
            if previous is not None:
                raise BlueprintGraphError(
                    f"duplicate node id {node_id!r}: "
                    f"{previous.path} and {document.path}"
                )
            nodes[node_id] = document
        if document.node_type != "module" or node_id is None:
            continue
        module_documents[node_id] = document
        module_ids_by_root[document.module_root] = node_id
        raw_exports = document.declaration.get("exports", {})
        if isinstance(raw_exports, dict):
            for exported_id in raw_exports:
                if not isinstance(exported_id, str):
                    continue
                previous_owner = export_owners.get(exported_id)
                if previous_owner is not None:
                    raise BlueprintGraphError(
                        f"duplicate export {exported_id!r}: "
                        f"{previous_owner!r} and {node_id!r}"
                    )
                export_owners[exported_id] = node_id
        raw_children = document.declaration.get("children", {})
        child_ids = {
            child_id
            for child_id in raw_children
            if isinstance(child_id, str)
        } if isinstance(raw_children, dict) else set()
        children[node_id] = child_ids
        for child_id in child_ids:
            previous_parent = parents.get(child_id)
            if previous_parent is not None and previous_parent != node_id:
                raise BlueprintGraphError(
                    f"module {child_id!r} has multiple parents: "
                    f"{previous_parent!r} and {node_id!r}"
                )
            parents[child_id] = node_id

    if caller_module_id not in module_documents:
        raise BlueprintGraphError(
            f"caller module {caller_module_id!r} does not exist"
        )
    target_owner = export_owners.get(interface_id)
    if target_owner is None:
        raise BlueprintGraphError(f"unknown export {interface_id!r}")

    selected = {caller_module_id, target_owner}
    while True:
        expanded = set(selected)
        for module_id in tuple(selected):
            parent_id = parents.get(module_id)
            if parent_id is not None:
                expanded.add(parent_id)
            expanded.update(children.get(module_id, ()))
        selected_roots = {
            module_documents[module_id].module_root
            for module_id in expanded
            if module_id in module_documents
        }
        for document in documents:
            if document.module_root not in selected_roots:
                continue
            declaration = dict(document.declaration)
            for referenced_id in _declared_interface_references(
                declaration
            ):
                provider_id = export_owners.get(referenced_id)
                if provider_id is not None:
                    expanded.add(provider_id)
            expanded.update(_declared_absolute_caller_references(declaration))
            for source_id in _declared_source_dependencies(declaration):
                target_document = nodes.get(source_id)
                if target_document is None:
                    continue
                owner_id = module_ids_by_root.get(target_document.module_root)
                if owner_id is not None:
                    expanded.add(owner_id)
        if expanded == selected:
            break
        selected = expanded

    missing = sorted(selected - module_documents.keys())
    if missing:
        raise BlueprintGraphError(
            "dispatch closure references unavailable modules: "
            + ", ".join(missing)
        )
    selected_roots = frozenset(
        module_documents[module_id].module_root for module_id in selected
    )
    return (
        tuple(
            document
            for document in documents
            if document.module_root in selected_roots
        ),
        selected_roots,
    )


def load_dispatch_blueprint_graph(
    repo_root: Path,
    *,
    caller_module_id: str,
    interface_id: str,
    schema_root: Path | None = None,
) -> DispatchBlueprintGraph:
    """Load one dispatch closure while warning on proven-unrelated defects.

    Intent
    ------
    Use repo root, caller module id, interface id, schema root to load one dispatch closure while warning on proven-unrelated defects.

    Rationale
    ---------
    The operation combines repo root, caller module id, interface id, schema root through resolve, DispatchBlueprintGraph, Path and ordered iteration, bounded failure checks, an explicit return value, making the resulting load dispatch blueprint graph behavior explicit across 3 conditional branches.

    Pseudocode
    ----------
    - set load_dispatch_blueprint_graph_inputs = repo root, caller module id, interface id, schema root
    - if load_dispatch_blueprint_graph_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in load_dispatch_blueprint_graph_inputs:
      - set validated_item = item
    - return load dispatch blueprint graph value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .load_repository_blueprint_graph:
      why:
        computes: "Supplies dependency position 1, load repository blueprint graph, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."

    InstantiationsFromRepo
    ----------------------
    ._load_v5_repository_blueprint_graph:
      why:
        constructs: "Supplies dependency position 1,  load v5 repository blueprint graph, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."
    .BlueprintDiagnostic:
      why:
        constructs: "Supplies dependency position 2, BlueprintDiagnostic, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."
    .DispatchBlueprintGraph:
      why:
        constructs: "Supplies dependency position 3, DispatchBlueprintGraph, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."
    .inventory.collect_blueprints:
      why:
        constructs: "Supplies dependency position 4, collect blueprints, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."
    ._dispatch_document_closure:
      why:
        constructs: "Supplies dependency position 5,  dispatch document closure, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 6, BlueprintGraphError, while transforming repo root, caller module id, interface id, schema root into the load dispatch blueprint graph value."
    """

    root = Path(repo_root).resolve()
    try:
        return DispatchBlueprintGraph(
            load_repository_blueprint_graph(
                root,
                schema_root=schema_root,
                expected_schema_version=5,
            )
        )
    except (BlueprintGraphError, BlueprintInventoryError) as full_error:
        inventory = collect_blueprints(
            root,
            expected_schema_version=5,
            skip_parse_errors=True,
        )
        selected_documents, selected_roots = _dispatch_document_closure(
            inventory.documents,
            caller_module_id=caller_module_id,
            interface_id=interface_id,
        )
        unrelated_issues = []
        known_roots = {
            document.module_root for document in inventory.documents
        }
        for issue in inventory.issues:
            issue_path = root / issue.relative_path
            owners = {
                module_root
                for module_root in known_roots
                if issue_path == module_root / "blueprint.yaml"
                or issue_path.is_relative_to(module_root)
            }
            if not owners or owners & selected_roots:
                raise BlueprintGraphError(
                    f"{issue.relative_path.as_posix()}: {issue.message}"
                ) from full_error
            unrelated_issues.append(
                BlueprintDiagnostic(
                    code="unrelated-blueprint-invalid",
                    message=issue.message,
                    path=issue.relative_path,
                )
            )

        selected_schema_root = (
            Path(schema_root)
            if schema_root is not None
            else root / "references" / "blueprint-schema"
        )
        if not (selected_schema_root / "module.schema.json").is_file():
            selected_schema_root = (
                Path(__file__).resolve().parents[3]
                / "references"
                / "blueprint-schema"
                / "migrations"
                / "v5"
            )
        graph = _load_v5_repository_blueprint_graph(
            root,
            selected_documents,
            schema_root=selected_schema_root,
        )
        diagnostics = tuple(unrelated_issues)
        if not diagnostics:
            diagnostics = (
                BlueprintDiagnostic(
                    code="unrelated-blueprint-invalid",
                    message=str(full_error),
                ),
            )
        return DispatchBlueprintGraph(graph, diagnostics)


def load_repository_blueprint_graph(
    repo_root: Path,
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 6,
) -> RepositoryBlueprintGraph:
    """Load one explicit repository-wide graph; v6 is canonical.

    Intent
    ------
    Use repo root, schema root, expected schema version to load one explicit repository-wide graph; v6 is canonical.

    Rationale
    ---------
    The operation combines repo root, schema root, expected schema version through resolve, tuple, _load_v4_repository_blueprint_graph and ordered iteration, bounded failure checks, an explicit return value, making the resulting load repository blueprint graph behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set load_repository_blueprint_graph_inputs = repo root, schema root, expected schema version
    - if load_repository_blueprint_graph_inputs violate blueprint invariants:
      - raise blueprint graph error
    - for item in load_repository_blueprint_graph_inputs:
      - set validated_item = item
    - return load repository blueprint graph value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .inventory.iter_blueprints:
      why:
        constructs: "Supplies dependency position 1, iter blueprints, while transforming repo root, schema root, expected schema version into the load repository blueprint graph value."
    ._load_v4_repository_blueprint_graph:
      why:
        constructs: "Supplies dependency position 2,  load v4 repository blueprint graph, while transforming repo root, schema root, expected schema version into the load repository blueprint graph value."
    ._load_v5_repository_blueprint_graph:
      why:
        constructs: "Supplies dependency position 3,  load v5 repository blueprint graph, while transforming repo root, schema root, expected schema version into the load repository blueprint graph value."
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 4, BlueprintGraphError, while transforming repo root, schema root, expected schema version into the load repository blueprint graph value."
    """

    if expected_schema_version not in {4, 5, 6}:
        raise ValueError("expected_schema_version must be 4, 5, or 6")
    root = Path(repo_root).resolve()
    documents = tuple(
        iter_inventory_blueprints(
            root,
            expected_schema_version=expected_schema_version,
        )
    )
    for document in documents:
        if (
            document.declaration.get("schema_version")
            != expected_schema_version
        ):
            raise BlueprintGraphError(
                f"{document.path}: repository graph requires schema_version "
                f"{expected_schema_version}"
            )

    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else (
            root / "references" / "blueprint-schema"
            if expected_schema_version == 6
            else root / "references" / "blueprint-schema" / "migrations" / f"v{expected_schema_version}"
        )
    )
    if not (selected_schema_root / "module.schema.json").is_file():
        selected_schema_root = (
            Path(__file__).resolve().parents[3]
            / "references"
            / "blueprint-schema"
            / (
                Path()
                if expected_schema_version == 6
                else Path("migrations") / f"v{expected_schema_version}"
            )
        )
    if expected_schema_version in {5, 6}:
        return _load_v5_repository_blueprint_graph(
            root,
            documents,
            schema_root=selected_schema_root,
            schema_version=expected_schema_version,
        )
    return _load_v4_repository_blueprint_graph(
        root,
        documents,
        schema_root=selected_schema_root,
    )


def repository_schema_version(repo_root: Path) -> int:
    """Return the canonical repository schema version, defaulting legacy trees to v4.

    Intent
    ------
    Use repo root to return the canonical repository schema version, defaulting legacy trees to v4.

    Rationale
    ---------
    The operation combines repo root through int, safe_load, isinstance and bounded failure checks, an explicit return value, making the resulting repository schema version behavior explicit across 1 conditional branches.

    Pseudocode
    ----------
    - set repository_schema_version_inputs = repo root
    - if repository_schema_version_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return repository schema version value

    Wraps
    -----
    - none
    """

    marker = (
        Path(repo_root)
        / "references"
        / "blueprint-schema"
        / "blueprint.yaml"
    )
    try:
        document = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 4
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(
            f"{marker}: cannot determine repository schema version"
        ) from exc
    version = (
        document.get("schema_version")
        if isinstance(document, dict)
        else None
    )
    if version not in {4, 5, 6}:
        raise ValueError(
            f"{marker}: repository schema version must be 4, 5, or 6"
        )
    return int(version)


def resolve_export(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
    version: int | None = None,
) -> tuple[BlueprintNode, BlueprintNode, InterfaceExport]:
    """Resolve one public export to its module and behavioral source.

    Intent
    ------
    Use graph, interface id, version to resolve one public export to its module and behavioral source.

    Rationale
    ---------
    The operation combines graph, interface id, version through get, BlueprintGraphError and bounded failure checks, an explicit return value, making the resulting resolve export behavior explicit across 4 conditional branches.

    Pseudocode
    ----------
    - set resolve_export_inputs = graph, interface id, version
    - if resolve_export_inputs violate blueprint invariants:
      - raise blueprint graph error
    - return resolve export value

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        constructs: "Supplies dependency position 1, BlueprintGraphError, while transforming graph, interface id, version into the resolve export value."
    """

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
    """Return the selected export's direct callable-interface authority.

    Intent
    ------
    Use graph, interface id to return the selected export's direct callable-interface authority.

    Rationale
    ---------
    The operation combines graph, interface id through resolve_export, tuple, sorted and an explicit return value, making the resulting runtime authority for export behavior explicit across 0 conditional branches.

    Pseudocode
    ----------
    - set runtime_authority_for_export_inputs = graph, interface id
    - return runtime authority for export value

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolve_export:
      why:
        computes: "Supplies dependency position 1, resolve export, while transforming graph, interface id into the runtime authority for export value."
    """

    resolve_export(graph, interface_id)
    return tuple(
        sorted(
            edge.target_interface_id
            for edge in graph.export_edges
            if edge.source_export_id == interface_id
        )
    )

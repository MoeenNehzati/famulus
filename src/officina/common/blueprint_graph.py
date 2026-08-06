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
from typing import Any, Callable, Mapping

import jsonschema
import yaml

from .atomic_files import AtomicWriteError, read_regular_file_bytes
from .blueprint_inventory import (
    BlueprintDocument,
    BlueprintInventoryError,
    JsonValue,
    _normalize_json,
    _StrictBlueprintLoader,
    collect_blueprints,
    iter_blueprints as iter_inventory_blueprints,
)
from .configured_schema import (
    ConfiguredSchemaError,
    configured_validator,
    schema_requires_configuration,
)
from .repository_paths import (
    RepositoryPathError,
    equivalent_root_relative_path,
    repository_relative_path,
)


class BlueprintGraphError(ValueError):
    """Signal that blueprint declarations cannot form a coherent graph."""


class BlueprintSchemaError(BlueprintGraphError):
    """Describe one graph-node failure against its concrete JSON Schema.

    Intent
    ------
    Preserve the blueprint path, JSON location, and schema diagnostic together.

    Rationale
    ---------
    Schema failures need structured location fields for tooling as well as the
    formatted message inherited from the general graph-error boundary.

    Pseudocode
    ----------
    - set schema_failure = blueprint path plus JSON path plus diagnostic

    Wraps
    -----
    - none
    """

    def __init__(self, blueprint_path: Path, json_path: str, message: str) -> None:
        """Initialize a structured blueprint schema failure.

        Intent
        ------
        Record machine-readable schema location fields and format the exception.

        Rationale
        ---------
        Keeping the original fields alongside the rendered text lets callers
        inspect a failure without reparsing a human-oriented error message.

        Pseudocode
        ----------
        - set schema_fields = blueprint_path json_path and message
        - set exception_message = located schema diagnostic

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
    """Carry shallow-frozen node fields and a declaration treated as read-only."""

    node_id: str
    node_type: str
    version: int
    module_root: Path
    blueprint_path: Path
    gateway_path: Path | None
    declaration: dict[str, Any]

@dataclass(frozen=True)
class BlueprintEdge:
    """Carry one version-pinned relationship between graph identifiers."""

    relation: str
    source_id: str
    target_id: str
    required_version: int
    target_blueprint_path: Path | None = None


@dataclass(frozen=True)
class InterfaceExport:
    """Describe a source interface exposed directly or through a module facade."""

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
    """Describe one child interface materialized through a namespace route."""

    route_owner_id: str
    child_module_id: str
    interface_id: str
    version: int
    terminal_module_id: str
    terminal_module_version: int


@dataclass(frozen=True)
class NamespaceRoute:
    """Group one parent-to-child route with its materialized interfaces."""

    route_owner_id: str
    child_module_id: str
    child_version: int
    declaration: Mapping[str, JsonValue]
    materialized_interfaces: tuple[RoutedInterface, ...]


@dataclass(frozen=True)
class ExportDependencyEdge:
    """Record one public export's direct dependency on another interface."""

    source_export_id: str
    target_interface_id: str
    target_version: int


@dataclass(frozen=True)
class HelperEdge:
    """Record one contract helper bound to a directly used interface."""

    source_export_id: str
    local_helper_id: str
    target_interface_id: str
    target_version: int
    binding: Mapping[str, JsonValue]


@dataclass(frozen=True)
class CertificationEdge:
    """Record one node relationship that contributes certification evidence."""

    relation: str
    source_node_id: str
    target_node_id: str
    target_version: int | None = None


@dataclass(frozen=True)
class RepositoryBlueprintGraph:
    """Aggregate shallow-frozen graph fields with read-only-by-contract mappings."""

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


@dataclass(frozen=True)
class BlueprintDiagnostic:
    """Carry one non-fatal blueprint defect outside a dispatch closure."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class DispatchBlueprintGraph:
    """Pair a dispatch-sufficient graph with diagnostics outside its closure."""

    graph: RepositoryBlueprintGraph
    diagnostics: tuple[BlueprintDiagnostic, ...] = ()


class RuntimeFileBinding:
    """Own a retained descriptor whose validation remains bound to later use.

    Intent
    ------
    Couple a safely opened runtime path, descriptor, and captured mode metadata.

    Rationale
    ---------
    Retaining the validated descriptor prevents later reads or execution checks
    from reopening a path whose components could have changed after validation.

    Pseudocode
    ----------
    - set runtime_binding = validated path descriptor and file mode

    Wraps
    -----
    - none
    """

    def __init__(self, path: Path, fd: int, mode: int) -> None:
        """Initialize ownership of an already validated runtime descriptor.

        Intent
        ------
        Store the diagnostic path, open descriptor, and observed file mode.

        Rationale
        ---------
        Construction does not reopen or revalidate the path; it transfers the
        caller's retained descriptor into a small lifecycle owner.

        Pseudocode
        ----------
        - set binding_fields = validated path descriptor and mode

        Wraps
        -----
        - none
        """
        self.path = path
        self.fd = fd
        self.mode = mode

    def close(self) -> None:
        """Close the retained descriptor once and mark the binding closed.

        Intent
        ------
        Release an open runtime descriptor without double-closing it later.

        Rationale
        ---------
        Setting the descriptor sentinel after a successful close makes explicit
        cleanup and destructor cleanup safely converge on one lifecycle state.

        Pseudocode
        ----------
        - if retained descriptor is open:
          - set descriptor_status = closed after releasing retained descriptor

        Wraps
        -----
        - none
        """
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def read_bytes(self) -> bytes:
        """Read all bytes from the retained descriptor from its beginning.

        Intent
        ------
        Return the bound file contents without reopening the validated path.

        Rationale
        ---------
        Seeking before chunked reads makes repeated reads deterministic while
        descriptor retention preserves the no-path-race guarantee.

        Pseudocode
        ----------
        - if retained descriptor is closed:
          - raise BlueprintGraphError
        - set chunks = bytes read from the descriptor beginning through end of file
        - return joined chunks

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            raises: "Reports an attempted read after the descriptor binding has been closed."
        """
        if self.fd < 0:
            raise BlueprintGraphError(f"{self.path}: runtime input binding is closed")
        os.lseek(self.fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(self.fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)

    def proc_path(self) -> str:
        """Return the host descriptor path for the retained runtime file.

        Intent
        ------
        Expose the open descriptor through the host's process-filesystem path.

        Rationale
        ---------
        Consumers that must execute the exact validated file need a descriptor
        path, and unsupported hosts must fail explicitly instead of reopening it.

        Pseudocode
        ----------
        - if descriptor is closed or process descriptor paths are unavailable:
          - raise BlueprintGraphError
        - return process descriptor path

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            raises: "Reports that descriptor-backed execution cannot be provided for this binding."
        """
        if self.fd < 0 or not Path("/proc/self/fd").is_dir():
            raise BlueprintGraphError(
                f"{self.path}: descriptor-backed execution is unavailable on this host"
            )
        return f"/proc/self/fd/{self.fd}"

    def is_effectively_executable(self) -> bool:
        """Check executable permission using effective IDs on the bound file.

        Intent
        ------
        Determine whether the retained descriptor can be executed by this process.

        Rationale
        ---------
        Effective-ID checks match runtime authority and using the descriptor path
        keeps the decision attached to the file that was originally validated.

        Pseudocode
        ----------
        - if effective-ID access checks are unavailable:
          - raise BlueprintGraphError
        - set descriptor_path = @proc_path()
        - return executable access for descriptor_path under effective IDs

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            raises: "Reports hosts that cannot perform an effective-ID executable check."
        """
        if os.access not in os.supports_effective_ids:
            raise BlueprintGraphError(
                f"{self.path}: effective-ID executable checks are unavailable on this host"
            )
        return os.access(self.proc_path(), os.X_OK, effective_ids=True)

    def __del__(self) -> None:
        """Best-effort close the retained descriptor during object finalization.

        Intent
        ------
        Release descriptor ownership when callers omit explicit cleanup.

        Rationale
        ---------
        Finalizers cannot safely propagate IO errors, so cleanup delegates to the
        idempotent lifecycle method and suppresses only close-time OS failures.

        Pseudocode
        ----------
        - set cleanup_attempt = close the retained descriptor when still open
        - return none after cleanup or a suppressed operating-system error

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
    """Return the deterministic identity key for one blueprint edge.

    Intent
    ------
    Normalize a graph relationship into fields suitable for stable sorting.

    Rationale
    ---------
    Converting the optional marker to POSIX text makes graph order independent of
    platform-specific path representation while preserving every edge component.

    Pseudocode
    ----------
    - set marker_text = target marker as POSIX text or none
    - return relation source target version and marker_text

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
    """Detect support for descriptor-relative no-follow runtime traversal.

    Intent
    ------
    Check the host primitives required to bind path validation to file use.

    Rationale
    ---------
    The secure traversal algorithm depends jointly on POSIX descriptors,
    no-follow flags, directory flags, and directory-relative open support.

    Pseudocode
    ----------
    - return whether all descriptor-safe open primitives are supported

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
    """Report whether runtime inputs can be opened without path races.

    Intent
    ------
    Expose the host capability check as the public module predicate.

    Rationale
    ---------
    Callers need to select safe runtime handling without depending on the private
    feature-detection helper or duplicating its exact platform requirements.

    Pseudocode
    ----------
    - return @_descriptor_safe_open_supported()

    Wraps
    -----
    _descriptor_safe_open_supported -> preprocess: none; postprocess: returns the capability result unchanged; fixed_arguments: none

    """

    return _descriptor_safe_open_supported()


def _graph_repository_relative_path(path: Path, repo_root: Path) -> Path:
    """Convert a runtime path to a repository-relative path or graph failure.

    Intent
    ------
    Enforce repository containment while translating path errors to graph errors.

    Rationale
    ---------
    Runtime graph callers should receive one domain exception even though the
    shared path helper owns the lower-level equivalent-root containment logic.

    Pseudocode
    ----------
    - set relative_path = repository-relative form of path
    - return relative_path or raise BlueprintGraphError on failed containment

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .repository_paths.repository_relative_path:
      why:
        constructs: "Builds the confined repository-relative path while recognizing equivalent roots."
    .BlueprintGraphError:
      why:
        raises: "Translates a repository containment failure into the graph-loader exception domain."
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
    """Validate runtime ownership and return absolute and repository-relative paths.

    Intent
    ------
    Confine one runtime input to both its owning root and the repository root.

    Rationale
    ---------
    Descriptor traversal begins at the repository, but node authority is narrower;
    validating both boundaries prevents a sibling node's file from being opened.

    Pseudocode
    ----------
    - set absolute_paths = normalized repository owner and input paths
    - set relative_path = input path relative to repository after owner validation
    - return absolute input path and relative_path

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .repository_paths.equivalent_root_relative_path:
      why:
        validates: "Checks that the runtime input is contained by its owning root across equivalent paths."

    InstantiationsFromRepo
    ----------------------
    ._graph_repository_relative_path:
      why:
        constructs: "Builds the repository-relative traversal path after owner containment succeeds."
    .BlueprintGraphError:
      why:
        raises: "Reports owner-boundary violations and inputs that do not name a file."
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
    """Open a confined runtime path through descriptor-relative no-follow traversal.

    Intent
    ------
    Bind file or directory validation to a retained descriptor without path races.

    Rationale
    ---------
    Walking each component relative to an already opened directory and rejecting
    symlinks ensures later use refers to the exact regular file or directory checked.

    Pseudocode
    ----------
    - set traversal_path = validated repository-relative runtime path
    - for component in traversal_path:
      - set current_descriptor = safely opened child descriptor
    - if final descriptor has the wrong file type:
      - raise BlueprintGraphError
    - return RuntimeFileBinding for the retained final descriptor

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._descriptor_safe_open_supported:
      why:
        validates: "Checks that the host provides every primitive required for no-follow descriptor traversal."

    InstantiationsFromRepo
    ----------------------
    ._runtime_relative_path:
      why:
        constructs: "Builds the confined absolute and repository-relative paths used by descriptor traversal."
    .RuntimeFileBinding:
      why:
        constructs: "Creates the lifecycle owner for the validated final descriptor and captured file mode."
    .BlueprintGraphError:
      why:
        raises: "Reports unsupported hosts, unsafe components, wrong file types, and descriptor-open failures."
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
    """Open a contained regular runtime file and optionally require executability.

    Intent
    ------
    Return a retained safe binding, closing it before any executable-check failure.

    Rationale
    ---------
    Executability must be checked on the validated descriptor rather than the path,
    and failed checks must not leak the descriptor that established that identity.

    Pseudocode
    ----------
    - set binding = safely opened runtime file descriptor
    - set executable_check = optional permission decision on the retained binding
    - raise BlueprintGraphError after closing a binding that fails executable_check
    - return binding

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._open_runtime_descriptor:
      why:
        constructs: "Builds the retained regular-file binding, using path-only mode for executable checks."
    .BlueprintGraphError:
      why:
        raises: "Reports a retained runtime file that does not satisfy the executable requirement."
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
    Return safe retained bindings for regular ``.py`` files under one package root.

    Rationale
    ---------
    Recursive descriptor-relative traversal rejects symlink components and closes
    all partially accumulated bindings when any package member cannot be secured.

    Pseudocode
    ----------
    - set package_root_binding = safely opened package directory
    - set traversal = sorted descriptor-relative package walk without symlinks
    - set source_bindings = retained regular Python file descriptors
    - return source_bindings after closing the root directory descriptor

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._open_runtime_descriptor:
      why:
        constructs: "Builds the retained root-directory binding used for descriptor-relative package traversal."
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
        """Recursively collect safe Python descriptors below one directory.

        Intent
        ------
        Traverse child entries in stable order while rejecting symbolic links.

        Rationale
        ---------
        Keeping each recursive step descriptor-relative prevents path substitution,
        while filtering by suffix limits retained bindings to Python sources.

        Pseudocode
        ----------
        - set child_entries = sorted directory entries classified without following links
        - set retained_sources = recursive directories plus regular Python file bindings

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .RuntimeFileBinding:
          why:
            constructs: "Creates the retained binding appended for each validated Python source descriptor."
        .BlueprintGraphError:
          why:
            raises: "Reports symbolic links, non-regular Python entries, and descriptor traversal failures."
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
    Return sorted package source paths and bytes after rejecting link-like entries.

    Rationale
    ---------
    Platforms without descriptor traversal still need a bounded package snapshot;
    the shared reader supplies atomicity checks at each owner-confined file boundary.

    Pseudocode
    ----------
    - set package_path = validated package root under owner and repository
    - set package_entries = package walk results collected without following links
    - set snapshots = regular Python paths paired with native-reader bytes
    - return snapshots or raise BlueprintGraphError on unsafe traversal

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._runtime_relative_path:
      why:
        constructs: "Builds the absolute package path after enforcing owner and repository containment."
    .atomic_files.read_regular_file_bytes:
      why:
        constructs: "Builds each source byte snapshot through the shared confined native-file reader."
    .BlueprintGraphError:
      why:
        raises: "Reports link-like components and package walk or atomic-reader failures."
    """

    package_absolute, _relative = _runtime_relative_path(
        package_root,
        owner_root,
        repo_root,
    )
    owner_absolute = Path(os.path.abspath(owner_root))
    snapshots: list[tuple[Path, bytes]] = []

    def raise_walk_error(error: OSError) -> None:
        """Re-raise an ``os.walk`` error for the outer graph-error boundary.

        Intent
        ------
        Prevent package traversal from silently skipping unreadable directories.

        Rationale
        ---------
        The snapshot contract must be complete, so the walk callback propagates its
        original OS failure and lets the enclosing handler attach package context.

        Pseudocode
        ----------
        - raise error

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
    """Validate and return one canonical package-snapshot source path.

    Intent
    ------
    Accept only normalized relative POSIX paths naming Python files below a root.

    Rationale
    ---------
    Strict path shape prevents ambiguous encodings, traversal, rootless sources,
    and cross-platform separator differences in signed snapshot payloads.

    Pseudocode
    ----------
    - if snapshot path is not canonical confined Python text:
      - raise BlueprintGraphError
    - return snapshot path text

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports empty, non-text, noncanonical, traversing, or non-Python snapshot paths."
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
    Serialize sorted unique Python source records into canonical ASCII JSON bytes.

    Rationale
    ---------
    Stable path normalization, base64, field order, and separators make equivalent
    package contents produce identical snapshot bytes for hashing and transport.

    Pseudocode
    ----------
    - set records = validated relative paths paired with canonical base64 sources
    - set canonical_records = sorted unique records under one package root
    - return canonical JSON bytes for format version and records

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._graph_repository_relative_path:
      why:
        computes: "Converts each source path into its owner-root-relative logical package path."

    InstantiationsFromRepo
    ----------------------
    ._runtime_python_snapshot_path:
      why:
        constructs: "Builds each validated canonical source path stored in the snapshot record."
    .BlueprintGraphError:
      why:
        raises: "Reports invalid bytes, duplicate paths, empty snapshots, or multiple package roots."
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
    Return ordered logical paths and bytes only from one canonical snapshot form.

    Rationale
    ---------
    Rejecting duplicate keys, extra fields, unsorted paths, and noncanonical base64
    prevents semantically equivalent payload variants from bypassing byte identity.

    Pseudocode
    ----------
    - set document = strict JSON decoded from payload
    - set validated_document = exact format version fields and nonempty file list
    - set decoded_sources = canonical ordered paths and decoded source bytes
    - return decoded_sources

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._runtime_python_snapshot_path:
      why:
        constructs: "Builds each validated logical Python path while decoding ordered records."
    .BlueprintGraphError:
      why:
        raises: "Reports malformed or noncanonical snapshot documents, paths, and source encodings."
    """

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Build a JSON object while rejecting duplicate member names.

        Intent
        ------
        Preserve strict object identity during package snapshot decoding.

        Rationale
        ---------
        Standard JSON decoding silently keeps one duplicate value, which would make
        payload interpretation depend on parser behavior rather than canonical form.

        Pseudocode
        ----------
        - set decoded_object = empty mapping
        - for pair in pairs:
          - set decoded_object = decoded_object extended by one unique pair
        - return decoded_object

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
    """Return an integer version after enforcing the positive-version contract.

    Intent
    ------
    Reject booleans, non-integers, zero, and negative version declarations.

    Rationale
    ---------
    Central validation gives every graph relation the same pin semantics and a
    contextual diagnostic instead of relying on Python's Boolean integer subtype.

    Pseudocode
    ----------
    - if version is not a positive non-Boolean integer:
      - raise BlueprintGraphError
    - return version

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports a version declaration outside the required positive-integer domain."
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
    """Resolve a blueprint locator within its declared repository boundary.

    Intent
    ------
    Convert a module-root or repository-root locator to one confined path.

    Rationale
    ---------
    Locator validation rejects absolute and parent-traversing inputs before graph
    identity comparisons, preventing declarations from escaping their chosen base.

    Pseudocode
    ----------
    - set locator_fields = validated mapping base and nonempty relative path
    - set candidate_path = selected base plus locator path
    - if candidate_path escapes selected base:
      - raise BlueprintGraphError
    - return candidate_path

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports malformed, unsupported, absolute, traversing, or escaping blueprint locators."
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
    """Render a JSON Schema diagnostic's absolute instance path.

    Intent
    ------
    Produce a stable dollar-rooted path including a missing required property.

    Rationale
    ---------
    ``jsonschema`` locates required-field errors at their parent, so extracting the
    property name gives users the concrete declaration field that needs repair.

    Pseudocode
    ----------
    - set path_parts = schema error absolute path
    - set complete_parts = path_parts plus any missing required property name
    - return dollar-rooted dotted and indexed path text

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
    """Load a concrete blueprint schema with local-reference resolution.

    Intent
    ------
    Return a configured or ordinary validator for one concrete schema file.

    Rationale
    ---------
    Schemas that declare configuration must use the configured loader, while plain
    schemas retain local URI resolution; every loader failure becomes a located
    schema error for consistent graph diagnostics.

    Pseudocode
    ----------
    - if sibling configuration exists:
      - return configured schema validator
    - set plain_schema = parsed schema accepted only when no configuration is required
    - return validator with local reference resolver

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .configured_schema.schema_requires_configuration:
      why:
        validates: "Checks whether a plain-loaded schema declares mandatory repository configuration."

    InstantiationsFromRepo
    ----------------------
    .configured_schema.configured_validator:
      why:
        constructs: "Builds the configured JSON Schema validator when a sibling configuration is present."
    .configured_schema.ConfiguredSchemaError:
      why:
        raises: "Represents a schema that requires configuration but has no sibling configuration file."
    .BlueprintSchemaError:
      why:
        raises: "Wraps schema IO, decoding, compilation, resolution, and configuration failures with location context."
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
    expected_schema_version: int = 5,
) -> tuple[BlueprintSchemaError, ...]:
    """Validate one node declaration and return its sorted concrete schema errors.

    Intent
    ------
    Enforce schema version and node type before validating with a cached schema.

    Rationale
    ---------
    Selecting the concrete schema explicitly prevents unknown node kinds from being
    filtered away, while validator caching keeps repository-wide loads efficient.

    Pseudocode
    ----------
    - set schema_selection = validated schema version and supported node type
    - set validator = cached or newly loaded concrete node validator
    - set schema_errors = validator findings sorted by JSON path and message
    - return located BlueprintSchemaError records for schema_errors

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._json_error_path:
      why:
        constructs: "Builds stable instance paths carried into sorting keys and returned schema diagnostics."
    ._load_schema_validator:
      why:
        constructs: "Builds and caches the concrete validator selected for the declaration's node type."
    .BlueprintSchemaError:
      why:
        constructs: "Creates each returned located schema diagnostic and wraps validator-resolution failures."
    .BlueprintGraphError:
      why:
        raises: "Reports unsupported repository schema versions and typed node kinds before schema validation."
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
    """Identify blueprint and certificate artifacts excluded from node content.

    Intent
    ------
    Protect canonical declarations and generated assurance files from content ownership.

    Rationale
    ---------
    Those artifacts define or attest the node rather than belonging to its authored
    behavioral content, so hashing them as content would create circular authority.

    Pseudocode
    ----------
    - return whether path names a blueprint marker or certificate directory

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
    """Return sorted regular files beneath a root without following directory links.

    Intent
    ------
    Enumerate candidate owned files while excluding symlinked directory subtrees.

    Rationale
    ---------
    Ownership matching needs a deterministic physical-file inventory and must not
    let a content regex cross its module boundary through a symbolic link.

    Pseudocode
    ----------
    - set regular_files = regular entries from a no-follow recursive walk
    - return regular_files in sorted order

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
    Apply every declared content regex to confined candidates and require the gateway.

    Rationale
    ---------
    Fail-closed matching prevents stale patterns, declaration artifacts, nested child
    roots, and missing gateways from entering a node's direct ownership set.

    Pseudocode
    ----------
    - set ownership_contract = validated node version owner root and content patterns
    - set candidates = regular owner files outside excluded child roots
    - set matched_paths = full regex matches for every required pattern
    - set content_check = matched_paths contain no forbidden artifact and include the gateway
    - return matched_paths in sorted order

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .repository_paths.equivalent_root_relative_path:
      why:
        validates: "Checks that the node ownership root lies within the repository across equivalent roots."
    ._regular_files_beneath:
      why:
        reads: "Enumerates regular candidate files without following symlinked directory subtrees."
    ._is_forbidden_content_artifact:
      why:
        validates: "Identifies matched declaration and certificate artifacts that cannot be node content."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports invalid ownership roots, patterns, matches, artifacts, and gateway coverage."
    """

    if node.declaration.get("schema_version") not in {4, 5}:
        raise BlueprintGraphError(
            f"{node.blueprint_path}: content resolution requires schema_version 4 or 5"
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
    Form the complete deterministic authored-input set used for node assurance.

    Rationale
    ---------
    Callers may omit the repository root only where the node's registered layout
    makes it unambiguous; otherwise inference fails rather than broadening ownership.

    Pseudocode
    ----------
    - set repository_root = supplied root or root inferred from registered layout
    - set authored_inputs = blueprint marker plus resolved node content
    - return authored_inputs in sorted unique order

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolved_node_content_paths:
      why:
        reads: "Resolves the regular files matched by the node's declared ownership patterns."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports a node layout from which the repository root cannot be inferred safely."
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
    Return the absolute path only after owner confinement and native regular-file checks.

    Rationale
    ---------
    The shared reader enforces platform-appropriate atomicity and link handling while
    this boundary translates its failures into repository graph diagnostics.

    Pseudocode
    ----------
    - set absolute_path = runtime path validated under owner and repository roots
    - set file_check = strict regular-file read of absolute_path
    - return absolute_path or raise BlueprintGraphError

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .atomic_files.read_regular_file_bytes:
      why:
        validates: "Checks that the confined runtime input is a stable regular file on the current platform."

    InstantiationsFromRepo
    ----------------------
    ._runtime_relative_path:
      why:
        constructs: "Builds the absolute confined path after enforcing both ownership boundaries."
    .BlueprintGraphError:
      why:
        raises: "Translates atomic-reader and operating-system failures into graph diagnostics."
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
    """Construct one typed graph node from an already parsed blueprint document.

    Intent
    ------
    Validate node identity and gateway shape before producing a shallow-frozen node record.

    Rationale
    ---------
    Inventory parsing and concrete schema validation are separate stages, so graph
    construction retains defensive checks before trusting fields used as index keys.

    Pseudocode
    ----------
    - set node_identity = validated nonempty identifier and supported node type
    - set gateway_path = declared gateway beneath the document module root
    - return BlueprintNode with validated positive version and declaration

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Builds the validated positive node version stored in the graph record."
    .BlueprintNode:
      why:
        constructs: "Creates the shallow-frozen graph node whose declaration dictionary remains read-only by contract."
    .BlueprintGraphError:
      why:
        raises: "Reports missing identifiers and unsupported typed node declarations."
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
    """Construct a version-4 graph node from one parsed inventory document.

    Intent
    ------
    Bind the shared node-construction routine to the legacy schema version.

    Rationale
    ---------
    Keeping the version pin in one adapter makes version-4 graph assembly explicit
    without duplicating identity, gateway, and version validation.

    Pseudocode
    ----------
    - return @_node_from_document(document with expected schema version four)

    Wraps
    -----
    _node_from_document -> preprocess: supplies the parsed document; postprocess: returns the constructed node unchanged; fixed_arguments: expected_schema_version=4
    """
    return _node_from_document(document, expected_schema_version=4)


def load_module_blueprint(
    repo_root: Path,
    module_root: Path,
    *,
    schema_root: Path | None = None,
    expected_schema_version: int = 4,
) -> BlueprintNode:
    """Load and validate one exact module marker without scanning siblings.

    Intent
    ------
    Return a confined module node whose marker, schema, gateway, and content are valid.

    Rationale
    ---------
    Installer and runtime callers sometimes know the precise module root; avoiding a
    repository scan isolates unrelated defects while retaining full node validation.

    Pseudocode
    ----------
    - set module_location = validated expected version and repository-contained module root
    - set declaration = strict YAML marker normalized into the JSON domain
    - set schema_findings = concrete-schema validation results for declaration
    - set module_node = graph node constructed from the parsed document
    - set authored_file_checks = strict validation results for every authored input
    - return module_node

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .atomic_files.read_regular_file_bytes:
      why:
        reads: "Reads the exact module marker through the strict owner-confined regular-file boundary."
    .authored_node_input_paths:
      why:
        reads: "Enumerates the marker and resolved content that comprise the module's authored inputs."
    .validate_runtime_file_path:
      why:
        validates: "Checks every authored input as a confined stable regular file before returning the node."

    InstantiationsFromRepo
    ----------------------
    .blueprint_inventory._normalize_json:
      why:
        transforms: "Converts the strict YAML mapping into the canonical JSON-compatible declaration domain."
    .blueprint_inventory.BlueprintDocument:
      why:
        constructs: "Creates the inventory document used by the shared node-construction boundary."
    ._declaration_schema_errors:
      why:
        constructs: "Builds any concrete schema failures for the exact marker declaration."
    ._node_from_document:
      why:
        constructs: "Creates the shallow-frozen module node while retaining its mutable declaration mapping by read-only convention."
    .BlueprintGraphError:
      why:
        raises: "Reports invalid versions, paths, documents, node kinds, identifiers, and authored inputs."
    """

    if expected_schema_version not in {4, 5}:
        raise ValueError("expected_schema_version must be 4 or 5")

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
    """Reject cycles in the directed public-export dependency graph.

    Intent
    ------
    Verify that every export dependency chain terminates without revisiting a node.

    Rationale
    ---------
    Runtime callable authority must be acyclic so resolution and certification have
    a finite deterministic dependency order.

    Pseudocode
    ----------
    - set export_children = adjacency lists derived from dependency edges
    - set traversal_result = depth-first checks over every export
    - raise BlueprintGraphError when an active export is revisited

    Wraps
    -----
    - none

    """
    children: dict[str, list[str]] = {interface_id: [] for interface_id in exports}
    for edge in edges:
        children[edge.source_export_id].append(edge.target_interface_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(interface_id: str) -> None:
        """Depth-first traverse one export while detecting an active revisit.

        Intent
        ------
        Mark one export and all sorted descendants complete or raise with its cycle.

        Rationale
        ---------
        Separate active and completed sets distinguish a genuine back edge from a
        shared dependency already proven acyclic.

        Pseudocode
        ----------
        - if interface is active:
          - raise BlueprintGraphError with the cycle path
        - set child_checks = traversal results for sorted incomplete children
        - set interface_status = complete

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            raises: "Reports the active traversal segment that closes an export dependency cycle."
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
    """Require a target node on every platform supported by its source.

    Intent
    ------
    Reject cross-node dependencies that narrow an explicitly supported platform set.

    Rationale
    ---------
    A source promising support on a host cannot depend on a target that declines that
    host; absent structured support metadata remains backward-compatible and unchecked.

    Pseudocode
    ----------
    - set platform_check = comparison of each source-enabled platform with target support
    - raise BlueprintGraphError for a missing target platform

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports the target node and required platform that violate compatibility."
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
    """Reject cycles among node relationships that affect certification.

    Intent
    ------
    Ensure certification dependencies over known nodes form an acyclic graph.

    Rationale
    ---------
    Node hashes can depend only on a finite prior evidence set; a cycle would make
    certificate construction recursive and prevent a stable assurance order.

    Pseudocode
    ----------
    - set certification_children = known-node adjacency lists from evidence edges
    - set traversal_result = depth-first checks over every certification node
    - raise BlueprintGraphError when traversal returns to an active node

    Wraps
    -----
    - none

    """
    children: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source_node_id in children and edge.target_node_id in children:
            children[edge.source_node_id].append(edge.target_node_id)
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        """Depth-first traverse one certification node and its sorted targets.

        Intent
        ------
        Complete one node's evidence dependency walk or identify its active cycle.

        Rationale
        ---------
        The active stack preserves a readable cycle path, while the completed set
        prevents repeated traversal of shared acyclic dependencies.

        Pseudocode
        ----------
        - if node is active:
          - raise BlueprintGraphError with the active cycle
        - set child_checks = traversal results for sorted incomplete certification children
        - set node_status = complete

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            raises: "Reports the active traversal segment that closes a certification cycle."
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
    """Collect unique local identifiers and mapping entries from a v4 list.

    Intent
    ------
    Return valid mapping entries while rejecting duplicate string identifiers.

    Rationale
    ---------
    Contract sections share local reference semantics, and one collector keeps their
    duplicate checks consistent while tolerating schema-handled nonmapping entries.

    Pseudocode
    ----------
    - set identifiers = unique string ids from mapping entries
    - set mappings = all mapping entries in original order
    - raise BlueprintGraphError on a duplicate identifier
    - return identifiers and mappings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports duplicate local identifiers within the named v4 contract section."
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
    """Require a string v4 reference to name an identifier in its local set.

    Intent
    ------
    Reject only present string references that do not resolve in the supplied domain.

    Rationale
    ---------
    Schema validation owns field typing and presence, while this graph check enforces
    semantic links among arguments, outputs, outcomes, effects, helpers, and IO.

    Pseudocode
    ----------
    - if reference is text and absent from valid identifiers:
      - raise BlueprintGraphError with context and reference kind

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports the unresolved local reference together with its contract context and kind."
    """
    if isinstance(value, str) and value not in valid:
        raise BlueprintGraphError(f"{context}: unknown {kind} {value!r}")


def _walk_v4_contract(
    value: object,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], str, object]]:
    """Enumerate mapping fields recursively with their v4 contract paths.

    Intent
    ------
    Return every mapping field and recurse through nested mappings and lists.

    Rationale
    ---------
    Reference validation applies to fields at several schema depths, so a generic
    path walk avoids coupling semantic checks to every concrete contract container.

    Pseudocode
    ----------
    - set discovered_fields = current mapping fields with their parent paths
    - set discovered_fields = discovered_fields plus recursively collected child fields
    - return discovered_fields

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._walk_v4_contract:
      why:
        constructs: "Builds the recursively discovered field records contributed by nested mapping and list children."
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
    """Reject absolute or parent-traversing paths inside a v4 contract.

    Intent
    ------
    Enforce portable repository-internal syntax for any string path field examined.

    Rationale
    ---------
    Drive prefixes, backslashes, absolute paths, and parent segments could make one
    declaration resolve differently across hosts or escape its intended boundary.

    Pseudocode
    ----------
    - if path text is absolute platform-specific or parent-traversing:
      - raise BlueprintGraphError with field context

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports an internal contract path that is nonportable or escapes through parent traversal."
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
    """Compile module filesystem authority declarations into comparable claims.

    Intent
    ------
    Return module id, match kind, path text, and optional compiled regular expression.

    Rationale
    ---------
    Contract and nested-authority checks need one normalized claim representation;
    compiling regexes once also surfaces malformed authority before overlap analysis.

    Pseudocode
    ----------
    - set authority_claims = normalized exact and regex filesystem claims
    - set compiled_claims = exact claims plus successfully compiled regex claims
    - return authority_claims in module declaration order

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports an invalid filesystem-authority regular expression with its declaration index."
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
    """Validate semantic references and effects within one version-4 contract.

    Intent
    ------
    Enforce local identifier integrity, effect symmetry, safe paths, and authority.

    Rationale
    ---------
    JSON Schema establishes shape, but references among arguments, helpers, IO,
    outcomes, and effects require graph-aware checks against module ownership.

    Pseudocode
    ----------
    - set local_indexes = contract arguments conditions outputs outcomes helpers effects and IO
    - set reference_checks = every nested local reference resolved against local_indexes
    - set effect_checks = output IO direction and outcome effect symmetry decisions
    - set authority_checks = safe filesystem writes outside neighboring claims
    - raise BlueprintGraphError for any failed semantic check

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._walk_v4_contract:
      why:
        reads: "Enumerates nested contract fields so every semantic local reference can be checked."
    ._require_v4_local_ref:
      why:
        validates: "Checks nested argument, IO, helper, output, outcome, and effect references against their local indexes."
    ._validate_v4_internal_path:
      why:
        validates: "Rejects nonportable or escaping filesystem paths before authority comparison."

    InstantiationsFromRepo
    ----------------------
    ._v4_local_ids:
      why:
        constructs: "Builds the unique identifier sets and ordered mapping entries for each contract section."
    .BlueprintGraphError:
      why:
        raises: "Reports invalid references, effect symmetry, IO direction, helper routes, paths, and foreign authority writes."
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
    """Build source dependency, interface-use, and certification relationships.

    Intent
    ------
    Validate each source's direct pins and append normalized graph edges.

    Rationale
    ---------
    Versioned source and interface declarations share locator and certification
    consequences, while version-specific interface authorization stays injectable.

    Pseudocode
    ----------
    - for source in sorted sources:
      - set dependency_edges = validated source pins and canonical locators
      - set interface_edges = resolver-approved direct interface uses
      - set certification_edges = target source evidence for both relation kinds
    - return interface uses grouped by source

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Builds validated version pins for each declared source dependency and interface use."
    ._resolve_locator:
      why:
        constructs: "Builds the confined canonical blueprint path for each direct source dependency."
    .BlueprintEdge:
      why:
        constructs: "Creates containment-independent graph edges for source and interface relationships."
    .CertificationEdge:
      why:
        constructs: "Creates the node evidence dependency associated with each validated source or interface use."
    .BlueprintGraphError:
      why:
        raises: "Reports malformed declarations, missing targets, stale versions, and noncanonical locators."
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
    """Derive export dependency and helper edges from effective source uses.

    Intent
    ------
    Link each export to directly used public interfaces and valid contract helpers.

    Rationale
    ---------
    Helpers may target only the implementing source's effective direct interface set,
    keeping export authority narrow and preventing undeclared transitive capability.

    Pseudocode
    ----------
    - for export in sorted exports:
      - set direct_uses = implementing source interface uses
      - set export_edges = public targets among direct_uses
      - set helper_edges = valid helper bindings whose targets occur in direct_uses
    - return export_edges and helper_edges

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._positive_version:
      why:
        constructs: "Builds each validated helper target version before matching the source's direct uses."
    .ExportDependencyEdge:
      why:
        constructs: "Creates an export edge for each directly used target that is itself public."
    .HelperEdge:
      why:
        constructs: "Creates the helper binding record after validating its direct interface target."
    .BlueprintGraphError:
      why:
        raises: "Reports malformed helpers, missing identifiers, and helper targets outside effective direct uses."
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
    """Assemble and validate the complete legacy version-4 repository graph.

    Intent
    ------
    Produce canonical v4 nodes, exports, dependencies, ownership, and assurance edges.

    Rationale
    ---------
    The legacy model separates modules and behavioral sources but still requires exact
    containment, private/public access, platform compatibility, and exclusive content.

    Pseudocode
    ----------
    - set nodes = schema-validated unique v4 nodes from inventory documents
    - set containment = modules paired with exactly owned behavioral sources
    - set interfaces_and_exports = validated source interfaces and module exports
    - set relationship_edges = source uses export dependencies helpers and certification
    - set file_owners = exclusive module and source content ownership
    - set cycle_checks = certification and export dependency acyclicity results
    - return RepositoryBlueprintGraph with sorted canonical indexes

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_v4_interface_contract:
      why:
        validates: "Checks semantic references, effects, paths, and authority for every source-owned interface contract."
    .resolved_node_content_paths:
      why:
        reads: "Resolves module and source content sets used to establish direct file ownership."
    ._reject_certification_cycles:
      why:
        validates: "Rejects recursive node evidence relationships before returning the graph."
    ._reject_export_cycles:
      why:
        validates: "Rejects recursive public callable authority before returning the graph."

    InstantiationsFromRepo
    ----------------------
    ._declaration_schema_errors:
      why:
        constructs: "Builds concrete schema findings for every inventory document before graph indexing."
    ._v4_node_from_document:
      why:
        constructs: "Creates each validated version-four graph node from its inventory document."
    ._resolve_locator:
      why:
        constructs: "Builds canonical contained-source marker paths for exact identity comparison."
    ._v4_authority_claims:
      why:
        constructs: "Builds normalized filesystem authority claims used during interface contract validation."
    ._positive_version:
      why:
        constructs: "Builds validated interface and dependency version values throughout v4 assembly."
    .InterfaceExport:
      why:
        constructs: "Creates each public module binding to its contained source interface."
    .BlueprintEdge:
      why:
        constructs: "Creates node relationships for module containment and source interface dependencies."
    ._build_source_relationships:
      why:
        constructs: "Builds direct source uses and their node and certification edge records."
    ._build_export_relationships:
      why:
        constructs: "Builds public export dependency and contract-helper edge records."
    .RepositoryBlueprintGraph:
      why:
        constructs: "Creates the final sorted v4 graph with ownership and dependency indexes."
    .BlueprintGraphError:
      why:
        raises: "Reports all v4 schema, identity, containment, access, ownership, version, and cycle defects."
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
        """Resolve one v4 interface use under privacy, access, version, and platform rules.

        Intent
        ------
        Return the graph relation and implementing source for an allowed direct use.

        Rationale
        ---------
        Private interfaces are module-local, while exports apply caller admission and
        platform compatibility before their implementing source becomes a dependency.

        Pseudocode
        ----------
        - if interface is private:
          - return private-use relation and source after module and version checks
        - if interface is exported:
          - return export-use relation and source after access and platform checks
        - raise BlueprintGraphError for an unresolved or disallowed interface

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._require_platform_compatibility:
          why:
            validates: "Checks that an exported target supports every platform promised by the caller source."

        InstantiationsFromRepo
        ----------------------
        ._positive_version:
          why:
            constructs: "Builds the actual private-interface version compared with the requested pin."
        .BlueprintGraphError:
          why:
            raises: "Reports cross-module private use, stale pins, denied exports, missing access, and unknown interfaces."
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
    """Resolve registered version-5 module parents, children, segments, and ancestry.

    Intent
    ------
    Validate exact child markers and return deterministic nested-module topology indexes.

    Rationale
    ---------
    Version-5 namespaces and ownership depend on registered hierarchy rather than raw
    directories, so duplicate parents, local segments, and cycles must fail closed.

    Pseudocode
    ----------
    - set parent_child_indexes = exact registered child locators and local segments
    - set ancestry_index = recursively resolved root-to-module ancestry for every module
    - return sorted parents children local segments and ancestry indexes

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolve_locator:
      why:
        validates: "Resolves each declared child locator for exact comparison with the canonical module marker."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports malformed child maps, noncanonical locators, duplicate parents or segments, containment failures, and cycles."
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
        """Resolve and cache one module's root-to-self ancestry tuple.

        Intent
        ------
        Follow registered parents recursively while detecting a registration cycle.

        Rationale
        ---------
        Memoization avoids repeated parent walks, and the active stack retains the exact
        cycle sequence needed for a useful topology diagnostic.

        Pseudocode
        ----------
        - return cached ancestry when available
        - raise BlueprintGraphError if module is active in the current parent walk
        - set ancestry = parent ancestry extended by module or module alone at a root
        - set ancestry_cache = ancestry_cache extended by resolved ancestry
        - return ancestry

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .BlueprintGraphError:
          why:
            raises: "Reports the active parent chain when registered module ancestry contains a cycle."
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


def _v5_sources(
    root: Path,
    modules: Mapping[str, BlueprintNode],
    sources: Mapping[str, BlueprintNode],
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Resolve version-5 behavioral-source containment for every module.

    Intent
    ------
    Validate canonical source markers, namespaces, roots, and single ownership.

    Rationale
    ---------
    Source declarations share a module root with their owner and must be contained
    exactly once so private interfaces and direct content ownership remain unambiguous.

    Pseudocode
    ----------
    - set source_markers = canonical behavioral-source identifiers by absolute marker
    - for module in sorted modules:
      - set contained_sources = exact namespaced source locators owned by module
    - set containment_check = every source owned exactly once under its canonical module root
    - return module-to-sources and source-to-module indexes

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolve_locator:
      why:
        validates: "Resolves each declared source marker for exact comparison with canonical source inventory."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports malformed source maps, wrong namespaces or roots, duplicate containment, and orphan sources."
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
    """Conservatively decide whether two filesystem authority claims can overlap.

    Intent
    ------
    Prove disjointness for exact-versus-regex cases and fail closed for two regexes.

    Rationale
    ---------
    General Python regular-expression intersection is unavailable here, so nested
    authority may proceed only when simple matching proves the claims do not intersect.

    Pseudocode
    ----------
    - if both claims are exact:
      - return whether paths are equal
    - if one claim is exact:
      - return whether the other regex matches it
    - return true for two regular-expression claims

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
    """Reject overlapping filesystem authority between ancestor and descendant modules.

    Intent
    ------
    Compare every nested module claim with each claim held by its registered ancestors.

    Rationale
    ---------
    Nested modules need exclusive mutation authority; an unresolved regex intersection
    is treated as overlap because silently shared authority would be unsafe.

    Pseudocode
    ----------
    - set claims_by_module = normalized filesystem claims for every module
    - set overlap_checks = descendant claims compared with every registered ancestor claim
    - raise BlueprintGraphError for any possible overlap

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._v4_authority_claims:
      why:
        reads: "Normalizes each module's exact and regex filesystem claims for ancestry comparison."
    ._v5_authority_claims_overlap:
      why:
        validates: "Conservatively decides whether one ancestor and descendant claim can intersect."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports the ancestor, descendant, and claim paths whose authority can overlap."
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
    """Keep executable behavior inside each managed skill's ``_rtx`` child.

    Intent
    ------
    Restrict repository-managed skill parents to Markdown sources without process bindings.

    Rationale
    ---------
    The registered runtime child owns executable implementation; enforcing that boundary
    prevents a discoverable parent from acquiring a second, ambiguous runtime surface.

    Pseudocode
    ----------
    - set managed_parents = repository skill modules with skill discovery
    - set source_checks = Markdown gateways without process bindings for managed_parents
    - raise BlueprintGraphError when source_checks fail

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports executable gateways or process bindings declared directly by a managed skill parent."
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
) -> tuple[dict[str, InterfaceExport], dict[str, InterfaceExport]]:
    """Build version-5 source interfaces, direct exports, and child facades.

    Intent
    ------
    Resolve every public interface to a validated source or one direct ``_rtx`` export.

    Rationale
    ---------
    Direct exports own access while facades preserve the terminal source declaration and
    may cross only the registered runtime-child boundary, keeping indirection bounded.

    Pseudocode
    ----------
    - set source_interfaces = validated namespaced interfaces for all sources
    - set direct_exports = module exports bound to interfaces owned by that module
    - set facade_exports = version-matched exports targeting one direct runtime child
    - return sorted source interface and public export indexes

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_v4_interface_contract:
      why:
        validates: "Applies shared semantic contract checks to every version-five source interface."

    InstantiationsFromRepo
    ----------------------
    ._v4_authority_claims:
      why:
        constructs: "Builds the normalized module authority set supplied to interface contract validation."
    ._positive_version:
      why:
        constructs: "Builds validated source-interface and facade target versions during export resolution."
    .InterfaceExport:
      why:
        constructs: "Creates source interface, direct export, and bounded runtime-child facade records."
    .BlueprintGraphError:
      why:
        raises: "Reports invalid interface namespaces, export ownership, facade targets, versions, and child structure."
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


def _v5_namespace_routes(
    modules: Mapping[str, BlueprintNode],
    exports: Mapping[str, InterfaceExport],
    module_children: Mapping[str, tuple[str, ...]],
) -> tuple[
    dict[tuple[str, str], NamespaceRoute],
    tuple[RoutedInterface, ...],
]:
    """Materialize recursive child namespace routes and routed interface records.

    Intent
    ------
    Compute each module's outward interface surface from direct exports and selected children.

    Rationale
    ---------
    Route declarations may expose all or a version-pinned subset of a child's outward
    surface, so recursive resolution must detect cycles and duplicate public identifiers.

    Pseudocode
    ----------
    - set direct_surfaces = public exports grouped by owning module
    - set route_surfaces = recursively materialized child selections and access filters
    - set routed_interfaces = flattened stable records from all route materializations
    - return route index and routed_interfaces

    Wraps
    -----
    - none

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
        """Resolve and cache one module's complete outward interface surface.

        Intent
        ------
        Merge direct exports with validated selections from registered child routes.

        Rationale
        ---------
        Recursive caching handles deep namespace routing efficiently, while an active
        stack detects route cycles before partially materialized surfaces are retained.

        Pseudocode
        ----------
        - return cached outward surface when available
        - set surface = direct exports for module
        - set surface = surface extended by version-checked child selections
        - set outward_surface_cache = outward_surface_cache extended by surface
        - return surface or raise BlueprintGraphError on route conflicts

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._positive_version:
          why:
            constructs: "Builds validated child-module and selected-interface version pins for this surface."
        .RoutedInterface:
          why:
            constructs: "Creates each materialized interface record selected from a child outward surface."
        .NamespaceRoute:
          why:
            constructs: "Creates the cached route record for one validated child surface selection."
        .BlueprintGraphError:
          why:
            raises: "Reports cycles, invalid surfaces, stale pins, private selections, access mismatches, and duplicates."
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
        for child_id, declaration in sorted(raw_routes.items()):
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
    """Assign version-5 direct file ownership across nested modules and sources.

    Intent
    ------
    Resolve content below each module while pruning children and enforcing source exclusivity.

    Rationale
    ---------
    Deepest registered ownership wins across modules, and sibling sources inside one
    module must remain disjoint subsets of their owner's nonchild content.

    Pseudocode
    ----------
    - set module_content = each module's matched files excluding direct child roots
    - set source_content = each contained source's matched files under the same exclusions
    - set ownership_checks = no blueprint inclusion source escape or sibling source overlap
    - return direct file owners with source matches preferred over module remainder

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolved_node_content_paths:
      why:
        reads: "Resolves each module and source content set while pruning registered child module roots."

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports blueprint inclusion, source escape from module content, and sibling source overlap."
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


def _unique_certification_edges(
    edges: list[CertificationEdge],
) -> tuple[CertificationEdge, ...]:
    """Deduplicate and deterministically order certification dependency edges.

    Intent
    ------
    Return one edge per relation, endpoints, and target-version identity.

    Rationale
    ---------
    Multiple graph derivations may establish the same assurance dependency; collapsing
    them avoids duplicate hashing inputs while stable order preserves reproducibility.

    Pseudocode
    ----------
    - set unique_edges = last edge for each certification identity tuple
    - return unique_edges sorted by source relation target and version

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
) -> RepositoryBlueprintGraph:
    """Assemble and validate the canonical version-5 repository blueprint graph.

    Intent
    ------
    Produce nested topology, interfaces, routes, ownership, authorization, and evidence.

    Rationale
    ---------
    Version 5 combines registered module hierarchy with behavioral-source boundaries;
    construction sequences dependent indexes before authorization and cycle validation.

    Pseudocode
    ----------
    - set nodes = schema-validated unique v5 nodes from inventory documents
    - set topology_sources = registered hierarchy and exact source containment
    - set interfaces_routes = source interfaces exports facades and namespace routes
    - set provisional_graph = indexes needed for authorization declaration validation
    - set authorization_declarations = validated against provisional_graph
    - set dependency_edges = authorized source uses export helpers and certification relations
    - set file_owners = deepest registered module and source content ownership
    - set cycle_checks = certification and export dependency acyclicity results
    - return complete RepositoryBlueprintGraph

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_v5_nested_authority:
      why:
        validates: "Checks that no descendant module can overlap a registered ancestor's filesystem authority."
    ._validate_v5_managed_skill_code_boundaries:
      why:
        validates: "Checks that managed skill parents keep executable behavior and process bindings in their runtime child."
    .blueprint_authorization._validate_authorization_declarations:
      why:
        validates: "Checks every version-five authorization declaration against the provisional topology and export indexes."
    ._reject_certification_cycles:
      why:
        validates: "Rejects recursive assurance dependencies in the completed version-five node graph."
    ._reject_export_cycles:
      why:
        validates: "Rejects recursive public callable authority in the completed version-five export graph."

    InstantiationsFromRepo
    ----------------------
    ._declaration_schema_errors:
      why:
        constructs: "Builds concrete schema findings for each version-five inventory document before indexing."
    ._node_from_document:
      why:
        constructs: "Creates each shallow-frozen typed node while retaining declaration mappings under a read-only contract."
    ._v5_topology:
      why:
        constructs: "Builds parent, child, local-segment, and ancestry indexes for registered modules."
    ._v5_sources:
      why:
        constructs: "Builds exact behavioral-source containment indexes for all modules and sources."
    ._v5_interfaces_and_exports:
      why:
        constructs: "Builds source interfaces, direct public exports, and runtime-child facade exports."
    ._v5_namespace_routes:
      why:
        constructs: "Builds recursive namespace routes and flattened routed interface records."
    .RepositoryBlueprintGraph:
      why:
        constructs: "Creates both the provisional authorization view and the final complete graph result."
    .BlueprintEdge:
      why:
        constructs: "Creates module containment, source containment, route, facade, and interface-use node edges."
    .CertificationEdge:
      why:
        constructs: "Creates evidence dependencies for containment, routes, facades, sources, and interfaces."
    ._unique_certification_edges:
      why:
        constructs: "Builds the duplicate-free deterministic evidence edge tuple at provisional and final stages."
    ._build_source_relationships:
      why:
        constructs: "Builds authorized direct source and interface relationships plus their evidence edges."
    ._build_export_relationships:
      why:
        constructs: "Builds export-level direct authority and contract helper relationships."
    ._v5_content_ownership:
      why:
        constructs: "Builds deepest registered direct file ownership across modules and behavioral sources."
    .BlueprintGraphError:
      why:
        raises: "Reports version-five schema, topology, source, authorization, ownership, dependency, and cycle defects."
    """
    validators: dict[str, jsonschema.protocols.Validator] = {}
    nodes: dict[str, BlueprintNode] = {}
    for document in documents:
        errors = _declaration_schema_errors(
            document.path,
            dict(document.declaration),
            schema_root,
            validators,
            expected_schema_version=5,
        )
        if errors:
            raise errors[0]
        node = _node_from_document(document, expected_schema_version=5)
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
            "version 5 repository graph requires at least one module"
        )
    if len(modules) + len(sources) != len(nodes):
        raise BlueprintGraphError(
            "version 5 repository graph permits only module and "
            "behavioral_source nodes"
        )

    (
        module_parents,
        module_children,
        module_local_segments,
        module_ancestry,
    ) = _v5_topology(root, modules)
    _validate_v5_nested_authority(modules, module_ancestry)
    module_sources, source_modules = _v5_sources(
        root,
        modules,
        sources,
    )
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
    )
    namespace_routes, routed_interfaces = _v5_namespace_routes(
        modules,
        exports,
        module_children,
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
        schema_version=5,
        source_modules=source_modules,
        source_interfaces=source_interfaces,
        module_parents=module_parents,
        module_children=module_children,
        module_local_segments=module_local_segments,
        module_ancestry=module_ancestry,
        namespace_routes=namespace_routes,
        routed_interfaces=routed_interfaces,
    )
    from .blueprint_authorization import (  # noqa: PLC0415
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
        """Resolve one v5 interface use through private or public authorization rules.

        Intent
        ------
        Return the relation and implementing source for an allowed version-pinned use.

        Rationale
        ---------
        Private interfaces remain module-local, whereas exports delegate admission to
        canonical authorization resolution and then require source platform compatibility.

        Pseudocode
        ----------
        - if interface is private:
          - return private-use relation and source after ownership and version checks
        - if interface is exported:
          - set authorization_request = AuthorizationRequest for caller source interface and version
          - set authorization_result = resolve_interface_authorization for provisional graph and authorization_request
          - return export-use relation and authorized implementing source from authorization_result
        - raise BlueprintGraphError for unresolved or denied uses

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._require_platform_compatibility:
          why:
            validates: "Checks the authorized implementing source against every platform promised by the caller source."

        InstantiationsFromRepo
        ----------------------
        .blueprint_authorization.AuthorizationRequest:
          why:
            constructs: "Creates the canonical authorization request from the caller module, source, interface, and version."
        .blueprint_authorization.resolve_interface_authorization:
          why:
            constructs: "Builds the authorization decision and implementing-source result used by public interface resolution."
        .BlueprintGraphError:
          why:
            raises: "Reports cross-module private use, stale versions, authorization denial, and unresolved interfaces."
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
        schema_version=5,
        source_modules=source_modules,
        source_interfaces=source_interfaces,
        module_parents=module_parents,
        module_children=module_children,
        module_local_segments=module_local_segments,
        module_ancestry=module_ancestry,
        namespace_routes=namespace_routes,
        routed_interfaces=routed_interfaces,
    )


def _declared_interface_references(value: JsonValue) -> tuple[str, ...]:
    """Return interface identifiers conservatively referenced by a declaration.

    Intent
    ------
    Find nested ``interface`` fields that contain canonical interface identifiers.

    Rationale
    ---------
    Dispatch closure selection must include possible providers before full schema and
    graph validation, so traversal intentionally accepts any JSON-shaped declaration.

    Pseudocode
    ----------
    - set referenced_interfaces = interface identifiers found by recursive JSON traversal
    - return referenced_interfaces in sorted unique order

    Wraps
    -----
    - none
    """

    found: set[str] = set()

    def visit(item: JsonValue) -> None:
        """Collect interface fields recursively from one JSON declaration fragment.

        Intent
        ------
        Traverse lists and mappings while ignoring scalar declaration values.

        Rationale
        ---------
        A local recursive walker shares the enclosing result set without exposing a
        broader helper API for a dispatch-closure-specific conservative scan.

        Pseudocode
        ----------
        - set nested_scan = recursive visits for list items or mapping members
        - set found = found plus any canonical interface field on this mapping

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
    """Return absolute module IDs named by nested access-policy caller lists.

    Intent
    ------
    Collect nonrelative callers that may be required by a selected dispatch closure.

    Rationale
    ---------
    Authorization validation needs the named caller modules present even when the
    declaration carrying their access policy belongs to another selected module.

    Pseudocode
    ----------
    - set caller_modules = absolute allowed callers found by recursive JSON traversal
    - return caller_modules in sorted unique order

    Wraps
    -----
    - none
    """

    found: set[str] = set()

    def visit(item: JsonValue) -> None:
        """Collect absolute allowed callers recursively from one JSON fragment.

        Intent
        ------
        Descend through lists and mappings and add nonrelative caller strings.

        Rationale
        ---------
        Access policies may occur at several declaration depths, so the closure scan
        must not rely on a single schema location before the selected graph is loaded.

        Pseudocode
        ----------
        - set nested_scan = recursive visits for list items or mapping members
        - set found = found plus nonrelative strings from allowed caller lists

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
    Extract unique textual source targets from a declaration's dependency list.

    Rationale
    ---------
    Dispatch closure expansion needs only provider ownership at this stage; concrete
    schema and version checks remain the responsibility of subsequent graph loading.

    Pseudocode
    ----------
    - if declaration or dependency list has the wrong container type:
      - return empty tuple
    - set source_ids = textual source fields from mapping dependencies
    - return source_ids in sorted unique order

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
    Return all documents needed to validate a caller, target export, and their dependencies.

    Rationale
    ---------
    Scoped dispatch may ignore proven-unrelated defects only after conservatively closing
    over parents, children, providers, access callers, and direct source dependencies.

    Pseudocode
    ----------
    - set indexes = documents by node module root export owner child and parent
    - set dispatch_endpoints = existing caller module and target export owner
    - set selected_modules = fixed-point expansion through family and declared dependencies
    - set availability_check = every selected module present in inventory
    - return documents under selected module roots and the selected roots

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._declared_interface_references:
      why:
        reads: "Finds conservatively referenced interface providers while expanding selected module declarations."
    ._declared_source_dependencies:
      why:
        reads: "Finds direct behavioral-source dependencies whose owning modules must join the closure."

    InstantiationsFromRepo
    ----------------------
    ._declared_absolute_caller_references:
      why:
        constructs: "Builds the absolute access-policy caller set added to the selected module closure."
    .BlueprintGraphError:
      why:
        raises: "Reports duplicate identities, invalid parentage, missing callers, exports, or referenced modules."
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
    Prefer the full canonical graph, then recover a valid conservative closure if needed.

    Rationale
    ---------
    A dispatch should not fail because of an invalid unrelated module, but any inventory
    issue touching the selected closure remains fatal and unrelated defects stay visible.

    Pseudocode
    ----------
    - set full_graph = attempted canonical version-five repository load
    - if full_graph succeeds:
      - return DispatchBlueprintGraph containing full_graph
    - set inventory = tolerant blueprint collection and dispatch closure selection
    - set issue_partition = fatal selected issues and nonfatal unrelated issues
    - set scoped_graph = validated version-five graph from selected documents
    - return DispatchBlueprintGraph with scoped_graph and unrelated diagnostics

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .load_repository_blueprint_graph:
      why:
        constructs: "Builds the preferred complete graph carried directly into the successful dispatch result."
    .blueprint_inventory.collect_blueprints:
      why:
        constructs: "Builds tolerant inventory evidence used to distinguish selected from unrelated defects."
    ._dispatch_document_closure:
      why:
        constructs: "Builds the conservative document and module-root selection for the requested dispatch."
    ._load_v5_repository_blueprint_graph:
      why:
        constructs: "Builds the canonical graph from only the selected closure documents after issue screening."
    .BlueprintDiagnostic:
      why:
        constructs: "Creates each nonfatal diagnostic preserved for a proven-unrelated blueprint defect."
    .DispatchBlueprintGraph:
      why:
        constructs: "Creates the public result pairing the selected graph with any unrelated diagnostics."
    .BlueprintGraphError:
      why:
        raises: "Reports inventory defects that cannot be proven outside the selected dispatch closure."
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
            else root / "references" / "blueprint"
        )
        if not (selected_schema_root / "module.schema.json").is_file():
            selected_schema_root = (
                Path(__file__).resolve().parents[3]
                / "references"
                / "blueprint"
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
    expected_schema_version: int = 5,
) -> RepositoryBlueprintGraph:
    """Load one explicit repository-wide graph with version 5 as the default.

    Intent
    ------
    Inventory one repository version and dispatch construction to its canonical loader.

    Rationale
    ---------
    Requiring every document to match the requested version prevents mixed semantics,
    while schema-root fallback keeps installed runtime loading independent of checkout layout.

    Pseudocode
    ----------
    - set version_request = supported requested schema version
    - set documents = inventory blueprints restricted to requested version
    - set version_check = every declaration version equal to version_request
    - set concrete_schema_root = supplied repository or installed fallback schema directory
    - return version-specific repository graph built from documents

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .blueprint_inventory.iter_blueprints:
      why:
        constructs: "Builds the strict inventory document sequence for the requested repository version."
    ._load_v5_repository_blueprint_graph:
      why:
        constructs: "Builds the canonical version-five graph when the requested version is five."
    ._load_v4_repository_blueprint_graph:
      why:
        constructs: "Builds the compatibility version-four graph when explicitly requested."
    .BlueprintGraphError:
      why:
        raises: "Reports a blueprint document whose declared version differs from the repository graph request."
    """

    if expected_schema_version not in {4, 5}:
        raise ValueError("expected_schema_version must be 4 or 5")
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
            root / "references" / "blueprint"
            if expected_schema_version == 5
            else root / "references" / "blueprint" / "migrations" / "v4"
        )
    )
    if not (selected_schema_root / "module.schema.json").is_file():
        selected_schema_root = (
            Path(__file__).resolve().parents[3]
            / "references"
            / "blueprint"
            / (
                Path()
                if expected_schema_version == 5
                else Path("migrations") / "v4"
            )
        )
    if expected_schema_version == 5:
        return _load_v5_repository_blueprint_graph(
            root,
            documents,
            schema_root=selected_schema_root,
        )
    return _load_v4_repository_blueprint_graph(
        root,
        documents,
        schema_root=selected_schema_root,
    )


def repository_schema_version(repo_root: Path) -> int:
    """Return the repository schema version, defaulting markerless legacy trees to four.

    Intent
    ------
    Read the canonical blueprint marker and accept only supported graph versions.

    Rationale
    ---------
    Older repositories predate the marker, so absence has a defined compatibility value;
    malformed or unsupported declarations must remain explicit configuration failures.

    Pseudocode
    ----------
    - set marker_document = parsed canonical blueprint marker when present
    - return four when marker is absent
    - raise ValueError when marker cannot be read or names an unsupported version
    - return marker schema version

    Wraps
    -----
    - none
    """

    marker = (
        Path(repo_root)
        / "references"
        / "blueprint"
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
    if version not in {4, 5}:
        raise ValueError(
            f"{marker}: repository schema version must be 4 or 5"
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
    Return the owning module, implementing source, and export after optional version checks.

    Rationale
    ---------
    Public resolution rejects module identifiers and stale pins, and verifies that the
    export's recorded source still exists with the required behavioral-source node type.

    Pseudocode
    ----------
    - set export_identity = callable public export for interface_id
    - set binding_check = optional version agreement and behavioral-source binding
    - set implementing_source = behavioral-source node named by export
    - return owning module implementing_source and export

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .BlueprintGraphError:
      why:
        raises: "Reports noncallable modules, unknown exports, stale versions, and missing source bindings."
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
    Validate the export and list only interfaces named by its outgoing export edges.

    Rationale
    ---------
    Runtime authority derives from the implementing source's effective direct uses,
    not from transitive dependencies or every interface present in the repository graph.

    Pseudocode
    ----------
    - set export_resolution = @resolve_export(graph interface_id)
    - set direct_authority = target interfaces on outgoing edges for interface_id
    - return direct_authority in sorted order

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .resolve_export:
      why:
        validates: "Confirms that the requested identifier resolves to a callable export before edge selection."
    """

    resolve_export(graph, interface_id)
    return tuple(
        sorted(
            edge.target_interface_id
            for edge in graph.export_edges
            if edge.source_export_id == interface_id
        )
    )

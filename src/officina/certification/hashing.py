"""Canonical node hashing and certification-basis derivation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
from typing import Any, Iterable, Mapping, Sequence

import yaml

from ..common.atomic_files import AtomicWriteError, read_regular_file_bytes
from ..blueprints.graph import (
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
)
from officina.configuration.configured_schema import ConfiguredSchemaError, load_configuration
from ..git.provenance import capture_git_snapshot, git_file_provenance_batch, run_git
from ..common.repository_paths import RepositoryPathError, repository_relative_path


class CertificationHashError(ValueError):
    """Raised when a graph cannot be certified deterministically."""


def _repository_path(path: Path, repo_root: Path) -> Path:
    try:
        return repo_root / repository_relative_path(path, repo_root)
    except RepositoryPathError as exc:
        raise CertificationHashError(str(exc)) from exc


V4_CERTIFICATION_BASIS_MANIFEST = Path(
    "skills/skill-drift/references/certification-basis-roots.json"
)
CERTIFICATION_BASIS_MANIFEST = Path(
    "references/certification-policy/certification-basis-roots.json"
)
CANONICAL_NODE_HASH_POLICY = Path(
    "references/certification-policy/node-hash-policy.yaml"
)
CERTIFIER_NODE_ID = "skill-certifier"
CERTIFIER_INTERFACE_ID = "skill-certifier.interface.certify"
V6_CERTIFIER_INTERFACE_ID = "skill-certifier._rtx.interface.certify"
CERTIFIER_INTERFACE_VERSION = 2
CERTIFIER_AUDIT_INTERFACE_VERSION = 2
CERTIFIER_AUDIT_INTERFACES = {
    "interface": "skill-certifier.source.audit-interface.interface.audit",
    "remainder": "skill-certifier.source.audit-behavioral-source.interface.audit",
    "module": "skill-certifier.source.audit-module.interface.audit",
}
EVIDENCE_ONLY_RELATIONS = frozenset({"certified-under"})
CERTIFIER_CHECK_REGISTRY: Mapping[str, tuple[str, int]] = {
    "deterministic": ("v4-deterministic", 1),
    "route-smoke": ("route-smoke-dependencies", 1),
    "semantic-review": ("blueprint-accuracy", 1),
}
V5_CERTIFIER_CHECK_REGISTRY: Mapping[str, tuple[str, int]] = {
    "deterministic": ("v5-deterministic", 1),
    "route-smoke": ("route-smoke-dependencies", 2),
    "semantic-review": ("blueprint-accuracy", 2),
}
V6_CERTIFIER_CHECK_REGISTRY: Mapping[str, tuple[str, int]] = {
    "deterministic": ("v6-deterministic", 1),
    "route-smoke": ("route-smoke-dependencies", 3),
    "semantic-review": ("blueprint-accuracy", 3),
}


def certifier_check_registry(
    expected_schema_version: int = 6,
) -> Mapping[str, tuple[str, int]]:
    """Select the immutable check registry for one repository schema."""

    if expected_schema_version == 4:
        return CERTIFIER_CHECK_REGISTRY
    if expected_schema_version == 5:
        return V5_CERTIFIER_CHECK_REGISTRY
    if expected_schema_version == 6:
        return V6_CERTIFIER_CHECK_REGISTRY
    raise ValueError("expected_schema_version must be 4, 5, or 6")


@dataclass(frozen=True)
class CertificationFacetHashState:
    """Canonical local and dependency hash state for one certification facet."""

    facet_id: str
    facet_type: str
    local_hash: str
    input_manifest: tuple[dict[str, str], ...] = ()
    dependency_hashes: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class NodeHashState:
    """Canonical hash state for one versioned node."""

    node_hash: str | None = None
    input_manifest: tuple[dict[str, str], ...] = ()
    dependency_hashes: tuple[dict[str, Any], ...] = ()
    certification_basis_hash: str | None = None
    facets: tuple[CertificationFacetHashState, ...] = ()


def certification_facet_claims(
    state: NodeHashState,
) -> tuple[dict[str, Any], ...]:
    """Project canonical facet hash states into signed payload claims."""

    if not isinstance(state, NodeHashState):
        raise CertificationHashError(
            "facet claim projection requires a canonical node hash state"
        )
    return tuple(
        {
            "id": facet.facet_id,
            "type": facet.facet_type,
            "local_hash": facet.local_hash,
            "input_manifest": [dict(entry) for entry in facet.input_manifest],
            "dependencies": [dict(entry) for entry in facet.dependency_hashes],
        }
        for facet in sorted(
            state.facets,
            key=lambda item: (
                item.facet_type != "remainder",
                item.facet_id,
            ),
        )
    )


def certification_target_postorder(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    requested: Sequence[str],
) -> tuple[str, ...]:
    """Order exact certification targets after every canonical dependency."""

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise CertificationHashError(
                f"canonical certification dependency cycle at {node_id}"
            )
        if node_id not in graph.nodes:
            raise CertificationHashError(
                f"unknown exact certification target: {node_id}"
            )
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            raise CertificationHashError(
                f"missing canonical certification state for {node_id}"
            )
        visiting.add(node_id)
        dependencies: list[tuple[str, str, int]] = []
        for dependency in state.dependency_hashes:
            relation = dependency.get("relation")
            if relation in EVIDENCE_ONLY_RELATIONS:
                continue
            target = dependency.get("target")
            version = dependency.get("version")
            if (
                not isinstance(relation, str)
                or not isinstance(target, str)
                or target not in graph.nodes
                or not isinstance(version, int)
                or isinstance(version, bool)
            ):
                raise CertificationHashError(
                    f"invalid canonical certification dependency for {node_id}"
                )
            dependencies.append((relation, target, version))
        for _relation, target, _version in sorted(dependencies):
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)
        ordered.append(node_id)

    for node_id in sorted(set(requested)):
        visit(node_id)
    return tuple(ordered)


@dataclass(frozen=True, order=True)
class RouteSmokeDependencyMapping:
    """One dynamically loaded route-smoke file resolved to existing authority."""

    path: str
    authority: str
    target_node_id: str | None


@dataclass(frozen=True, order=True)
class _ContractReference:
    locator: str
    path: Path
    digest: str


def _require_sha256_hash(node_id: str, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise CertificationHashError(f"{node_id}: {field} must be a sha256 hash")
    prefix, separator, hexadecimal = value.partition(":")
    if (
        prefix != "sha256"
        or not separator
        or len(hexadecimal) != 64
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise CertificationHashError(f"{node_id}: {field} must be a sha256 hash")
    return value


def _route_smoke_path(
    value: Path | str,
    repo_root: Path,
    *,
    allow_outside: bool,
) -> tuple[Path, str]:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    absolute = Path(os.path.abspath(candidate))
    try:
        relative_path = repository_relative_path(absolute, repo_root)
        relative = relative_path.as_posix()
        absolute = repo_root / relative_path
    except RepositoryPathError:
        if not allow_outside:
            raise CertificationHashError(
                f"unmapped route-smoke dependency outside repository: {absolute}"
            )
        relative = absolute.as_posix()
    try:
        metadata = absolute.lstat()
    except OSError as exc:
        raise CertificationHashError(
            f"unmapped route-smoke dependency {relative}: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise CertificationHashError(
            f"unmapped route-smoke dependency is not a regular file: {relative}"
        )
    return absolute, relative


def map_route_smoke_dependencies(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    source_node_id: str,
    loaded_paths: Iterable[Path | str],
    certification_basis_paths: Iterable[Path | str],
    repo_root: Path,
) -> tuple[RouteSmokeDependencyMapping, ...]:
    """Resolve every dynamic Python dependency to node input, edge, or basis."""

    root = Path(repo_root).resolve()
    source = graph.nodes.get(source_node_id)
    if source is None or source.node_type != "behavioral_source":
        raise CertificationHashError(
            f"route-smoke source must be a behavioral source: {source_node_id}"
        )
    source_module_id = graph.source_modules.get(source_node_id)
    manifest_paths: dict[str, set[str]] = {}
    validated_states: dict[str, NodeHashState] = {}
    for node_id in sorted(graph.nodes):
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            raise CertificationHashError(
                f"{node_id}: route-smoke mapping requires canonical v4 node state"
            )
        _require_sha256_hash(node_id, "node_hash", state.node_hash)
        if not isinstance(state.input_manifest, tuple):
            raise CertificationHashError(
                f"{node_id}: route-smoke mapping found invalid input manifest"
            )
        if not isinstance(state.dependency_hashes, tuple):
            raise CertificationHashError(
                f"{node_id}: route-smoke mapping found invalid dependency hashes"
            )
        paths: set[str] = set()
        for entry in state.input_manifest:
            path = entry.get("path") if isinstance(entry, Mapping) else None
            if not isinstance(path, str):
                raise CertificationHashError(
                    f"{node_id}: route-smoke mapping found invalid input manifest"
                )
            paths.add(path)
        manifest_paths[node_id] = paths
        validated_states[node_id] = state

    reachable: set[str] = set()
    pending = [source_node_id]
    children: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    required_node_dependency_fields = {
        "relation",
        "target",
        "version",
        "node_hash",
    }
    required_interface_dependency_fields = {
        "relation",
        "target",
        "interface",
        "version",
        "interface_hash",
    }
    for node_id, state in validated_states.items():
        for index, dependency in enumerate(state.dependency_hashes):
            if not isinstance(dependency, Mapping):
                raise CertificationHashError(
                    f"{node_id}: invalid dependency hash at index {index}"
                )
            fields = set(dependency)
            relation = dependency.get("relation")
            if relation in EVIDENCE_ONLY_RELATIONS:
                continue
            target_id = dependency.get("target")
            version = dependency.get("version")
            target = (
                graph.nodes.get(target_id) if isinstance(target_id, str) else None
            )
            target_state = (
                validated_states.get(target_id)
                if isinstance(target_id, str)
                else None
            )
            valid = False
            if fields == required_node_dependency_fields:
                target_hash = dependency.get("node_hash")
                valid = (
                    isinstance(relation, str)
                    and bool(relation)
                    and target is not None
                    and target_state is not None
                    and isinstance(version, int)
                    and not isinstance(version, bool)
                    and version == target.version
                    and target_hash == target_state.node_hash
                )
            elif fields == required_interface_dependency_fields:
                interface_id = dependency.get("interface")
                interface_hash = dependency.get("interface_hash")
                if (
                    relation in {"uses-private-interface", "uses-export"}
                    and target is not None
                    and target_state is not None
                    and isinstance(interface_id, str)
                    and isinstance(version, int)
                    and not isinstance(version, bool)
                ):
                    try:
                        extracted = extract_interface_from_blueprint(
                            graph,
                            interface_id,
                            version,
                        )
                        source_interface_id = extracted.get("source_interface")
                        source_facet = next(
                            (
                                facet
                                for facet in target_state.facets
                                if facet.facet_id == source_interface_id
                            ),
                            None,
                        )
                    except CertificationHashError:
                        extracted = None
                        source_facet = None
                    valid = (
                        extracted is not None
                        and source_facet is not None
                        and extracted.get("source_node") == target_id
                        and interface_hash
                        == compute_interface_hash(
                            extracted,
                            input_manifest=source_facet.input_manifest,
                        )
                    )
            if not valid:
                raise CertificationHashError(
                    f"{node_id}: invalid dependency hash at index {index}"
                )
            assert isinstance(target_id, str)
            children[node_id].add(target_id)
    while pending:
        current = pending.pop()
        for target_id in sorted(children.get(current, ())):
            if target_id not in reachable and target_id != source_node_id:
                reachable.add(target_id)
                pending.append(target_id)

    basis = {
        absolute: label
        for absolute, label in (
            _route_smoke_path(path, root, allow_outside=True)
            for path in certification_basis_paths
        )
    }
    mappings: dict[str, RouteSmokeDependencyMapping] = {}
    for absolute, relative in (
        _route_smoke_path(path, root, allow_outside=True) for path in loaded_paths
    ):
        try:
            repository_relative_path(absolute, root)
        except RepositoryPathError:
            basis_label = basis.get(absolute)
            if basis_label is None:
                raise CertificationHashError(
                    f"unmapped route-smoke dependency outside repository: {absolute}"
                )
            mapping = RouteSmokeDependencyMapping(
                basis_label, "certification-basis", None
            )
            mappings[basis_label] = mapping
            continue
        if relative in manifest_paths[source_node_id]:
            mapping = RouteSmokeDependencyMapping(
                relative, "direct-input", source_node_id
            )
        else:
            direct_owner = graph.direct_file_owners.get(absolute)
            candidates = {
                target_id
                for target_id in reachable
                if relative in manifest_paths[target_id]
            }
            if direct_owner in candidates:
                candidates = {direct_owner}
            if len(candidates) == 1:
                mapping = RouteSmokeDependencyMapping(
                    relative,
                    "certification-dependency",
                    next(iter(candidates)),
                )
            elif candidates:
                raise CertificationHashError(
                    f"unmapped route-smoke dependency {relative}: ambiguous authority"
                )
            elif absolute in basis:
                mapping = RouteSmokeDependencyMapping(
                    relative, "certification-basis", None
                )
            elif graph.schema_version in {5, 6}:
                reachable_modules = {
                    graph.source_modules.get(node_id)
                    for node_id in reachable
                }
                module_candidates = {
                    module_id
                    for module_id in {
                        source_module_id,
                        *reachable,
                        *reachable_modules,
                    }
                    if isinstance(module_id, str)
                    and (module := graph.nodes.get(module_id)) is not None
                    and module.node_type == "module"
                    and absolute
                    == Path(os.path.abspath(module.module_root / "__init__.py"))
                    and relative in manifest_paths[module_id]
                }
                if len(module_candidates) == 1:
                    mapping = RouteSmokeDependencyMapping(
                        relative,
                        "module-package-input",
                        next(iter(module_candidates)),
                    )
                elif module_candidates:
                    raise CertificationHashError(
                        f"unmapped route-smoke dependency {relative}: "
                        "ambiguous module package authority"
                    )
                else:
                    detail = (
                        "no authority" if not candidates else "ambiguous authority"
                    )
                    raise CertificationHashError(
                        f"unmapped route-smoke dependency {relative}: {detail}"
                    )
            else:
                detail = "no authority" if not candidates else "ambiguous authority"
                raise CertificationHashError(
                    f"unmapped route-smoke dependency {relative}: {detail}"
                )
        mappings[relative] = mapping
    return tuple(mappings[path] for path in sorted(mappings))


def route_smoke_trace_signature(
    mappings: Iterable[RouteSmokeDependencyMapping],
) -> tuple[tuple[str, str, str | None], ...]:
    """Return the stable projection compared by the certification audit."""

    return tuple(
        sorted(
            (mapping.path, mapping.authority, mapping.target_node_id)
            for mapping in mappings
        )
    )


_STABLE_CHECK_FIELDS = ("id", "version", "passed", "findings")


def _default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "references" / "blueprint-schema"



def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def certification_basis_roots_path(
    repo_root: Path,
    *,
    expected_schema_version: int = 6,
) -> Path:
    """Return the canonical repository-owned certification-basis manifest."""

    if expected_schema_version == 4:
        relative = V4_CERTIFICATION_BASIS_MANIFEST
    elif expected_schema_version in {5, 6}:
        relative = CERTIFICATION_BASIS_MANIFEST
    else:
        raise ValueError("expected_schema_version must be 4, 5, or 6")
    return Path(repo_root).resolve() / relative


def _tracked_basis_paths_at_head(root: Path) -> tuple[PurePosixPath, ...]:
    """Return paths tracked at one captured HEAD, or none outside a Git root."""

    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        return ()
    try:
        result = run_git(
            root,
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            snapshot.commit,
            check=False,
        )
    except OSError as exc:
        raise CertificationHashError(
            f"cannot enumerate certification basis at {snapshot.commit}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CertificationHashError(
            f"cannot enumerate certification basis at {snapshot.commit}: {detail}"
        )
    return tuple(
        PurePosixPath(os.fsdecode(raw_path))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    )


def _basis_pattern_matches(path: PurePosixPath, pattern: PurePosixPath) -> bool:
    """Match pathlib-style path segments, with ``**`` matching zero or more."""

    path_parts = path.parts
    pattern_parts = pattern.parts

    @lru_cache(maxsize=None)
    def matches(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return matches(pattern_index + 1, path_index) or (
                path_index < len(path_parts)
                and matches(pattern_index, path_index + 1)
            )
        return (
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], part)
            and matches(pattern_index + 1, path_index + 1)
        )

    return matches(0, 0)


def resolve_certification_basis_paths(
    repo_root: Path,
    *,
    expected_schema_version: int = 6,
    allow_non_atomic: bool = False,
) -> tuple[Path, ...]:
    """Resolve the canonical manifest without accepting caller-selected inputs."""

    root = Path(repo_root).resolve()
    manifest = certification_basis_roots_path(
        root,
        expected_schema_version=expected_schema_version,
    )
    try:
        raw = json.loads(
            read_regular_file_bytes(
                manifest,
                allowed_root=root,
                allow_non_atomic=allow_non_atomic,
            ).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationHashError(
            f"cannot read certification basis manifest {manifest}: {exc}"
        ) from exc
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise CertificationHashError(
            f"{manifest}: certification basis manifest must contain a JSON string list"
        )
    tracked_at_head = _tracked_basis_paths_at_head(root)
    selected = {manifest}
    for pattern in raw:
        relative = Path(pattern)
        if relative.is_absolute() or ".." in relative.parts:
            raise CertificationHashError(
                f"certification basis root must stay under target package: {pattern}"
            )
        is_pattern = any(character in pattern for character in "*?[]")
        current_candidates = (
            sorted(root.glob(pattern), key=lambda path: path.as_posix())
            if is_pattern
            else [root / relative]
        )
        tracked_candidates = {
            root.joinpath(*path.parts)
            for path in tracked_at_head
            if _basis_pattern_matches(path, PurePosixPath(pattern))
        }
        matched_regular_file = False
        for path in sorted(
            {*current_candidates, *tracked_candidates},
            key=lambda candidate: candidate.as_posix(),
        ):
            try:
                read_regular_file_bytes(
                    path,
                    allowed_root=root,
                    allow_non_atomic=allow_non_atomic,
                )
            except OSError as exc:
                try:
                    path.lstat()
                except FileNotFoundError:
                    if path not in tracked_candidates:
                        continue
                    raise CertificationHashError(
                        f"tracked certification basis input is missing: {path}"
                    ) from exc
                except OSError:
                    pass
                raise CertificationHashError(
                    "certification basis input is not a confined regular file: "
                    f"{path}: {exc}"
                ) from exc
            selected.add(path)
            matched_regular_file = True
        if not matched_regular_file:
            raise CertificationHashError(
                f"certification basis pattern matched no files: {pattern}"
            )
    return tuple(sorted(selected, key=lambda path: path.as_posix()))


def compute_certification_basis_hash(
    repo_root: Path,
    *,
    expected_schema_version: int = 6,
    allow_non_atomic: bool = False,
) -> str:
    """Hash the explicitly selected certification-basis manifest and files."""

    root = Path(repo_root).resolve()
    entries: list[dict[str, str]] = []
    for path in resolve_certification_basis_paths(
        root,
        expected_schema_version=expected_schema_version,
        allow_non_atomic=allow_non_atomic,
    ):
        try:
            relative = repository_relative_path(path, root).as_posix()
        except RepositoryPathError as exc:
            raise CertificationHashError(
                f"certification basis input is outside repository: {path}"
            ) from exc
        data = read_regular_file_bytes(
            path,
            allowed_root=root,
            allow_non_atomic=allow_non_atomic,
        )
        if path == root / CANONICAL_NODE_HASH_POLICY:
            try:
                data = _canonical_bytes(yaml.safe_load(data.decode("utf-8")))
            except (UnicodeError, yaml.YAMLError, TypeError, ValueError) as exc:
                raise CertificationHashError(
                    f"cannot canonicalize node hash policy: {path}"
                ) from exc
        entries.append({"path": relative, "digest": _hash_bytes(data)})
    if not entries:
        raise CertificationHashError("certification basis must contain at least one input")
    return _hash_value(entries)


def expected_certifier_checks(
    expected_schema_version: int = 6,
) -> tuple[dict[str, object], ...]:
    """Return the exact passed records owned by the versioned certifier registry."""

    return normalize_node_checks(
        {
            "id": check_id,
            "version": version,
            "passed": True,
            "findings": [],
        }
        for check_id, version in certifier_check_registry(
            expected_schema_version
        ).values()
    )


def derive_certifier_identity(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    source_commit: str,
) -> dict[str, object]:
    """Derive the certifier identity from the current graph and Git snapshot."""

    node = graph.nodes.get(CERTIFIER_NODE_ID)
    if node is None or node.node_type != "module":
        raise CertificationHashError("canonical certifier module is absent from the graph")
    if graph.schema_version == 6:
        interface_id = V6_CERTIFIER_INTERFACE_ID
        interface_owner_id = f"{CERTIFIER_NODE_ID}._rtx"
        interface_version = CERTIFIER_INTERFACE_VERSION
    else:
        interface_id = CERTIFIER_INTERFACE_ID
        interface_owner_id = CERTIFIER_NODE_ID
        interface_version = 1
    export = graph.exports.get(interface_id)
    if (
        export is None
        or export.module_node_id != interface_owner_id
        or export.version != interface_version
    ):
        raise CertificationHashError(
            "canonical certifier interface is absent or has the wrong version"
        )
    state = states.get(CERTIFIER_NODE_ID)
    node_hash = state.node_hash if isinstance(state, NodeHashState) else None
    if (
        not isinstance(node_hash, str)
        or len(node_hash) != 71
        or not node_hash.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in node_hash[7:])
    ):
        raise CertificationHashError("canonical certifier node state is unavailable")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        raise CertificationHashError("canonical certifier source commit is unavailable")
    return {
        "interface": interface_id,
        "version": interface_version,
        "node_hash": node_hash,
        "source_commit": source_commit,
    }


def _hash_value(value: Any) -> str:
    return _hash_bytes(_canonical_bytes(value))


def extract_interface_from_blueprint(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
    version: int,
) -> dict[str, Any]:
    """Return the canonical blueprint projection for one resolved interface.

    The projection binds the requested interface identity to its implementing
    source interface and gateway while excluding every unrelated field in the
    provider module and behavioral-source blueprints.
    """

    if not isinstance(graph, RepositoryBlueprintGraph):
        raise CertificationHashError(
            "interface extraction requires a repository blueprint graph"
        )
    if (
        not isinstance(interface_id, str)
        or not interface_id
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        raise CertificationHashError(
            "interface extraction requires an interface id and positive version"
        )

    resolved = graph.exports.get(interface_id)
    if resolved is None:
        resolved = graph.source_interfaces.get(interface_id)
    if resolved is not None:
        declaration = resolved.declaration
        actual_version = resolved.version
        source_node_id = resolved.source_node_id
        source_interface_id = resolved.source_interface_id
    else:
        source_node_id, marker, _local_name = interface_id.rpartition(".interface.")
        source = graph.nodes.get(source_node_id) if marker else None
        interfaces = (
            source.declaration.get("interfaces")
            if source is not None and source.node_type == "behavioral_source"
            else None
        )
        declaration = (
            interfaces.get(interface_id)
            if isinstance(interfaces, Mapping)
            else None
        )
        actual_version = (
            declaration.get("version")
            if isinstance(declaration, Mapping)
            else None
        )
        source_interface_id = interface_id

    source = (
        graph.nodes.get(source_node_id)
        if isinstance(source_node_id, str)
        else None
    )
    gateway = (
        source.declaration.get("gateway")
        if source is not None and source.node_type == "behavioral_source"
        else None
    )
    if (
        not isinstance(declaration, Mapping)
        or actual_version != version
        or not isinstance(source_node_id, str)
        or not isinstance(source_interface_id, str)
        or not isinstance(gateway, Mapping)
    ):
        raise CertificationHashError(
            f"unresolved interface blueprint projection: {interface_id}@{version}"
        )
    return {
        "id": interface_id,
        "version": version,
        "source_node": source_node_id,
        "source_interface": source_interface_id,
        "gateway": deepcopy(dict(gateway)),
        "declaration": deepcopy(dict(declaration)),
        **(
            {"export_declaration": deepcopy(dict(resolved.export_declaration))}
            if resolved is not None
            and isinstance(resolved.export_declaration, Mapping)
            else {}
        ),
    }


def compute_interface_hash(
    extracted_interface: Mapping[str, Any],
    *,
    input_manifest: Iterable[Mapping[str, str]] = (),
) -> str:
    """Hash one interface's canonical local declaration and content manifest."""

    if not isinstance(extracted_interface, Mapping):
        raise CertificationHashError(
            "interface hashing requires an extracted interface mapping"
        )
    projection = deepcopy(dict(extracted_interface))
    declaration = projection.get("declaration")
    if not isinstance(declaration, Mapping):
        raise CertificationHashError(
            "interface hashing requires a declaration mapping"
        )
    local_declaration = deepcopy(dict(declaration))
    local_declaration.pop("uses_interfaces", None)
    projection["declaration"] = local_declaration
    manifest = tuple(dict(entry) for entry in input_manifest)
    return _hash_value(
        {
            "interface": projection,
            "input_manifest": manifest,
        }
    )


def _reference_candidates(value: object) -> tuple[tuple[str, str], ...]:
    found: set[tuple[str, str]] = set()

    def visit(current: object) -> None:
        if isinstance(current, Mapping):
            path = current.get("path")
            fragment = current.get("fragment")
            if isinstance(path, str) and isinstance(fragment, str) and fragment.startswith("#"):
                found.add((path, fragment))
            for key, child in current.items():
                if key == "contract_references":
                    continue
                if (
                    key in {"schema", "format"}
                    and isinstance(child, str)
                    and child.lower().endswith((".json", ".yaml", ".yml"))
                ):
                    found.add((child, "#"))
                visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _validated_owned_input(owner_root: Path, path: Path) -> Path:
    """Resolve one regular file beneath its owner without following symlinks."""

    owner_absolute = Path(os.path.abspath(owner_root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(owner_absolute)
    except ValueError as exc:
        raise CertificationHashError(
            f"{path}: input must remain under {owner_root}"
        ) from exc
    current = owner_absolute
    try:
        for component in relative.parts:
            current = current / component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise CertificationHashError(
                    f"{path}: input contains a symlink component"
                )
        if not stat.S_ISREG(metadata.st_mode):
            raise CertificationHashError(f"{path}: input must be a regular file")
    except FileNotFoundError as exc:
        raise CertificationHashError(f"{path}: input does not exist") from exc
    try:
        path_absolute.resolve(strict=True).relative_to(
            owner_absolute.resolve(strict=True)
        )
    except ValueError as exc:
        raise CertificationHashError(f"{path}: input resolves outside its owner") from exc
    return path_absolute


def _resolve_reference_path(owner_root: Path, base: Path, locator: str) -> Path:
    relative = Path(locator)
    if relative.is_absolute():
        raise CertificationHashError(
            f"reference path {locator!r} must be relative to its locator base"
        )
    candidate = Path(os.path.abspath(base / relative))
    owner = Path(os.path.abspath(owner_root))
    try:
        candidate.relative_to(owner)
    except ValueError as exc:
        raise CertificationHashError(
            f"reference path {locator!r} escapes its confinement root"
        ) from exc
    return _validated_owned_input(owner, candidate)


def _parse_reference_document(path: Path, payload: bytes) -> object:
    try:
        text = payload.decode("utf-8")
        if path.suffix == ".json" or path.name.endswith(".schema.json"):
            return json.loads(text)
        return yaml.safe_load(text)
    except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise CertificationHashError(f"{path}: cannot parse referenced document: {exc}") from exc


def _validate_fragment(document: object, fragment: str, path: Path) -> None:
    if fragment in {"", "#"}:
        return
    if not fragment.startswith("#/"):
        raise CertificationHashError(f"{path}: unsupported reference fragment {fragment!r}")
    current = document
    try:
        for raw_part in fragment[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, Mapping):
                current = current[part]
            else:
                raise KeyError(part)
    except (KeyError, IndexError, ValueError) as exc:
        raise CertificationHashError(f"{path}: unresolved reference fragment {fragment!r}") from exc


def _recursive_contract_references(
    owner_root: Path,
    seeds: Iterable[tuple[str, str]],
) -> tuple[_ContractReference, ...]:
    """Resolve the complete confined file closure of authored contract locators."""

    return _recursive_contract_references_from_roots(
        (owner_root, owner_root, path, fragment)
        for path, fragment in seeds
    )


def _recursive_contract_references_from_roots(
    seeds: Iterable[tuple[Path, Path, str, str]],
) -> tuple[_ContractReference, ...]:
    """Resolve contract closures with an explicit confinement and locator base."""

    pending = list(seeds)
    entries: dict[str, _ContractReference] = {}
    parsed_paths: set[Path] = set()
    while pending:
        confined_root, base, locator_path, fragment = pending.pop(0)
        path = _resolve_reference_path(confined_root, base, locator_path)
        relative = path.relative_to(confined_root).as_posix()
        locator = f"{relative}{fragment}"
        payload = path.read_bytes()
        document = _parse_reference_document(path, payload)
        _validate_fragment(document, fragment, path)
        entries[locator] = _ContractReference(locator, path, _hash_bytes(payload))
        if path in parsed_paths:
            continue
        parsed_paths.add(path)
        for child_path, child_fragment in _reference_candidates(document):
            pending.append(
                (confined_root, path.parent, child_path, child_fragment)
            )
        if isinstance(document, (Mapping, list)):
            refs: list[str] = []

            def collect_refs(current: object) -> None:
                if isinstance(current, Mapping):
                    ref = current.get("$ref")
                    if isinstance(ref, str):
                        refs.append(ref)
                    for child in current.values():
                        collect_refs(child)
                elif isinstance(current, list):
                    for child in current:
                        collect_refs(child)

            collect_refs(document)
            for ref in sorted(set(refs)):
                path_text, separator, ref_fragment = ref.partition("#")
                if not path_text:
                    continue
                if "://" in path_text:
                    raise CertificationHashError(
                        f"{path}: external reference URI is unsupported: {ref}"
                    )
                pending.append((
                    confined_root,
                    path.parent,
                    path_text,
                    f"#{ref_fragment}" if separator else "#",
                ))
    return tuple(entries[locator] for locator in sorted(entries))


def normalize_node_checks(
    checks: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Project node checks to stable semantic fields in canonical order."""

    normalized = []
    identities: set[tuple[object, object]] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            raise CertificationHashError("node check must be a mapping")
        try:
            item = {field: deepcopy(check[field]) for field in _STABLE_CHECK_FIELDS}
        except KeyError as exc:
            raise CertificationHashError(
                f"node check is missing stable field {exc.args[0]!r}"
            ) from exc
        if item["passed"] is not True:
            raise CertificationHashError("cannot certify failed node check")
        identity = (item["id"], item["version"])
        try:
            duplicate = identity in identities
        except TypeError as exc:
            raise CertificationHashError("node check identity must be scalar") from exc
        if duplicate:
            raise CertificationHashError(
                f"duplicate node check identity {identity[0]!r} version {identity[1]!r}"
            )
        identities.add(identity)
        normalized.append(item)
    try:
        return tuple(
            sorted(normalized, key=lambda item: (str(item["id"]), int(item["version"])))
        )
    except (TypeError, ValueError) as exc:
        raise CertificationHashError("node check version must be an integer") from exc



def load_node_hash_policy(
    policy_path: Path,
    *,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate the canonical ordered node-input policy."""

    path = Path(policy_path)
    try:
        policy = load_configuration(
            path,
            config_schema_path=(Path(schema_path) if schema_path is not None else None),
        )
    except ConfiguredSchemaError as exc:
        raise CertificationHashError(f"{path}: cannot load node hash policy: {exc}") from exc
    return policy


def _git_exclude_matches(
    repo_root: Path,
    relative_paths: Iterable[str],
    pattern: str,
) -> set[str]:
    """Return candidate files matched by one Git exclude pattern."""

    candidates = tuple(sorted(set(relative_paths)))
    if not candidates:
        return set()
    for relative in candidates:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise CertificationHashError(
                f"Git exclude candidate must be repository-relative: {relative}"
            )
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(repo_root).resolve()),
                "--literal-pathspecs",
                "ls-files",
                "--cached",
                "--others",
                "--ignored",
                "-z",
                f"--exclude={pattern}",
                "--",
                *candidates,
            ],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise CertificationHashError(
            f"cannot apply Git exclude pattern {pattern!r}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CertificationHashError(
            f"cannot apply Git exclude pattern {pattern!r}: {detail}"
        )
    return {
        os.fsdecode(path)
        for path in result.stdout.split(b"\0")
        if path
    }


def _reserved_certification_output(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    name = path.name
    return (
        ".certificates" in path.parts
        or ".certificate-history" in path.parts
        or "pooled-blueprint-review" in name
    )


def _read_node_input(
    node: BlueprintNode,
    path: Path,
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> bytes:
    try:
        relative = repository_relative_path(path, repo_root)
        repository_owner = (
            repo_root / repository_relative_path(node.module_root, repo_root)
        )
        return read_regular_file_bytes(
            repo_root / relative,
            allowed_root=repository_owner,
            allow_non_atomic=allow_non_atomic,
        )
    except (AtomicWriteError, RepositoryPathError, OSError) as exc:
        raise CertificationHashError(str(exc)) from exc


def _v4_node_input_manifests(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    allow_non_atomic: bool = False,
) -> tuple[
    dict[str, tuple[dict[str, str], ...]],
    dict[str, set[str]],
]:
    """Resolve policy-selected inputs and cross-owner contract dependencies."""

    root = Path(repo_root).resolve()
    owned_paths = {
        _repository_path(Path(os.path.abspath(path)), root): owner_id
        for path, owner_id in graph.direct_file_owners.items()
    }
    contract_dependencies: dict[str, set[str]] = {
        node_id: set() for node_id in graph.nodes
    }
    mandatory_contract_paths: dict[str, set[Path]] = {
        node_id: set() for node_id in graph.nodes
    }
    for node_id, node in sorted(graph.nodes.items()):
        references = list(_recursive_contract_references(
            node.module_root, _reference_candidates(node.declaration)
        ))
        structured = node.declaration.get("contract_references", [])
        if not isinstance(structured, list):
            raise CertificationHashError(
                f"{node.blueprint_path}: contract_references must be a list"
            )
        structured_seeds: list[tuple[Path, Path, str, str]] = []
        for index, locator in enumerate(structured):
            if not isinstance(locator, Mapping):
                raise CertificationHashError(
                    f"{node.blueprint_path}: contract_references[{index}] must be a mapping"
                )
            base_name = locator.get("base")
            path = locator.get("path")
            fragment = locator.get("fragment", "#")
            if (
                base_name not in {"module-root", "repository-root"}
                or not isinstance(path, str)
                or not isinstance(fragment, str)
            ):
                raise CertificationHashError(
                    f"{node.blueprint_path}: invalid contract_references[{index}]"
                )
            confined = node.module_root if base_name == "module-root" else root
            structured_seeds.append((confined, confined, path, fragment))
        references.extend(
            _recursive_contract_references_from_roots(structured_seeds)
        )
        for reference in references:
            canonical_reference = _repository_path(
                Path(os.path.abspath(reference.path)),
                root,
            )
            owner_id = owned_paths.get(canonical_reference)
            if owner_id is None:
                raise CertificationHashError(
                    f"{node.blueprint_path}: referenced contract "
                    f"{reference.locator!r} is not directly owned by a node"
                )
            mandatory_contract_paths[owner_id].add(canonical_reference)
            if owner_id != node_id:
                contract_dependencies[node_id].add(owner_id)

    mandatory_paths_by_node: dict[str, set[Path]] = {}
    all_paths = set(owned_paths)
    for node_id, node in sorted(graph.nodes.items()):
        mandatory_paths = {
            _repository_path(
                Path(os.path.abspath(node.blueprint_path)),
                root,
            ),
        }
        if node.gateway_path is not None:
            mandatory_paths.add(
                _repository_path(
                    Path(os.path.abspath(node.gateway_path)),
                    root,
                )
            )
        mandatory_paths.update(mandatory_contract_paths[node_id])
        for mandatory_path in mandatory_paths:
            try:
                repository_relative_path(mandatory_path, root)
            except RepositoryPathError as exc:
                raise CertificationHashError(
                    f"{mandatory_path}: mandatory node input is outside the repository"
                ) from exc
        mandatory_paths_by_node[node_id] = mandatory_paths
        all_paths.update(mandatory_paths)

    relative_paths: dict[Path, str] = {}
    for path in sorted(all_paths):
        try:
            relative_paths[path] = repository_relative_path(
                path,
                root,
            ).as_posix()
        except RepositoryPathError as exc:
            raise CertificationHashError(
                f"{path}: cannot determine Git provenance"
            ) from exc
    try:
        provenance = git_file_provenance_batch(root, all_paths)
    except (ValueError, OSError) as exc:
        raise CertificationHashError(
            f"{root}: cannot determine Git provenance"
        ) from exc

    selected = {
        path: provenance[path] == "tracked"
        for path in owned_paths
    }
    final_actions: dict[Path, str] = {}
    raw_rules = policy.get("rules")
    if not isinstance(raw_rules, list):
        raise CertificationHashError("node hash policy rules must be a list")
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, Mapping):
            raise CertificationHashError(f"node hash policy rule {index} must be a mapping")
        action = rule.get("action")
        pattern = rule.get("pattern")
        if action not in {"include", "exclude"} or not isinstance(pattern, str):
            raise CertificationHashError(f"node hash policy rule {index} is invalid")
        matched_relatives = _git_exclude_matches(
            root,
            relative_paths.values(),
            pattern,
        )
        matches = [
            path for path, relative in relative_paths.items()
            if relative in matched_relatives
        ]
        owned_matches = [
            path for path in matches
            if path in owned_paths
        ]
        if (
            action == "include"
            and rule.get("require_match") is True
            and not owned_matches
        ):
            raise CertificationHashError(
                f"node hash policy include {pattern!r} requires at least one match"
            )
        for path in owned_matches:
            selected[path] = action == "include"
        for path in matches:
            final_actions[path] = action

    manifests: dict[str, tuple[dict[str, str], ...]] = {}
    for node_id, node in sorted(graph.nodes.items()):
        node_paths = {
            path
            for path, owner_id in owned_paths.items()
            if owner_id == node_id and selected[path]
        }
        for mandatory_path in mandatory_paths_by_node[node_id]:
            relative = relative_paths[mandatory_path]
            if final_actions.get(mandatory_path) == "exclude":
                raise CertificationHashError(
                    f"{node_id}: mandatory blueprint, gateway, or contract input "
                    f"cannot be excluded: {relative}"
                )
            node_paths.add(mandatory_path)

        entries: list[dict[str, str]] = []
        for path in sorted(node_paths):
            relative = relative_paths[path]
            if _reserved_certification_output(relative):
                raise CertificationHashError(
                    f"{relative}: reserved certification output cannot be a node input"
                )
            payload = _read_node_input(
                node,
                path,
                root,
                allow_non_atomic=allow_non_atomic,
            )
            entries.append(
                {
                    "path": relative,
                    "digest": _hash_bytes(payload),
                    "git_provenance": provenance[path],
                }
            )
        manifests[node_id] = tuple(entries)
    return manifests, contract_dependencies


def _v6_local_facet_states(
    graph: RepositoryBlueprintGraph,
    manifests: Mapping[str, tuple[dict[str, str], ...]],
    interface_contract_paths: Mapping[str, tuple[str, ...]],
    repo_root: Path,
) -> tuple[
    dict[str, tuple[CertificationFacetHashState, ...]],
    dict[str, str],
    dict[str, str],
]:
    """Derive v6 source-local interface and remainder facet identities.

    Dependency hashes are intentionally absent from this pass. The caller uses
    the resulting local interface hashes to build dependency records without
    recursively folding dependency content into local identity.
    """

    root = Path(repo_root).resolve()
    facets_by_node: dict[str, tuple[CertificationFacetHashState, ...]] = {}
    interface_hashes: dict[str, str] = {}
    node_hashes: dict[str, str] = {}
    for node_id, node in sorted(graph.nodes.items()):
        if node.node_type != "behavioral_source":
            continue
        entries_by_path = {
            str(entry["path"]): dict(entry) for entry in manifests[node_id]
        }
        interface_facets: list[CertificationFacetHashState] = []
        claimed_paths: set[str] = set()
        raw_interfaces = node.declaration.get("interfaces")
        if not isinstance(raw_interfaces, Mapping):
            raise CertificationHashError(
                f"{node.blueprint_path}: interfaces must be a mapping"
            )
        for interface_id in sorted(raw_interfaces):
            resolved_paths = graph.interface_content_paths.get(interface_id)
            if resolved_paths is None:
                raise CertificationHashError(
                    f"{interface_id}: canonical interface content is unavailable"
                )
            relative_paths = {
                repository_relative_path(path, root).as_posix()
                for path in resolved_paths
            }
            relative_paths.update(interface_contract_paths.get(interface_id, ()))
            claimed_paths.update(relative_paths)
            interface_manifest = tuple(
                entries_by_path[path]
                for path in sorted(relative_paths)
                if path in entries_by_path
            )
            resolved = graph.source_interfaces.get(interface_id)
            if resolved is None:
                raise CertificationHashError(
                    f"{interface_id}: canonical source interface is unavailable"
                )
            extracted = extract_interface_from_blueprint(
                graph,
                interface_id,
                resolved.version,
            )
            local_hash = compute_interface_hash(
                extracted,
                input_manifest=interface_manifest,
            )
            interface_hashes[interface_id] = local_hash
            interface_facets.append(
                CertificationFacetHashState(
                    facet_id=interface_id,
                    facet_type="interface",
                    local_hash=local_hash,
                    input_manifest=interface_manifest,
                )
            )

        blueprint_relative = repository_relative_path(
            node.blueprint_path,
            root,
        ).as_posix()
        remainder_manifest = tuple(
            entry
            for path, entry in sorted(entries_by_path.items())
            if path not in claimed_paths and path != blueprint_relative
        )
        core_declaration = deepcopy(node.declaration)
        core_declaration.pop("interfaces", None)
        core_declaration.pop("dependencies", None)
        core_declaration.pop("uses_interfaces", None)
        remainder_hash = _hash_value(
            {
                "node_id": node_id,
                "node_type": node.node_type,
                "version": node.version,
                "declaration": core_declaration,
                "input_manifest": remainder_manifest,
            }
        )
        remainder = CertificationFacetHashState(
            facet_id=node_id,
            facet_type="remainder",
            local_hash=remainder_hash,
            input_manifest=remainder_manifest,
        )
        facets = (remainder, *interface_facets)
        facets_by_node[node_id] = facets
        node_hashes[node_id] = _hash_value(
            {
                "node_id": node_id,
                "node_type": node.node_type,
                "version": node.version,
                "remainder_hash": remainder_hash,
                "interfaces": [
                    {
                        "id": facet.facet_id,
                        "version": graph.source_interfaces[facet.facet_id].version,
                        "interface_hash": facet.local_hash,
                    }
                    for facet in interface_facets
                ],
            }
        )

    for interface_id, resolved in sorted(graph.exports.items()):
        source_interface_id = resolved.source_interface_id
        source_node_id = resolved.source_node_id
        if (
            not isinstance(source_interface_id, str)
            or not isinstance(source_node_id, str)
        ):
            raise CertificationHashError(
                f"{interface_id}: export source interface is unavailable"
            )
        source_facet = next(
            (
                facet
                for facet in facets_by_node.get(source_node_id, ())
                if facet.facet_id == source_interface_id
            ),
            None,
        )
        if source_facet is None:
            raise CertificationHashError(
                f"{interface_id}: source interface facet is unavailable"
            )
        extracted = extract_interface_from_blueprint(
            graph,
            interface_id,
            resolved.version,
        )
        interface_hashes[interface_id] = compute_interface_hash(
            extracted,
            input_manifest=source_facet.input_manifest,
        )
    return facets_by_node, interface_hashes, node_hashes


def _v6_interface_contract_attribution(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Retain the originating interface for resolved contract references."""

    root = Path(repo_root).resolve()
    owners = {
        _repository_path(Path(os.path.abspath(path)), root): owner_id
        for path, owner_id in graph.direct_file_owners.items()
    }
    paths_by_interface: dict[str, tuple[str, ...]] = {}
    dependencies_by_interface: dict[str, tuple[str, ...]] = {}
    for node_id, node in sorted(graph.nodes.items()):
        if node.node_type != "behavioral_source":
            continue
        raw_interfaces = node.declaration.get("interfaces")
        if not isinstance(raw_interfaces, Mapping):
            raise CertificationHashError(
                f"{node.blueprint_path}: interfaces must be a mapping"
            )
        for interface_id, declaration in sorted(raw_interfaces.items()):
            if not isinstance(interface_id, str) or not isinstance(
                declaration,
                Mapping,
            ):
                raise CertificationHashError(
                    f"{node.blueprint_path}: invalid interface declaration"
                )
            local_paths: set[str] = set()
            dependency_ids: set[str] = set()
            for reference in _recursive_contract_references(
                node.module_root,
                _reference_candidates(declaration),
            ):
                path = _repository_path(
                    Path(os.path.abspath(reference.path)),
                    root,
                )
                owner_id = owners.get(path)
                if owner_id is None:
                    raise CertificationHashError(
                        f"{node.blueprint_path}: referenced contract "
                        f"{reference.locator!r} is not directly owned by a node"
                    )
                if owner_id == node_id:
                    local_paths.add(repository_relative_path(path, root).as_posix())
                else:
                    dependency_ids.add(owner_id)
            paths_by_interface[interface_id] = tuple(sorted(local_paths))
            dependencies_by_interface[interface_id] = tuple(
                sorted(dependency_ids)
            )
    return paths_by_interface, dependencies_by_interface


def _compute_node_hash_states(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    policy_path: Path,
    certification_basis_hash: str,
    certification_basis_paths: Iterable[Path | str],
    allow_non_atomic: bool = False,
) -> dict[str, NodeHashState]:
    policy = load_node_hash_policy(policy_path)
    manifests, contract_dependencies = _v4_node_input_manifests(
        graph,
        repo_root,
        policy,
        allow_non_atomic=allow_non_atomic,
    )
    node_hashes = {
        node_id: _hash_value(
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "version": node.version,
                "input_manifest": manifests[node_id],
            }
        )
        for node_id, node in sorted(graph.nodes.items())
    }
    local_facets_by_node: dict[
        str,
        tuple[CertificationFacetHashState, ...],
    ] = {}
    interface_hashes: dict[str, str] = {}
    interface_contract_dependencies: dict[str, tuple[str, ...]] = {}
    if graph.schema_version == 6:
        (
            interface_contract_paths,
            interface_contract_dependencies,
        ) = _v6_interface_contract_attribution(graph, repo_root)
        (
            local_facets_by_node,
            interface_hashes,
            source_node_hashes,
        ) = _v6_local_facet_states(
            graph,
            manifests,
            interface_contract_paths,
            repo_root,
        )
        node_hashes.update(source_node_hashes)
    dependencies_by_node: dict[str, set[tuple[str, str, int | None]]] = {
        node_id: set() for node_id in graph.nodes
    }
    for edge in graph.certification_edges:
        if graph.schema_version == 6 and edge.relation in {
            "uses-private-interface",
            "uses-export",
        }:
            continue
        dependencies_by_node[edge.source_node_id].add(
            (edge.relation, edge.target_node_id, edge.target_version)
        )
    for source_id, target_ids in contract_dependencies.items():
        for target_id in target_ids:
            dependencies_by_node[source_id].add(
                (
                    "references-cross-owner-contract",
                    target_id,
                    graph.nodes[target_id].version,
                )
            )

    interface_dependencies_by_node: dict[
        str,
        set[tuple[str, str, str, int, str]],
    ] = {node_id: set() for node_id in graph.nodes}
    if graph.schema_version == 6:
        for edge in graph.node_edges:
            if edge.relation not in {
                "uses-private-interface",
                "uses-export",
            }:
                continue
            extracted = extract_interface_from_blueprint(
                graph,
                edge.target_id,
                edge.required_version,
            )
            target_id = extracted["source_node"]
            if not isinstance(target_id, str) or target_id not in graph.nodes:
                raise CertificationHashError(
                    f"{edge.source_id}: interface target is unavailable: "
                    f"{edge.target_id}"
                )
            interface_dependencies_by_node[edge.source_id].add(
                (
                    edge.relation,
                    target_id,
                    edge.target_id,
                    edge.required_version,
                    interface_hashes[edge.target_id],
                )
            )

        if CERTIFIER_NODE_ID in graph.nodes:
            versions = {V6_CERTIFIER_INTERFACE_ID: CERTIFIER_INTERFACE_VERSION}
            versions.update(
                (interface_id, CERTIFIER_AUDIT_INTERFACE_VERSION)
                for interface_id in CERTIFIER_AUDIT_INTERFACES.values()
            )
            try:
                certified_under = {
                    interface_id: (
                        "certified-under",
                        str(extract_interface_from_blueprint(
                            graph, interface_id, version
                        )["source_node"]),
                        interface_id,
                        version,
                        interface_hashes[interface_id],
                    )
                    for interface_id, version in versions.items()
                }
            except (CertificationHashError, KeyError) as exc:
                raise CertificationHashError(
                    "canonical certifier interfaces are incomplete"
                ) from exc
            for node_id, node in graph.nodes.items():
                facet_type = "module" if node.node_type == "module" else "remainder"
                selected = {
                    certified_under[V6_CERTIFIER_INTERFACE_ID],
                    certified_under[CERTIFIER_AUDIT_INTERFACES[facet_type]],
                }
                if facet_type == "remainder" and node.declaration.get("interfaces"):
                    selected.add(certified_under[CERTIFIER_AUDIT_INTERFACES["interface"]])
                interface_dependencies_by_node[node_id].update(selected)

    visiting: list[str] = []
    visited: set[str] = set()

    def reject_dependency_cycle(node_id: str) -> None:
        if node_id in visiting:
            start = visiting.index(node_id)
            cycle = visiting[start:] + [node_id]
            raise CertificationHashError(
                "certification dependency cycle includes " + " -> ".join(cycle)
            )
        if node_id in visited:
            return
        visiting.append(node_id)
        dependency_targets = {
            (relation, target_id, version)
            for relation, target_id, version in dependencies_by_node[node_id]
        }
        dependency_targets.update(
            (relation, target_id, version)
            for relation, target_id, _interface_id, version, _interface_hash
            in interface_dependencies_by_node[node_id]
            if relation not in EVIDENCE_ONLY_RELATIONS
        )
        for _relation, target_id, _version in sorted(
            dependency_targets,
            key=lambda item: (item[0], item[1], item[2] or 0),
        ):
            reject_dependency_cycle(target_id)
        visiting.pop()
        visited.add(node_id)

    for node_id in sorted(graph.nodes):
        reject_dependency_cycle(node_id)

    states: dict[str, NodeHashState] = {}
    for node_id in sorted(graph.nodes):
        node_dependency_hashes = [
            {
                "relation": relation,
                "target": target_id,
                "version": version,
                "node_hash": node_hashes[target_id],
            }
            for relation, target_id, version in sorted(
                dependencies_by_node[node_id],
                key=lambda item: (item[0], item[1], item[2] or 0),
            )
        ]
        interface_dependency_hashes = [
            {
                "relation": relation,
                "target": target_id,
                "interface": interface_id,
                "version": version,
                "interface_hash": interface_hash,
            }
            for relation, target_id, interface_id, version, interface_hash in sorted(
                interface_dependencies_by_node[node_id],
                key=lambda item: (item[0], item[1], item[2], item[3]),
            )
        ]
        dependency_hashes = tuple(
            sorted(
                (*node_dependency_hashes, *interface_dependency_hashes),
                key=lambda item: (
                    str(item["relation"]),
                    str(item["target"]),
                    str(item.get("interface", "")),
                    int(item["version"]),
                ),
            )
        )
        facets: tuple[CertificationFacetHashState, ...] = ()
        input_manifest = manifests[node_id]
        if node_id in local_facets_by_node:
            local_facets = local_facets_by_node[node_id]
            claimed_interface_uses = {
                use
                for facet in local_facets
                if facet.facet_type == "interface"
                for use in graph.interface_uses.get(facet.facet_id, ())
            }
            claimed_contract_dependencies = {
                target_id
                for facet in local_facets
                if facet.facet_type == "interface"
                for target_id in interface_contract_dependencies.get(
                    facet.facet_id,
                    (),
                )
            }
            populated: list[CertificationFacetHashState] = []
            for facet in local_facets:
                certifier_interfaces = {
                    V6_CERTIFIER_INTERFACE_ID,
                    CERTIFIER_AUDIT_INTERFACES[facet.facet_type],
                }
                if facet.facet_type == "interface":
                    declared_uses = set(
                        graph.interface_uses.get(facet.facet_id, ())
                    )
                    facet_dependencies = tuple(
                        sorted(
                            (
                                *(
                                    dependency
                                    for dependency in interface_dependency_hashes
                                    if (
                                        dependency["relation"] == "certified-under"
                                        and dependency["interface"]
                                        in certifier_interfaces
                                    )
                                    or (
                                        dependency["interface"],
                                        dependency["version"],
                                    ) in declared_uses
                                ),
                                *(
                                    dependency
                                    for dependency in node_dependency_hashes
                                    if dependency["relation"]
                                    == "references-cross-owner-contract"
                                    and dependency["target"]
                                    in interface_contract_dependencies.get(
                                        facet.facet_id,
                                        (),
                                    )
                                ),
                            ),
                            key=lambda item: (
                                str(item["relation"]),
                                str(item["target"]),
                                str(item.get("interface", "")),
                                int(item["version"]),
                            ),
                        )
                    )
                else:
                    facet_dependencies = tuple(
                        dependency
                        for dependency in dependency_hashes
                        if (
                            dependency["relation"] != "certified-under"
                            or dependency["interface"] in certifier_interfaces
                        )
                        if not (
                            "interface" in dependency
                            and (
                                dependency["interface"],
                                dependency["version"],
                            )
                            in claimed_interface_uses
                        )
                        and not (
                            dependency["relation"]
                            == "references-cross-owner-contract"
                            and dependency["target"]
                            in claimed_contract_dependencies
                        )
                    )
                populated.append(
                    CertificationFacetHashState(
                        facet_id=facet.facet_id,
                        facet_type=facet.facet_type,
                        local_hash=facet.local_hash,
                        input_manifest=facet.input_manifest,
                        dependency_hashes=facet_dependencies,
                    )
                )
            facets = tuple(populated)
            input_manifest = tuple(
                entry
                for _path, entry in sorted(
                    {
                        str(entry["path"]): dict(entry)
                        for facet in facets
                        for entry in facet.input_manifest
                    }.items()
                )
            )
        states[node_id] = NodeHashState(
            node_hash=node_hashes[node_id],
            input_manifest=input_manifest,
            dependency_hashes=dependency_hashes,
            certification_basis_hash=certification_basis_hash,
            facets=facets,
        )
    return states


def compute_node_hash_states(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    policy_path: Path,
    certification_basis_hash: str,
    certification_basis_paths: Iterable[Path | str] = (),
    allow_non_atomic: bool = False,
) -> dict[str, NodeHashState]:
    """Compute canonical local hashes and static graph dependency claims."""

    if not isinstance(graph, RepositoryBlueprintGraph):
        raise CertificationHashError(
            "node hashing requires a repository blueprint graph"
        )
    schema_versions = {
        node.declaration.get("schema_version")
        for node in graph.nodes.values()
    }
    if schema_versions != {graph.schema_version} or graph.schema_version not in {
        4,
        5,
        6,
    }:
        raise CertificationHashError(
            "node hashing requires one closed all-v4, all-v5, or all-v6 repository graph"
        )
    return _compute_node_hash_states(
        graph,
        repo_root=repo_root,
        policy_path=policy_path,
        certification_basis_hash=certification_basis_hash,
        certification_basis_paths=certification_basis_paths,
        allow_non_atomic=allow_non_atomic,
    )

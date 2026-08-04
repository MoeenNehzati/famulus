"""Persistent, data-only cache for validated dispatcher route state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintDiagnostic,
    BlueprintNode,
    CertificationEdge,
    DispatchBlueprintGraph,
    ExportDependencyEdge,
    HelperEdge,
    InterfaceExport,
    NamespaceRoute,
    RepositoryBlueprintGraph,
    RoutedInterface,
)
from officina.common.blueprint_authorization import AuthorizationResult
from officina.common.certification_types import CertificationDecision


_KIND = "__officina_catalog_kind__"
_FORMAT_VERSION = 5
_DATACLASS_TYPES = {
    cls.__name__: cls
    for cls in (
        BlueprintEdge,
        BlueprintNode,
        CertificationEdge,
        ExportDependencyEdge,
        HelperEdge,
        InterfaceExport,
        NamespaceRoute,
        RepositoryBlueprintGraph,
        RoutedInterface,
        BlueprintDiagnostic,
        DispatchBlueprintGraph,
    )
}


@dataclass(frozen=True)
class CatalogRoute:
    """Identity of one reusable closure-scoped dispatch graph."""

    caller_module_id: str
    target_interface_id: str


@dataclass(frozen=True)
class CatalogLookup:
    """Result of classifying one route-catalog lookup."""

    status: str
    graph: DispatchBlueprintGraph | None = None


def lookup_route_graph(
    repo_root: Path,
    route: CatalogRoute,
    *,
    cache_root: Path | None = None,
) -> CatalogLookup:
    """Classify and load one route-catalog entry."""

    root = Path(repo_root).resolve()
    selected_root, path = _catalog_path(root, route, cache_root=cache_root)
    if not path.exists():
        return CatalogLookup("missing")
    try:
        raw = read_regular_file_bytes(
            path,
            allowed_root=selected_root,
            allow_non_atomic=True,
        )
    except (AtomicWriteError, OSError):
        return CatalogLookup("unavailable")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return CatalogLookup("malformed")
    if not isinstance(payload, dict):
        return CatalogLookup("malformed")
    if payload.get("format_version") != _FORMAT_VERSION:
        return CatalogLookup("stale")
    if (
        payload.get("repo_root") != root.as_posix()
        or payload.get("caller_module_id") != route.caller_module_id
        or payload.get("target_interface_id") != route.target_interface_id
        or not isinstance(payload.get("graph"), dict)
    ):
        return CatalogLookup("malformed")
    fingerprint_status = _fingerprints_status(root, payload.get("inputs"))
    if fingerprint_status != "hit":
        return CatalogLookup(fingerprint_status)
    try:
        decoded = _decode_value(payload["graph"], repo_root=root)
    except (TypeError, ValueError, OSError):
        return CatalogLookup("malformed")
    if not isinstance(decoded, DispatchBlueprintGraph):
        return CatalogLookup("malformed")
    return CatalogLookup("hit", decoded)


def compact_route_graph(
    graph: RepositoryBlueprintGraph,
    authorization: AuthorizationResult,
) -> RepositoryBlueprintGraph:
    """Keep only graph state required to replay one authorized route."""

    module_ids = {
        *authorization.caller_ancestry,
        *authorization.target_ancestry,
        *authorization.terminal_ancestry,
        *(item.module_id for item in authorization.resolved_callers),
        *(item.owner_module_id for item in authorization.resolved_callers),
        *(item.route_owner_id for item in authorization.crossed_namespace_gates),
        *(item.child_module_id for item in authorization.crossed_namespace_gates),
    }
    for module_id in tuple(module_ids):
        module_ids.update(graph.module_ancestry.get(module_id, ()))

    requested = graph.exports[authorization.requested_interface_id]
    terminal_id = authorization.terminal_interface_id or requested.interface_id
    terminal = graph.exports[terminal_id]
    selected_exports = {requested.interface_id, terminal.interface_id}
    source_ids = {
        source_id
        for export_id in selected_exports
        if (source_id := graph.exports[export_id].source_node_id) is not None
    }
    caller_id = authorization.caller_module_id
    source_ids.update(graph.module_sources.get(caller_id, ()))
    selected_node_ids = module_ids | source_ids

    namespace_routes = {
        key: route
        for key, route in graph.namespace_routes.items()
        if key
        in {
            (gate.route_owner_id, gate.child_module_id)
            for gate in authorization.crossed_namespace_gates
        }
    }
    routed_interfaces = tuple(
        item
        for route in namespace_routes.values()
        for item in route.materialized_interfaces
    )
    return RepositoryBlueprintGraph(
        nodes={
            node_id: graph.nodes[node_id]
            for node_id in sorted(selected_node_ids)
        },
        node_edges=tuple(
            edge
            for edge in graph.node_edges
            if edge.source_id in selected_node_ids
            and edge.target_id in selected_node_ids
        ),
        exports={
            export_id: graph.exports[export_id]
            for export_id in sorted(selected_exports)
        },
        export_edges=tuple(
            edge
            for edge in graph.export_edges
            if edge.source_export_id in selected_exports
            and edge.target_interface_id in selected_exports
        ),
        helper_edges=tuple(
            edge
            for edge in graph.helper_edges
            if edge.source_export_id in selected_exports
            and edge.target_interface_id in selected_exports
        ),
        certification_edges=tuple(
            edge
            for edge in graph.certification_edges
            if edge.source_node_id in selected_node_ids
            and edge.target_node_id in selected_node_ids
        ),
        module_sources={
            module_id: tuple(
                source_id
                for source_id in graph.module_sources.get(module_id, ())
                if source_id in selected_node_ids
            )
            for module_id in sorted(module_ids)
        },
        direct_file_owners={
            path: owner
            for path, owner in graph.direct_file_owners.items()
            if owner in selected_node_ids
        },
        schema_version=graph.schema_version,
        source_modules={
            source_id: module_id
            for source_id, module_id in graph.source_modules.items()
            if source_id in selected_node_ids and module_id in module_ids
        },
        source_interfaces={
            interface_id: export
            for interface_id, export in graph.source_interfaces.items()
            if interface_id in selected_exports
        },
        module_parents={
            module_id: graph.module_parents[module_id]
            for module_id in sorted(module_ids)
        },
        module_children={
            module_id: tuple(
                child
                for child in graph.module_children.get(module_id, ())
                if child in module_ids
            )
            for module_id in sorted(module_ids)
        },
        module_local_segments={
            module_id: graph.module_local_segments[module_id]
            for module_id in sorted(module_ids)
            if module_id in graph.module_local_segments
        },
        module_ancestry={
            module_id: graph.module_ancestry[module_id]
            for module_id in sorted(module_ids)
        },
        namespace_routes=namespace_routes,
        routed_interfaces=routed_interfaces,
    )


def _selected_cache_root(cache_root: Path | None) -> Path:
    if cache_root is not None:
        return Path(cache_root).resolve()
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return (base / "famulus" / "dispatcher").resolve()


def _catalog_path(
    repo_root: Path,
    route: CatalogRoute,
    *,
    cache_root: Path | None,
) -> tuple[Path, Path]:
    selected_root = _selected_cache_root(cache_root)
    repo_key = hashlib.sha256(repo_root.as_posix().encode("utf-8")).hexdigest()[:24]
    route_value = json.dumps(
        [route.caller_module_id, route.target_interface_id],
        separators=(",", ":"),
    )
    route_key = hashlib.sha256(route_value.encode("utf-8")).hexdigest()
    return selected_root, selected_root / repo_key / f"{route_key}.json"


def _graph_input_paths(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> tuple[Path, ...]:
    selected = {node.blueprint_path.resolve() for node in graph.nodes.values()}
    schema_root = repo_root / "references" / "blueprint"
    if schema_root.is_dir():
        selected.update(path.resolve() for path in schema_root.rglob("*") if path.is_file())
    configured_schema = repo_root / "src" / "officina" / "common" / "configuration.schema.json"
    if configured_schema.is_file():
        selected.add(configured_schema.resolve())
    return tuple(sorted(selected, key=lambda path: path.as_posix()))


def _fingerprint(path: Path, repo_root: Path) -> dict[str, int | str]:
    resolved = path.resolve()
    if resolved != repo_root and not resolved.is_relative_to(repo_root):
        raise ValueError(f"catalog input escapes repository: {path}")
    status = resolved.stat()
    if resolved.is_file():
        kind = "file"
    elif resolved.is_dir():
        kind = "directory"
    else:
        raise ValueError(f"catalog input is neither a file nor directory: {path}")
    return {
        "path": resolved.relative_to(repo_root).as_posix(),
        "kind": kind,
        "size": status.st_size,
        "mtime_ns": status.st_mtime_ns,
        "ctime_ns": status.st_ctime_ns,
    }


def _fingerprints_status(repo_root: Path, payload: Any) -> str:
    if not isinstance(payload, list):
        return "malformed"
    for item in payload:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "kind",
            "size",
            "mtime_ns",
            "ctime_ns",
        }:
            return "malformed"
        relative = item["path"]
        kind = item["kind"]
        if not isinstance(relative, str) or kind not in {"file", "directory"}:
            return "malformed"
        path = (repo_root / relative).resolve()
        if path != repo_root and not path.is_relative_to(repo_root):
            return "malformed"
        try:
            status = path.stat()
        except OSError:
            return "stale"
        if (
            (kind == "file" and not path.is_file())
            or (kind == "directory" and not path.is_dir())
            or status.st_size != item["size"]
            or status.st_mtime_ns != item["mtime_ns"]
            or status.st_ctime_ns != item["ctime_ns"]
        ):
            return "stale"
    return "hit"


def _fingerprints_current(repo_root: Path, payload: Any) -> bool:
    return _fingerprints_status(repo_root, payload) == "hit"


def _certification_input_paths(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
) -> tuple[Path, ...]:
    from officina.common.certification_hashing import (
        CertificationHashError,
        resolve_certification_basis_paths,
    )
    from officina.common.certification_view import certificate_log_path

    selected = set(_graph_input_paths(repo_root, graph))
    selected.update(path.resolve() for path in graph.direct_file_owners)
    module_roots = {node.module_root.resolve() for node in graph.nodes.values()}
    selected.update(module_roots)
    for module_root in module_roots:
        if module_root.is_dir():
            selected.update(
                path.resolve()
                for path in module_root.rglob("*")
                if path.is_dir()
            )
    selected.update(
        path.resolve()
        for node in graph.nodes.values()
        if (path := certificate_log_path(node)).is_file()
    )
    certification_root = repo_root / "references" / "certification"
    if certification_root.is_dir():
        selected.update(
            path.resolve()
            for path in certification_root.rglob("*")
            if path.is_file()
        )
    try:
        selected.update(
            path.resolve()
            for path in resolve_certification_basis_paths(
                repo_root,
                expected_schema_version=graph.schema_version,
                allow_non_atomic=True,
            )
        )
    except (CertificationHashError, OSError, TypeError, ValueError):
        # An unavailable basis produces an uncertified decision, whose cache
        # lifetime is bounded independently of these fingerprints.
        pass
    return tuple(sorted(selected, key=lambda path: path.as_posix()))


def _read_catalog_payload(
    repo_root: Path,
    route: CatalogRoute,
    *,
    cache_root: Path | None,
) -> tuple[Path, Path, dict[str, Any]] | None:
    selected_root, path = _catalog_path(repo_root, route, cache_root=cache_root)
    try:
        payload = json.loads(
            read_regular_file_bytes(
                path,
                allowed_root=selected_root,
                allow_non_atomic=True,
            ).decode("utf-8")
        )
    except (
        AtomicWriteError,
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
    return (
        (selected_root, path, payload)
        if isinstance(payload, dict)
        else None
    )


def _write_catalog_payload(
    selected_root: Path,
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_replace_bytes(
        path,
        (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
        allowed_root=selected_root,
        mode=0o600,
        allow_non_atomic=True,
    )


def load_route_graph(
    repo_root: Path,
    route: CatalogRoute,
    *,
    cache_root: Path | None = None,
) -> DispatchBlueprintGraph | None:
    """Load one fresh route graph, or return ``None`` on any cache miss."""

    return lookup_route_graph(
        repo_root,
        route,
        cache_root=cache_root,
    ).graph


def store_route_graph(
    repo_root: Path,
    route: CatalogRoute,
    scoped_graph: DispatchBlueprintGraph,
    *,
    cache_root: Path | None = None,
) -> None:
    """Atomically store one validated route graph and its input fingerprints."""

    root = Path(repo_root).resolve()
    selected_root, path = _catalog_path(root, route, cache_root=cache_root)
    inputs = [
        _fingerprint(item, root)
        for item in _graph_input_paths(root, scoped_graph.graph)
    ]
    payload = {
        "format_version": _FORMAT_VERSION,
        "repo_root": root.as_posix(),
        "caller_module_id": route.caller_module_id,
        "target_interface_id": route.target_interface_id,
        "inputs": inputs,
        "graph": _encode_value(scoped_graph),
    }
    _write_catalog_payload(selected_root, path, payload)


def load_route_certification_decision(
    repo_root: Path,
    route: CatalogRoute,
    *,
    cache_root: Path | None = None,
) -> CertificationDecision | None:
    """Load a fresh certification decision bound to its runtime inputs."""

    root = Path(repo_root).resolve()
    if load_route_graph(root, route, cache_root=cache_root) is None:
        return None
    loaded = _read_catalog_payload(root, route, cache_root=cache_root)
    if loaded is None:
        return None
    _selected_root, _path, payload = loaded
    raw = payload.get("certification")
    if not isinstance(raw, dict) or set(raw) != {
        "certified",
        "code",
        "message",
        "generated_ns",
        "inputs",
    }:
        return None
    if not _fingerprints_current(root, raw["inputs"]):
        return None
    certified = raw["certified"]
    generated_ns = raw["generated_ns"]
    if (
        not isinstance(certified, bool)
        or not isinstance(raw["code"], str)
        or not isinstance(raw["message"], str)
        or not isinstance(generated_ns, int)
    ):
        return None
    try:
        return CertificationDecision(certified, raw["code"], raw["message"])
    except ValueError:
        return None


def store_route_certification_decision(
    repo_root: Path,
    route: CatalogRoute,
    graph: RepositoryBlueprintGraph,
    decision: CertificationDecision,
    *,
    cache_root: Path | None = None,
) -> bool:
    """Attach a fingerprint-bound certification decision to a route entry."""

    root = Path(repo_root).resolve()
    if load_route_graph(root, route, cache_root=cache_root) is None:
        return False
    loaded = _read_catalog_payload(root, route, cache_root=cache_root)
    if loaded is None:
        return False
    selected_root, path, payload = loaded
    payload["certification"] = {
        "certified": decision.certified,
        "code": decision.code,
        "message": decision.message,
        "generated_ns": time.time_ns(),
        "inputs": [
            _fingerprint(item, root)
            for item in _certification_input_paths(root, graph)
        ],
    }
    _write_catalog_payload(selected_root, path, payload)
    return True


def _encode_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return {_KIND: "path", "value": value.as_posix()}
    if is_dataclass(value) and not isinstance(value, type):
        type_name = type(value).__name__
        if type_name not in _DATACLASS_TYPES:
            raise TypeError(f"unsupported catalog dataclass: {type_name}")
        return {
            _KIND: "dataclass",
            "type": type_name,
            "fields": {
                item.name: _encode_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    if isinstance(value, Mapping):
        entries = [
            [_encode_value(key), _encode_value(item)]
            for key, item in value.items()
        ]
        entries.sort(
            key=lambda pair: json.dumps(
                pair[0], sort_keys=True, separators=(",", ":")
            )
        )
        return {_KIND: "mapping", "entries": entries}
    if isinstance(value, tuple):
        return {_KIND: "tuple", "items": [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return {_KIND: "list", "items": [_encode_value(item) for item in value]}
    raise TypeError(f"unsupported catalog value: {type(value).__name__}")


def _decode_value(value: Any, *, repo_root: Path) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if not isinstance(value, dict):
        raise ValueError("catalog values must be tagged objects or JSON scalars")
    kind = value.get(_KIND)
    if kind == "path":
        raw = value.get("value")
        if not isinstance(raw, str):
            raise ValueError("catalog path must be a string")
        original = Path(raw)
        path = original if original.is_absolute() else repo_root / original
        resolved = path.resolve()
        if resolved != repo_root and not resolved.is_relative_to(repo_root):
            raise ValueError(f"catalog path escapes repository: {raw}")
        return resolved if original.is_absolute() else original
    if kind == "tuple" or kind == "list":
        items = value.get("items")
        if not isinstance(items, list):
            raise ValueError(f"catalog {kind} items must be a list")
        decoded = [_decode_value(item, repo_root=repo_root) for item in items]
        return tuple(decoded) if kind == "tuple" else decoded
    if kind == "mapping":
        entries = value.get("entries")
        if not isinstance(entries, list):
            raise ValueError("catalog mapping entries must be a list")
        result: dict[Any, Any] = {}
        for pair in entries:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("catalog mapping entry must contain key and value")
            key = _decode_value(pair[0], repo_root=repo_root)
            if key in result:
                raise ValueError("catalog mapping contains a duplicate key")
            result[key] = _decode_value(pair[1], repo_root=repo_root)
        return result
    if kind == "dataclass":
        type_name = value.get("type")
        raw_fields = value.get("fields")
        cls = _DATACLASS_TYPES.get(type_name)
        if cls is None or not isinstance(raw_fields, dict):
            raise ValueError(f"unsupported catalog dataclass: {type_name!r}")
        expected = {item.name for item in fields(cls)}
        if set(raw_fields) != expected:
            raise ValueError(f"catalog fields do not match {type_name}")
        return cls(
            **{
                name: _decode_value(item, repo_root=repo_root)
                for name, item in raw_fields.items()
            }
        )
    raise ValueError(f"unsupported catalog value kind: {kind!r}")


def encode_repository_graph(graph: RepositoryBlueprintGraph) -> dict[str, Any]:
    """Encode one repository graph as JSON-compatible, non-executable data."""

    encoded = _encode_value(graph)
    if not isinstance(encoded, dict):
        raise TypeError("repository graph encoder produced a non-object")
    return encoded


def decode_repository_graph(
    payload: dict[str, Any],
    *,
    repo_root: Path,
) -> RepositoryBlueprintGraph:
    """Decode one allow-listed repository graph rooted at ``repo_root``."""

    root = Path(repo_root).resolve()
    decoded = _decode_value(payload, repo_root=root)
    if not isinstance(decoded, RepositoryBlueprintGraph):
        raise ValueError("catalog payload is not a repository graph")
    return decoded

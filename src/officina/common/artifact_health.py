"""Recursive health certification for typed blueprint graphs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable, Mapping, Sequence

import jsonschema

from .audit_records import attach_record_authentication, record_authentication_matches
from .blueprint_graph import BlueprintEdge, BlueprintNode, SkillBlueprintGraph
from .blueprint_template import load_schema, schema_validator


class ArtifactHealthError(ValueError):
    """Raised when a graph cannot be certified deterministically."""


@dataclass(frozen=True)
class NodeHealthStatus:
    node_id: str
    healthy: bool
    concerns: tuple[str, ...]
    expected_certified_health_hash: str
    recorded_certified_health_hash: str | None
    admitted_record_hash: str | None = None


@dataclass(frozen=True)
class GraphHealthReport:
    root_id: str
    healthy: bool
    nodes: dict[str, NodeHealthStatus]


@dataclass(frozen=True)
class NodeHashState:
    blueprint_file_hash: str
    blueprint_contract_hash: str
    bound_file_hash: str | None
    local_hash: str
    downstream_artifact_hash: str
    artifact_graph_hash: str
    downstream_health_hash: str
    certified_health_hash: str
    dependencies: tuple[dict[str, Any], ...]
    schema_hash: str
    policy_hash: str


_SCHEMA_BY_NODE_TYPE = {
    "skill": "skill.schema.json",
    "llm-interface": "llm-interface.schema.json",
    "machine-interface": "machine-interface.schema.json",
    "behavior-source": "behavior-source.schema.json",
}
_DEFAULT_CERTIFIER = {
    "interface": "skill-audit.machine.certify",
    "version": 1,
}
_STABLE_CHECK_FIELDS = ("id", "version", "passed", "findings")
_REFRESH_CONCERNS = {
    "missing-health-record",
    "authentication-failed",
    "invalid-health-record",
    "artifact-stale",
    "dependency-stale",
    "schema-stale",
    "policy-stale",
    "checks-stale",
    "blueprint-file-changed",
}
CANONICAL_GRAPH_SCHEMA_INPUTS = (
    "schema.json",
    "schema-meta.json",
    "common.schema.json",
    "legacy-skill.schema.json",
    "skill.schema.json",
    "llm-interface.schema.json",
    "machine-interface.schema.json",
    "behavior-source.schema.json",
    "health.schema.json",
    "schema.annotated-draft.json",
    "template.yaml",
)
POOLED_REVIEW_SCHEMA_INPUTS = ("pooled-review.schema.json",)


def _default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "references" / "blueprint"


def blueprint_schema_hash(schema_root: Path | None = None) -> str:
    """Hash the complete authoritative blueprint graph schema input set."""

    root = (
        Path(schema_root)
        if schema_root is not None
        else _default_schema_root()
    )
    paths = [root / name for name in CANONICAL_GRAPH_SCHEMA_INPUTS]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise ArtifactHealthError(
            f"{root}: missing blueprint schema inputs: {', '.join(missing)}"
        )
    manifest = [
        {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in paths
    ]
    return _hash_value(manifest)


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


def _hash_value(value: Any) -> str:
    return _hash_bytes(_canonical_bytes(value))


@lru_cache(maxsize=None)
def _audit_hash_policy(blueprint_type: str, schema_root_text: str) -> dict[str, str]:
    try:
        schema_name = _SCHEMA_BY_NODE_TYPE[blueprint_type]
    except KeyError as exc:
        raise ArtifactHealthError(f"unsupported blueprint type {blueprint_type!r}") from exc
    schema_path = Path(schema_root_text) / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return {
        field: definition["x-famulus"]["audit_hash"]
        for field, definition in schema["properties"].items()
    }


def _contract_projection(node: BlueprintNode, schema_root: Path) -> dict[str, Any]:
    if node.virtual or node.declaration.get("schema_version") != 2:
        return _legacy_contract_projection(node.declaration)
    policy = _audit_hash_policy(node.blueprint_type, str(schema_root.resolve()))
    unknown = set(node.declaration) - set(policy)
    if unknown:
        raise ArtifactHealthError(
            f"{node.blueprint_path}: fields missing schema audit-hash policy: {sorted(unknown)}"
        )
    return {
        field: deepcopy(value)
        for field, value in node.declaration.items()
        if policy[field] == "include"
    }


def _legacy_contract_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            field: _legacy_contract_projection(child)
            for field, child in value.items()
            if field != "direct_io"
        }
    if isinstance(value, list):
        return [_legacy_contract_projection(child) for child in value]
    return deepcopy(value)


def normalize_node_checks(
    checks: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Project node checks to stable semantic fields in canonical order."""

    normalized = []
    identities: set[tuple[object, object]] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            raise ArtifactHealthError("node check must be a mapping")
        try:
            item = {field: deepcopy(check[field]) for field in _STABLE_CHECK_FIELDS}
        except KeyError as exc:
            raise ArtifactHealthError(
                f"node check is missing stable field {exc.args[0]!r}"
            ) from exc
        if item["passed"] is not True:
            raise ArtifactHealthError("cannot certify failed node check")
        identity = (item["id"], item["version"])
        try:
            duplicate = identity in identities
        except TypeError as exc:
            raise ArtifactHealthError("node check identity must be scalar") from exc
        if duplicate:
            raise ArtifactHealthError(
                f"duplicate node check identity {identity[0]!r} version {identity[1]!r}"
            )
        identities.add(identity)
        normalized.append(item)
    try:
        return tuple(
            sorted(normalized, key=lambda item: (str(item["id"]), int(item["version"])))
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactHealthError("node check version must be an integer") from exc


def local_input_paths_for_node(node: BlueprintNode) -> tuple[Path, ...]:
    """Return the canonical node-local file scope used by hashing and Git checks."""

    paths = {_validated_owned_input(node.skill_root, node.blueprint_path)}
    if node.binding_path is not None:
        paths.add(_validated_owned_input(node.skill_root, node.binding_path))
    declared_inputs = node.declaration.get("local_hash_inputs", [])
    if not isinstance(declared_inputs, list):
        raise ArtifactHealthError("local_hash_inputs must be a list")
    for declared in declared_inputs:
        if not isinstance(declared, str) or not declared:
            raise ArtifactHealthError("local_hash_inputs entries must be non-empty strings")
        relative = Path(declared)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactHealthError(
                f"{declared!r}: local_hash_input must be owner-relative without parent traversal"
            )
        paths.add(_validated_owned_input(node.skill_root, node.skill_root / relative))
    return tuple(sorted(paths))


def _validated_owned_input(owner_root: Path, path: Path) -> Path:
    owner_absolute = Path(os.path.abspath(owner_root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(owner_absolute)
    except ValueError as exc:
        raise ArtifactHealthError(
            f"{path}: local input must be owner-relative under {owner_root}"
        ) from exc

    current = owner_absolute
    try:
        for component in relative.parts:
            current = current / component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ArtifactHealthError(f"{path}: local input contains a symlink component")
        if not stat.S_ISREG(metadata.st_mode):
            raise ArtifactHealthError(f"{path}: local input must be a regular file")
    except FileNotFoundError as exc:
        raise ArtifactHealthError(f"{path}: local input does not exist") from exc

    try:
        path_absolute.resolve(strict=True).relative_to(owner_absolute.resolve(strict=True))
    except ValueError as exc:
        raise ArtifactHealthError(f"{path}: local input resolves outside node owner") from exc
    return path


def _edges_by_source(graph: SkillBlueprintGraph) -> dict[str, list[BlueprintEdge]]:
    result = {node_id: [] for node_id in graph.nodes}
    for edge in graph.edges:
        if edge.target_id not in graph.nodes:
            raise ArtifactHealthError(
                f"{edge.source_id}: unresolved downstream node {edge.target_id!r}"
            )
        result[edge.source_id].append(edge)
    for edges in result.values():
        edges.sort(key=lambda edge: (edge.relation, edge.target_id, edge.required_version))
    return result


def _node_local_hash_components(
    node: BlueprintNode,
    schema_root: Path,
) -> tuple[str, str, str | None, str]:
    blueprint_file_hash = _hash_bytes(node.blueprint_path.read_bytes())
    blueprint_contract_hash = _hash_value(_contract_projection(node, schema_root))
    bound_file_hash = (
        _hash_bytes(node.binding_path.read_bytes()) if node.binding_path is not None else None
    )
    owned_paths = local_input_paths_for_node(node)
    semantic_file_hashes = [
        {
            "path": _display_path(path, node.skill_root),
            "sha256": _hash_bytes(path.read_bytes()),
        }
        for path in owned_paths
        if path not in {node.blueprint_path, node.binding_path}
    ]
    local_hash = _hash_value(
        {
            "node_id": node.node_id,
            "blueprint_type": node.blueprint_type,
            "version": node.version,
            "blueprint_contract_hash": blueprint_contract_hash,
            "bound_file_hash": bound_file_hash,
            "local_hash_inputs": semantic_file_hashes,
        }
    )
    return blueprint_file_hash, blueprint_contract_hash, bound_file_hash, local_hash


def compute_node_hash_states(
    graph: SkillBlueprintGraph,
    *,
    policy_hash: str,
    schema_hash: str,
    checks_by_node: Mapping[str, Sequence[Mapping[str, object]]],
    schema_root: Path,
    certifier: Mapping[str, Any],
    health_hash_overrides: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, NodeHashState]:
    """Compute deterministic node hash states for a resolved blueprint graph."""

    edges_by_source = _edges_by_source(graph)
    states: dict[str, NodeHashState] = {}
    visiting: set[str] = set()

    def compute(node_id: str) -> NodeHashState:
        if node_id in states:
            return states[node_id]
        if node_id in visiting:
            raise ArtifactHealthError(f"blueprint health cycle includes {node_id}")
        visiting.add(node_id)
        node = graph.nodes[node_id]
        child_states: list[tuple[BlueprintEdge, NodeHashState]] = [
            (edge, compute(edge.target_id)) for edge in edges_by_source[node_id]
        ]

        (
            blueprint_file_hash,
            blueprint_contract_hash,
            bound_file_hash,
            local_hash,
        ) = _node_local_hash_components(node, schema_root)
        dependencies = tuple(
            {
                "relation": edge.relation,
                "target": edge.target_id,
                "version": edge.required_version,
                "artifact_graph_hash": child.artifact_graph_hash,
                "certified_health_hash": child.certified_health_hash,
            }
            for edge, child in child_states
        )
        downstream_artifact_hash = _hash_value(
            [
                {
                    "relation": item["relation"],
                    "target": item["target"],
                    "version": item["version"],
                    "artifact_graph_hash": item["artifact_graph_hash"],
                }
                for item in dependencies
            ]
        )
        artifact_graph_hash = _hash_value(
            {
                "local_hash": local_hash,
                "downstream_artifact_hash": downstream_artifact_hash,
            }
        )
        downstream_health_hash = _hash_value(
            [
                {
                    "relation": item["relation"],
                    "target": item["target"],
                    "version": item["version"],
                    "certified_health_hash": item["certified_health_hash"],
                }
                for item in dependencies
            ]
        )
        checks = normalize_node_checks(checks_by_node.get(node_id, ()))
        certified_health_hash = _hash_value(
            {
                "local_hash": local_hash,
                "downstream_health_hash": downstream_health_hash,
                "schema_hash": schema_hash,
                "policy_hash": policy_hash,
                "checks": checks,
                "certifier": certifier,
            }
        )
        override = (health_hash_overrides or {}).get(node_id)
        if override is not None and override[0] == artifact_graph_hash:
            certified_health_hash = override[1]
        state = NodeHashState(
            blueprint_file_hash=blueprint_file_hash,
            blueprint_contract_hash=blueprint_contract_hash,
            bound_file_hash=bound_file_hash,
            local_hash=local_hash,
            downstream_artifact_hash=downstream_artifact_hash,
            artifact_graph_hash=artifact_graph_hash,
            downstream_health_hash=downstream_health_hash,
            certified_health_hash=certified_health_hash,
            dependencies=dependencies,
            schema_hash=schema_hash,
            policy_hash=policy_hash,
        )
        states[node_id] = state
        visiting.remove(node_id)
        return state

    for node_id in sorted(graph.nodes):
        compute(node_id)
    return states


def _unadmitted_child_health_overrides(
    graph: SkillBlueprintGraph,
    admitted_records: Mapping[str, dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """Recover unreadable child health only from authenticated parent projections."""

    edges_by_source = _edges_by_source(graph)
    candidates: dict[str, set[tuple[str, str]]] = {}
    for source_id, record in admitted_records.items():
        dependencies = record.get("dependencies")
        if not isinstance(dependencies, list):
            continue
        for edge in edges_by_source[source_id]:
            if edge.target_id in admitted_records:
                continue
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    continue
                if (
                    dependency.get("relation") != edge.relation
                    or dependency.get("target") != edge.target_id
                    or dependency.get("version") != edge.required_version
                ):
                    continue
                artifact_hash = dependency.get("artifact_graph_hash")
                health_hash = dependency.get("certified_health_hash")
                if isinstance(artifact_hash, str) and isinstance(health_hash, str):
                    candidates.setdefault(edge.target_id, set()).add(
                        (artifact_hash, health_hash)
                    )
    return {
        node_id: next(iter(values))
        for node_id, values in candidates.items()
        if len(values) == 1
    }


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _repository_root(graph: SkillBlueprintGraph) -> Path:
    return graph.skill_root.parent.parent


def _source_input_paths(
    graph: SkillBlueprintGraph,
    node: BlueprintNode,
) -> tuple[str, ...]:
    repo_root = _repository_root(graph)
    result: list[str] = []
    for path in local_input_paths_for_node(node):
        try:
            result.append(path.relative_to(repo_root).as_posix())
        except ValueError as exc:
            raise ArtifactHealthError(
                f"{path}: node-local input is outside repository {repo_root}"
            ) from exc
    return tuple(sorted(result))


def _node_record_payload(
    node: BlueprintNode,
    state: NodeHashState,
    *,
    source: Mapping[str, object],
    checks: Sequence[Mapping[str, object]],
    certified_at: str,
    certifier: Mapping[str, object] = _DEFAULT_CERTIFIER,
) -> dict[str, object]:
    hashes = {
        "blueprint_file_hash": state.blueprint_file_hash,
        "blueprint_contract_hash": state.blueprint_contract_hash,
        "bound_file_hash": state.bound_file_hash,
        "local_hash": state.local_hash,
        "downstream_artifact_hash": state.downstream_artifact_hash,
        "artifact_graph_hash": state.artifact_graph_hash,
        "downstream_health_hash": state.downstream_health_hash,
        "certified_health_hash": state.certified_health_hash,
        "schema_hash": state.schema_hash,
        "policy_hash": state.policy_hash,
    }
    return {
        "health_schema_version": 1,
        "record_type": "skill-health" if node.blueprint_type == "skill" else "node-health",
        "subject": {
            "id": node.node_id,
            "blueprint_type": node.blueprint_type,
            "version": node.version,
            "blueprint_path": _display_path(node.blueprint_path, node.skill_root),
            "binding_path": (
                _display_path(node.binding_path, node.skill_root)
                if node.binding_path is not None
                else None
            ),
        },
        "certification": {"result": "passed", "certified_at": certified_at},
        "certifier": deepcopy(dict(certifier)),
        "source": deepcopy(dict(source)),
        "hashes": hashes,
        "dependencies": [deepcopy(item) for item in state.dependencies],
        "checks": [deepcopy(dict(item)) for item in checks],
        "coverage": {},
    }


def _require_sha256_hash(node_id: str, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise ArtifactHealthError(f"{node_id}: {field} must be a sha256 hash")
    prefix, separator, hexadecimal = value.partition(":")
    if (
        prefix != "sha256"
        or not separator
        or len(hexadecimal) != 64
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise ArtifactHealthError(f"{node_id}: {field} must be a sha256 hash")
    return value


def _validate_node_hash_state(
    graph: SkillBlueprintGraph,
    node_id: str,
    states: Mapping[str, NodeHashState],
    *,
    checks: Sequence[Mapping[str, object]],
    schema_root: Path,
) -> NodeHashState:
    state = states.get(node_id)
    if not isinstance(state, NodeHashState):
        raise ArtifactHealthError(f"{node_id}: missing or invalid NodeHashState")
    for field in (
        "blueprint_file_hash",
        "blueprint_contract_hash",
        "local_hash",
        "downstream_artifact_hash",
        "artifact_graph_hash",
        "downstream_health_hash",
        "certified_health_hash",
        "schema_hash",
        "policy_hash",
    ):
        _require_sha256_hash(node_id, field, getattr(state, field))
    if state.bound_file_hash is not None:
        _require_sha256_hash(node_id, "bound_file_hash", state.bound_file_hash)

    node = graph.nodes[node_id]
    expected_local = _node_local_hash_components(node, schema_root)
    actual_local = (
        state.blueprint_file_hash,
        state.blueprint_contract_hash,
        state.bound_file_hash,
        state.local_hash,
    )
    if actual_local != expected_local:
        fields = (
            "blueprint_file_hash",
            "blueprint_contract_hash",
            "bound_file_hash",
            "local_hash",
        )
        mismatch = next(
            field
            for field, actual, expected in zip(fields, actual_local, expected_local)
            if actual != expected
        )
        raise ArtifactHealthError(f"{node_id}: state {mismatch} does not match live node")

    expected_dependencies = []
    for edge in _edges_by_source(graph)[node_id]:
        child = states.get(edge.target_id)
        if not isinstance(child, NodeHashState):
            raise ArtifactHealthError(
                f"{node_id}: missing dependency state for {edge.target_id}"
            )
        _require_sha256_hash(
            edge.target_id, "artifact_graph_hash", child.artifact_graph_hash
        )
        _require_sha256_hash(
            edge.target_id, "certified_health_hash", child.certified_health_hash
        )
        expected_dependencies.append(
            {
                "relation": edge.relation,
                "target": edge.target_id,
                "version": edge.required_version,
                "artifact_graph_hash": child.artifact_graph_hash,
                "certified_health_hash": child.certified_health_hash,
            }
        )
    if state.dependencies != tuple(expected_dependencies):
        raise ArtifactHealthError(
            f"{node_id}: state dependencies do not match direct graph projection"
        )

    downstream_artifact_hash = _hash_value(
        [
            {
                "relation": item["relation"],
                "target": item["target"],
                "version": item["version"],
                "artifact_graph_hash": item["artifact_graph_hash"],
            }
            for item in expected_dependencies
        ]
    )
    artifact_graph_hash = _hash_value(
        {
            "local_hash": state.local_hash,
            "downstream_artifact_hash": downstream_artifact_hash,
        }
    )
    downstream_health_hash = _hash_value(
        [
            {
                "relation": item["relation"],
                "target": item["target"],
                "version": item["version"],
                "certified_health_hash": item["certified_health_hash"],
            }
            for item in expected_dependencies
        ]
    )
    for field, expected in (
        ("downstream_artifact_hash", downstream_artifact_hash),
        ("artifact_graph_hash", artifact_graph_hash),
        ("downstream_health_hash", downstream_health_hash),
    ):
        if getattr(state, field) != expected:
            raise ArtifactHealthError(f"{node_id}: state {field} is inconsistent")

    certified_health_hash = _hash_value(
        {
            "local_hash": state.local_hash,
            "downstream_health_hash": state.downstream_health_hash,
            "schema_hash": state.schema_hash,
            "policy_hash": state.policy_hash,
            "checks": checks,
            "certifier": _DEFAULT_CERTIFIER,
        }
    )
    if state.certified_health_hash != certified_health_hash:
        raise ArtifactHealthError(
            f"{node_id}: state certified_health_hash is inconsistent with supplied checks"
        )
    return state


def build_node_health_record(
    graph: SkillBlueprintGraph,
    node_id: str,
    states: Mapping[str, NodeHashState],
    *,
    source: Mapping[str, object],
    checks: Sequence[Mapping[str, object]],
    key: bytes,
    certified_at: str,
    schema_root: Path | None = None,
) -> dict[str, object]:
    """Build, strictly validate, and authenticate one node health record."""

    node = graph.nodes[node_id]
    resolved_schema_root = Path(schema_root) if schema_root is not None else _default_schema_root()
    if not isinstance(source, Mapping):
        raise ArtifactHealthError(f"{node_id}: source must be a mapping")
    expected_paths = list(_source_input_paths(graph, node))
    if source.get("input_paths") != expected_paths:
        raise ArtifactHealthError(
            f"{node_id}: source input_paths must equal node-local inputs {expected_paths}"
        )
    normalized_checks = normalize_node_checks(checks)
    state = _validate_node_hash_state(
        graph,
        node_id,
        states,
        checks=normalized_checks,
        schema_root=resolved_schema_root,
    )
    record = _node_record_payload(
        node,
        state,
        source=source,
        checks=normalized_checks,
        certified_at=certified_at,
    )
    try:
        authenticated = attach_record_authentication(record, key)
        if not record_authentication_matches(authenticated, key):
            raise ArtifactHealthError(f"{node_id}: record authentication self-check failed")
        validator = schema_validator(
            load_schema(resolved_schema_root / "health.schema.json")
        )
        validator.validate(authenticated)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise ArtifactHealthError(
            f"{node_id}: invalid node health record at {location}: {exc.message}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ArtifactHealthError(f"{node_id}: invalid node health record: {exc}") from exc

    expected_subject = _node_record_payload(
        node,
        state,
        source=source,
        checks=normalized_checks,
        certified_at=certified_at,
    )
    for field in ("subject", "certifier", "source", "dependencies"):
        if authenticated.get(field) != expected_subject[field]:
            raise ArtifactHealthError(f"{node_id}: invalid {field} projection")
    hashes = authenticated.get("hashes")
    if not isinstance(hashes, dict):
        raise ArtifactHealthError(f"{node_id}: invalid hashes projection")
    if hashes.get("schema_hash") != state.schema_hash:
        raise ArtifactHealthError(f"{node_id}: invalid schema_hash projection")
    if hashes.get("policy_hash") != state.policy_hash:
        raise ArtifactHealthError(f"{node_id}: invalid policy_hash projection")
    return authenticated


def certify_graph(
    graph: SkillBlueprintGraph,
    policy_hash: str,
    schema_hash: str,
    checks: list[dict[str, Any]],
    *,
    key: bytes,
    certified_at: str,
    schema_root: Path | None = None,
    certifier: Mapping[str, Any] = _DEFAULT_CERTIFIER,
) -> dict[str, dict[str, Any]]:
    """Deprecated compatibility-only wrapper that builds test graph records."""

    compatibility_checks = [
        {
            **deepcopy(check),
            "version": deepcopy(check.get("version", 1)),
            "findings": deepcopy(check.get("findings", [])),
        }
        for check in checks
    ]
    normalized = normalize_node_checks(compatibility_checks)
    resolved_schema_root = Path(schema_root) if schema_root is not None else _default_schema_root()
    normalized_certifier = deepcopy(dict(certifier))
    checks_by_node = {graph.root.node_id: normalized}
    states = compute_node_hash_states(
        graph,
        policy_hash=policy_hash,
        schema_hash=schema_hash,
        checks_by_node=checks_by_node,
        schema_root=resolved_schema_root,
        certifier=normalized_certifier,
    )
    records: dict[str, dict[str, Any]] = {}
    for node_id in sorted(graph.nodes):
        node_checks = normalized if node_id == graph.root.node_id else ()
        source = {
            "vcs": "git",
            "commit": "0" * 40,
            "input_paths": list(_source_input_paths(graph, graph.nodes[node_id])),
        }
        record = _node_record_payload(
            graph.nodes[node_id],
            states[node_id],
            source=source,
            checks=node_checks,
            certified_at=certified_at,
            certifier=normalized_certifier,
        )
        records[node_id] = attach_record_authentication(record, key)
    return records


def check_graph_health(
    graph: SkillBlueprintGraph,
    records: Mapping[str, dict[str, Any]],
    policy_hash: str,
    schema_hash: str,
    key: bytes,
    schema_root: Path | None = None,
    certifier: Mapping[str, Any] = _DEFAULT_CERTIFIER,
) -> GraphHealthReport:
    """Verify records against live files, authenticating children before parents."""

    resolved_schema_root = Path(schema_root) if schema_root is not None else _default_schema_root()
    normalized_certifier = deepcopy(dict(certifier))
    validator = schema_validator(load_schema(resolved_schema_root / "health.schema.json"))
    admitted_records: dict[str, dict[str, Any]] = {}
    admission_concerns: dict[str, list[str]] = {node_id: [] for node_id in graph.nodes}
    for node_id, node in graph.nodes.items():
        record = records.get(node_id)
        if not isinstance(record, dict):
            admission_concerns[node_id].append("missing-health-record")
            continue
        try:
            authenticated = record_authentication_matches(record, key)
        except (TypeError, ValueError):
            authenticated = False
        if not authenticated:
            admission_concerns[node_id].append("authentication-failed")
            continue
        try:
            validator.validate(record)
        except jsonschema.ValidationError:
            admission_concerns[node_id].append("invalid-health-record")
            continue
        expected_record_type = "skill-health" if node.blueprint_type == "skill" else "node-health"
        expected_subject = {
            "id": node.node_id,
            "blueprint_type": node.blueprint_type,
            "version": node.version,
            "blueprint_path": _display_path(node.blueprint_path, node.skill_root),
            "binding_path": (
                _display_path(node.binding_path, node.skill_root)
                if node.binding_path is not None
                else None
            ),
        }
        try:
            stable_checks = normalize_node_checks(record.get("checks", ()))
        except (ArtifactHealthError, TypeError, ValueError):
            stable_checks = ()
            checks_match = False
        else:
            checks_match = record.get("checks") == list(stable_checks)
        source = record.get("source")
        source_matches = (
            isinstance(source, dict)
            and source.get("input_paths") == list(_source_input_paths(graph, node))
        )
        if (
            record.get("record_type") != expected_record_type
            or record.get("subject") != expected_subject
            or record.get("certifier") != normalized_certifier
            or not source_matches
            or not checks_match
        ):
            admission_concerns[node_id].append("invalid-health-record")
            continue
        admitted_records[node_id] = record

    checks_by_node = {
        node_id: deepcopy(record["checks"])
        for node_id, record in admitted_records.items()
    }
    health_hash_overrides = _unadmitted_child_health_overrides(
        graph,
        admitted_records,
    )
    states = compute_node_hash_states(
        graph,
        policy_hash=policy_hash,
        schema_hash=schema_hash,
        checks_by_node=checks_by_node,
        schema_root=resolved_schema_root,
        certifier=normalized_certifier,
        health_hash_overrides=health_hash_overrides,
    )
    edges_by_source = _edges_by_source(graph)
    statuses: dict[str, NodeHealthStatus] = {}

    def check(node_id: str) -> NodeHealthStatus:
        if node_id in statuses:
            return statuses[node_id]
        child_statuses = [check(edge.target_id) for edge in edges_by_source[node_id]]
        state = states[node_id]
        record = records.get(node_id)
        concerns: list[str] = list(admission_concerns[node_id])
        recorded_hash: str | None = None
        admitted_record_hash: str | None = None
        if node_id in admitted_records:
            hashes = record.get("hashes")
            value = record.get("record_hash")
            admitted_record_hash = value if isinstance(value, str) else None
            if isinstance(hashes, dict):
                value = hashes.get("certified_health_hash")
                recorded_hash = value if isinstance(value, str) else None
            if isinstance(hashes, dict):
                if hashes.get("blueprint_file_hash") != state.blueprint_file_hash:
                    concerns.append("blueprint-file-changed")
                artifact_fields = {
                    "blueprint_contract_hash": state.blueprint_contract_hash,
                    "bound_file_hash": state.bound_file_hash,
                    "local_hash": state.local_hash,
                }
                dependency_fields = {
                    "downstream_artifact_hash": state.downstream_artifact_hash,
                    "artifact_graph_hash": state.artifact_graph_hash,
                    "downstream_health_hash": state.downstream_health_hash,
                }
                artifact_stale = any(
                    hashes.get(field) != expected
                    for field, expected in artifact_fields.items()
                )
                dependency_stale = any(
                    hashes.get(field) != expected
                    for field, expected in dependency_fields.items()
                )
                schema_stale = hashes.get("schema_hash") != schema_hash
                policy_stale = hashes.get("policy_hash") != policy_hash
                if artifact_stale:
                    concerns.append("artifact-stale")
                if dependency_stale:
                    concerns.append("dependency-stale")
                if schema_stale:
                    concerns.append("schema-stale")
                if policy_stale:
                    concerns.append("policy-stale")
                if (
                    hashes.get("certified_health_hash") != state.certified_health_hash
                    and not any(
                        (artifact_stale, dependency_stale, schema_stale, policy_stale)
                    )
                ):
                    concerns.append("checks-stale")
                if record.get("dependencies") != list(state.dependencies):
                    concerns.append("invalid-health-record")
                    concerns.append("dependency-stale")
            else:
                concerns.append("invalid-health-record")
        if any(not child.healthy for child in child_statuses):
            concerns.append("downstream-unhealthy")
        nonfatal = {"blueprint-file-changed"}
        healthy = not any(concern not in nonfatal for concern in concerns)
        status = NodeHealthStatus(
            node_id=node_id,
            healthy=healthy,
            concerns=tuple(dict.fromkeys(concerns)),
            expected_certified_health_hash=state.certified_health_hash,
            recorded_certified_health_hash=recorded_hash,
            admitted_record_hash=admitted_record_hash,
        )
        statuses[node_id] = status
        return status

    root_status = check(graph.root.node_id)
    for node_id in sorted(graph.nodes):
        check(node_id)
    return GraphHealthReport(graph.root.node_id, root_status.healthy, statuses)


def node_requires_refresh(status: NodeHealthStatus) -> bool:
    """Return whether a node's own health record must be replaced."""

    return any(concern in _REFRESH_CONCERNS for concern in status.concerns)


def health_path_for_node(node: BlueprintNode) -> Path:
    """Return the generated health sidecar path for a graph node."""

    if node.blueprint_type == "skill":
        return node.skill_root / ".last_audit.json"
    if node.virtual:
        local_name = node.node_id.rsplit(".", 1)[-1]
        if node.binding_path is None:
            return node.skill_root / f".{node.node_id}.{local_name}.health.json"
        return node.binding_path.with_name(
            f".{node.binding_path.name}.{local_name}.health.json"
        )
    suffix = ".blueprint.yaml"
    if not node.blueprint_path.name.endswith(suffix):
        raise ArtifactHealthError(f"unexpected subordinate blueprint name: {node.blueprint_path}")
    stem = node.blueprint_path.name.removesuffix(suffix)
    return node.blueprint_path.with_name(f"{stem}.health.json")

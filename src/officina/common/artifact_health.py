"""Recursive health certification for typed blueprint graphs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
from typing import Any, Iterable, Mapping, Sequence

import jsonschema
import yaml

from .audit_records import attach_record_authentication, record_authentication_matches
from .blueprint_graph import (
    BlueprintEdge,
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
    SkillBlueprintGraph,
    open_runtime_file,
    resolved_node_content_paths,
)
from .git_provenance import git_file_provenance
from .blueprint_template import load_schema, schema_validator
from .process_binding_compiler import gateway_language_name
from officina.runtime.python_machine_interface import (
    PythonRouteSmokeTraceError,
    trace_python_route_smoke_dependencies,
)


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
    # Canonical v4 state.
    node_hash: str | None = None
    input_manifest: tuple[dict[str, str], ...] = ()
    dependency_hashes: tuple[dict[str, Any], ...] = ()
    certification_basis_hash: str | None = None
    # Explicit pre-v4 health state retained only until the atomic cutover.
    blueprint_file_hash: str | None = None
    blueprint_contract_hash: str | None = None
    bound_file_hash: str | None = None
    local_hash: str | None = None
    downstream_artifact_hash: str | None = None
    artifact_graph_hash: str | None = None
    downstream_health_hash: str | None = None
    certified_health_hash: str | None = None
    dependencies: tuple[dict[str, Any], ...] = ()
    schema_hash: str | None = None
    policy_hash: str | None = None


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
        relative = absolute.relative_to(repo_root).as_posix()
    except ValueError:
        if not allow_outside:
            raise ArtifactHealthError(
                f"unmapped route-smoke dependency outside repository: {absolute}"
            )
        relative = absolute.as_posix()
    try:
        metadata = absolute.lstat()
    except OSError as exc:
        raise ArtifactHealthError(
            f"unmapped route-smoke dependency {relative}: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ArtifactHealthError(
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
        raise ArtifactHealthError(
            f"route-smoke source must be a behavioral source: {source_node_id}"
        )
    manifest_paths: dict[str, set[str]] = {}
    validated_states: dict[str, NodeHashState] = {}
    for node_id in sorted(graph.nodes):
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            raise ArtifactHealthError(
                f"{node_id}: route-smoke mapping requires canonical v4 node state"
            )
        _require_sha256_hash(node_id, "node_hash", state.node_hash)
        if not isinstance(state.input_manifest, tuple):
            raise ArtifactHealthError(
                f"{node_id}: route-smoke mapping found invalid input manifest"
            )
        if not isinstance(state.dependency_hashes, tuple):
            raise ArtifactHealthError(
                f"{node_id}: route-smoke mapping found invalid dependency hashes"
            )
        paths: set[str] = set()
        for entry in state.input_manifest:
            path = entry.get("path") if isinstance(entry, Mapping) else None
            if not isinstance(path, str):
                raise ArtifactHealthError(
                    f"{node_id}: route-smoke mapping found invalid input manifest"
                )
            paths.add(path)
        manifest_paths[node_id] = paths
        validated_states[node_id] = state

    reachable: set[str] = set()
    pending = [source_node_id]
    children: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    required_dependency_fields = {"relation", "target", "version", "node_hash"}
    for node_id, state in validated_states.items():
        for index, dependency in enumerate(state.dependency_hashes):
            if (
                not isinstance(dependency, Mapping)
                or set(dependency) != required_dependency_fields
            ):
                raise ArtifactHealthError(
                    f"{node_id}: invalid dependency hash at index {index}"
                )
            relation = dependency.get("relation")
            target_id = dependency.get("target")
            version = dependency.get("version")
            target_hash = dependency.get("node_hash")
            target = (
                graph.nodes.get(target_id) if isinstance(target_id, str) else None
            )
            target_state = (
                validated_states.get(target_id)
                if isinstance(target_id, str)
                else None
            )
            if (
                not isinstance(relation, str)
                or not relation
                or target is None
                or target_state is None
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version != target.version
                or target_hash != target_state.node_hash
            ):
                raise ArtifactHealthError(
                    f"{node_id}: invalid dependency hash at index {index}"
                )
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
            absolute.relative_to(root)
        except ValueError:
            basis_label = basis.get(absolute)
            if basis_label is None:
                raise ArtifactHealthError(
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
        elif absolute in basis:
            mapping = RouteSmokeDependencyMapping(
                relative, "certification-basis", None
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
            if len(candidates) != 1:
                detail = "no authority" if not candidates else "ambiguous authority"
                raise ArtifactHealthError(
                    f"unmapped route-smoke dependency {relative}: {detail}"
                )
            mapping = RouteSmokeDependencyMapping(
                relative,
                "certification-dependency",
                next(iter(candidates)),
            )
        mappings[relative] = mapping
    return tuple(mappings[path] for path in sorted(mappings))


def route_smoke_trace_signature(
    mappings: Iterable[RouteSmokeDependencyMapping],
) -> tuple[tuple[str, str, str | None], ...]:
    """Return the stable projection used for pre/post migration comparison."""

    return tuple(
        sorted(
            (mapping.path, mapping.authority, mapping.target_node_id)
            for mapping in mappings
        )
    )


_SCHEMA_BY_NODE_TYPE = {
    "skill": "v2/skill.schema.json",
    "llm-interface": "v2/llm-interface.schema.json",
    "machine-interface": "v2/machine-interface.schema.json",
    "behavior-source": "v2/behavior-source.schema.json",
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
    "default-llm-interface.schema.json",
    "llm-interface.schema.json",
    "machine-interface.schema.json",
    "machine-module.schema.json",
    "behavior-source.schema.json",
    "caller-contract.schema.json",
    "direct-io.schema.json",
    "interface-conformance.schema.json",
    "conformance-boundary-operations.schema.json",
    "conformance-operations/common.schema.json",
    "conformance-operations/filesystem.schema.json",
    "conformance-operations/clock.schema.json",
    "conformance-operations/network.schema.json",
    "conformance-operations/helpers.schema.json",
    "conformance-operations/subprocess.schema.json",
    "conformance-operations/calendar.schema.json",
    "conformance-operations/email.schema.json",
    "interface-admissibility-profile.schema.json",
    "interface-admissibility-result.schema.json",
    "interface-projection.schema.json",
    "health.schema.json",
    "v2/common.schema.json",
    "v2/skill.schema.json",
    "v2/default-llm-interface.schema.json",
    "v2/llm-interface.schema.json",
    "v2/machine-interface.schema.json",
    "v2/behavior-source.schema.json",
    "schema.annotated-draft.json",
    "template.yaml",
)
POOLED_REVIEW_SCHEMA_INPUTS = (
    "pooled-review.schema.json",
    "certificate.schema.json",
)


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
    missing = [path.relative_to(root).as_posix() for path in paths if not path.is_file()]
    if missing:
        raise ArtifactHealthError(
            f"{root}: missing blueprint schema inputs: {', '.join(missing)}"
        )
    manifest = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
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


def _target_node_hash(node: BlueprintNode, repo_root: Path) -> str:
    try:
        content_paths = resolved_node_content_paths(node, repo_root)
    except BlueprintGraphError as exc:
        raise ArtifactHealthError(str(exc)) from exc
    content = []
    for path in content_paths:
        validated = _validated_owned_input(node.skill_root, path)
        content.append(
            {
                "path": validated.relative_to(node.skill_root).as_posix(),
                "digest": _hash_bytes(validated.read_bytes()),
            }
        )
    return _hash_value(
        {
            "blueprint": node.declaration,
            "content": content,
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


def _resolve_reference_path(owner_root: Path, base: Path, locator: str) -> Path:
    relative = Path(locator)
    if relative.is_absolute() or ".." in relative.parts:
        raise ArtifactHealthError(
            f"reference path {locator!r} must remain under the module owner root"
        )
    candidate = Path(os.path.abspath(base / relative))
    owner = Path(os.path.abspath(owner_root))
    try:
        candidate.relative_to(owner)
    except ValueError as exc:
        raise ArtifactHealthError(
            f"reference path {locator!r} escapes the module owner root"
        ) from exc
    return _validated_owned_input(owner, candidate)


def _parse_reference_document(path: Path, payload: bytes) -> object:
    try:
        text = payload.decode("utf-8")
        if path.suffix == ".json" or path.name.endswith(".schema.json"):
            return json.loads(text)
        return yaml.safe_load(text)
    except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ArtifactHealthError(f"{path}: cannot parse referenced document: {exc}") from exc


def _validate_fragment(document: object, fragment: str, path: Path) -> None:
    if fragment in {"", "#"}:
        return
    if not fragment.startswith("#/"):
        raise ArtifactHealthError(f"{path}: unsupported reference fragment {fragment!r}")
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
        raise ArtifactHealthError(f"{path}: unresolved reference fragment {fragment!r}") from exc


def _recursive_contract_references(
    owner_root: Path,
    seeds: Iterable[tuple[str, str]],
) -> tuple[_ContractReference, ...]:
    """Resolve the complete confined file closure of authored contract locators."""

    pending: list[tuple[Path, str, str]] = [
        (owner_root, path, fragment) for path, fragment in seeds
    ]
    entries: dict[str, _ContractReference] = {}
    parsed_paths: set[Path] = set()
    while pending:
        base, locator_path, fragment = pending.pop(0)
        path = _resolve_reference_path(owner_root, base, locator_path)
        relative = path.relative_to(owner_root).as_posix()
        locator = f"{relative}{fragment}"
        payload = path.read_bytes()
        document = _parse_reference_document(path, payload)
        _validate_fragment(document, fragment, path)
        entries[locator] = _ContractReference(locator, path, _hash_bytes(payload))
        if path in parsed_paths:
            continue
        parsed_paths.add(path)
        for child_path, child_fragment in _reference_candidates(document):
            pending.append((path.parent, child_path, child_fragment))
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
                    raise ArtifactHealthError(
                        f"{path}: external reference URI is unsupported: {ref}"
                    )
                pending.append(
                    (path.parent, path_text, f"#{ref_fragment}" if separator else "#")
                )
    return tuple(entries[locator] for locator in sorted(entries))


def _contract_reference_manifest(module: BlueprintNode) -> tuple[dict[str, str], ...]:
    manifest = module.declaration.get("conformance_manifest")
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("path"), str):
        raise ArtifactHealthError(
            f"{module.blueprint_path}: machine module requires conformance_manifest.path"
        )
    seeds = [(manifest["path"], "#")]
    seeds.extend(
        (path, fragment)
        for path, fragment in _reference_candidates(module.declaration)
        if path != manifest["path"]
    )
    return tuple(
        {"locator": reference.locator, "digest": reference.digest}
        for reference in _recursive_contract_references(module.skill_root, seeds)
    )


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
    declared_inputs_value = node.declaration.get("local_hash_inputs", [])
    if not isinstance(declared_inputs_value, list):
        raise ArtifactHealthError("local_hash_inputs must be a list")
    declared_inputs = list(declared_inputs_value)
    default_interface = node.declaration.get("default_interface")
    if node.blueprint_type == "skill" and isinstance(default_interface, dict):
        inline_inputs = default_interface.get("local_hash_inputs", [])
        if not isinstance(inline_inputs, list):
            raise ArtifactHealthError(
                "default_interface.local_hash_inputs must be a list"
            )
        declared_inputs.extend(inline_inputs)
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


def load_node_hash_policy(
    policy_path: Path,
    *,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate the canonical ordered node-input policy."""

    path = Path(policy_path)
    selected_schema = (
        Path(schema_path)
        if schema_path is not None
        else _default_schema_root().parent / "certification" / "node-hash-policy.schema.json"
    )
    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = json.loads(selected_schema.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ArtifactHealthError(f"{path}: cannot load node hash policy: {exc}") from exc
    if not isinstance(policy, dict):
        raise ArtifactHealthError(f"{path}: node hash policy must be a mapping")
    errors = sorted(
        jsonschema.Draft7Validator(schema).iter_errors(policy),
        key=lambda error: (tuple(str(part) for part in error.path), error.message),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(f"[{part!r}]" for part in first.path)
        raise ArtifactHealthError(
            f"{path}: node hash policy schema error at {location}: {first.message}"
        )
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
            raise ArtifactHealthError(
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
        raise ArtifactHealthError(
            f"cannot apply Git exclude pattern {pattern!r}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ArtifactHealthError(
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
        or name == ".last_audit.json"
        or name.endswith(".audit.json")
        or name.endswith(".health.json")
        or "pooled-blueprint-review" in name
    )


def _read_node_input(node: BlueprintNode, path: Path, repo_root: Path) -> bytes:
    try:
        binding = open_runtime_file(path, node.skill_root, repo_root)
    except BlueprintGraphError as exc:
        raise ArtifactHealthError(str(exc)) from exc
    try:
        return binding.read_bytes()
    finally:
        binding.close()


def _v4_node_input_manifests(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
    policy: Mapping[str, Any],
) -> tuple[
    dict[str, tuple[dict[str, str], ...]],
    dict[str, set[str]],
]:
    """Resolve policy-selected inputs and cross-owner contract dependencies."""

    root = Path(repo_root).resolve()
    owned_paths = {
        Path(os.path.abspath(path)): owner_id
        for path, owner_id in graph.direct_file_owners.items()
    }
    provenance: dict[Path, str] = {}
    relative_paths: dict[Path, str] = {}
    for path in sorted(owned_paths):
        try:
            relative = path.relative_to(root).as_posix()
            provenance[path] = git_file_provenance(root, path)
        except (ValueError, OSError) as exc:
            raise ArtifactHealthError(f"{path}: cannot determine Git provenance") from exc
        relative_paths[path] = relative

    selected = {
        path: provenance[path] == "tracked"
        for path in owned_paths
    }
    final_actions: dict[Path, str] = {}
    raw_rules = policy.get("rules")
    if not isinstance(raw_rules, list):
        raise ArtifactHealthError("node hash policy rules must be a list")
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, Mapping):
            raise ArtifactHealthError(f"node hash policy rule {index} must be a mapping")
        action = rule.get("action")
        pattern = rule.get("pattern")
        if action not in {"include", "exclude"} or not isinstance(pattern, str):
            raise ArtifactHealthError(f"node hash policy rule {index} is invalid")
        matched_relatives = _git_exclude_matches(
            root,
            relative_paths.values(),
            pattern,
        )
        matches = [
            path for path, relative in relative_paths.items()
            if relative in matched_relatives
        ]
        if action == "include" and rule.get("require_match") is True and not matches:
            raise ArtifactHealthError(
                f"node hash policy include {pattern!r} requires at least one match"
            )
        for path in matches:
            selected[path] = action == "include"
            final_actions[path] = action

    manifests: dict[str, tuple[dict[str, str], ...]] = {}
    contract_dependencies: dict[str, set[str]] = {
        node_id: set() for node_id in graph.nodes
    }
    mandatory_contract_paths: dict[str, set[Path]] = {
        node_id: set() for node_id in graph.nodes
    }
    for node_id, node in sorted(graph.nodes.items()):
        references = _recursive_contract_references(
            node.skill_root,
            _reference_candidates(node.declaration),
        )
        for reference in references:
            canonical_reference = Path(os.path.abspath(reference.path))
            owner_id = owned_paths.get(canonical_reference)
            if owner_id is None:
                raise ArtifactHealthError(
                    f"{node.blueprint_path}: referenced contract "
                    f"{reference.locator!r} is not directly owned by a node"
                )
            mandatory_contract_paths[owner_id].add(canonical_reference)
            if owner_id != node_id:
                contract_dependencies[node_id].add(owner_id)

    for node_id, node in sorted(graph.nodes.items()):
        node_paths = {
            path
            for path, owner_id in owned_paths.items()
            if owner_id == node_id and selected[path]
        }
        mandatory_paths = {
            Path(os.path.abspath(node.blueprint_path)),
        }
        if node.gateway_path is not None:
            mandatory_paths.add(Path(os.path.abspath(node.gateway_path)))
        mandatory_paths.update(mandatory_contract_paths[node_id])

        for mandatory_path in mandatory_paths:
            try:
                relative = mandatory_path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ArtifactHealthError(
                    f"{mandatory_path}: mandatory node input is outside the repository"
                ) from exc
            final_action = final_actions.get(mandatory_path)
            if mandatory_path not in owned_paths:
                for rule in raw_rules:
                    pattern = rule.get("pattern") if isinstance(rule, Mapping) else None
                    if (
                        isinstance(pattern, str)
                        and relative in _git_exclude_matches(root, (relative,), pattern)
                    ):
                        action = rule.get("action")
                        final_action = action if isinstance(action, str) else None
            if final_action == "exclude":
                raise ArtifactHealthError(
                    f"{node_id}: mandatory blueprint, gateway, or contract input "
                    f"cannot be excluded: {relative}"
                )
            node_paths.add(mandatory_path)

        entries: list[dict[str, str]] = []
        for path in sorted(node_paths):
            try:
                relative = path.relative_to(root).as_posix()
                path_provenance = provenance.get(path) or git_file_provenance(root, path)
            except (ValueError, OSError) as exc:
                raise ArtifactHealthError(f"{path}: cannot determine Git provenance") from exc
            if _reserved_certification_output(relative):
                raise ArtifactHealthError(
                    f"{relative}: reserved certification output cannot be a node input"
                )
            payload = _read_node_input(node, path, root)
            entries.append(
                {
                    "path": relative,
                    "digest": _hash_bytes(payload),
                    "git_provenance": path_provenance,
                }
            )
        manifests[node_id] = tuple(entries)
    return manifests, contract_dependencies


def _compute_v4_node_hash_states(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    policy_path: Path,
    certification_basis_hash: str,
    certification_basis_paths: Iterable[Path | str],
) -> dict[str, NodeHashState]:
    trace_specs: list[tuple[str, str, str]] = []
    for node_id, node in sorted(graph.nodes.items()):
        if node.node_type != "behavioral_source" or node.gateway_path is None:
            continue
        gateway = node.declaration.get("gateway")
        language = gateway.get("language") if isinstance(gateway, Mapping) else None
        if not isinstance(language, str) or gateway_language_name(language) != "Python":
            continue
        interfaces = node.declaration.get("interfaces")
        if not isinstance(interfaces, Mapping):
            continue
        try:
            gateway_path = node.gateway_path.relative_to(node.skill_root).as_posix()
        except ValueError as exc:
            raise ArtifactHealthError(
                f"{node_id}: Python gateway must remain inside its module"
            ) from exc
        for interface_id, declaration in sorted(interfaces.items()):
            binding = (
                declaration.get("process_binding")
                if isinstance(declaration, Mapping)
                else None
            )
            entry = binding.get("entry") if isinstance(binding, Mapping) else None
            if (
                isinstance(binding, Mapping)
                and binding.get("kind") == "process"
                and isinstance(entry, str)
            ):
                trace_specs.append((node_id, str(interface_id), f"{gateway_path}:{entry}"))

    root = Path(repo_root).resolve()

    def collect_traces() -> dict[tuple[str, str], tuple[Path, ...]]:
        traces: dict[tuple[str, str], tuple[Path, ...]] = {}
        for node_id, interface_id, entrypoint in trace_specs:
            try:
                traces[(node_id, interface_id)] = trace_python_route_smoke_dependencies(
                    graph.nodes[node_id].skill_root,
                    root,
                    entrypoint,
                )
            except PythonRouteSmokeTraceError as exc:
                raise ArtifactHealthError(
                    f"{interface_id}: {exc}"
                ) from exc
        return traces

    basis_paths = tuple(certification_basis_paths)
    before_traces = collect_traces()
    policy = load_node_hash_policy(policy_path)
    manifests, contract_dependencies = _v4_node_input_manifests(
        graph, repo_root, policy
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
    dependencies_by_node: dict[str, set[tuple[str, str, int | None]]] = {
        node_id: set() for node_id in graph.nodes
    }
    for edge in graph.certification_edges:
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

    visiting: list[str] = []
    visited: set[str] = set()

    def reject_dependency_cycle(node_id: str) -> None:
        if node_id in visiting:
            start = visiting.index(node_id)
            cycle = visiting[start:] + [node_id]
            raise ArtifactHealthError(
                "certification dependency cycle includes " + " -> ".join(cycle)
            )
        if node_id in visited:
            return
        visiting.append(node_id)
        for _relation, target_id, _version in sorted(
            dependencies_by_node[node_id],
            key=lambda item: (item[0], item[1], item[2] or 0),
        ):
            reject_dependency_cycle(target_id)
        visiting.pop()
        visited.add(node_id)

    for node_id in sorted(graph.nodes):
        reject_dependency_cycle(node_id)

    states: dict[str, NodeHashState] = {}
    for node_id in sorted(graph.nodes):
        dependency_hashes = tuple(
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
        )
        states[node_id] = NodeHashState(
            node_hash=node_hashes[node_id],
            input_manifest=manifests[node_id],
            dependency_hashes=dependency_hashes,
            certification_basis_hash=certification_basis_hash,
        )

    after_traces = collect_traces()
    for node_id, interface_id, _entrypoint in trace_specs:
        before = map_route_smoke_dependencies(
            graph,
            states,
            source_node_id=node_id,
            loaded_paths=before_traces[(node_id, interface_id)],
            certification_basis_paths=basis_paths,
            repo_root=root,
        )
        after = map_route_smoke_dependencies(
            graph,
            states,
            source_node_id=node_id,
            loaded_paths=after_traces[(node_id, interface_id)],
            certification_basis_paths=basis_paths,
            repo_root=root,
        )
        if route_smoke_trace_signature(before) != route_smoke_trace_signature(after):
            raise ArtifactHealthError(
                f"{interface_id}: route-smoke dependency trace changed during hash preparation"
            )
    return states


def health_node_ids(graph: SkillBlueprintGraph) -> tuple[str, ...]:
    """Return graph nodes that own independent health records."""

    return tuple(
        sorted(node_id for node_id, node in graph.nodes.items() if not node.embedded)
    )


def health_owner_node_id(graph: SkillBlueprintGraph, node_id: str) -> str:
    """Return the health-owning node for a logical graph node."""

    node = graph.nodes[node_id]
    if not node.embedded:
        return node_id
    candidates = [
        candidate.node_id
        for candidate in graph.nodes.values()
        if candidate.skill_root == node.skill_root
        and candidate.blueprint_type == "skill"
        and not candidate.embedded
    ]
    if len(candidates) != 1:
        raise ArtifactHealthError(
            f"{node_id}: embedded interface must have exactly one health-owning skill root"
        )
    return candidates[0]


def health_edges(graph: SkillBlueprintGraph) -> tuple[BlueprintEdge, ...]:
    """Project logical graph edges onto nodes with independent health records."""

    certified_ids = set(health_node_ids(graph))
    projected: dict[tuple[str, str, str, int, str | None], BlueprintEdge] = {}
    for edge in graph.edges:
        if edge.source_id not in graph.nodes or edge.target_id not in graph.nodes:
            raise ArtifactHealthError(
                f"{edge.source_id}: unresolved downstream node {edge.target_id!r}"
            )
        source_id = health_owner_node_id(graph, edge.source_id)
        target_id = health_owner_node_id(graph, edge.target_id)
        if source_id == target_id:
            continue
        projected_edge = BlueprintEdge(
            edge.relation,
            source_id,
            target_id,
            edge.required_version,
            edge.target_blueprint_path,
        )
        key = (
            projected_edge.relation,
            projected_edge.source_id,
            projected_edge.target_id,
            projected_edge.required_version,
            str(projected_edge.target_blueprint_path)
            if projected_edge.target_blueprint_path is not None
            else None,
        )
        projected.setdefault(key, projected_edge)
    return tuple(sorted(projected.values(), key=lambda edge: (
        edge.source_id,
        edge.relation,
        edge.target_id,
        edge.required_version,
    )))


def health_postorder_node_ids(graph: SkillBlueprintGraph) -> tuple[str, ...]:
    """Return health-owning nodes after their projected dependencies."""

    edges_by_source: dict[str, list[str]] = {
        node_id: [] for node_id in health_node_ids(graph)
    }
    for edge in health_edges(graph):
        edges_by_source[edge.source_id].append(edge.target_id)
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for target_id in sorted(edges_by_source[node_id]):
            visit(target_id)
        ordered.append(node_id)

    for root_id in graph.root_node_ids or (graph.root.node_id,):
        visit(health_owner_node_id(graph, root_id))
    for node_id in health_node_ids(graph):
        visit(node_id)
    return tuple(ordered)


def _edges_by_source(graph: SkillBlueprintGraph) -> dict[str, list[BlueprintEdge]]:
    certified_ids = set(health_node_ids(graph))
    result = {node_id: [] for node_id in certified_ids}
    for edge in health_edges(graph):
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
    graph: SkillBlueprintGraph | RepositoryBlueprintGraph,
    *,
    policy_hash: str | None = None,
    schema_hash: str | None = None,
    checks_by_node: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    schema_root: Path | None = None,
    certifier: Mapping[str, Any] | None = None,
    health_hash_overrides: Mapping[str, tuple[str, str]] | None = None,
    repo_root: Path | None = None,
    policy_path: Path | None = None,
    certification_basis_hash: str | None = None,
    certification_basis_paths: Iterable[Path | str] = (),
) -> dict[str, NodeHashState]:
    """Compute deterministic node hash states through the one generation-aware path."""

    if isinstance(graph, RepositoryBlueprintGraph) and any(
        node.declaration.get("schema_version") == 4 for node in graph.nodes.values()
    ):
        if repo_root is None or policy_path is None or certification_basis_hash is None:
            raise ArtifactHealthError(
                "v4 node hashing requires repo_root, policy_path, and "
                "certification_basis_hash"
            )
        return _compute_v4_node_hash_states(
            graph,
            repo_root=repo_root,
            policy_path=policy_path,
            certification_basis_hash=certification_basis_hash,
            certification_basis_paths=certification_basis_paths,
        )
    if not isinstance(graph, SkillBlueprintGraph):
        raise ArtifactHealthError("pre-v4 node hashing requires a skill blueprint graph")
    if (
        policy_hash is None
        or schema_hash is None
        or schema_root is None
        or certifier is None
    ):
        raise ArtifactHealthError(
            "pre-v4 node hashing requires policy_hash, schema_hash, schema_root, and certifier"
        )
    selected_checks_by_node = checks_by_node or {}

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
        checks = normalize_node_checks(selected_checks_by_node.get(node_id, ()))
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

    for node_id in health_node_ids(graph):
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
    if node.embedded:
        raise ArtifactHealthError(
            f"{node_id}: embedded default interface is certified with its skill root"
        )
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
    for node_id in health_node_ids(graph):
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
    certified_node_ids = health_node_ids(graph)
    admission_concerns: dict[str, list[str]] = {
        node_id: [] for node_id in certified_node_ids
    }
    for node_id in certified_node_ids:
        node = graph.nodes[node_id]
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
    for node_id in certified_node_ids:
        check(node_id)
    return GraphHealthReport(graph.root.node_id, root_status.healthy, statuses)


def node_requires_refresh(status: NodeHealthStatus) -> bool:
    """Return whether a node's own health record must be replaced."""

    return any(concern in _REFRESH_CONCERNS for concern in status.concerns)


def health_path_for_node(node: BlueprintNode) -> Path:
    """Return the generated health sidecar path for a graph node."""

    if node.embedded:
        raise ArtifactHealthError(
            f"{node.node_id}: embedded default interface has no separate health path"
        )
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

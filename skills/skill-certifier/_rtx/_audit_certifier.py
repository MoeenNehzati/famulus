#!/usr/bin/env python3
"""Audit-record certifier for blueprint-backed installed skills."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

from officina.common.artifact_health import (
    CANONICAL_GRAPH_SCHEMA_INPUTS,
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_CHECK_REGISTRY,
    POOLED_REVIEW_SCHEMA_INPUTS,
    ArtifactHealthError,
    GraphHealthReport,
    NodeHashState,
    NodeHealthStatus,
    blueprint_schema_hash,
    build_node_health_record,
    check_graph_health,
    compute_node_hash_states,
    compute_certification_basis_hash,
    derive_certifier_identity,
    expected_certifier_checks,
    health_edges,
    health_node_ids,
    health_path_for_node,
    health_postorder_node_ids,
    local_input_paths_for_node,
    node_requires_refresh,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from officina.common.audit_records import (
    attach_record_digest,
    certificate_public_key_root,
    load_or_create_hmac_key,
    record_digest_matches,
    canonical_certificate_envelope_bytes,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    sign_certificate_payload,
)
from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_compare_and_append_bytes,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from officina.common.blueprint_graph import (
    RepositoryBlueprintGraph,
    SkillBlueprintGraph,
    load_repository_blueprint_graph,
    load_validated_skill_blueprint_graph,
)
from officina.common.certification_view import (
    certificate_log_path,
    evaluate_certificate_currentness,
)
from officina.common.git_provenance import (
    BLUEPRINT_V4_MECHANICAL_REF,
    BLUEPRINT_V4_SOURCE_OVERLAY_REF,
    GitMaterializationError,
    GitSnapshot,
    blueprint_v4_mechanical_commit,
    blueprint_v4_source_overlay_commit,
    capture_git_snapshot,
    check_commit_readiness,
    materialize_git_commit,
    run_git,
    snapshot_head_matches,
)
from officina.common.pooled_blueprint import (
    certify_pooled_review,
    check_pooled_review,
    pooled_review_health_path,
    pooled_review_path,
    render_pooled_review,
)
from officina.runtime.python_machine_interface import (
    DispatchCall,
    PythonArgvMachineInterface,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_RECORD_NAME = ".last_audit.json"
OUTPUT_SCHEMA_VERSION = 1
TEXT_FILE_SUFFIXES = {".md", ".markdown", ".py", ".txt", ".yaml", ".yml", ".json"}
REQUIRED_SCHEMA_INPUTS = (
    *CANONICAL_GRAPH_SCHEMA_INPUTS,
    *POOLED_REVIEW_SCHEMA_INPUTS,
)
POOL_STATUSES = frozenset({"reused", "written", "not-written", "failed"})
IMPLICIT_DIRECTORY_PATTERNS = (
    re.compile(r"\b(?:look|scan|search|inspect|read)\s+under\s+([A-Za-z0-9_./\\-]+)", re.IGNORECASE),
    re.compile(r"\b(?:executables|scripts|tools|helpers|modules)\s+under\s+([A-Za-z0-9_./\\-]+)", re.IGNORECASE),
    re.compile(r"\b([A-Za-z0-9_./\\-]+)\s+(?:directory|folder)\s+for\s+(?:executables|scripts|tools|helpers|modules)", re.IGNORECASE),
)
GENERATED_BLOCK_RE = re.compile(
    r"<!-- BEGIN BLUEPRINT (?:CONTRACT|INTERFACES) -->.*?<!-- END BLUEPRINT (?:CONTRACT|INTERFACES) -->",
    re.DOTALL,
)
COMMAND_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:`{0,3})\s*"
    r"(?:python3?|bash|sh|pytest|npm|node|dispatcher|\./|/[^/\s]+/|_rtx/|scripts/)\b"
)
EXECUTION_VERB_RE = re.compile(
    r"\b(?:run|execute|invoke|launch|shell out to|call)\b.*"
    r"(?:`[^`]*(?:python3?|bash|sh|pytest|npm|node|dispatcher|_rtx/|scripts/|\./)[^`]*`|"
    r"\b(?:python3?|bash|sh|pytest|npm|node|dispatcher)\b|_rtx/|scripts/|\./)",
    re.IGNORECASE,
)
IMPLEMENTATION_PATH_RE = re.compile(r"(?:^|[`'\"\s(])(?:_rtx/|scripts/)[A-Za-z0-9_./\\-]+")


class AuditError(RuntimeError):
    """Raised when certification cannot safely continue."""


@dataclass(frozen=True)
class V4CertificationResult:
    """Private Task-3 result for converted temporary repositories."""

    node_ids: tuple[str, ...]
    source_commit: str


@dataclass(frozen=True)
class V4GateSnapshot:
    """Exact derived node/content snapshot presented to certifier-owned gates."""

    node_id: str
    node_hash: str
    source_commit: str
    input_manifest: tuple[Mapping[str, object], ...]
    dependencies: tuple[Mapping[str, object], ...]
    certification_basis_hash: str
    certifier_identity: Mapping[str, object]


@dataclass(frozen=True)
class V4CompletenessFinding:
    """One certifier-owned semantic presence requirement for a v4 draft."""

    subject_id: str
    blueprint_path: Path
    field: str
    message: str


V4_REQUIRED_CONTRACT_SECTIONS = (
    "arguments",
    "preconditions",
    "interaction",
    "caller_warnings",
    "outputs",
    "outcomes",
    "execution",
    "helpers",
    "direct_io",
)


def v4_certification_completeness_findings(
    graph: RepositoryBlueprintGraph,
) -> tuple[V4CompletenessFinding, ...]:
    """Return deterministic semantic omissions that prohibit v4 signing."""

    findings: list[V4CompletenessFinding] = []
    for node_id, node in sorted(graph.nodes.items()):
        description = node.declaration.get("description")
        if not isinstance(description, str) or not description.strip():
            findings.append(
                V4CompletenessFinding(
                    node_id,
                    node.blueprint_path,
                    "description",
                    "module and behavioral-source descriptions are mandatory before signing",
                )
            )
        if node.node_type != "behavioral_source":
            continue
        interfaces = node.declaration.get("interfaces", {})
        if not isinstance(interfaces, Mapping):
            continue
        for interface_id, interface in sorted(interfaces.items()):
            if not isinstance(interface_id, str) or not isinstance(interface, Mapping):
                continue
            interface_description = interface.get("description")
            if (
                not isinstance(interface_description, str)
                or not interface_description.strip()
            ):
                findings.append(
                    V4CompletenessFinding(
                        interface_id,
                        node.blueprint_path,
                        "description",
                        "interface description is mandatory before signing",
                    )
                )
            contract = interface.get("contract")
            for section in V4_REQUIRED_CONTRACT_SECTIONS:
                if not isinstance(contract, Mapping) or section not in contract:
                    findings.append(
                        V4CompletenessFinding(
                            interface_id,
                            node.blueprint_path,
                            f"contract.{section}",
                            f"contract section {section} is mandatory before signing",
                        )
                    )
            if isinstance(contract, Mapping):
                execution = contract.get("execution")
                verification = (
                    execution.get("verification")
                    if isinstance(execution, Mapping)
                    else None
                )
                if isinstance(execution, Mapping) and (
                    not isinstance(verification, list) or not verification
                ):
                    findings.append(
                        V4CompletenessFinding(
                            interface_id,
                            node.blueprint_path,
                            "contract.execution.verification",
                            "final execution verification must be nonempty before signing",
                        )
                    )
                direct_io = contract.get("direct_io")
                network = (
                    direct_io.get("network")
                    if isinstance(direct_io, Mapping)
                    else None
                )
                if isinstance(network, list):
                    for index, entry in enumerate(network):
                        if isinstance(entry, Mapping) and not (
                            isinstance(entry.get("endpoint"), str)
                            and entry["endpoint"].strip()
                        ):
                            findings.append(
                                V4CompletenessFinding(
                                    interface_id,
                                    node.blueprint_path,
                                    f"contract.direct_io.network[{index}].endpoint",
                                    "network endpoint must be complete before signing",
                                )
                            )
    return tuple(findings)


def v4_protected_projection(
    graph: RepositoryBlueprintGraph,
) -> dict[str, object]:
    """Project every migration fact that a semantic repair may not change."""

    projected: dict[str, object] = {}
    for node_id, node in sorted(graph.nodes.items()):
        declaration = node.declaration
        record: dict[str, object] = {
            "node_type": node.node_type,
            "version": node.version,
            "gateway": deepcopy(declaration.get("gateway")),
            "content": deepcopy(declaration.get("content")),
        }
        if node.node_type == "module":
            record.update(
                {
                    "authority": deepcopy(declaration.get("authority")),
                    "sources": deepcopy(declaration.get("sources")),
                    "exports": deepcopy(declaration.get("exports")),
                    "discovery": deepcopy(declaration.get("discovery")),
                }
            )
        else:
            interfaces = declaration.get("interfaces", {})
            record.update(
                {
                    "dependencies": deepcopy(declaration.get("dependencies")),
                    "uses_interfaces": deepcopy(
                        declaration.get("uses_interfaces")
                    ),
                    "platform_support": deepcopy(
                        declaration.get("platform_support")
                    ),
                    "runtime_dependencies": deepcopy(
                        declaration.get("runtime_dependencies")
                    ),
                    "interfaces": {
                        interface_id: {
                            "version": interface.get("version"),
                            "process_binding": deepcopy(
                                interface.get("process_binding")
                            ),
                        }
                        for interface_id, interface in sorted(interfaces.items())
                        if isinstance(interface_id, str)
                        and isinstance(interface, Mapping)
                    },
                }
            )
        projected[node_id] = record
    helper_edges = tuple(
        {
            "source_export_id": edge.source_export_id,
            "local_helper_id": edge.local_helper_id,
            "target_interface_id": edge.target_interface_id,
            "target_version": edge.target_version,
            "binding": deepcopy(edge.binding),
        }
        for edge in sorted(
            graph.helper_edges,
            key=lambda item: (
                item.source_export_id,
                item.local_helper_id,
                item.target_interface_id,
                item.target_version,
            ),
        )
    )
    return {"nodes": projected, "helper_edges": helper_edges}


@dataclass(frozen=True)
class V4CandidateInspection:
    """Read-only semantic-completeness report for one committed candidate."""

    node_ids: tuple[str, ...]
    source_commit: str
    findings: tuple[V4CompletenessFinding, ...]
    review_context: tuple["V4LegacyReviewContext", ...]
    reconciliation_digest: str


@dataclass(frozen=True)
class V4LegacyReviewContext:
    """One immutable legacy claim supplied as non-blocking review evidence."""

    subject_id: str
    blueprint_path: Path
    field: str
    message: str
    target_id: str
    claim: str


class _EphemeralSecretBackend:
    name = "ephemeral-v4-candidate"

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        self._values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        return self._values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self._values.pop((namespace, key), None) is not None


def _v4_hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _v4_postorder(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    requested: Sequence[str],
) -> tuple[str, ...]:
    """Order exact targets after every dependency in canonical hash state."""

    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            raise AuditError(f"missing canonical v4 state for {node_id}")
        for dependency in state.dependency_hashes:
            target = dependency.get("target")
            if not isinstance(target, str) or target not in graph.nodes:
                raise AuditError(f"invalid canonical v4 dependency for {node_id}")
            visit(target)
        ordered.append(node_id)

    for node_id in requested:
        if node_id not in graph.nodes:
            raise AuditError(f"unknown exact v4 certification target: {node_id}")
        visit(node_id)
    return tuple(ordered)


def _v4_payload(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, object],
    node_id: str,
    *,
    source_commit: str,
    key_id: str,
    previous_entry_hash: str | None,
    certifier_identity: Mapping[str, object],
    checks: Sequence[Mapping[str, object]],
    certified_at: str,
) -> dict[str, object]:
    node = graph.nodes[node_id]
    state = states[node_id]
    if node.gateway_path is None:
        raise AuditError(f"{node_id}: certificate subject requires a gateway path")
    return {
        "certificate_schema_version": 1,
        "subject": {
            "id": node.node_id,
            "node_type": node.node_type,
            "version": node.version,
            "blueprint_path": node.blueprint_path.relative_to(repo_root).as_posix(),
            "gateway_path": node.gateway_path.relative_to(repo_root).as_posix(),
        },
        "node_hash": state.node_hash,
        "source_commit": source_commit,
        "input_manifest": [dict(entry) for entry in state.input_manifest],
        "dependencies": [dict(entry) for entry in state.dependency_hashes],
        "certification_basis_hash": state.certification_basis_hash,
        "certifier": dict(certifier_identity),
        "checks": [dict(check) for check in checks],
        "key_id": key_id,
        "previous_entry_hash": previous_entry_hash,
        "certified_at": certified_at,
    }


def _v4_gate_snapshot(
    node_id: str,
    state: object,
    *,
    source_commit: str,
    certifier_identity: Mapping[str, object],
) -> V4GateSnapshot:
    node_hash = getattr(state, "node_hash", None)
    basis_hash = getattr(state, "certification_basis_hash", None)
    if not isinstance(node_hash, str) or not isinstance(basis_hash, str):
        raise AuditError(f"{node_id}: canonical gate snapshot is unavailable")
    return V4GateSnapshot(
        node_id=node_id,
        node_hash=node_hash,
        source_commit=source_commit,
        input_manifest=tuple(
            dict(entry) for entry in getattr(state, "input_manifest", ())
        ),
        dependencies=tuple(
            dict(entry) for entry in getattr(state, "dependency_hashes", ())
        ),
        certification_basis_hash=basis_hash,
        certifier_identity=dict(certifier_identity),
    )


def _passed_v4_check(gate_name: str) -> dict[str, object]:
    try:
        check_id, version = CERTIFIER_CHECK_REGISTRY[gate_name]
    except KeyError as exc:
        raise AuditError(f"{gate_name} gate is unavailable") from exc
    return {
        "id": check_id,
        "version": version,
        "passed": True,
        "findings": [],
    }


def _v4_deterministic_check(
    snapshot: V4GateSnapshot,
    *,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
) -> dict[str, object]:
    """Assert that the owned derived state is exactly the state being signed."""

    node = graph.nodes.get(snapshot.node_id)
    state = states.get(snapshot.node_id)
    if node is None or not isinstance(state, NodeHashState):
        raise AuditError(f"{snapshot.node_id}: deterministic state is unavailable")
    reconstructed = _v4_gate_snapshot(
        snapshot.node_id,
        state,
        source_commit=snapshot.source_commit,
        certifier_identity=snapshot.certifier_identity,
    )
    if reconstructed != snapshot:
        raise AuditError(f"{snapshot.node_id}: deterministic snapshot changed")
    if any(
        finding.subject_id in {node.node_id, *node.declaration.get("interfaces", {})}
        for finding in v4_certification_completeness_findings(graph)
    ):
        raise AuditError(f"{snapshot.node_id}: deterministic completeness failed")
    return _passed_v4_check("deterministic")


def _v4_semantic_attestation(
    snapshot: V4GateSnapshot,
    *,
    reviewed_commit: str,
) -> dict[str, object]:
    """Record that the LLM attested this exact committed snapshot."""

    if not reviewed_commit or snapshot.source_commit != reviewed_commit:
        raise AuditError(f"{snapshot.node_id}: semantic review does not match HEAD")
    return _passed_v4_check("semantic-audit")


def _v4_blueprint_paths(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
) -> set[Path]:
    try:
        return {
            node.blueprint_path.relative_to(repo_root)
            for node in graph.nodes.values()
        }
    except ValueError as exc:
        raise AuditError("v4 blueprint path escapes its repository") from exc


def _materialize_v4_local_inputs(
    source_root: Path,
    target_root: Path,
    states: Mapping[str, NodeHashState],
    *,
    allow_non_atomic: bool,
) -> None:
    """Overlay exact ignored/untracked node inputs needed to load the graph."""

    relative_paths = {
        Path(entry["path"])
        for state in states.values()
        for entry in state.input_manifest
        if entry.get("git_provenance") != "tracked"
        and isinstance(entry.get("path"), str)
    }
    for relative in sorted(relative_paths):
        if relative.is_absolute() or ".." in relative.parts:
            raise AuditError("local v4 input escapes the candidate repository")
        current = target_root
        for part in relative.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise AuditError(f"unsafe local v4 input parent: {relative.as_posix()}")
        target = target_root / relative
        if target.exists() or target.is_symlink():
            raise AuditError(f"local v4 input collides with commit: {relative.as_posix()}")
        try:
            data = read_regular_file_bytes(
                source_root / relative,
                allowed_root=source_root,
                allow_non_atomic=allow_non_atomic,
            )
            atomic_replace_bytes(
                target,
                data,
                allowed_root=target_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            )
        except (AtomicWriteError, OSError) as exc:
            raise AuditError(
                f"cannot materialize local v4 input: {relative.as_posix()}"
            ) from exc


def _validate_v4_semantic_attestation(
    repo_root: Path,
    reviewed_graph: RepositoryBlueprintGraph,
    reviewed_states: Mapping[str, NodeHashState],
    *,
    mechanical_commit: str,
    reviewed_commit: str,
    allow_non_atomic: bool,
) -> None:
    """Prove that LLM review changed semantics but no protected mechanics."""

    with tempfile.TemporaryDirectory(prefix="v4-mechanical-commit-") as raw_root:
        mechanical_root = Path(raw_root)
        try:
            materialize_git_commit(
                repo_root,
                mechanical_commit,
                mechanical_root,
                allow_non_atomic=allow_non_atomic,
            )
            _materialize_v4_local_inputs(
                repo_root,
                mechanical_root,
                reviewed_states,
                allow_non_atomic=allow_non_atomic,
            )
            mechanical_graph = load_repository_blueprint_graph(
                mechanical_root,
                schema_root=mechanical_root / "references" / "blueprint",
            )
        except (GitMaterializationError, ArtifactHealthError, OSError, ValueError) as exc:
            raise AuditError(f"mechanical commit cannot be reconstructed: {exc}") from exc
        if not mechanical_graph.nodes or any(
            node.declaration.get("schema_version") != 4
            for node in mechanical_graph.nodes.values()
        ):
            raise AuditError("mechanical commit does not contain an all-v4 graph")
        ancestry = run_git(
            repo_root,
            "merge-base",
            "--is-ancestor",
            mechanical_commit,
            reviewed_commit,
            check=False,
        )
        if ancestry.returncode != 0:
            raise AuditError("mechanical commit is not an ancestor of reviewed commit")
        changed = run_git(
            repo_root,
            "diff",
            "--name-only",
            "--no-renames",
            "--no-ext-diff",
            "-z",
            mechanical_commit,
            reviewed_commit,
            "--",
            check=False,
        )
        if changed.returncode != 0:
            raise AuditError("cannot compare mechanical and reviewed commits")
        changed_paths = {
            Path(os.fsdecode(raw_path))
            for raw_path in changed.stdout.rstrip(b"\0").split(b"\0")
            if raw_path
        }
        allowed_paths = _v4_blueprint_paths(
            mechanical_graph, mechanical_root
        ) | _v4_blueprint_paths(reviewed_graph, repo_root)
        unexpected = sorted(changed_paths - allowed_paths)
        if unexpected:
            raise AuditError(
                "semantic review may change only blueprint files: "
                + ", ".join(path.as_posix() for path in unexpected)
            )
        if v4_protected_projection(mechanical_graph) != v4_protected_projection(
            reviewed_graph
        ):
            raise AuditError("semantic review changed the protected projection")


def _verify_executing_candidate_certifier(
    root: Path,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
) -> None:
    executing = Path(__file__).resolve()
    try:
        executing_relative = executing.relative_to(root).as_posix()
    except ValueError as exc:
        raise AuditError("executing certifier bytes are outside the candidate") from exc
    owners = [
        node_id
        for node_id, node in graph.nodes.items()
        if node.node_type == "behavioral_source"
        and node.gateway_path is not None
        and node.gateway_path.resolve() == executing
    ]
    if len(owners) != 1:
        raise AuditError("executing certifier bytes have no unique candidate owner")
    executing_digest = "sha256:" + hashlib.sha256(executing.read_bytes()).hexdigest()
    owner_state = states.get(owners[0])
    if not isinstance(owner_state, NodeHashState) or not any(
        entry.get("path") == executing_relative
        and entry.get("digest") == executing_digest
        for entry in owner_state.input_manifest
    ):
        raise AuditError("executing certifier bytes do not match the candidate manifest")


def _certify_v4_repository(
    repo_root: Path,
    *,
    target_node_ids: Sequence[str],
    public_key_root: Path,
    secret_backend: object,
    reviewed_commit: str,
    certified_at: str,
    before_append: object | None = None,
    after_append: object | None = None,
    allow_non_atomic: bool = False,
    require_candidate_execution: bool = False,
    require_migration_review: bool = True,
) -> V4CertificationResult:
    """Certify exact v4 targets from one committed repository snapshot.

    Migration candidates additionally require the reserved mechanical baseline
    and protected semantic-review transition. The live v4 route uses the same
    writer after the repository cutover without replaying that migration-only
    transition.
    """

    if allow_non_atomic:
        raise AuditError("non-atomic mode is diagnostic-only and cannot sign")

    root = Path(repo_root).resolve()
    if require_migration_review:
        atomic = run_git(
            root, "config", "--bool", "--get", "famulus.candidateAtomicGuarantee",
            check=False,
        )
        if atomic.returncode == 0 and atomic.stdout.strip() == b"false":
            raise AuditError("non-atomic diagnostic candidate is non-certifiable")
        temp_root = Path(tempfile.gettempdir()).resolve()
        if not root.is_relative_to(temp_root):
            raise AuditError(
                "private v4 certification is restricted to temporary repositories"
            )
    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        raise AuditError("v4 certification requires the exact Git repository root")
    if snapshot.commit != reviewed_commit:
        raise AuditError("v4 certification HEAD does not match the reviewed commit")
    mechanical_commit: str | None = None
    if require_migration_review:
        try:
            mechanical_commit = blueprint_v4_mechanical_commit(root)
        except GitMaterializationError as exc:
            raise AuditError(
                f"candidate mechanical baseline is unavailable: {exc}"
            ) from exc
    selected_schema_root = root / "references" / "blueprint"
    policy_path = root / CANONICAL_NODE_HASH_POLICY

    def derive() -> tuple[
        RepositoryBlueprintGraph,
        dict[str, NodeHashState],
        str,
        tuple[Path, ...],
        dict[str, object],
    ]:
        try:
            graph = load_repository_blueprint_graph(root, schema_root=selected_schema_root)
            if not graph.nodes or any(
                node.declaration.get("schema_version") != 4
                for node in graph.nodes.values()
            ):
                raise AuditError(
                    "private certificate writer accepts only all-v4 repositories"
                )
            completeness = v4_certification_completeness_findings(graph)
            if completeness:
                first = completeness[0]
                raise AuditError(
                    "v4 certification completeness failed: "
                    f"{first.subject_id}:{first.field} "
                    f"({len(completeness)} finding(s))"
                )
            basis_paths = resolve_certification_basis_paths(
                root,
                allow_non_atomic=allow_non_atomic,
            )
            basis_hash = compute_certification_basis_hash(
                root,
                allow_non_atomic=allow_non_atomic,
            )
            states = compute_node_hash_states(
                graph,
                repo_root=root,
                policy_path=policy_path,
                certification_basis_hash=basis_hash,
                certification_basis_paths=basis_paths,
                allow_non_atomic=allow_non_atomic,
            )
            certifier_identity = derive_certifier_identity(
                graph, states, snapshot.commit
            )
            if require_candidate_execution:
                _verify_executing_candidate_certifier(root, graph, states)
        except ArtifactHealthError as exc:
            raise AuditError(str(exc)) from exc
        return graph, states, basis_hash, basis_paths, certifier_identity

    graph, states, basis_hash, basis_paths, certifier_identity = derive()
    if mechanical_commit is not None:
        _validate_v4_semantic_attestation(
            root,
            graph,
            states,
            mechanical_commit=mechanical_commit,
            reviewed_commit=reviewed_commit,
            allow_non_atomic=allow_non_atomic,
        )
    order = _v4_postorder(graph, states, tuple(target_node_ids))
    normalized_checks: dict[str, tuple[dict[str, object], ...]] = {}

    tracked_paths: set[Path] = {
        *basis_paths,
        *(node.blueprint_path for node in graph.nodes.values()),
    }
    local_claims: dict[str, str] = {}
    for state in states.values():
        for entry in state.input_manifest:
            path = root / entry["path"]
            if entry["git_provenance"] == "tracked":
                tracked_paths.add(path)
            else:
                local_claims[entry["path"]] = entry["digest"]
    ordered_tracked_paths = tuple(sorted(tracked_paths))

    def require_commit_readiness(current_snapshot: object, phase: str) -> None:
        readiness = check_commit_readiness(
            current_snapshot,
            ordered_tracked_paths,
            _expected_file_hashes(current_snapshot, ordered_tracked_paths),
            allow_non_atomic=allow_non_atomic,
        )
        if not readiness.stamp_worthy:
            raise AuditError(
                f"tracked certification input changed {phase}: "
                + ",".join(readiness.reasons)
            )

    def require_local_claims(phase: str) -> None:
        if any(
            _v4_hash_bytes(
                read_regular_file_bytes(
                    root / path,
                    allowed_root=root,
                    allow_non_atomic=allow_non_atomic,
                )
            )
            != digest
            for path, digest in local_claims.items()
        ):
            raise AuditError(f"local input changed {phase}")

    require_commit_readiness(snapshot, "before certification")
    require_local_claims("before certification")
    tracked_claims: dict[Path, tuple[str, bool]] = {}
    for path in ordered_tracked_paths:
        try:
            metadata = path.lstat()
            payload = read_regular_file_bytes(
                path,
                allowed_root=root,
                allow_non_atomic=allow_non_atomic,
            )
        except (AtomicWriteError, OSError) as exc:
            raise AuditError(f"tracked certification input is unavailable: {path}") from exc
        tracked_claims[path] = (
            _v4_hash_bytes(payload),
            bool(metadata.st_mode & stat.S_IXUSR),
        )
    try:
        public_key_relative = Path(
            os.path.abspath(public_key_root)
        ).relative_to(root)
    except ValueError as exc:
        raise AuditError("certificate public-key root is outside repository") from exc

    def require_frozen_tracked_inputs(phase: str) -> None:
        status = run_git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            check=False,
        )
        if status.returncode != 0:
            raise AuditError(f"repository status is unavailable {phase}")
        for record in status.stdout.rstrip(b"\0").split(b"\0"):
            if not record:
                continue
            if not record.startswith(b"?? "):
                raise AuditError(f"tracked repository state changed {phase}")
            try:
                relative = Path(os.fsdecode(record[3:]))
            except UnicodeError as exc:
                raise AuditError(f"untracked repository state changed {phase}") from exc
            if relative.is_relative_to(public_key_relative) or (
                ".certificates" in relative.parts
                and relative.suffix == ".jsonl"
            ) or (
                relative.as_posix() in local_claims
            ):
                continue
            raise AuditError(
                f"untracked repository state changed {phase}: {relative}"
            )
        index = run_git(
            root,
            "diff-index",
            "--cached",
            "--quiet",
            snapshot.commit,
            "--",
            check=False,
        )
        if index.returncode != 0:
            raise AuditError(f"tracked certification index changed {phase}")
        for path, (expected_digest, expected_executable) in tracked_claims.items():
            try:
                metadata = path.lstat()
                payload = read_regular_file_bytes(
                    path,
                    allowed_root=root,
                    allow_non_atomic=allow_non_atomic,
                )
            except (AtomicWriteError, OSError) as exc:
                raise AuditError(
                    f"tracked certification input changed {phase}: {path}"
                ) from exc
            if (
                _v4_hash_bytes(payload) != expected_digest
                or bool(metadata.st_mode & stat.S_IXUSR) != expected_executable
            ):
                raise AuditError(
                    f"tracked certification input changed {phase}: {path}"
                )

    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    written: list[str] = []
    for node_id in order:
        log_path = certificate_log_path(graph.nodes[node_id])
        certificate_root = log_path.parent
        if not certificate_root.exists():
            certificate_root.mkdir(mode=0o700)
        try:
            metadata = certificate_root.lstat()
            certificate_root.resolve().relative_to(graph.nodes[node_id].skill_root.resolve())
        except (OSError, ValueError) as exc:
            raise AuditError(f"unsafe certificate output root: {certificate_root}") from exc
        if certificate_root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise AuditError(f"unsafe certificate output root: {certificate_root}")
        old_bytes: bytes | None = None
        previous_hash = None
        if log_path.exists():
            old_bytes = read_regular_file_bytes(
                log_path,
                allowed_root=graph.nodes[node_id].skill_root,
                allow_non_atomic=allow_non_atomic,
            )
            previous_entries = parse_certificate_log(
                old_bytes,
                public_key_root,
                require_active_final=False,
                allow_non_atomic=allow_non_atomic,
            )
            previous_hash = certificate_entry_hash(previous_entries[-1])
        gate_snapshot = _v4_gate_snapshot(
            node_id,
            states[node_id],
            source_commit=snapshot.commit,
            certifier_identity=certifier_identity,
        )
        gate_records = (
            _v4_deterministic_check(gate_snapshot, graph=graph, states=states),
            _v4_semantic_attestation(
                gate_snapshot,
                reviewed_commit=reviewed_commit,
            ),
        )
        normalized_checks[node_id] = normalize_node_checks(gate_records)
        if normalized_checks[node_id] != expected_certifier_checks():
            raise AuditError(f"{node_id}: certifier gate registry changed")
        if callable(before_append):
            before_append(node_id)
        if not snapshot_head_matches(snapshot):
            raise AuditError("HEAD changed during certification")
        require_frozen_tracked_inputs("before certificate append")
        require_local_claims("during certification")
        if log_path.exists():
            if (
                old_bytes is None
                or read_regular_file_bytes(
                    log_path,
                    allowed_root=graph.nodes[node_id].skill_root,
                    allow_non_atomic=allow_non_atomic,
                )
                != old_bytes
            ):
                raise AuditError("certificate log changed during certification")
        elif old_bytes is not None:
            raise AuditError("certificate log changed during certification")
        payload = _v4_payload(
            root,
            graph,
            states,
            node_id,
            source_commit=snapshot.commit,
            key_id=key.key_id,
            previous_entry_hash=previous_hash,
            certifier_identity=certifier_identity,
            checks=normalized_checks[node_id],
            certified_at=certified_at,
        )
        envelope = sign_certificate_payload(payload, key)
        frame = canonical_certificate_envelope_bytes(envelope) + b"\n"
        try:
            atomic_compare_and_append_bytes(
                log_path,
                frame,
                expected_previous_bytes=old_bytes,
                allowed_root=graph.nodes[node_id].skill_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            )
        except AtomicWriteError as exc:
            raise AuditError("certificate log changed during certification") from exc
        try:
            appended_metadata = log_path.lstat()
        except OSError as exc:
            raise AuditError("post-write certificate log is unavailable") from exc
        if not stat.S_ISREG(appended_metadata.st_mode):
            raise AuditError("post-write certificate log is not a regular file")
        if callable(after_append):
            after_append(node_id)
        final_snapshot = capture_git_snapshot(root)
        if (
            final_snapshot is None
            or final_snapshot.repo_root != root
            or final_snapshot.commit != snapshot.commit
        ):
            raise AuditError("HEAD changed after certificate append")
        if normalized_checks[node_id] != expected_certifier_checks():
            raise AuditError("certifier checks changed after certificate append")
        require_frozen_tracked_inputs("after certificate append")
        require_local_claims("after certificate append")
        try:
            final_metadata = log_path.lstat()
        except OSError as exc:
            raise AuditError("post-write certificate log changed") from exc
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (appended_metadata.st_dev, appended_metadata.st_ino)
        ):
            raise AuditError("post-write certificate log changed")
        expected_log_bytes = (old_bytes or b"") + frame
        if (
            read_regular_file_bytes(
                log_path,
                allowed_root=graph.nodes[node_id].skill_root,
                allow_non_atomic=allow_non_atomic,
            )
            != expected_log_bytes
        ):
            raise AuditError("post-write certificate log changed")
        written.append(node_id)

    final_snapshot = capture_git_snapshot(root)
    if (
        final_snapshot is None
        or final_snapshot.repo_root != root
        or final_snapshot.commit != snapshot.commit
    ):
        raise AuditError("HEAD changed after certification")
    require_commit_readiness(final_snapshot, "after certification")
    require_local_claims("after certification")
    (
        final_graph,
        final_states,
        final_basis_hash,
        final_basis_paths,
        final_certifier_identity,
    ) = derive()
    if (
        final_graph != graph
        or final_states != states
        or final_basis_hash != basis_hash
        or final_basis_paths != basis_paths
        or final_certifier_identity != certifier_identity
    ):
        raise AuditError(
            "graph, dependency, basis, or local input changed during certification"
        )
    final_report = evaluate_certificate_currentness(
        final_graph,
        final_states,
        repo_root=root,
        public_key_root=public_key_root,
        source_commit=final_snapshot.commit,
        certifier_identity=final_certifier_identity,
        checks_by_node=normalized_checks,
        schema_root=selected_schema_root,
        allow_non_atomic=allow_non_atomic,
    )
    for node_id in written:
        if not final_report.nodes[node_id].current:
            raise AuditError(
                f"post-write certificate verification failed for {node_id}"
            )
    return V4CertificationResult(tuple(written), snapshot.commit)


def _load_v4_migration_candidate(
    repo_root: Path,
) -> tuple[Path, GitSnapshot, RepositoryBlueprintGraph]:
    root = Path(repo_root).resolve()
    atomic = run_git(
        root, "config", "--bool", "--get", "famulus.candidateAtomicGuarantee",
        check=False,
    )
    if atomic.returncode == 0 and atomic.stdout.strip() == b"false":
        raise AuditError("non-atomic diagnostic candidate is non-certifiable")
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not root.is_relative_to(temp_root):
        raise AuditError("v4 migration workflow is restricted to temporary repositories")
    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        raise AuditError("v4 migration workflow requires an isolated Git repository")
    dirty = run_git(root, "status", "--porcelain=v1", "-z", check=False)
    if dirty.returncode != 0 or dirty.stdout:
        raise AuditError("v4 migration candidate must be clean")
    schema_root = root / "references" / "blueprint"
    try:
        graph = load_repository_blueprint_graph(root, schema_root=schema_root)
    except Exception as exc:
        raise AuditError(f"v4 migration candidate graph is invalid: {exc}") from exc
    if not graph.nodes or any(
        node.declaration.get("schema_version") != 4 for node in graph.nodes.values()
    ):
        raise AuditError("v4 migration workflow requires an all-v4 repository")
    return root, snapshot, graph


def _v4_module_renames(root: Path) -> dict[str, str]:
    try:
        migration_map = yaml.safe_load(
            (root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
                encoding="utf-8"
            )
        )
        decisions = migration_map["declarations"]["version_2"]["merge_decisions"]
    except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise AuditError("candidate migration map cannot derive claim targets") from exc
    renames: dict[str, str] = {}
    for decision in decisions:
        if not isinstance(decision, Mapping) or "target_module" not in decision:
            continue
        inputs = decision.get("inputs")
        target = decision.get("target_module")
        owners = {
            Path(value).parts[1]
            for value in inputs or ()
            if isinstance(value, str)
            and len(Path(value).parts) >= 3
            and Path(value).parts[0] == "skills"
        }
        if len(owners) != 1 or not isinstance(target, str):
            raise AuditError("candidate migration map has ambiguous claim target")
        renames[owners.pop()] = target
    return renames


def _v4_legacy_review_context(
    root: Path, graph: RepositoryBlueprintGraph
) -> tuple[V4LegacyReviewContext, ...]:
    """Read immutable legacy claims without treating them as failed checks."""

    context: list[V4LegacyReviewContext] = []
    try:
        root_commit = blueprint_v4_source_overlay_commit(root)
    except GitMaterializationError as exc:
        raise AuditError("candidate authorized source overlay is unavailable") from exc
    renames = _v4_module_renames(root)
    tree = run_git(
        root, "ls-tree", "-r", "--name-only", "-z", root_commit, check=False
    )
    if tree.returncode != 0:
        raise AuditError("candidate legacy root tree is unavailable")
    for raw_path in tree.stdout.rstrip(b"\0").split(b"\0"):
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        if (
            len(relative.parts) != 3
            or relative.parts[0] != "skills"
            or relative.name != "blueprint.yaml"
        ):
            continue
        shown = run_git(
            root,
            "show",
            f"{root_commit}:{relative.as_posix()}",
            check=False,
        )
        if shown.returncode != 0:
            raise AuditError(
                f"candidate legacy evidence is unavailable: {relative.as_posix()}"
            )
        try:
            document = yaml.safe_load(shown.stdout.decode("utf-8"))
        except (UnicodeError, yaml.YAMLError) as exc:
            raise AuditError(
                f"candidate legacy evidence is invalid: {relative.as_posix()}"
            ) from exc
        summary = document.get("skill_interface") if isinstance(document, Mapping) else None
        if summary is None:
            continue
        if not isinstance(summary, Mapping):
            raise AuditError(
                f"candidate legacy skill_interface is invalid: {relative.as_posix()}"
            )
        subject = relative.parts[1]
        target_id = f"{renames.get(subject, subject)}.interface.default"
        owners = [
            (node.node_id, export.get("source_interface"))
            for node in graph.nodes.values()
            if node.node_type == "module"
            and isinstance(
                export := node.declaration.get("exports", {}).get(target_id),
                Mapping,
            )
        ]
        if len(owners) != 1 or not isinstance(owners[0][1], str):
            raise AuditError(
                f"legacy skill_interface has no unique default target: {subject}"
            )
        for section in ("inputs", "outputs", "side_effects"):
            claims = summary.get(section, [])
            if not isinstance(claims, list) or not all(
                isinstance(claim, str) for claim in claims
            ):
                raise AuditError(
                    f"candidate legacy skill_interface.{section} is invalid: "
                    f"{relative.as_posix()}"
                )
            for index, claim in enumerate(claims):
                context.append(
                    V4LegacyReviewContext(
                        subject,
                        root / relative,
                        f"legacy.skill_interface.{section}[{index}]",
                        "review exact immutable legacy claim against "
                        f"{target_id}: {claim}",
                        target_id,
                        claim,
                    )
                )
    return tuple(context)


def _v4_reconciliation_digest(
    root: Path,
    context: Sequence[V4LegacyReviewContext],
) -> str:
    payload = [
        {
            "subject_id": item.subject_id,
            "blueprint_path": item.blueprint_path.relative_to(root).as_posix(),
            "field": item.field,
            "target_id": item.target_id,
            "claim": item.claim,
        }
        for item in context
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_v4_reconciliation_commit(
    root: Path,
    *,
    mechanical_commit: str,
    reviewed_commit: str,
    digest: str,
) -> None:
    if reviewed_commit == mechanical_commit:
        raise AuditError("reviewed commit must strictly descend from mechanical baseline")
    message = run_git(root, "show", "-s", "--format=%B", reviewed_commit, check=False)
    if message.returncode != 0:
        raise AuditError("reviewed audit commit message is unavailable")
    trailer = f"Famulus-Legacy-Claims-Reconciled: {digest}"
    lines = message.stdout.decode("utf-8").splitlines()
    if lines.count(trailer) != 1:
        raise AuditError("reviewed audit commit has unresolved legacy claims")


def inspect_v4_migration_candidate(repo_root: Path) -> V4CandidateInspection:
    """Report blocking omissions and immutable semantic-review context."""

    root, snapshot, graph = _load_v4_migration_candidate(repo_root)
    context = _v4_legacy_review_context(root, graph)
    return V4CandidateInspection(
        node_ids=tuple(sorted(graph.nodes)),
        source_commit=snapshot.commit,
        findings=v4_certification_completeness_findings(graph),
        review_context=context,
        reconciliation_digest=_v4_reconciliation_digest(root, context),
    )


def certify_v4_migration_candidate(
    repo_root: Path,
    *,
    reviewed_commit: str,
    certified_at: str | None = None,
) -> V4CertificationResult:
    """Certify an exact candidate commit after cooperative LLM review."""

    root, snapshot, graph = _load_v4_migration_candidate(repo_root)
    if snapshot.commit != reviewed_commit:
        raise AuditError("candidate HEAD does not match the reviewed commit")
    try:
        mechanical_commit = blueprint_v4_mechanical_commit(root)
    except GitMaterializationError as exc:
        raise AuditError("candidate mechanical baseline is unavailable") from exc
    context = _v4_legacy_review_context(root, graph)
    _require_v4_reconciliation_commit(
        root,
        mechanical_commit=mechanical_commit,
        reviewed_commit=reviewed_commit,
        digest=_v4_reconciliation_digest(root, context),
    )
    findings = v4_certification_completeness_findings(graph)
    if findings:
        first = findings[0]
        raise AuditError(
            "candidate semantic review left certification findings: "
            f"{first.subject_id}:{first.field} ({len(findings)} finding(s))"
        )
    timestamp = certified_at or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    public_key_root = root / ".candidate-certification" / "public-keys"
    current = root
    for part in public_key_root.relative_to(root).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AuditError(f"unsafe candidate key path component: {current}")
    return _certify_v4_repository(
        root,
        target_node_ids=tuple(sorted(graph.nodes)),
        public_key_root=public_key_root,
        secret_backend=_EphemeralSecretBackend(),
        reviewed_commit=reviewed_commit,
        certified_at=timestamp,
        require_candidate_execution=(
            os.environ.get("FAMULUS_CANDIDATE_CERTIFIER") == "1"
        ),
    )


class Dispatcher(Protocol):
    """Small protocol for dispatcher-backed calls, with test doubles."""

    def dispatch(
        self,
        key: str,
        *,
        args: Sequence[str] | None = None,
        stdin: str | bytes | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
        check: bool = False,
        text: bool | None = None,
        repo_root: Path | None = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "stdout_tail": tail(self.stdout),
            "stderr_tail": tail(self.stderr),
        }


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str
    path: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload = {"kind": self.kind, "message": self.message}
        if self.path is not None:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class TargetHash:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    skill_root: Path
    hashes: dict[str, Any]

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> "TargetHash":
        skill = expect_string(item.get("skill"), "skill")
        package_root = Path(expect_string(item.get("package_root"), "package_root"))
        skills_root = Path(expect_string(item.get("skills_root"), "skills_root"))
        hashes = item.get("hashes")
        if not isinstance(hashes, dict):
            raise AuditError(f"{skill}: hash payload is missing hashes object")
        return cls(
            skill=skill,
            source=expect_string(item.get("source"), "source"),
            package_root=package_root,
            skills_root=skills_root,
            skill_root=skills_root / skill,
            hashes=hashes,
        )


@dataclass(frozen=True)
class NodeAuditOutcome:
    node_id: str
    semantic_status: str
    health_status: str
    stamp_worthy: bool
    stamp_status: str
    reasons: tuple[str, ...]
    record_path: Path | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "semantic_status": self.semantic_status,
            "health_status": self.health_status,
            "stamp_worthy": self.stamp_worthy,
            "stamp_status": self.stamp_status,
            "reasons": list(self.reasons),
            "record_path": self.record_path.as_posix() if self.record_path else None,
        }


@dataclass(frozen=True)
class AuditOutcome:
    skill: str
    source: str
    skill_root: Path
    semantic_status: str
    stamp_worthy: bool
    stamp_status: str
    nodes: tuple[NodeAuditOutcome, ...]
    pool_status: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "skill_root": self.skill_root.as_posix(),
            "semantic_status": self.semantic_status,
            "stamp_worthy": self.stamp_worthy,
            "stamp_status": self.stamp_status,
            "nodes": [node.as_payload() for node in self.nodes],
            "pool_status": self.pool_status,
        }


@dataclass(frozen=True)
class AuditContext:
    graph: SkillBlueprintGraph
    repo_root: Path
    schema_root: Path
    policy_hash: str
    schema_hash: str
    key: bytes
    snapshot: GitSnapshot | None
    node_checks: Mapping[str, tuple[dict[str, object], ...]]
    raw_evidence: tuple[CommandResult, ...]


def tail(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def expect_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuditError(f"hash payload field `{field}` must be a non-empty string")
    return value


def run_local_command(name: str, command: list[str], *, repo_root: Path = REPO_ROOT) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    return CommandResult(
        name=name,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _blueprint_sync_check(dispatcher: Dispatcher) -> CommandResult:
    sync = dispatcher.dispatch("sync-blueprints", args=["--check"], text=True, check=False)
    return CommandResult(
        name="blueprint-sync",
        command=["skill-maker.interface.sync-blueprints", "--check"],
        exit_code=sync.returncode,
        stdout=sync.stdout or "",
        stderr=sync.stderr or "",
    )


def _require_mechanical_results(results: list[CommandResult]) -> list[CommandResult]:
    failed = [result for result in results if not result.passed]
    if failed:
        raise AuditError(f"mechanical check failed: {failed[0].name}")
    return results


def run_mechanical_checks(dispatcher: Dispatcher, *, repo_root: Path = REPO_ROOT) -> list[CommandResult]:
    """Run the retained legacy global gate before writing legacy records."""

    return _require_mechanical_results([
        _blueprint_sync_check(dispatcher),
        run_local_command("validators", [sys.executable, "validators/runner.py"], repo_root=repo_root),
        run_local_command("tests", [sys.executable, "scripts/run-python-tests.py", "--suite", "precommit"], repo_root=repo_root),
    ])


def run_v4_mechanical_checks(
    dispatcher: Dispatcher,
    *,
    repo_root: Path = REPO_ROOT,
) -> list[CommandResult]:
    """Run only blueprint-conformance gates owned by v4 certification."""

    return _require_mechanical_results([
        _blueprint_sync_check(dispatcher),
        run_local_command(
            "validators",
            [sys.executable, "validators/runner.py"],
            repo_root=repo_root,
        ),
    ])


def compute_hash_payload(dispatcher: Dispatcher, target: str | None = None) -> dict[str, Any]:
    args = ["compute-hashes", "--json"]
    if target:
        path = Path(target).expanduser()
        if is_path_like(target) and path.exists():
            args = ["compute-hashes", "--skill-root", str(path.resolve()), "--json"]
        else:
            args = ["compute-hashes", target, "--json"]
    completed = dispatcher.dispatch("compute-hashes", args=args, text=True, check=False)
    if completed.returncode != 0:
        raise AuditError((completed.stderr or completed.stdout or "compute-hashes failed").strip())
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuditError(f"compute-hashes did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AuditError("compute-hashes returned non-object JSON")
    return payload


def collect_targets(dispatcher: Dispatcher, targets: Sequence[str]) -> list[TargetHash]:
    """Resolve requested names/paths through the drift hash interface."""

    raw_items: list[dict[str, Any]] = []
    if targets:
        for target in targets:
            raw_items.extend(hash_items(compute_hash_payload(dispatcher, target)))
    else:
        raw_items.extend(hash_items(compute_hash_payload(dispatcher)))

    resolved = [TargetHash.from_payload(item) for item in raw_items]
    seen: set[tuple[str, str]] = set()
    deduped: list[TargetHash] = []
    for target in resolved:
        key = (target.skill, target.skill_root.resolve(strict=False).as_posix())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    if not deduped:
        raise AuditError("no blueprint-backed target skills were resolved")
    return deduped


def collect_exact_target(dispatcher: Dispatcher, request: str) -> TargetHash:
    """Resolve one explicit request without admitting provider substitutions."""

    items = hash_items(compute_hash_payload(dispatcher, request))
    if len(items) != 1:
        raise AuditError(
            f"explicit request `{request}` resolved to {len(items)} compute-hashes results; expected exactly one"
        )
    target = TargetHash.from_payload(items[0])
    if is_path_like(request):
        requested_root = Path(request).expanduser().resolve(strict=False)
        if target.skill_root.resolve(strict=False) != requested_root:
            raise AuditError(
                f"explicit path request `{request}` resolved to wrong skill root "
                f"`{target.skill_root.as_posix()}`"
            )
    elif target.skill != request:
        raise AuditError(
            f"explicit skill request `{request}` resolved to wrong skill `{target.skill}`"
        )
    return target


def hash_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("skills")
    if not isinstance(items, list):
        raise AuditError("compute-hashes payload is missing skills list")
    if not all(isinstance(item, dict) for item in items):
        raise AuditError("compute-hashes skills entries must be objects")
    return items


def is_path_like(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith((".", "~"))


def load_blueprint(skill_root: Path) -> dict[str, Any]:
    path = skill_root / "blueprint.yaml"
    if not path.is_file():
        raise AuditError(f"{skill_root.as_posix()}: missing blueprint.yaml")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AuditError(f"{path.as_posix()}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise AuditError(f"{path.as_posix()}: top level must be a mapping")
    return raw


def semantic_findings(target: TargetHash) -> list[Finding]:
    """Return deterministic exactness findings the runtime can check."""

    blueprint = load_blueprint(target.skill_root)
    findings: list[Finding] = []
    findings.extend(check_declared_roots_exist(target.skill_root, target.package_root, blueprint))
    findings.extend(check_runtime_entrypoints_exist(target.skill_root, blueprint))
    findings.extend(check_skill_md_execution_logic(target.skill_root))
    findings.extend(check_implicit_directory_references(target.skill_root, target.package_root, blueprint))
    return findings


def check_declared_roots_exist(skill_root: Path, package_root: Path, blueprint: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for interface_name, spec in iter_interfaces(blueprint):
        for root, source in behavior_source_paths(spec):
            path = resolve_declared_root(skill_root, package_root, root)
            if path is not None and not (path.exists() or path.is_symlink()):
                findings.append(
                    Finding(
                        "missing-declared-root",
                        f"{interface_name}.{source} declares missing root `{root}`",
                        path.as_posix(),
                    )
                )
    return findings


def check_runtime_entrypoints_exist(skill_root: Path, blueprint: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for interface_name, spec in iter_interfaces(blueprint, namespaces=("machine",)):
        invocation = spec.get("invocation")
        if not isinstance(invocation, dict):
            findings.append(Finding("invalid-blueprint", f"{interface_name}.invocation must be a mapping"))
            continue
        kind = invocation.get("kind")
        if kind == "python_machine_interface":
            entrypoint = invocation.get("entrypoint")
            if not isinstance(entrypoint, str) or ":" not in entrypoint:
                findings.append(Finding("invalid-blueprint", f"{interface_name}.invocation.entrypoint is invalid"))
                continue
            path_text = entrypoint.split(":", 1)[0]
            path = skill_root / path_text
            if not path.is_file():
                findings.append(
                    Finding("missing-runtime-entrypoint", f"{interface_name} entrypoint does not exist", path.as_posix())
                )
        elif kind == "command":
            argv = invocation.get("argv")
            if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
                findings.append(Finding("invalid-blueprint", f"{interface_name}.invocation.argv is invalid"))
                continue
            first = argv[0]
            if "/" in first or "\\" in first:
                path = skill_root / first
                if not path.exists():
                    findings.append(
                        Finding("missing-runtime-entrypoint", f"{interface_name} command does not exist", path.as_posix())
                    )
    return findings


def check_implicit_directory_references(
    skill_root: Path,
    package_root: Path,
    blueprint: dict[str, Any],
) -> list[Finding]:
    declared_roots = collect_declared_paths(skill_root, package_root, blueprint)
    findings: list[Finding] = []
    for path in iter_text_files(skill_root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in IMPLICIT_DIRECTORY_PATTERNS:
            for match in pattern.finditer(text):
                token = match.group(1).strip(".,;:)]}\"'")
                if not token or token.startswith(("http://", "https://")):
                    continue
                candidate = (path.parent / token).resolve(strict=False)
                try:
                    candidate.relative_to(skill_root.resolve())
                except ValueError:
                    continue
                if candidate.is_dir() and not any(covers(root, candidate) for root in declared_roots):
                    findings.append(
                        Finding(
                            "implicit-root-not-declared",
                            f"{relative_to(path, skill_root)} implicitly references directory `{token}`",
                            candidate.as_posix(),
                        )
                    )
    return dedupe_findings(findings)


def check_skill_md_execution_logic(skill_root: Path) -> list[Finding]:
    """Flag hand-authored SKILL.md execution instructions not routed through interfaces."""

    path = skill_root / "SKILL.md"
    if not path.is_file():
        return [Finding("missing-skill-file", "SKILL.md is missing", path.as_posix())]

    text = strip_generated_blocks(path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    in_fence = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not stripped:
            continue
        reason = unencapsulated_execution_reason(stripped, in_fence=in_fence)
        if reason is not None:
            findings.append(
                Finding(
                    "unencapsulated-execution",
                    f"SKILL.md line {line_number} contains execution logic outside an interface: {reason}",
                    path.as_posix(),
                )
            )
    return findings


def strip_generated_blocks(text: str) -> str:
    return GENERATED_BLOCK_RE.sub("", text)


def unencapsulated_execution_reason(line: str, *, in_fence: bool = False) -> str | None:
    normalized = line.strip()
    if not normalized:
        return None
    if COMMAND_LINE_RE.search(normalized):
        return "command-like instruction"
    if EXECUTION_VERB_RE.search(normalized):
        return "execution verb with command or implementation path"
    if IMPLEMENTATION_PATH_RE.search(normalized):
        return "direct implementation path reference"
    if in_fence and re.search(r"\b(?:python3?|bash|sh|pytest|npm|node|dispatcher)\b", normalized):
        return "command reference inside code block"
    return None


def iter_interfaces(
    blueprint: dict[str, Any],
    *,
    namespaces: Sequence[str] = ("llm", "machine"),
) -> list[tuple[str, dict[str, Any]]]:
    interfaces = blueprint.get("interfaces")
    if not isinstance(interfaces, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for namespace in namespaces:
        entries = interfaces.get(namespace)
        if not isinstance(entries, dict):
            continue
        for name, spec in sorted(entries.items()):
            if isinstance(spec, dict):
                result.append((f"{namespace}.{name}", spec))
    return result


def resolve_declared_root(skill_root: Path, package_root: Path, root: str) -> Path | None:
    if os.path.isabs(root) or ".." in Path(root).parts:
        return None
    if root.startswith("$repo/"):
        return (package_root / root[len("$repo/") :]).resolve(strict=False)
    return (skill_root / root).resolve(strict=False)


def collect_declared_paths(skill_root: Path, package_root: Path, blueprint: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for _interface_name, spec in iter_interfaces(blueprint):
        for root, _source in behavior_source_paths(spec):
            path = resolve_declared_root(skill_root, package_root, root)
            if path is not None:
                paths.append(path)
    return paths


def behavior_source_paths(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Return declared behavior-shaping paths for an interface."""

    paths: list[tuple[str, str]] = []
    binding = spec.get("binding")
    if isinstance(binding, dict):
        binding_path = binding.get("path")
        if isinstance(binding_path, str):
            paths.append((binding_path, "binding"))
    for source_label, container in (("behavior_sources", spec), ("invocation.behavior_sources", spec.get("invocation"))):
        if not isinstance(container, dict):
            continue
        value = container.get("behavior_sources", [])
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                paths.append((entry["path"], source_label))
    return paths


def iter_text_files(skill_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill_root.rglob("*"), key=lambda item: item.as_posix()):
        if AUDIT_RECORD_NAME in path.parts or "__pycache__" in path.parts:
            continue
        if path.is_file() and path.suffix.lower() in TEXT_FILE_SUFFIXES:
            files.append(path)
    return files


def covers(root: Path, candidate: Path) -> bool:
    root = root.resolve(strict=False)
    candidate = candidate.resolve(strict=False)
    if root == candidate:
        return True
    if root.is_dir() or not root.suffix:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False
    return False


def dedupe_findings(findings: Sequence[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.kind, finding.message, finding.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def build_record(
    target: TargetHash,
    *,
    mechanical_checks: Sequence[CommandResult],
    semantic_results: Sequence[Finding],
    source: Mapping[str, object],
    timestamp: str | None = None,
) -> dict[str, Any]:
    hashes = dict(target.hashes)
    audit_policy_hash = hashes.pop("policy", None)
    if not isinstance(audit_policy_hash, str):
        raise AuditError(f"{target.skill}: hash payload is missing policy hash")
    record = {
        "skill": target.skill,
        "timestamp": timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
        "audit_policy_hash": audit_policy_hash,
        "git_commit": source.get("commit"),
        "source": dict(source),
        "checks": {
            "mechanical": [
                {"name": result.name, "passed": result.passed}
                for result in mechanical_checks
            ],
            "semantic": {
                "passed": not semantic_results,
                "findings": [finding.as_payload() for finding in semantic_results],
            },
        },
        "hashes": hashes,
    }
    return attach_record_digest(record)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _semantic_check(findings: Sequence[Finding]) -> dict[str, object]:
    return {
        "id": "semantic-exactness",
        "version": 1,
        "passed": not findings,
        "findings": [finding.as_payload() for finding in findings],
    }


def _checks_pass(checks: Sequence[Mapping[str, object]]) -> bool:
    return all(check.get("passed") is True for check in checks)


def _policy_input_paths(
    repo_root: Path,
    schema_root: Path,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    paths = {
        path
        for path in schema_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    paths.update(schema_root / name for name in REQUIRED_SCHEMA_INPUTS)
    reasons: set[str] = set()
    manifest_path = (
        repo_root
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    paths.add(manifest_path)
    patterns: list[str] = []
    if manifest_path.is_symlink():
        reasons.add("unsafe-policy-manifest")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            reasons.add("invalid-policy-manifest")
        else:
            if isinstance(manifest, list) and all(isinstance(item, str) for item in manifest):
                patterns = manifest
            else:
                reasons.add("invalid-policy-manifest")
    for pattern in patterns:
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            reasons.add(f"invalid-policy-pattern:{pattern}")
            continue
        if any(char in pattern for char in "*?[]"):
            matches = sorted(repo_root.glob(pattern))
            if not matches:
                reasons.add(f"missing-policy-input:{pattern}")
        else:
            matches = [repo_root / pattern]
        for match in matches:
            if match.is_symlink() or not match.is_dir():
                paths.add(match)
                continue
            children = [
                child
                for child in sorted(match.rglob("*"))
                if child.is_file() or child.is_symlink()
            ]
            if not children:
                reasons.add(f"missing-policy-input:{pattern}")
            paths.update(children)
    for relative in (
        "skills/skill-certifier/_rtx/_audit_certifier.py",
        "src/officina/common/artifact_health.py",
        "src/officina/common/atomic_files.py",
        "src/officina/common/audit_records.py",
        "src/officina/common/blueprint_graph.py",
        "src/officina/common/blueprint_template.py",
        "src/officina/common/git_provenance.py",
        "src/officina/common/pooled_blueprint.py",
    ):
        paths.add(repo_root / relative)
    return tuple(sorted(paths)), tuple(sorted(reasons))


def _expected_file_hashes(
    snapshot: GitSnapshot | None,
    paths: Sequence[Path],
) -> dict[str, str]:
    if snapshot is None:
        return {}
    expected: dict[str, str] = {}
    for path in paths:
        absolute = Path(os.path.abspath(path))
        try:
            relative = absolute.relative_to(snapshot.repo_root).as_posix()
            metadata = path.lstat()
        except (FileNotFoundError, ValueError):
            continue
        if stat.S_ISREG(metadata.st_mode):
            expected[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return expected


def _policy_evidence(
    snapshot: GitSnapshot | None,
    repo_root: Path,
    schema_root: Path,
) -> CommandResult:
    paths, manifest_reasons = _policy_input_paths(repo_root, schema_root)
    readiness = check_commit_readiness(
        snapshot,
        paths,
        _expected_file_hashes(snapshot, paths),
    )
    reasons = tuple(sorted({*manifest_reasons, *readiness.reasons}))
    return CommandResult(
        name="policy-readiness",
        command=["commit-readiness", "policy-bundle"],
        exit_code=0 if readiness.stamp_worthy and not reasons else 1,
        stdout="",
        stderr="\n".join(reasons),
    )


def _policy_is_ready(context: AuditContext) -> bool:
    return any(
        evidence.name == "policy-readiness" and evidence.passed
        for evidence in context.raw_evidence
    )


def _key_is_ready(context: AuditContext) -> bool:
    return any(
        evidence.name == "key-readiness" and evidence.passed
        for evidence in context.raw_evidence
    )


def _key_reasons(context: AuditContext) -> tuple[str, ...]:
    return tuple(
        line
        for evidence in context.raw_evidence
        if evidence.name == "key-readiness" and not evidence.passed
        for line in evidence.stderr.splitlines()
        if line
    ) or ("key-not-ready",)


def _read_graph_records(graph: SkillBlueprintGraph) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for node_id in health_node_ids(graph):
        node = graph.nodes[node_id]
        path = health_path_for_node(node)
        if path.is_symlink():
            continue
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records[node_id] = value
    return records


def _passing_checks(context: AuditContext) -> dict[str, tuple[dict[str, object], ...]]:
    return {
        node_id: checks if _checks_pass(checks) else ()
        for node_id, checks in context.node_checks.items()
    }


def _current_states(context: AuditContext) -> dict[str, Any]:
    return compute_node_hash_states(
        context.graph,
        policy_hash=context.policy_hash,
        schema_hash=context.schema_hash,
        checks_by_node=_passing_checks(context),
        schema_root=context.schema_root,
        certifier={"interface": "skill-audit.machine.certify", "version": 1},
    )


def check_graph_health_from_disk(context: AuditContext) -> GraphHealthReport:
    records = _read_graph_records(context.graph)
    report = check_graph_health(
        context.graph,
        records,
        context.policy_hash,
        context.schema_hash,
        context.key,
        context.schema_root,
    )
    states = _current_states(context)
    statuses: dict[str, NodeHealthStatus] = {}
    for node_id, status in report.nodes.items():
        concerns = list(status.concerns)
        checks_stale = False
        record = records.get(node_id)
        expected_checks = list(context.node_checks.get(node_id, ()))
        if record is not None and record.get("checks") != expected_checks:
            concerns.append("checks-stale")
            checks_stale = True
        if not _checks_pass(context.node_checks.get(node_id, ())):
            concerns.append("checks-stale")
            checks_stale = True
        concerns = list(dict.fromkeys(concerns))
        statuses[node_id] = NodeHealthStatus(
            node_id=node_id,
            healthy=status.healthy and not checks_stale,
            concerns=tuple(concerns),
            expected_certified_health_hash=states[node_id].certified_health_hash,
            recorded_certified_health_hash=status.recorded_certified_health_hash,
            admitted_record_hash=status.admitted_record_hash,
        )
    root = statuses[report.root_id]
    return GraphHealthReport(report.root_id, root.healthy, statuses)


def _health_status(status: NodeHealthStatus, semantic_status: str) -> str:
    if semantic_status == "failed":
        return "unhealthy"
    if status.healthy:
        return "healthy"
    if "missing-health-record" in status.concerns:
        return "unstamped"
    return "refresh-required"


def _node_is_current(status: NodeHealthStatus) -> bool:
    return (
        status.healthy
        and status.recorded_certified_health_hash
        == status.expected_certified_health_hash
        and not node_requires_refresh(status)
    )


def _child_ids(graph: SkillBlueprintGraph, node_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(edge.target_id for edge in health_edges(graph) if edge.source_id == node_id)
    )


def audit_and_maybe_stamp_node(
    context: AuditContext,
    node_id: str,
    outcomes: Mapping[str, NodeAuditOutcome],
) -> NodeAuditOutcome:
    report = check_graph_health_from_disk(context)
    status = report.nodes[node_id]
    checks = context.node_checks.get(node_id, ())
    semantic_status = "passed" if _checks_pass(checks) else "failed"
    record_path = health_path_for_node(context.graph.nodes[node_id])
    health_status = _health_status(status, semantic_status)
    if semantic_status == "failed":
        reason_set = {
            f"semantic:{finding.get('kind', 'finding')}"
            for check in checks
            for finding in check.get("findings", [])
            if isinstance(finding, dict)
        } or {"semantic-check-failed"}
        if not _key_is_ready(context):
            reason_set.update(_key_reasons(context))
        reasons = tuple(sorted(reason_set))
        return NodeAuditOutcome(
            node_id, semantic_status, health_status, False, "not-written", reasons, record_path
        )

    readiness_reasons: list[str] = []
    if not _policy_is_ready(context):
        readiness_reasons.append("policy-not-commit-backed")
    if not _key_is_ready(context):
        readiness_reasons.extend(_key_reasons(context))
    if readiness_reasons:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            tuple(dict.fromkeys(readiness_reasons)),
            record_path,
        )
    unavailable_children = [
        child_id
        for child_id in _child_ids(context.graph, node_id)
        if child_id not in outcomes
        or outcomes[child_id].stamp_status not in {"reused", "written"}
        or outcomes[child_id].health_status != "healthy"
        or not _node_is_current(report.nodes[child_id])
    ]
    if unavailable_children:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            tuple(f"child-not-current:{child_id}" for child_id in unavailable_children),
            record_path,
        )
    if not snapshot_head_matches(context.snapshot):
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            ("head-changed",),
            record_path,
        )

    input_paths = local_input_paths_for_node(context.graph.nodes[node_id])
    expected_hashes = _expected_file_hashes(context.snapshot, input_paths)
    readiness = check_commit_readiness(
        context.snapshot,
        input_paths,
        expected_hashes,
    )
    if not readiness.stamp_worthy or readiness.source is None:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            readiness.reasons,
            record_path,
        )
    try:
        record = build_node_health_record(
            context.graph,
            node_id,
            _current_states(context),
            source=readiness.source,
            checks=checks,
            key=context.key,
            certified_at=_certified_at(context),
            schema_root=context.schema_root,
        )
        final_readiness = check_commit_readiness(
            context.snapshot,
            input_paths,
            expected_hashes,
        )
        if not final_readiness.stamp_worthy:
            return NodeAuditOutcome(
                node_id,
                semantic_status,
                health_status,
                False,
                "not-written",
                final_readiness.reasons,
                record_path,
            )
        if not snapshot_head_matches(context.snapshot):
            return NodeAuditOutcome(
                node_id,
                semantic_status,
                health_status,
                False,
                "not-written",
                ("head-changed",),
                record_path,
            )
        atomic_replace_bytes(
            record_path,
            _json_bytes(record),
            allowed_root=context.graph.nodes[node_id].skill_root,
            mode=0o600,
        )
        written_status = check_graph_health_from_disk(context).nodes[node_id]
        if not _node_is_current(written_status):
            raise AuditError(f"post-write node verification failed for {node_id}")
    except (OSError, TypeError, ValueError, AuditError) as exc:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            "unhealthy",
            True,
            "failed",
            (str(exc),),
            record_path,
        )
    return NodeAuditOutcome(
        node_id, semantic_status, "healthy", True, "written", (), record_path
    )


def _finish_pool(context: AuditContext, report: GraphHealthReport) -> str:
    if not report.healthy:
        return "not-written"
    records = _read_graph_records(context.graph)
    if set(records) != set(health_node_ids(context.graph)):
        return "not-written"
    pool_path = pooled_review_path(context.graph.skill_root)
    pool_health_path = pooled_review_health_path(context.graph.skill_root)
    if pool_path.is_symlink() or pool_health_path.is_symlink():
        return "failed"
    rendered = render_pooled_review(context.graph, records).encode("utf-8")
    try:
        current = check_pooled_review(
            pool_path,
            pool_health_path,
            report,
            context.key,
            graph=context.graph,
            records=records,
            schema_root=context.schema_root,
        )
        if current.healthy and pool_path.read_bytes() == rendered:
            return "reused"
        atomic_replace_bytes(
            pool_path,
            rendered,
            allowed_root=context.graph.skill_root,
            mode=0o600,
        )
        root_record = records[context.graph.root.node_id]
        certification = root_record.get("certification", {})
        certified_at = certification.get("certified_at") if isinstance(certification, dict) else None
        if not isinstance(certified_at, str):
            raise AuditError("root health record has no certification timestamp")
        pool_record = certify_pooled_review(
            pool_path,
            root_record,
            key=context.key,
            certified_at=certified_at,
        )
        atomic_replace_bytes(
            pool_health_path,
            _json_bytes(pool_record),
            allowed_root=context.graph.skill_root,
            mode=0o600,
        )
        verified = check_pooled_review(
            pool_path,
            pool_health_path,
            report,
            context.key,
            graph=context.graph,
            records=records,
            schema_root=context.schema_root,
        )
        return "written" if verified.healthy else "failed"
    except (OSError, TypeError, ValueError, AuditError):
        return "failed"


def finish_root_and_pool(
    context: AuditContext,
    outcomes: Mapping[str, NodeAuditOutcome],
) -> AuditOutcome:
    report = check_graph_health_from_disk(context)
    reconciled = dict(outcomes)
    graph_current: dict[str, bool] = {}
    for node_id in health_postorder_node_ids(context.graph):
        status = report.nodes[node_id]
        graph_current[node_id] = _node_is_current(status) and all(
            graph_current[child_id] for child_id in _child_ids(context.graph, node_id)
        )
        outcome = outcomes[node_id]
        if outcome.stamp_status == "reused" and not graph_current[node_id]:
            reconciled[node_id] = replace(
                outcome,
                health_status=_health_status(status, outcome.semantic_status),
                stamp_worthy=False,
                stamp_status="not-written",
                reasons=tuple(
                    dict.fromkeys((*outcome.reasons, "health-changed-before-finalization"))
                ),
            )
    root_id = context.graph.root.node_id
    root = reconciled[root_id]
    root_status = report.nodes[root_id]
    if root.stamp_status == "written" and not graph_current[root_id]:
        root = replace(
            root,
            health_status=_health_status(root_status, root.semantic_status),
            stamp_worthy=False,
            stamp_status="not-written",
            reasons=tuple(dict.fromkeys((*root.reasons, "health-changed-before-finalization"))),
        )
        reconciled[root_id] = root
    ordered = tuple(
        reconciled[node_id] for node_id in health_postorder_node_ids(context.graph)
    )
    stamp_status = "failed" if any(node.stamp_status == "failed" for node in ordered) else root.stamp_status
    pool_status = _finish_pool(context, report)
    if pool_status not in POOL_STATUSES:
        raise AuditError(f"invalid pool status: {pool_status}")
    return AuditOutcome(
        skill=context.graph.root.node_id,
        source="path",
        skill_root=context.graph.skill_root,
        semantic_status=(
            "passed" if all(node.semantic_status == "passed" for node in ordered) else "failed"
        ),
        stamp_worthy=root.stamp_worthy,
        stamp_status=stamp_status,
        nodes=ordered,
        pool_status=pool_status,
    )


def _semantic_only_typed_outcome(context: AuditContext) -> AuditOutcome:
    outcomes = []
    for node_id in health_postorder_node_ids(context.graph):
        checks = context.node_checks.get(node_id, ())
        semantic_status = "passed" if _checks_pass(checks) else "failed"
        reason_list = (
            ["policy-not-commit-backed"]
            if semantic_status == "passed"
            else ["semantic-check-failed"]
        )
        if not _key_is_ready(context):
            reason_list.extend(_key_reasons(context))
        reasons = tuple(dict.fromkeys(reason_list))
        outcomes.append(
            NodeAuditOutcome(
                node_id=node_id,
                semantic_status=semantic_status,
                health_status="unstamped" if semantic_status == "passed" else "unhealthy",
                stamp_worthy=False,
                stamp_status="not-written",
                reasons=reasons,
                record_path=health_path_for_node(context.graph.nodes[node_id]),
            )
        )
    return AuditOutcome(
        skill=context.graph.root.node_id,
        source="path",
        skill_root=context.graph.skill_root,
        semantic_status=(
            "passed" if all(node.semantic_status == "passed" for node in outcomes) else "failed"
        ),
        stamp_worthy=False,
        stamp_status="not-written",
        nodes=tuple(outcomes),
        pool_status="not-written",
    )


def audit_typed_graph(context: AuditContext) -> AuditOutcome:
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (context.schema_root / name for name in REQUIRED_SCHEMA_INPUTS)
    ):
        return _semantic_only_typed_outcome(context)
    try:
        report = check_graph_health_from_disk(context)
    except (OSError, TypeError, ValueError):
        if not _policy_is_ready(context):
            return _semantic_only_typed_outcome(context)
        raise
    outcomes: dict[str, NodeAuditOutcome] = {}
    for node_id in health_postorder_node_ids(context.graph):
        status = report.nodes[node_id]
        checks = context.node_checks.get(node_id, ())
        if _node_is_current(status) and _checks_pass(checks):
            outcomes[node_id] = NodeAuditOutcome(
                node_id=node_id,
                semantic_status="passed",
                health_status="healthy",
                stamp_worthy=True,
                stamp_status="reused",
                reasons=(),
                record_path=health_path_for_node(context.graph.nodes[node_id]),
            )
            continue
        outcomes[node_id] = audit_and_maybe_stamp_node(context, node_id, outcomes)
    return finish_root_and_pool(context, outcomes)


def _certified_at(context: AuditContext) -> str:
    for evidence in context.raw_evidence:
        if evidence.name == "certification-time" and evidence.stdout:
            return evidence.stdout
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _make_audit_context(
    target: TargetHash,
    mechanical: Sequence[CommandResult],
    timestamp: str | None,
) -> AuditContext:
    schema_root = target.package_root / "references" / "blueprint"
    graph = load_validated_skill_blueprint_graph(target.skill_root, schema_root)
    findings = semantic_findings(target)
    policy_hash = target.hashes.get("policy")
    if not isinstance(policy_hash, str):
        raise AuditError(f"{target.skill}: hash payload is missing policy hash")
    try:
        schema_hash = blueprint_schema_hash(schema_root)
    except (OSError, ValueError):
        schema_hash = "sha256:" + "0" * 64
    snapshot = capture_git_snapshot(target.package_root)
    policy = _policy_evidence(snapshot, target.package_root, schema_root)
    key_root = target.package_root / "skills" / "skill-certifier"
    key_path = key_root / ".health-authentication-key"
    key_readiness = CommandResult(
        name="key-readiness",
        command=["load-or-create-hmac-key", key_path.as_posix()],
        exit_code=0,
        stdout="",
        stderr="",
    )
    unsafe_component = next(
        (
            path
            for path in (target.package_root / "skills", key_root, key_path)
            if path.is_symlink()
        ),
        None,
    )
    if unsafe_component is not None:
        key = b"\0" * 32
        key_readiness = replace(
            key_readiness,
            exit_code=1,
            stderr=f"key-unavailable:{unsafe_component}: unsafe symlink key path component",
        )
    elif policy.passed or key_path.exists():
        try:
            key = load_or_create_hmac_key(key_path, allowed_root=key_root)
        except (OSError, ValueError) as exc:
            key = b"\0" * 32
            key_readiness = replace(
                key_readiness,
                exit_code=1,
                stderr=f"key-unavailable:{exc}",
            )
    else:
        key = b"\0" * 32
    node_checks = {node_id: () for node_id in health_node_ids(graph)}
    node_checks[graph.root.node_id] = (_semantic_check(findings),)
    evidence = [*mechanical, policy, key_readiness]
    evidence.append(
        CommandResult(
            name="certification-time",
            command=[],
            exit_code=0,
            stdout=timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
            stderr="",
        )
    )
    return AuditContext(
        graph=graph,
        repo_root=target.package_root,
        schema_root=schema_root,
        policy_hash=policy_hash,
        schema_hash=schema_hash,
        key=key,
        snapshot=snapshot,
        node_checks=node_checks,
        raw_evidence=tuple(evidence),
    )


def _verify_written_nodes(context: AuditContext, outcome: AuditOutcome) -> None:
    report = check_graph_health_from_disk(context)
    for node in outcome.nodes:
        if node.stamp_status != "written":
            continue
        status = report.nodes.get(node.node_id)
        if status is None or not _node_is_current(status):
            raise AuditError(f"post-write node verification failed for {node.node_id}")


def _mark_failed(outcome: AuditOutcome, message: str) -> AuditOutcome:
    root_id = outcome.skill
    nodes = tuple(
        replace(
            node,
            stamp_status="failed",
            reasons=tuple(dict.fromkeys((*node.reasons, message))),
        )
        if node.node_id == root_id
        else node
        for node in outcome.nodes
    )
    return replace(outcome, stamp_status="failed", nodes=nodes)


def verify_post_write(dispatcher: Dispatcher, target: TargetHash) -> None:
    completed = dispatcher.dispatch(
        "drift-status",
        args=["status", "--skill-root", str(target.skill_root.resolve()), "--json"],
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AuditError((completed.stderr or completed.stdout or "drift-status failed").strip())
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuditError(f"drift-status did not return JSON: {exc}") from exc
    skills = payload.get("skills")
    if (
        not isinstance(skills, list)
        or len(skills) != 1
        or not isinstance(skills[0], dict)
        or skills[0].get("skill") != target.skill
    ):
        raise AuditError("drift-status did not return the exact requested skill")
    if skills[0].get("derived_status") != "audit-current":
        raise AuditError(f"post-write drift verification failed for {target.skill}")


def _legacy_input_paths(skill_root: Path) -> tuple[Path, ...]:
    excluded_names = {
        AUDIT_RECORD_NAME,
        ".pooled-blueprint-review.yaml",
        ".pooled-blueprint-review.health.json",
        ".health-authentication-key",
    }
    paths: list[Path] = []
    for path in sorted(skill_root.rglob("*")):
        if "__pycache__" in path.parts or path.name in excluded_names:
            continue
        if path.name.endswith(".health.json"):
            continue
        if path.is_file() or path.is_symlink():
            paths.append(path)
    return tuple(paths)


def _legacy_record_is_current(
    path: Path,
    target: TargetHash,
    findings: Sequence[Finding],
) -> bool:
    if findings or path.is_symlink():
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict):
        return False
    try:
        digest_matches = record_digest_matches(record)
    except (TypeError, ValueError):
        return False
    if not digest_matches:
        return False
    hashes = dict(target.hashes)
    policy_hash = hashes.pop("policy", None)
    return (
        record.get("skill") == target.skill
        and record.get("audit_policy_hash") == policy_hash
        and record.get("hashes") == hashes
        and record.get("checks", {}).get("semantic", {}).get("passed") is True
    )


def _legacy_outcome(
    target: TargetHash,
    *,
    semantic_status: str,
    stamp_worthy: bool,
    stamp_status: str,
    health_status: str,
    reasons: tuple[str, ...],
) -> AuditOutcome:
    path = target.skill_root / AUDIT_RECORD_NAME
    node = NodeAuditOutcome(
        node_id=target.skill,
        semantic_status=semantic_status,
        health_status=health_status,
        stamp_worthy=stamp_worthy,
        stamp_status=stamp_status,
        reasons=reasons,
        record_path=path,
    )
    return AuditOutcome(
        skill=target.skill,
        source=target.source,
        skill_root=target.skill_root,
        semantic_status=semantic_status,
        stamp_worthy=stamp_worthy,
        stamp_status=stamp_status,
        nodes=(node,),
        pool_status="not-written",
    )


def _audit_legacy_target(
    target: TargetHash,
    mechanical: Sequence[CommandResult],
    timestamp: str | None,
) -> tuple[AuditOutcome, tuple[CommandResult, ...]]:
    findings = semantic_findings(target)
    semantic_status = "passed" if not findings else "failed"
    record_path = target.skill_root / AUDIT_RECORD_NAME
    snapshot = capture_git_snapshot(target.package_root)
    schema_root = target.package_root / "references" / "blueprint"
    policy = _policy_evidence(snapshot, target.package_root, schema_root)
    evidence = tuple([*mechanical, policy])
    if _legacy_record_is_current(record_path, target, findings):
        return (
            _legacy_outcome(
                target,
                semantic_status="passed",
                stamp_worthy=True,
                stamp_status="reused",
                health_status="healthy",
                reasons=(),
            ),
            evidence,
        )
    if findings:
        reasons = tuple(f"semantic:{finding.kind}" for finding in findings)
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unhealthy",
                reasons=reasons,
            ),
            evidence,
        )
    if not policy.passed:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=("policy-not-commit-backed",),
            ),
            evidence,
        )
    if not snapshot_head_matches(snapshot):
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=("head-changed",),
            ),
            evidence,
        )
    input_paths = _legacy_input_paths(target.skill_root)
    expected_hashes = _expected_file_hashes(snapshot, input_paths)
    readiness = check_commit_readiness(
        snapshot,
        input_paths,
        expected_hashes,
    )
    if not readiness.stamp_worthy or readiness.source is None:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=readiness.reasons,
            ),
            evidence,
        )
    record = build_record(
        target,
        mechanical_checks=mechanical,
        semantic_results=findings,
        source=readiness.source,
        timestamp=timestamp,
    )
    final_readiness = check_commit_readiness(
        snapshot,
        input_paths,
        expected_hashes,
    )
    if not final_readiness.stamp_worthy:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=final_readiness.reasons,
            ),
            evidence,
        )
    if not snapshot_head_matches(snapshot):
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=("head-changed",),
            ),
            evidence,
        )
    try:
        atomic_replace_bytes(
            record_path,
            _json_bytes(record),
            allowed_root=target.skill_root,
            mode=0o600,
        )
    except (OSError, TypeError, ValueError) as exc:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=True,
                stamp_status="failed",
                health_status="unhealthy",
                reasons=(str(exc),),
            ),
            evidence,
        )
    return (
        _legacy_outcome(
            target,
            semantic_status=semantic_status,
            stamp_worthy=True,
            stamp_status="written",
            health_status="healthy",
            reasons=(),
        ),
        evidence,
    )


def _failure_outcome(target: TargetHash, message: str) -> AuditOutcome:
    return _legacy_outcome(
        target,
        semantic_status="failed",
        stamp_worthy=False,
        stamp_status="failed",
        health_status="unhealthy",
        reasons=(message,),
    )


def _target_for_failed_request(request: str, repo_root: Path) -> TargetHash:
    path = Path(request).expanduser()
    skill_root = path.resolve(strict=False) if is_path_like(request) else repo_root / "skills" / request
    return TargetHash(
        skill=skill_root.name,
        source="path" if is_path_like(request) else "name",
        package_root=repo_root,
        skills_root=skill_root.parent,
        skill_root=skill_root,
        hashes={},
    )


def _audit_target(
    dispatcher: Dispatcher,
    target: TargetHash,
    mechanical: Sequence[CommandResult],
    timestamp: str | None,
    *,
    reviewed_repository: Path | None = None,
    reviewed_commit: str | None = None,
) -> tuple[AuditOutcome, tuple[CommandResult, ...]]:
    try:
        schema_version = load_blueprint(target.skill_root).get("schema_version")
        if schema_version == 4:
            root = target.package_root.resolve()
            if reviewed_repository is None or reviewed_commit is None:
                raise AuditError(
                    "v4 certification requires explicit LLM-reviewed repository "
                    "and commit attestation"
                )
            if Path(reviewed_repository).resolve() != root:
                raise AuditError(
                    "v4 semantic review repository does not match the target repository"
                )
            graph = load_repository_blueprint_graph(
                root,
                schema_root=root / "references" / "blueprint",
            )
            target_root = target.skill_root.resolve()
            target_node_ids = tuple(
                sorted(
                    node_id
                    for node_id, node in graph.nodes.items()
                    if node.skill_root.resolve() == target_root
                )
            )
            if not target_node_ids:
                raise AuditError(
                    f"{target.skill}: no v4 nodes are owned by the requested module"
                )
            snapshot = capture_git_snapshot(root)
            if snapshot is None or snapshot.repo_root != root:
                raise AuditError("v4 certification requires the exact Git repository root")
            certified_at = timestamp or datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            result = _certify_v4_repository(
                root,
                target_node_ids=target_node_ids,
                public_key_root=certificate_public_key_root(root),
                secret_backend=None,
                reviewed_commit=reviewed_commit,
                certified_at=certified_at,
                require_candidate_execution=True,
                require_migration_review=False,
            )
            written = set(result.node_ids)
            nodes = tuple(
                NodeAuditOutcome(
                    node_id=node_id,
                    semantic_status="passed",
                    health_status="healthy",
                    stamp_worthy=True,
                    stamp_status="written",
                    reasons=(),
                    record_path=certificate_log_path(graph.nodes[node_id]),
                )
                for node_id in result.node_ids
            )
            if not set(target_node_ids) <= written:
                raise AuditError("v4 certifier did not write every requested node")
            return (
                AuditOutcome(
                    skill=target.skill,
                    source=target.source,
                    skill_root=target.skill_root,
                    semantic_status="passed",
                    stamp_worthy=True,
                    stamp_status="written",
                    nodes=nodes,
                    pool_status="not-written",
                ),
                (),
            )
        typed = schema_version == 2
        context: AuditContext | None = None
        if typed:
            context = _make_audit_context(target, mechanical, timestamp)
            outcome = replace(audit_typed_graph(context), source=target.source)
            evidence = context.raw_evidence
        else:
            outcome, evidence = _audit_legacy_target(target, mechanical, timestamp)
        if any(node.stamp_status == "written" for node in outcome.nodes):
            try:
                if context is not None:
                    _verify_written_nodes(context, outcome)
                root = next(node for node in outcome.nodes if node.node_id == outcome.skill)
                if root.stamp_status == "written":
                    verify_post_write(dispatcher, target)
            except (OSError, TypeError, ValueError, AuditError) as exc:
                return _mark_failed(outcome, str(exc)), evidence
        return outcome, evidence
    except (OSError, TypeError, ValueError, AuditError) as exc:
        return _failure_outcome(target, str(exc)), tuple(mechanical)


def certify(
    dispatcher: Dispatcher,
    *,
    targets: Sequence[str],
    repo_root: Path = REPO_ROOT,
    skip_mechanical: bool = False,
    timestamp: str | None = None,
    reviewed_repository: Path | None = None,
    reviewed_commit: str | None = None,
) -> tuple[list[CommandResult], list[AuditOutcome]]:
    mechanical: list[CommandResult] = []
    evidence: list[CommandResult] = []
    outcomes: list[AuditOutcome] = []
    seen: set[tuple[str, str]] = set()
    resolved_targets: list[TargetHash] = []

    if targets:
        for request in targets:
            try:
                target = collect_exact_target(dispatcher, request)
            except AuditError as exc:
                outcomes.append(_failure_outcome(_target_for_failed_request(request, repo_root), str(exc)))
                continue
            identity = (target.skill, target.skill_root.resolve(strict=False).as_posix())
            if identity in seen:
                continue
            seen.add(identity)
            resolved_targets.append(target)
    else:
        for target in collect_targets(dispatcher, ()):
            identity = (target.skill, target.skill_root.resolve(strict=False).as_posix())
            if identity in seen:
                continue
            seen.add(identity)
            resolved_targets.append(target)

    v4_roots: set[Path] = set()
    has_legacy_target = False
    for target in resolved_targets:
        try:
            if load_blueprint(target.skill_root).get("schema_version") == 4:
                v4_roots.add(target.package_root.resolve())
            else:
                has_legacy_target = True
        except (OSError, TypeError, ValueError, AuditError):
            continue
    if v4_roots and has_legacy_target:
        raise AuditError(
            "one certification invocation cannot mix v4 and legacy targets"
        )
    if len(v4_roots) > 1:
        raise AuditError(
            "one v4 certification invocation may attest only one repository"
        )
    if v4_roots:
        expected_root = next(iter(v4_roots))
        if reviewed_repository is None or reviewed_commit is None:
            raise AuditError(
                "v4 certification requires explicit LLM-reviewed repository "
                "and commit attestation"
            )
        if Path(reviewed_repository).resolve() != expected_root:
            raise AuditError(
                "v4 semantic review repository does not match the target repository"
            )
    if not skip_mechanical:
        mechanical = (
            run_v4_mechanical_checks(dispatcher, repo_root=repo_root)
            if v4_roots
            else run_mechanical_checks(dispatcher, repo_root=repo_root)
        )
        evidence.extend(mechanical)

    for target in resolved_targets:
        outcome, target_evidence = _audit_target(
            dispatcher,
            target,
            mechanical,
            timestamp,
            reviewed_repository=reviewed_repository,
            reviewed_commit=reviewed_commit,
        )
        outcomes.append(outcome)
        for item in target_evidence:
            if item not in evidence:
                evidence.append(item)
    return evidence, outcomes


def render_text(outcomes: Sequence[AuditOutcome]) -> str:
    lines = [
        "# Skill Audit Report",
        "",
        "| Source | Skill | Semantic | Stamp | Pool |",
        "|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(outcome.source),
                    markdown_cell(outcome.skill),
                    markdown_cell(outcome.semantic_status),
                    markdown_cell(outcome.stamp_status),
                    markdown_cell(outcome.pool_status),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify skill audit records.")
    parser.add_argument("command", choices=["certify"])
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--skip-mechanical", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timestamp", help=argparse.SUPPRESS)
    parser.add_argument(
        "--reviewed-repository",
        type=Path,
        help="Exact repository root whose v4 blueprints the LLM reviewed.",
    )
    parser.add_argument(
        "--reviewed-commit",
        help="Exact commit reviewed by the LLM before v4 finalization.",
    )
    return parser


def main(argv: Sequence[str] | None = None, dispatcher: Dispatcher | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    runtime = dispatcher or Interface()
    try:
        evidence, outcomes = certify(
            runtime,
            targets=args.targets,
            skip_mechanical=args.skip_mechanical,
            timestamp=args.timestamp,
            reviewed_repository=args.reviewed_repository,
            reviewed_commit=args.reviewed_commit,
        )
    except AuditError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    certified = [
        {**outcome.as_payload(), "status": "audit-current"}
        for outcome in outcomes
        if outcome.semantic_status == "passed"
        and outcome.stamp_status in {"reused", "written"}
    ]
    not_written = [
        outcome.as_payload()
        for outcome in outcomes
        if outcome.semantic_status == "passed" and outcome.stamp_status == "not-written"
    ]
    failed = [
        outcome.as_payload()
        for outcome in outcomes
        if outcome.semantic_status == "failed" or outcome.stamp_status == "failed"
    ]
    payload = {
        "ok": not failed,
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evidence": [result.as_payload() for result in evidence],
        "certified": certified,
        "not_written": not_written,
        "failed": failed,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(outcomes), end="")
    return 2 if failed else 0


class Interface(PythonArgvMachineInterface):
    """Dispatcher adapter for skill audit certification."""

    dispatches = {
        "compute-hashes": DispatchCall(
            caller_skill="skill-certifier",
            target_skill="skill-drift",
            interface="skill-drift.interface.compute-hashes",
            smoke_args=("compute-hashes", "--json"),
        ),
        "drift-status": DispatchCall(
            caller_skill="skill-certifier",
            target_skill="skill-drift",
            interface="skill-drift.interface.drift-status",
            smoke_args=("status", "--json"),
        ),
        "sync-blueprints": DispatchCall(
            caller_skill="skill-certifier",
            target_skill="skill-maker",
            interface="skill-maker.interface.sync-blueprints",
            smoke_args=("--check",),
        ),
    }

    def run(self, argv: list[str]) -> int:
        return main(argv, dispatcher=self)


if __name__ == "__main__":
    raise SystemExit(main())

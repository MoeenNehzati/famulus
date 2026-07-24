#!/usr/bin/env python3
"""Certificate issuer for blueprint-backed modules."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

from officina.common.certification_hashing import (
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_CHECK_REGISTRY,
    CertificationHashError,
    NodeHashState,
    compute_node_hash_states,
    compute_certification_basis_hash,
    derive_certifier_identity,
    expected_certifier_checks,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from officina.common.certificate_records import (
    certificate_public_key_root,
    canonical_certificate_envelope_bytes,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    provision_certificate_signing_material,
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
    load_repository_blueprint_graph,
)
from officina.common.certification_view import (
    CertificateCurrentnessView,
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
    PooledReviewValidationError,
    pooled_review_path,
    render_pooled_review,
)
from officina.runtime.python_machine_interface import (
    DispatchCall,
    PythonArgvMachineInterface,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_SCHEMA_VERSION = 1


class CertificationError(RuntimeError):
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


def _expected_file_hashes(
    snapshot: GitSnapshot | None,
    paths: Sequence[Path],
) -> dict[str, str]:
    """Capture the exact regular-file bytes passed to commit readiness."""

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
            expected[relative] = _v4_hash_bytes(path.read_bytes())
    return expected


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
            raise CertificationError(f"missing canonical v4 state for {node_id}")
        for dependency in state.dependency_hashes:
            target = dependency.get("target")
            if not isinstance(target, str) or target not in graph.nodes:
                raise CertificationError(f"invalid canonical v4 dependency for {node_id}")
            visit(target)
        ordered.append(node_id)

    for node_id in requested:
        if node_id not in graph.nodes:
            raise CertificationError(f"unknown exact v4 certification target: {node_id}")
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
        raise CertificationError(f"{node_id}: certificate subject requires a gateway path")
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
        raise CertificationError(f"{node_id}: canonical gate snapshot is unavailable")
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
        raise CertificationError(f"{gate_name} gate is unavailable") from exc
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
        raise CertificationError(f"{snapshot.node_id}: deterministic state is unavailable")
    reconstructed = _v4_gate_snapshot(
        snapshot.node_id,
        state,
        source_commit=snapshot.source_commit,
        certifier_identity=snapshot.certifier_identity,
    )
    if reconstructed != snapshot:
        raise CertificationError(f"{snapshot.node_id}: deterministic snapshot changed")
    if any(
        finding.subject_id in {node.node_id, *node.declaration.get("interfaces", {})}
        for finding in v4_certification_completeness_findings(graph)
    ):
        raise CertificationError(f"{snapshot.node_id}: deterministic completeness failed")
    return _passed_v4_check("deterministic")


def _v4_semantic_attestation(
    snapshot: V4GateSnapshot,
    *,
    reviewed_commit: str,
) -> dict[str, object]:
    """Record that the LLM attested this exact committed snapshot."""

    if not reviewed_commit or snapshot.source_commit != reviewed_commit:
        raise CertificationError(f"{snapshot.node_id}: semantic review does not match HEAD")
    return _passed_v4_check("semantic-review")


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
        raise CertificationError("v4 blueprint path escapes its repository") from exc


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
            raise CertificationError("local v4 input escapes the candidate repository")
        current = target_root
        for part in relative.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise CertificationError(f"unsafe local v4 input parent: {relative.as_posix()}")
        target = target_root / relative
        if target.exists() or target.is_symlink():
            raise CertificationError(f"local v4 input collides with commit: {relative.as_posix()}")
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
            raise CertificationError(
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
        except (GitMaterializationError, CertificationHashError, OSError, ValueError) as exc:
            raise CertificationError(f"mechanical commit cannot be reconstructed: {exc}") from exc
        if not mechanical_graph.nodes or any(
            node.declaration.get("schema_version") != 4
            for node in mechanical_graph.nodes.values()
        ):
            raise CertificationError("mechanical commit does not contain an all-v4 graph")
        ancestry = run_git(
            repo_root,
            "merge-base",
            "--is-ancestor",
            mechanical_commit,
            reviewed_commit,
            check=False,
        )
        if ancestry.returncode != 0:
            raise CertificationError("mechanical commit is not an ancestor of reviewed commit")
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
            raise CertificationError("cannot compare mechanical and reviewed commits")
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
            raise CertificationError(
                "semantic review may change only blueprint files: "
                + ", ".join(path.as_posix() for path in unexpected)
            )
        if v4_protected_projection(mechanical_graph) != v4_protected_projection(
            reviewed_graph
        ):
            raise CertificationError("semantic review changed the protected projection")


def _verify_executing_candidate_certifier(
    root: Path,
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
) -> None:
    executing = Path(__file__).resolve()
    try:
        executing_relative = executing.relative_to(root).as_posix()
    except ValueError as exc:
        raise CertificationError("executing certifier bytes are outside the candidate") from exc
    owners = [
        node_id
        for node_id, node in graph.nodes.items()
        if node.node_type == "behavioral_source"
        and node.gateway_path is not None
        and node.gateway_path.resolve() == executing
    ]
    if len(owners) != 1:
        raise CertificationError("executing certifier bytes have no unique candidate owner")
    executing_digest = "sha256:" + hashlib.sha256(executing.read_bytes()).hexdigest()
    owner_state = states.get(owners[0])
    if not isinstance(owner_state, NodeHashState) or not any(
        entry.get("path") == executing_relative
        and entry.get("digest") == executing_digest
        for entry in owner_state.input_manifest
    ):
        raise CertificationError("executing certifier bytes do not match the candidate manifest")


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
        raise CertificationError("non-atomic mode is diagnostic-only and cannot sign")

    root = Path(repo_root).resolve()
    if require_migration_review:
        atomic = run_git(
            root, "config", "--bool", "--get", "famulus.candidateAtomicGuarantee",
            check=False,
        )
        if atomic.returncode == 0 and atomic.stdout.strip() == b"false":
            raise CertificationError("non-atomic diagnostic candidate is non-certifiable")
        temp_root = Path(tempfile.gettempdir()).resolve()
        if not root.is_relative_to(temp_root):
            raise CertificationError(
                "private v4 certification is restricted to temporary repositories"
            )
    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        raise CertificationError("v4 certification requires the exact Git repository root")
    if snapshot.commit != reviewed_commit:
        raise CertificationError("v4 certification HEAD does not match the reviewed commit")
    mechanical_commit: str | None = None
    if require_migration_review:
        try:
            mechanical_commit = blueprint_v4_mechanical_commit(root)
        except GitMaterializationError as exc:
            raise CertificationError(
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
                raise CertificationError(
                    "private certificate writer accepts only all-v4 repositories"
                )
            completeness = v4_certification_completeness_findings(graph)
            if completeness:
                first = completeness[0]
                raise CertificationError(
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
        except CertificationHashError as exc:
            raise CertificationError(str(exc)) from exc
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
    pooled_review_relatives = {
        pooled_review_path(node.skill_root).relative_to(root)
        for node in graph.nodes.values()
        if node.node_type == "module"
    }

    def require_commit_readiness(current_snapshot: object, phase: str) -> None:
        readiness = check_commit_readiness(
            current_snapshot,
            ordered_tracked_paths,
            _expected_file_hashes(current_snapshot, ordered_tracked_paths),
            allow_non_atomic=allow_non_atomic,
        )
        if not readiness.stamp_worthy:
            raise CertificationError(
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
            raise CertificationError(f"local input changed {phase}")

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
            raise CertificationError(f"tracked certification input is unavailable: {path}") from exc
        tracked_claims[path] = (
            _v4_hash_bytes(payload),
            bool(metadata.st_mode & stat.S_IXUSR),
        )
    try:
        public_key_relative = Path(
            os.path.abspath(public_key_root)
        ).relative_to(root)
    except ValueError as exc:
        raise CertificationError("certificate public-key root is outside repository") from exc

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
            raise CertificationError(f"repository status is unavailable {phase}")
        for record in status.stdout.rstrip(b"\0").split(b"\0"):
            if not record:
                continue
            if not record.startswith(b"?? "):
                raise CertificationError(f"tracked repository state changed {phase}")
            try:
                relative = Path(os.fsdecode(record[3:]))
            except UnicodeError as exc:
                raise CertificationError(f"untracked repository state changed {phase}") from exc
            if relative.is_relative_to(public_key_relative) or (
                ".certificates" in relative.parts
                and relative.suffix == ".jsonl"
            ) or (
                relative.as_posix() in local_claims
            ) or (
                relative in pooled_review_relatives
            ):
                continue
            raise CertificationError(
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
            raise CertificationError(f"tracked certification index changed {phase}")
        for path, (expected_digest, expected_executable) in tracked_claims.items():
            try:
                metadata = path.lstat()
                payload = read_regular_file_bytes(
                    path,
                    allowed_root=root,
                    allow_non_atomic=allow_non_atomic,
                )
            except (AtomicWriteError, OSError) as exc:
                raise CertificationError(
                    f"tracked certification input changed {phase}: {path}"
                ) from exc
            if (
                _v4_hash_bytes(payload) != expected_digest
                or bool(metadata.st_mode & stat.S_IXUSR) != expected_executable
            ):
                raise CertificationError(
                    f"tracked certification input changed {phase}: {path}"
                )

    if Path(public_key_root).resolve() == certificate_public_key_root(root):
        key = provision_certificate_signing_material(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
    else:
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
            raise CertificationError(f"unsafe certificate output root: {certificate_root}") from exc
        if certificate_root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise CertificationError(f"unsafe certificate output root: {certificate_root}")
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
            raise CertificationError(f"{node_id}: certifier gate registry changed")
        if callable(before_append):
            before_append(node_id)
        if not snapshot_head_matches(snapshot):
            raise CertificationError("HEAD changed during certification")
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
                raise CertificationError("certificate log changed during certification")
        elif old_bytes is not None:
            raise CertificationError("certificate log changed during certification")
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
            raise CertificationError("certificate log changed during certification") from exc
        try:
            appended_metadata = log_path.lstat()
        except OSError as exc:
            raise CertificationError("post-write certificate log is unavailable") from exc
        if not stat.S_ISREG(appended_metadata.st_mode):
            raise CertificationError("post-write certificate log is not a regular file")
        if callable(after_append):
            after_append(node_id)
        final_snapshot = capture_git_snapshot(root)
        if (
            final_snapshot is None
            or final_snapshot.repo_root != root
            or final_snapshot.commit != snapshot.commit
        ):
            raise CertificationError("HEAD changed after certificate append")
        if normalized_checks[node_id] != expected_certifier_checks():
            raise CertificationError("certifier checks changed after certificate append")
        require_frozen_tracked_inputs("after certificate append")
        require_local_claims("after certificate append")
        try:
            final_metadata = log_path.lstat()
        except OSError as exc:
            raise CertificationError("post-write certificate log changed") from exc
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (appended_metadata.st_dev, appended_metadata.st_ino)
        ):
            raise CertificationError("post-write certificate log changed")
        expected_log_bytes = (old_bytes or b"") + frame
        if (
            read_regular_file_bytes(
                log_path,
                allowed_root=graph.nodes[node_id].skill_root,
                allow_non_atomic=allow_non_atomic,
            )
            != expected_log_bytes
        ):
            raise CertificationError("post-write certificate log changed")
        written.append(node_id)

    final_snapshot = capture_git_snapshot(root)
    if (
        final_snapshot is None
        or final_snapshot.repo_root != root
        or final_snapshot.commit != snapshot.commit
    ):
        raise CertificationError("HEAD changed after certification")
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
        raise CertificationError(
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
            raise CertificationError(
                f"post-write certificate verification failed for {node_id}"
            )
    pooled_view = CertificateCurrentnessView(final_report)
    for module_id in sorted(
        node_id
        for node_id in target_node_ids
        if node_id in final_graph.nodes
        and final_graph.nodes[node_id].node_type == "module"
    ):
        module = final_graph.nodes[module_id]
        path = pooled_review_path(module.skill_root)
        try:
            rendered = render_pooled_review(
                final_graph,
                pooled_view,
                root_id=module_id,
            ).encode("utf-8")
            atomic_replace_bytes(
                path,
                rendered,
                allowed_root=module.skill_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            )
            if read_regular_file_bytes(
                path,
                allowed_root=module.skill_root,
                allow_non_atomic=allow_non_atomic,
            ) != rendered:
                raise CertificationError(
                    f"{module_id}: post-write pooled review changed"
                )
        except (
            AtomicWriteError,
            OSError,
            PooledReviewValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise CertificationError(
                f"{module_id}: pooled review write failed: {exc}"
            ) from exc
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
        raise CertificationError("non-atomic diagnostic candidate is non-certifiable")
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not root.is_relative_to(temp_root):
        raise CertificationError("v4 migration workflow is restricted to temporary repositories")
    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        raise CertificationError("v4 migration workflow requires an isolated Git repository")
    dirty = run_git(root, "status", "--porcelain=v1", "-z", check=False)
    if dirty.returncode != 0 or dirty.stdout:
        raise CertificationError("v4 migration candidate must be clean")
    schema_root = root / "references" / "blueprint"
    try:
        graph = load_repository_blueprint_graph(root, schema_root=schema_root)
    except Exception as exc:
        raise CertificationError(f"v4 migration candidate graph is invalid: {exc}") from exc
    if not graph.nodes or any(
        node.declaration.get("schema_version") != 4 for node in graph.nodes.values()
    ):
        raise CertificationError("v4 migration workflow requires an all-v4 repository")
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
        raise CertificationError("candidate migration map cannot derive claim targets") from exc
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
            raise CertificationError("candidate migration map has ambiguous claim target")
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
        raise CertificationError("candidate authorized source overlay is unavailable") from exc
    renames = _v4_module_renames(root)
    tree = run_git(
        root, "ls-tree", "-r", "--name-only", "-z", root_commit, check=False
    )
    if tree.returncode != 0:
        raise CertificationError("candidate legacy root tree is unavailable")
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
            raise CertificationError(
                f"candidate legacy evidence is unavailable: {relative.as_posix()}"
            )
        try:
            document = yaml.safe_load(shown.stdout.decode("utf-8"))
        except (UnicodeError, yaml.YAMLError) as exc:
            raise CertificationError(
                f"candidate legacy evidence is invalid: {relative.as_posix()}"
            ) from exc
        summary = document.get("skill_interface") if isinstance(document, Mapping) else None
        if summary is None:
            continue
        if not isinstance(summary, Mapping):
            raise CertificationError(
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
            raise CertificationError(
                f"legacy skill_interface has no unique default target: {subject}"
            )
        for section in ("inputs", "outputs", "side_effects"):
            claims = summary.get(section, [])
            if not isinstance(claims, list) or not all(
                isinstance(claim, str) for claim in claims
            ):
                raise CertificationError(
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
        raise CertificationError("reviewed commit must strictly descend from mechanical baseline")
    message = run_git(root, "show", "-s", "--format=%B", reviewed_commit, check=False)
    if message.returncode != 0:
        raise CertificationError("reviewed certification commit message is unavailable")
    trailer = f"Famulus-Legacy-Claims-Reconciled: {digest}"
    lines = message.stdout.decode("utf-8").splitlines()
    if lines.count(trailer) != 1:
        raise CertificationError(
            "reviewed certification commit has unresolved legacy claims"
        )


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
        raise CertificationError("candidate HEAD does not match the reviewed commit")
    try:
        mechanical_commit = blueprint_v4_mechanical_commit(root)
    except GitMaterializationError as exc:
        raise CertificationError("candidate mechanical baseline is unavailable") from exc
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
        raise CertificationError(
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
            raise CertificationError(f"unsafe candidate key path component: {current}")
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
    """Small protocol for dispatcher-backed mechanical calls."""

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
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


@dataclass(frozen=True)
class TargetHash:
    module: str
    source: str
    package_root: Path
    module_root: Path
    hashes: dict[str, Any]

    @classmethod
    def from_payload(cls, item: Mapping[str, Any]) -> "TargetHash":
        module = item.get("skill")
        package_root = item.get("package_root")
        skills_root = item.get("skills_root")
        hashes = item.get("hashes")
        if (
            not isinstance(module, str)
            or not module
            or not isinstance(package_root, str)
            or not isinstance(skills_root, str)
            or not isinstance(hashes, dict)
        ):
            raise CertificationError("compute-hashes returned an invalid module record")
        return cls(
            module=module,
            source=str(item.get("source", "unknown")),
            package_root=Path(package_root),
            module_root=Path(skills_root) / module,
            hashes=hashes,
        )


@dataclass(frozen=True)
class NodeCertificationOutcome:
    node_id: str
    certificate_path: Path

    def as_payload(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "certificate_path": self.certificate_path.as_posix(),
        }


@dataclass(frozen=True)
class CertificationOutcome:
    module: str
    source: str
    module_root: Path
    nodes: tuple[NodeCertificationOutcome, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "source": self.source,
            "module_root": self.module_root.as_posix(),
            "status": "certified",
            "nodes": [node.as_payload() for node in self.nodes],
        }


def run_local_command(
    name: str,
    command: list[str],
    *,
    repo_root: Path = REPO_ROOT,
) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    completed = dispatcher.dispatch(
        "sync-blueprints",
        args=["--check"],
        text=True,
        check=False,
    )
    return CommandResult(
        "blueprint-sync",
        ["skill-maker.interface.sync-blueprints", "--check"],
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )


def run_v4_mechanical_checks(
    dispatcher: Dispatcher,
    *,
    repo_root: Path = REPO_ROOT,
) -> list[CommandResult]:
    """Run blueprint-conformance checks owned by certification."""

    results = [
        _blueprint_sync_check(dispatcher),
        run_local_command(
            "validators",
            [sys.executable, "validators/runner.py"],
            repo_root=repo_root,
        ),
    ]
    failed = [result for result in results if not result.passed]
    if failed:
        raise CertificationError(
            "mechanical certification checks failed: "
            + ", ".join(result.name for result in failed)
        )
    return results


def compute_hash_payload(
    dispatcher: Dispatcher,
    target: str | None = None,
) -> dict[str, Any]:
    args = ["compute-hashes", "--json"]
    if target:
        path = Path(target).expanduser()
        if (
            "/" in target
            or "\\" in target
            or target.startswith((".", "~"))
        ) and path.exists():
            args = [
                "compute-hashes",
                "--skill-root",
                str(path.resolve()),
                "--json",
            ]
        else:
            args = ["compute-hashes", target, "--json"]
    completed = dispatcher.dispatch(
        "compute-hashes",
        args=args,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CertificationError(
            (completed.stderr or completed.stdout or "compute-hashes failed").strip()
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CertificationError(f"compute-hashes did not return JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise CertificationError("compute-hashes payload is missing its module list")
    return payload


def _hash_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("skills")
    if not isinstance(items, list) or not all(
        isinstance(item, dict) for item in items
    ):
        raise CertificationError("compute-hashes returned invalid module records")
    return items


def collect_targets(
    dispatcher: Dispatcher,
    requests: Sequence[str],
) -> list[TargetHash]:
    items: list[dict[str, Any]] = []
    if requests:
        for request in requests:
            resolved = _hash_items(compute_hash_payload(dispatcher, request))
            if len(resolved) != 1:
                raise CertificationError(
                    f"explicit target {request!r} resolved to "
                    f"{len(resolved)} modules"
                )
            target = TargetHash.from_payload(resolved[0])
            path_like = (
                "/" in request
                or "\\" in request
                or request.startswith((".", "~"))
            )
            if path_like:
                if (
                    target.module_root.resolve()
                    != Path(request).expanduser().resolve()
                ):
                    raise CertificationError(
                        f"explicit target {request!r} resolved to the wrong module"
                    )
            elif target.module != request:
                raise CertificationError(
                    f"explicit target {request!r} resolved to {target.module!r}"
                )
            items.append(resolved[0])
    else:
        items.extend(_hash_items(compute_hash_payload(dispatcher)))

    result: list[TargetHash] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        target = TargetHash.from_payload(item)
        identity = (target.module, target.module_root.resolve().as_posix())
        if identity not in seen:
            seen.add(identity)
            result.append(target)
    if not result:
        raise CertificationError("no blueprint-backed modules were resolved")
    return result


def reviewed_repository_target_requests(
    graph: RepositoryBlueprintGraph,
    requests: Sequence[str],
) -> tuple[str, ...]:
    """Resolve requested modules only within the reviewed repository graph."""

    module_roots = tuple(
        sorted(
            {
                node.skill_root.resolve()
                for node in graph.nodes.values()
                if node.node_type == "module"
            },
            key=lambda path: path.as_posix(),
        )
    )
    if not module_roots:
        raise CertificationError("reviewed repository contains no v4 modules")
    if not requests:
        return tuple(root.as_posix() for root in module_roots)

    roots_by_name: dict[str, list[Path]] = {}
    for root in module_roots:
        roots_by_name.setdefault(root.name, []).append(root)

    resolved: list[str] = []
    for request in requests:
        path_like = (
            "/" in request
            or "\\" in request
            or request.startswith((".", "~"))
        )
        if path_like:
            candidate = Path(request).expanduser().resolve()
            matches = [root for root in module_roots if root == candidate]
        else:
            matches = roots_by_name.get(request, [])
        if len(matches) != 1:
            raise CertificationError(
                f"target {request!r} resolves to {len(matches)} modules "
                "in the reviewed repository"
            )
        resolved.append(matches[0].as_posix())
    return tuple(resolved)


def certify(
    dispatcher: Dispatcher,
    *,
    targets: Sequence[str],
    skip_mechanical: bool = False,
    timestamp: str | None = None,
    reviewed_repository: Path | None = None,
    reviewed_commit: str | None = None,
) -> tuple[list[CommandResult], list[CertificationOutcome]]:
    """Issue certificates for exact v4 module targets."""

    if reviewed_repository is None or reviewed_commit is None:
        raise CertificationError(
            "certification requires the exact LLM-reviewed repository and commit"
        )
    repository = Path(reviewed_repository).resolve()

    graph = load_repository_blueprint_graph(
        repository,
        schema_root=repository / "references" / "blueprint",
    )
    if any(
        node.declaration.get("schema_version") != 4
        for node in graph.nodes.values()
    ):
        raise CertificationError("certification accepts only an all-v4 repository")

    exact_requests = reviewed_repository_target_requests(graph, targets)
    resolved = collect_targets(dispatcher, exact_requests)
    repositories = {target.package_root.resolve() for target in resolved}
    if repositories != {repository}:
        raise CertificationError(
            "semantic review repository does not match the target repository"
        )

    target_nodes_by_module: dict[str, tuple[str, ...]] = {}
    requested_node_ids: set[str] = set()
    for target in resolved:
        node_ids = tuple(
            sorted(
                node_id
                for node_id, node in graph.nodes.items()
                if node.skill_root.resolve() == target.module_root.resolve()
            )
        )
        if not node_ids:
            raise CertificationError(f"{target.module}: module owns no v4 nodes")
        target_nodes_by_module[target.module] = node_ids
        requested_node_ids.update(node_ids)

    evidence = (
        []
        if skip_mechanical
        else run_v4_mechanical_checks(dispatcher, repo_root=repository)
    )
    result = _certify_v4_repository(
        repository,
        target_node_ids=tuple(sorted(requested_node_ids)),
        public_key_root=certificate_public_key_root(repository),
        secret_backend=None,
        reviewed_commit=reviewed_commit,
        certified_at=timestamp
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        require_candidate_execution=True,
        require_migration_review=False,
    )
    written = set(result.node_ids)
    missing = requested_node_ids - written
    if missing:
        raise CertificationError(
            "certifier did not issue every requested certificate: "
            + ", ".join(sorted(missing))
        )

    outcomes = [
        CertificationOutcome(
            module=target.module,
            source=target.source,
            module_root=target.module_root,
            nodes=tuple(
                NodeCertificationOutcome(
                    node_id=node_id,
                    certificate_path=certificate_log_path(graph.nodes[node_id]),
                )
                for node_id in result.node_ids
                if node_id in target_nodes_by_module[target.module]
            ),
        )
        for target in resolved
    ]
    return evidence, outcomes


def render_text(outcomes: Sequence[CertificationOutcome]) -> str:
    lines = [
        "# Certificate Report",
        "",
        "| Source | Module | Nodes |",
        "|---|---|---|",
    ]
    for outcome in outcomes:
        lines.append(
            f"| {outcome.source} | {outcome.module} | "
            + ", ".join(node.node_id for node in outcome.nodes)
            + " |"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue signed certificates for blueprint-backed modules."
    )
    parser.add_argument("command", choices=["certify"])
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-mechanical", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timestamp", help=argparse.SUPPRESS)
    parser.add_argument("--reviewed-repository", type=Path, required=True)
    parser.add_argument("--reviewed-commit", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    dispatcher: Dispatcher | None = None,
) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
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
    except CertificationError as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "error": str(exc)},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "ok": True,
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evidence": [item.as_payload() for item in evidence],
        "certified": [outcome.as_payload() for outcome in outcomes],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(outcomes), end="")
    return 0


class Interface(PythonArgvMachineInterface):
    """Dispatcher adapter for certificate issuance."""

    dispatches = {
        "compute-hashes": DispatchCall(
            caller_skill="skill-certifier",
            target_skill="skill-drift",
            interface="skill-drift.interface.compute-hashes",
            smoke_args=("compute-hashes", "--json"),
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

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
from typing import Any, Mapping, Sequence

from officina.common.certification_hashing import (
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_CHECK_REGISTRY,
    CertificationHashError,
    NodeHashState,
    certification_target_postorder,
    compute_node_hash_states,
    compute_certification_basis_hash,
    certifier_check_registry,
    derive_certifier_identity,
    expected_certifier_checks,
    map_route_smoke_dependencies,
    normalize_node_checks,
    resolve_certification_basis_paths,
    route_smoke_trace_signature,
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
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.certification_view import (
    CertificateCurrentnessView,
    certificate_log_path,
    evaluate_certificate_currentness,
)
from officina.common.git_provenance import (
    GitMaterializationError,
    GitSnapshot,
    blueprint_v4_mechanical_commit,
    capture_git_snapshot,
    check_commit_readiness,
    materialize_git_commit,
    run_git,
    snapshot_head_matches,
)
from officina.common.repository_paths import (
    RepositoryPathError,
    repository_relative_path,
)
from officina.common.pooled_blueprint import (
    PooledReviewValidationError,
    pooled_review_path,
    render_pooled_review,
)
from officina.common.process_binding_compiler import gateway_language_name
from officina.runtime.python_machine_interface import (
    PythonArgvMachineInterface,
    PythonProcessTarget,
    PythonProcessTargetError,
    PythonRouteSmokeTraceError,
    logical_python_package_name,
    trace_python_route_smoke_dependencies_batch,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_SCHEMA_VERSION = 1
RouteSmokeAuditResult = tuple[
    tuple[
        str,
        PythonProcessTarget,
        tuple[tuple[str, str, str | None], ...],
    ],
    ...,
]


class CertificationError(RuntimeError):
    """CertificationError marks certification-stop failures raised by this issuer.

    Intent
    ------
    Carry deliberate rejection messages through a dedicated exception type so CLI handling can distinguish certification denials from unexpected crashes.

    Rationale
    ---------
    Repository, provenance, hash, signing, and policy gates all reject by message; the shared type keeps those denials catchable without weakening the individual gate text.

    Pseudocode
    ----------
    - raise certification_rejection_message

    Wraps
    -----
    - none
    """


@dataclass(frozen=True)
class V4CertificationResult:
    """V4CertificationResult records the certificates written by the private issuer.

    Intent
    ------
    Store the source module, reviewed commit, module root, and per-node outcomes returned by the certificate writer.

    Rationale
    ---------
    The public API needs one immutable value that can be rendered as text or JSON without re-reading logs after issuance.

    Pseudocode
    ----------
    - set result_fields = module source module_root nodes
    - return issued_certificate_record

    Wraps
    -----
    - none
    """

    node_ids: tuple[str, ...]
    source_commit: str


@dataclass(frozen=True)
class V4GateSnapshot:
    """V4GateSnapshot freezes the evidence used by v4 gate checks.

    Intent
    ------
    Group node hash, source commit, manifest, dependency hashes, basis digest, and certifier identity into one gate input.

    Rationale
    ---------
    Deterministic and semantic gates must compare the same evidence bundle that later enters the signed certificate payload.

    Pseudocode
    ----------
    - set snapshot_fields = node_hash source_commit manifests identity
    - return snapshot_fields

    Wraps
    -----
    - none
    """

    node_id: str
    node_hash: str
    source_commit: str
    input_manifest: tuple[Mapping[str, object], ...]
    dependencies: tuple[Mapping[str, object], ...]
    certification_basis_hash: str
    certifier_identity: Mapping[str, object]


@dataclass(frozen=True)
class V4CompletenessFinding:
    """V4CompletenessFinding names one missing certification disclosure.

    Intent
    ------
    Record the node, blueprint path, subject, field, and message for a v4 completeness gap.

    Rationale
    ---------
    Completeness auditing reports all missing review material at once, so each finding needs enough location data for a human to repair the blueprint.

    Pseudocode
    ----------
    - set finding_fields = node_id subject field message
    - return finding_fields

    Wraps
    -----
    - none
    """

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
    """v4_certification_completeness_findings lists missing v4 signing disclosures.

    Intent
    ------
    Scan nodes and skill interfaces for descriptions, required contract sections, verification rows, and endpoint disclosures before signing.

    Rationale
    ---------
    The pre-signing gate returns a complete tuple of repair targets instead of hiding later missing fields behind the first failure.

    Pseudocode
    ----------
    - set findings = empty_collection
    - for node in graph_nodes:
      - set findings = findings_with_node_gaps
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .V4CompletenessFinding:
      why:
        constructs: "Each finding is a carried certification-completeness product identifying subject, blueprint path, field, and remediation message."
    """

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
    """v4_protected_projection extracts the v4 fields protected during semantic review.

    Intent
    ------
    Build a comparable projection of module ids, node kinds, gateway paths, dependencies, and interface contracts.

    Rationale
    ---------
    Semantic migration review may alter blueprint wording, but it must not silently change executable wiring or protected dependency structure.

    Pseudocode
    ----------
    - set projection = protected_node_and_interface_fields
    - return projection

    Wraps
    -----
    - none
    """

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


class _EphemeralSecretBackend:
    """_EphemeralSecretBackend keeps signing secrets in memory for tests.

    Intent
    ------
    Provide the secret-backend protocol used by certificate signing without touching a persistent key store.

    Rationale
    ---------
    Tests need deterministic isolation for generated keys; the namespace/key dictionary mimics storage while remaining process-local.

    Pseudocode
    ----------
    - set secret_table = empty_mapping
    - return backend

    Wraps
    -----
    - none
    """
    name = "ephemeral-v4-candidate"

    def __init__(self) -> None:
        """__init__ creates the in-memory namespace table.

        Intent
        ------
        Initialize the private mapping that stores test signing secrets by namespace and key.

        Rationale
        ---------
        The backend must start empty for each test run so key provisioning cannot leak across certification scenarios.

        Pseudocode
        ----------
        - set values = empty_mapping
        - return initialized_backend

        Wraps
        -----
        - none
        """
        self._values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        """store writes one namespaced secret value.

        Intent
        ------
        Persist the supplied bytes under the namespace/key pair used by the signing-material helpers.

        Rationale
        ---------
        The fake backend has to match the production lookup contract while avoiding filesystem or external secret-service dependencies.

        Pseudocode
        ----------
        - set stored_secret = secret_bytes
        - return stored_secret

        Wraps
        -----
        - none
        """
        self._values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        """lookup reads one namespaced secret value.

        Intent
        ------
        Return the bytes previously stored for the namespace/key pair, or no value when the key is absent.

        Rationale
        ---------
        Certificate helpers use lookup to decide whether to reuse signing material, so absence must be represented without raising.

        Pseudocode
        ----------
        - set value = values_for_namespace_key
        - return value

        Wraps
        -----
        - none
        """
        return self._values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        """clear removes one namespaced secret value.

        Intent
        ------
        Delete the stored bytes for a namespace/key pair and report whether anything was removed.

        Rationale
        ---------
        Tests can reset signing state through the same backend abstraction that production code uses for key lifecycle operations.

        Pseudocode
        ----------
        - set existed = remove_namespace_key
        - return existed

        Wraps
        -----
        - none
        """
        return self._values.pop((namespace, key), None) is not None


def _v4_hash_bytes(value: bytes) -> str:
    """_v4_hash_bytes returns the canonical SHA-256 label for byte evidence.

    Intent
    ------
    Convert an already-read byte payload into the prefixed digest string used in manifests and deterministic comparisons.

    Rationale
    ---------
    Keeping the prefixing rule in one helper prevents certificate evidence from mixing raw hexadecimal hashes with canonical digest labels.

    Pseudocode
    ----------
    - set digest = hashed_bytes
    - return prefixed_digest

    Wraps
    -----
    - none
    """
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _expected_file_hashes(
    snapshot: GitSnapshot | None,
    paths: Sequence[Path],
) -> dict[str, str]:
    """_expected_file_hashes computes digest expectations for regular files.

    Intent
    ------
    Walk candidate paths inside a Git snapshot and return repo-relative SHA-256 labels for files that still exist as regular files.

    Rationale
    ---------
    Commit-readiness checks compare expected and observed input hashes, so this helper filters missing, escaping, and non-file paths before hashing.

    Pseudocode
    ----------
    - set expected = empty_mapping
    - for path in input_paths:
      - set expected = expected_with_regular_file_digest
    - return expected

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._v4_hash_bytes:
      why:
        serializes: "The helper digest becomes the expected hash label for tracked file evidence."
    """

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


def _python_route_smoke_trace_specs(
    graph: RepositoryBlueprintGraph,
    certification_node_ids: Sequence[str],
) -> tuple[tuple[str, str, PythonProcessTarget], ...]:
    """_python_route_smoke_trace_specs selects Python process bindings for route-smoke tracing.

    Intent
    ------
    Walk requested behavioral-source nodes, reject unknown ids, ignore non-Python gateways, and emit unique process targets with interface ids.

    Rationale
    ---------
    Route-smoke auditing needs executable Python targets rather than raw blueprint fragments; this helper preserves ownership while normalizing gateway bindings.

    Pseudocode
    ----------
    - raise %officina.common.certification_hashing.CertificationHashError(invalid_route_smoke_subject)
    - set selected = requested_behavioral_sources
    - for interface in selected_interfaces:
      - if gateway_language_is_python:
        - set selected = selected_with_process_target
    - return selected

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.process_binding_compiler.gateway_language_name:
      why:
        computes: "Classifies the gateway language so only Python process bindings enter route-smoke tracing."

    InstantiationsFromRepo
    ----------------------
    officina.common.certification_hashing.CertificationHashError:
      why:
        raises: "Rejected route-smoke inputs leave the helper as a typed hash-policy error."
    officina.runtime.python_machine_interface.PythonProcessTarget:
      why:
        constructs: "The returned trace spec carries this process target into dependency tracing."
    officina.runtime.python_machine_interface.logical_python_package_name:
      why:
        transforms: "The v5 module id becomes logical package evidence stored on each constructed process target."
    """

    selected: set[str] = set()
    for node_id in certification_node_ids:
        if node_id not in graph.nodes:
            raise CertificationHashError(
                f"unknown route-smoke certification node: {node_id}"
            )
        selected.add(node_id)
    specifications: dict[tuple[str, PythonProcessTarget], str] = {}
    for node_id, node in sorted(graph.nodes.items()):
        if node_id not in selected:
            continue
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
            gateway_path = node.gateway_path.relative_to(
                node.module_root
            ).as_posix()
        except ValueError as exc:
            raise CertificationHashError(
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
                try:
                    logical_package = None
                    logical_entrypoint = None
                    if graph.schema_version == 5:
                        module_id = graph.source_modules[node_id]
                        logical_package = logical_python_package_name(module_id)
                        path = Path(gateway_path)
                        physical_parts = (
                            path.parent.parts
                            if path.name == "__init__.py"
                            else (*path.parent.parts, path.stem)
                        )
                        suffix = ".".join(
                            part
                            for part in physical_parts
                            if part not in {"", "."}
                        )
                        logical_entrypoint = (
                            logical_package
                            if not suffix
                            else f"{logical_package}.{suffix}"
                        )
                    python_target = PythonProcessTarget(
                        Path(gateway_path),
                        entry,
                        logical_package=logical_package,
                        logical_entrypoint=logical_entrypoint,
                    )
                except PythonProcessTargetError as exc:
                    raise CertificationHashError(
                        f"{node_id}: invalid Python process target: {exc}"
                    ) from exc
                specifications.setdefault(
                    (node_id, python_target),
                    str(interface_id),
                )
    return tuple(
        (node_id, interface_id, python_target)
        for (node_id, python_target), interface_id in sorted(
            specifications.items(),
            key=lambda item: (
                item[0][0],
                item[0][1].gateway_path.as_posix(),
                item[0][1].process_entry,
            ),
        )
    )


def audit_route_smoke_dependencies(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    repo_root: Path,
    certification_basis_paths: Sequence[Path],
    certification_node_ids: Sequence[str],
    schema_root: Path | None = None,
) -> RouteSmokeAuditResult:
    """audit_route_smoke_dependencies maps traced Python imports to certification dependencies.

    Intent
    ------
    Trace selected Python process targets, map loaded files back to blueprint dependencies, and return stable signatures for each node/interface pair.

    Rationale
    ---------
    The certifier must prove runtime imports seen during smoke execution are covered by the same node-hash graph that is about to be signed.

    Pseudocode
    ----------
    - set trace_specs = selected_route_smoke_targets
    - set traces = traced_python_process_dependencies
    - set mapped = mapped_blueprint_dependencies
    - return route_smoke_audit_rows

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._python_route_smoke_trace_specs:
      why:
        constructs: "Selected route-smoke specifications are carried into batch tracing and final audit rows."
    officina.common.certification_hashing.CertificationHashError:
      why:
        raises: "Trace and mapping failures leave the audit as a typed certification-hash rejection."
    officina.common.certification_hashing.map_route_smoke_dependencies:
      why:
        transforms: "Loaded Python paths become dependency mappings consumed by the signature step."
    officina.common.certification_hashing.route_smoke_trace_signature:
      why:
        serializes: "Mapped dependencies become the tuple signature returned in each route-smoke audit row."
    officina.runtime.python_machine_interface.trace_python_route_smoke_dependencies_batch:
      why:
        constructs: "Observed import traces are carried forward for dependency mapping."
    """

    root = Path(repo_root).resolve()
    trace_specs = _python_route_smoke_trace_specs(
        graph,
        certification_node_ids,
    )
    specifications = tuple(
        (graph.nodes[node_id].module_root, python_target)
        for node_id, _interface_id, python_target in trace_specs
    )
    try:
        trace_options = {}
        if graph.schema_version == 5:
            trace_options = {
                "expected_schema_version": 5,
                "schema_root": (
                    Path(schema_root)
                    if schema_root is not None
                    else root / "references" / "blueprint" / "v5"
                ),
            }
        traces = trace_python_route_smoke_dependencies_batch(
            root,
            specifications,
            **trace_options,
        )
    except (PythonRouteSmokeTraceError, ValueError) as exc:
        raise CertificationHashError(str(exc)) from exc
    results: list[
        tuple[
            str,
            str,
            tuple[tuple[str, str, str | None], ...],
        ]
    ] = []
    for node_id, _interface_id, python_target in trace_specs:
        key = (graph.nodes[node_id].module_root.resolve(), python_target)
        mappings = map_route_smoke_dependencies(
            graph,
            states,
            source_node_id=node_id,
            loaded_paths=traces[key],
            certification_basis_paths=certification_basis_paths,
            repo_root=root,
        )
        results.append(
            (
                node_id,
                python_target,
                route_smoke_trace_signature(mappings),
            )
        )
    return tuple(results)


def _v4_route_smoke_audit(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    repo_root: Path,
    certification_basis_paths: Sequence[Path],
    certification_node_ids: Sequence[str],
    schema_root: Path | None = None,
) -> RouteSmokeAuditResult:
    """_v4_route_smoke_audit runs route-smoke dependency auditing when configured.

    Intent
    ------
    Invoke the route-smoke mapper for selected certification nodes and convert hash-policy failures into certification failures.

    Rationale
    ---------
    Route-smoke evidence is optional by schema version, but when required it must fail before certificate payloads are signed.

    Pseudocode
    ----------
        - set audit_rows = traced_route_smoke_dependencies
        - return audit_rows

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Route-smoke tracing failures are converted into certification denials before gate evidence is recorded."
    .audit_route_smoke_dependencies:
      why:
        constructs: "Route-smoke audit rows become optional gate evidence for the private writer."
    """
    try:
        schema_options = (
            {"schema_root": schema_root}
            if graph.schema_version == 5
            else {}
        )
        return audit_route_smoke_dependencies(
            graph,
            states,
            repo_root=repo_root,
            certification_basis_paths=certification_basis_paths,
            certification_node_ids=certification_node_ids,
            **schema_options,
        )
    except CertificationHashError as exc:
        raise CertificationError(str(exc)) from exc


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
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_v4_payload builds the dictionary signed as a node certificate.

    Intent
    ------
    Gather subject paths, node-hash state, certifier identity, checks, key metadata, previous-entry linkage, and timestamp into one payload.

    Rationale
    ---------
    Signing code should receive a stable repository-relative payload shape, with missing gateway paths rejected before envelope serialization.

    Pseudocode
    ----------
    - raise %.CertificationError(missing_gateway_path)
    - set payload_paths = repository_relative_subject_paths
    - return certificate_payload_mapping

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Converts subject blueprint and gateway paths into repository-relative payload strings."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "A certificate subject without a gateway path leaves as a typed certifier rejection."
    """
    node = graph.nodes[node_id]
    state = states[node_id]
    if node.gateway_path is None:
        raise CertificationError(f"{node_id}: certificate subject requires a gateway path")
    return {
        "certificate_schema_version": (
            2 if expected_schema_version == 5 else 1
        ),
        "subject": {
            "id": node.node_id,
            "node_type": node.node_type,
            "version": node.version,
            "blueprint_path": repository_relative_path(
                node.blueprint_path,
                repo_root,
            ).as_posix(),
            "gateway_path": repository_relative_path(
                node.gateway_path,
                repo_root,
            ).as_posix(),
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
    """_v4_gate_snapshot builds the evidence view consumed by v4 gates.

    Intent
    ------
    Extract one node state, basis hash, source commit, manifests, dependencies, and certifier identity for deterministic gate evaluation.

    Rationale
    ---------
    Gate helpers should not reach back into the whole graph when checking one node; this snapshot gives them the bounded evidence they need.

    Pseudocode
    ----------
        - raise %.CertificationError(missing_gate_state)
        - set snapshot = node_gate_evidence
        - return snapshot

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Missing node state or basis evidence rejects the snapshot before deterministic gates can read it."
    .V4GateSnapshot:
      why:
        constructs: "The snapshot object packages one node evidence bundle for deterministic comparisons."
    """
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


def _passed_v4_check(
    gate_name: str,
    *,
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_passed_v4_check creates a passed check record from the certifier registry.

    Intent
    ------
    Resolve a gate name against the schema-versioned registry and return the normalized passed-check mapping.

    Rationale
    ---------
    Certificate payloads store check ids and versions, not ad hoc labels, so missing registry entries must reject before signing.

    Pseudocode
    ----------
    - set registry = versioned_check_registry
    - raise %.CertificationError(missing_gate)
    - return passed_check_mapping

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.certification_hashing.certifier_check_registry:
      why:
        validates: "Selects the versioned check registry used to authorize the requested gate name."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "A missing gate name leaves this helper as a typed certifier rejection."
    """
    registry = (
        CERTIFIER_CHECK_REGISTRY
        if expected_schema_version == 4
        else certifier_check_registry(expected_schema_version)
    )
    try:
        check_id, version = registry[gate_name]
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
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_v4_deterministic_check validates one node against deterministic v4 evidence.

    Intent
    ------
    Compare recomputed node hashes, input manifests, dependency hashes, basis digest, and certifier identity for a gate snapshot.

    Rationale
    ---------
    A certificate is meaningful only if the deterministic evidence at signing time equals the reviewed evidence captured in the node state.

    Pseudocode
    ----------
        - set expected = reviewed_gate_snapshot
        - if observed_evidence_differs:
          - raise %.CertificationError(deterministic_mismatch)
        - return passed_check

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .v4_certification_completeness_findings:
      why:
        orchestrates: "The completeness scan confirms schema disclosures before deterministic evidence is accepted."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Hash, manifest, dependency, basis, or identity mismatches reject the deterministic gate."
    ._passed_v4_check:
      why:
        constructs: "The passed-check row records the successful gate name and registry version."
    ._v4_gate_snapshot:
      why:
        constructs: "The reconstructed snapshot provides the observed evidence compared against the reviewed snapshot."
    """

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
    return _passed_v4_check(
        "deterministic",
        expected_schema_version=expected_schema_version,
    )


def _v4_semantic_attestation(
    snapshot: V4GateSnapshot,
    *,
    reviewed_commit: str,
    expected_schema_version: int = 4,
) -> dict[str, object]:
    """_v4_semantic_attestation creates the semantic-review check row when migration review is required.

    Intent
    ------
    Run semantic-attestation replay for v4 migration certificates and return the corresponding passed gate record.

    Rationale
    ---------
    The migration path allows reviewed blueprint edits only when replay proves the protected projection stayed stable.

    Pseudocode
    ----------
        - if migration_review_required:
          - set attestation = semantic_replay_result
        - return semantic_check_row

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Failed semantic replay rejects migration-review certification before a passed check is emitted."
    ._passed_v4_check:
      why:
        constructs: "The passed-check row records the successful gate name and registry version."
    """

    if not reviewed_commit or snapshot.source_commit != reviewed_commit:
        raise CertificationError(f"{snapshot.node_id}: semantic review does not match HEAD")
    return _passed_v4_check(
        "semantic-review",
        expected_schema_version=expected_schema_version,
    )


def _v4_blueprint_paths(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
) -> set[Path]:
    """_v4_blueprint_paths collects blueprint paths contained in a graph.

    Intent
    ------
    Convert every graph node blueprint path into a repository-relative path set.

    Rationale
    ---------
    Semantic-attestation diff checks need a compact allowlist of blueprint files so unrelated reviewed changes are rejected.

    Pseudocode
    ----------
        - set paths = empty_set
        - for node in graph_nodes:
          - set paths = paths_with_repo_relative_blueprint
        - return paths

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.common.repository_paths.repository_relative_path:
      why:
        constructs: "The path normalizer converts blueprint locations into the allowlist used by semantic attestation."
    .CertificationError:
      why:
        raises: "Blueprint paths that escape the repository reject the semantic-attestation allowlist."
    """
    try:
        return {
            repository_relative_path(node.blueprint_path, repo_root)
            for node in graph.nodes.values()
        }
    except RepositoryPathError as exc:
        raise CertificationError("v4 blueprint path escapes its repository") from exc


def _materialize_v4_local_inputs(
    source_root: Path,
    target_root: Path,
    states: Mapping[str, NodeHashState],
    *,
    allow_non_atomic: bool,
) -> None:
    """_materialize_v4_local_inputs copies untracked v4 inputs into a reconstructed commit tree.

    Intent
    ------
    Find local manifest entries, reject unsafe paths or collisions, read each byte payload, and write it under the temporary mechanical tree.

    Rationale
    ---------
    Semantic-attestation replay must restore declared local evidence exactly while preserving repository-boundary and atomic-write guarantees.

    Pseudocode
    ----------
    - raise %.CertificationError(unsafe_local_input)
    - set copied_inputs = declared_local_manifest_entries
    - for input_path in copied_inputs:
      - set copied_inputs = copied_inputs_with_materialized_bytes
    - return copied_inputs

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.atomic_files.atomic_replace_bytes:
      why:
        writes: "Writes each declared local input into the reconstructed tree after path and collision checks."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Unsafe or unreadable local-input materialization leaves as a typed certifier rejection."
    officina.common.atomic_files.read_regular_file_bytes:
      why:
        serializes: "The copied byte payload is read once and carried into the bounded atomic write."
    """

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
    """_validate_v4_semantic_attestation replays the mechanical baseline behind a reviewed v4 commit.

    Intent
    ------
    Materialize the mechanical commit, restore local inputs, load its graph, verify ancestry, restrict changed files, and compare protected projections.

    Rationale
    ---------
    The semantic-review certificate depends on proving that review commits changed only allowed blueprint text and not executable protected structure.

    Pseudocode
    ----------
    - set mechanical_tree = materialized_mechanical_commit
    - set mechanical_graph = loaded_mechanical_blueprint_graph
    - set changed_paths = reviewed_diff_paths
    - if protected_projection_changed:
      - raise %.CertificationError(semantic_attestation_failed)
    - return attestation_passed

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._materialize_v4_local_inputs:
      why:
        writes: "Restores declared local inputs into the temporary mechanical tree before graph loading."
    ._v4_blueprint_paths:
      why:
        computes: "Builds the allowed blueprint-path set used to reject unrelated reviewed-file changes."
    .v4_protected_projection:
      why:
        validates: "Compares protected graph projections after path-level review checks pass."
    officina.common.git_provenance.materialize_git_commit:
      why:
        writes: "Expands the mechanical commit into the temporary attestation workspace."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Any failed replay, ancestry, diff, or projection check leaves as a typed certifier rejection."
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why:
        constructs: "The replayed graph provides the baseline projection compared against the reviewed graph."
    officina.common.git_provenance.run_git:
      why:
        constructs: "Ancestry and changed-path command results are carried into attestation branch decisions."
    """

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
                expected_schema_version=4,
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
    """_verify_executing_candidate_certifier proves the running certifier belongs to the candidate graph.

    Intent
    ------
    Locate this file inside the candidate root, find its behavioral-source owner, enforce module ownership, and compare its digest to the manifest.

    Rationale
    ---------
    Self-certification is valid only when the process issuing certificates is itself the source file represented by the candidate node hash.

    Pseudocode
    ----------
    - set executing_relative = repository_relative_certifier_path
    - if owner_or_digest_mismatch:
      - raise %.CertificationError(candidate_certifier_mismatch)
    - return ownership_verified

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Normalizes the executing certifier path before matching it against manifest entries."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Path escapes, missing ownership, wrong module ownership, and digest mismatch leave as typed rejections."
    """
    executing = Path(__file__).resolve()
    try:
        executing_relative = repository_relative_path(executing, root).as_posix()
    except RepositoryPathError as exc:
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
    expected_owner = {
        5: "skill-certifier-rtx",
        6: "skill-certifier._rtx",
    }.get(graph.schema_version)
    if (
        expected_owner is not None
        and graph.source_modules.get(owners[0]) != expected_owner
    ):
        raise CertificationError(
            f"executing v{graph.schema_version} certifier source must belong to "
            f"{expected_owner}"
        )
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
    require_migration_review: bool = False,
    expected_schema_version: int = 6,
    schema_root: Path | None = None,
) -> V4CertificationResult:
    """_certify_v4_repository issues signed certificates for selected v4 or v5 nodes.

    Intent
    ------
    Freeze repository state, derive graph hashes and certifier identity, run gates, sign each target payload, and append certificate log entries.

    Rationale
    ---------
    This private writer binds Git inputs, local evidence, key material, pooled reviews, and log tails into one exact reviewed commit before signing.

    Pseudocode
    ----------
        - raise %.CertificationError(rejected_repository_or_gate)
        - set derived = graph_hashes_identity_and_basis
        - set gate_rows = deterministic_and_route_smoke_checks
        - set envelope = signed_certificate_envelope
        - return certification_result

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._expected_file_hashes:
      why:
        computes: "Builds digest expectations for tracked inputs during readiness checks."
    ._v4_hash_bytes:
      why:
        computes: "Hashes tracked and local input bytes for freeze checks."
    ._validate_v4_semantic_attestation:
      why:
        validates: "Replays the mechanical baseline before semantic-review certificates are allowed."
    ._verify_executing_candidate_certifier:
      why:
        validates: "Confirms candidate self-certification is running the owned certifier source."
    officina.common.atomic_files.atomic_compare_and_append_bytes:
      why:
        writes: "Appends each signed envelope only if the current log tail matches the expected entry hash."
    officina.common.atomic_files.atomic_replace_bytes:
      why:
        writes: "Publishes generated pooled-review artifacts and public-key bytes within bounded roots."
    officina.common.atomic_files.read_regular_file_bytes:
      why:
        computes: "Reads frozen tracked and local inputs for digest comparison."
    officina.common.certificate_records.certificate_public_key_root:
      why:
        computes: "Locates the public-key directory used when the writer provisions signing material."
    officina.common.certification_hashing.expected_certifier_checks:
      why:
        validates: "Checks that the gate list matches the schema-versioned registry."
    officina.common.git_provenance.snapshot_head_matches:
      why:
        validates: "Confirms later Git snapshots still match the reviewed commit before and after writes."
    officina.common.pooled_blueprint.pooled_review_path:
      why:
        computes: "Selects per-module pooled-review artifact paths that must be tolerated during freeze checks."
    officina.common.pooled_blueprint.render_pooled_review:
      why:
        serializes: "Renders pooled blueprint review bytes before the bounded artifact write."
    officina.common.repository_paths.repository_relative_path:
      why:
        transforms: "Normalizes certification input and artifact paths against the repository root."

    InstantiationsFromRepo
    ----------------------
    ._passed_v4_check:
      why:
        constructs: "Creates normalized pass records only after each gate succeeds."
    ._v4_deterministic_check:
      why:
        constructs: "Recomputes deterministic node evidence before signing each payload."
    ._v4_semantic_attestation:
      why:
        constructs: "Builds the optional semantic-attestation check record for v4 migration certificates."
    .CertificationError:
      why:
        raises: "Repository, graph, key, freeze, gate, signing, or append failures leave as typed certifier rejections."
    .V4CertificationResult:
      why:
        constructs: "The returned result carries the written node ids and reviewed commit to public callers."
    ._v4_gate_snapshot:
      why:
        constructs: "Gate snapshots carry node-hash evidence into deterministic and payload construction."
    ._v4_payload:
      why:
        constructs: "Payload mappings are carried into envelope serialization and signing."
    ._v4_route_smoke_audit:
      why:
        constructs: "The route-smoke audit result is compared for stability and recorded as passed gate evidence."
    ._v4_hash_bytes:
      why:
        serializes: "Input byte digests are carried into frozen-input comparisons and certificate evidence."
    .v4_certification_completeness_findings:
      why:
        constructs: "Completeness findings are carried into the pre-signing rejection branch."
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why:
        constructs: "The loaded graph drives target expansion, hash derivation, and certificate-log placement."
    officina.common.certificate_records.canonical_certificate_envelope_bytes:
      why:
        serializes: "Canonical envelope bytes are carried into signing, entry hashing, and log append."
    officina.common.certificate_records.certificate_entry_hash:
      why:
        serializes: "Entry hashes are carried into log-tail verification and the next append expectation."
    officina.common.certificate_records.load_or_create_certificate_signing_key:
      why:
        constructs: "Signing keys are carried into payload signing and public-key provisioning."
    officina.common.certificate_records.parse_certificate_log:
      why:
        constructs: "Existing log entries are carried into previous-entry hash and currentness checks."
    officina.common.certificate_records.provision_certificate_signing_material:
      why:
        constructs: "Provisioned key metadata selects the signer identity and public verification material for each envelope."
    officina.common.certificate_records.sign_certificate_payload:
      why:
        serializes: "Payload signatures are carried into canonical envelope construction."
    officina.common.certification_hashing.certification_target_postorder:
      why:
        transforms: "Expanded target ids become the dependency-respecting certification order."
    officina.common.certification_hashing.compute_certification_basis_hash:
      why:
        serializes: "The basis digest binds node hashes and signed payloads to the same reviewed support files."
    officina.common.certification_hashing.compute_node_hash_states:
      why:
        constructs: "Node hash states carry manifests, dependency hashes, and basis hashes through every gate."
    officina.common.certification_hashing.derive_certifier_identity:
      why:
        constructs: "The derived identity ties gate evidence and signed payloads to the issuing certifier implementation."
    officina.common.certification_hashing.normalize_node_checks:
      why:
        transforms: "Raw check rows become normalized payload check lists per node."
    officina.common.certification_hashing.resolve_certification_basis_paths:
      why:
        transforms: "Resolved basis paths feed basis hashing, freeze checks, and route-smoke mapping."
    officina.common.certification_view.CertificateCurrentnessView:
      why:
        constructs: "Currentness records are carried into stale-certificate rejection decisions."
    officina.common.certification_view.certificate_log_path:
      why:
        constructs: "Per-node log paths are carried into log parsing and append writes."
    officina.common.certification_view.evaluate_certificate_currentness:
      why:
        constructs: "Currentness evaluation results decide whether a node can be skipped or must be rewritten."
    officina.common.git_provenance.blueprint_v4_mechanical_commit:
      why:
        constructs: "The baseline commit identifies the tree that semantic review is allowed to refine."
    officina.common.git_provenance.capture_git_snapshot:
      why:
        constructs: "Git snapshots are carried into HEAD matching, readiness checks, and status freezes."
    officina.common.git_provenance.check_commit_readiness:
      why:
        constructs: "Readiness results are carried into tracked-input rejection decisions."
    officina.common.git_provenance.run_git:
      why:
        constructs: "Git command results are carried into status, index, and branch-gate decisions."
    """

    root = Path(repo_root).resolve()
    if expected_schema_version not in {4, 5, 6}:
        raise CertificationError(
            f"unsupported certification schema version: {expected_schema_version}"
        )
    if require_migration_review and expected_schema_version != 4:
        raise CertificationError("the frozen migration writer accepts only v4")
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

    def porcelain_status_records(phase: str) -> tuple[bytes, ...]:
        """porcelain_status_records returns raw porcelain status records for a freeze phase.

Intent
------
Run `git status --porcelain=v1 -z --untracked-files=all`, reject an unavailable status command, and return nonempty raw records for the caller's phase-specific checks.

Rationale
---------
The enclosing writer needs byte-preserving status records so it can distinguish preexisting untracked files from new certificate artifacts without losing undecodable path evidence.

Pseudocode
----------
- set status = Git status command result
- raise %.CertificationError(status_unavailable)
- return records_from(status)

Wraps
-----
- none

InstantiationsFromRepo
----------------------
.CertificationError:
  why:
    raises: "Unavailable Git status leaves as a typed freeze-phase rejection."
officina.common.git_provenance.run_git:
  why:
    constructs: "The status command result is parsed into the returned raw record tuple."
        """
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
        return tuple(
            record
            for record in status.stdout.rstrip(b"\0").split(b"\0")
            if record
        )

    initial_untracked_records: set[bytes] = set()
    for record in porcelain_status_records("before certification"):
        if not record.startswith(b"?? "):
            raise CertificationError(
                "tracked repository state changed before certification"
            )
        try:
            os.fsdecode(record[3:])
        except UnicodeError as exc:
            raise CertificationError(
                "untracked repository state changed before certification"
            ) from exc
        initial_untracked_records.add(record)

    mechanical_commit: str | None = None
    if require_migration_review:
        try:
            mechanical_commit = blueprint_v4_mechanical_commit(root)
        except GitMaterializationError as exc:
            raise CertificationError(
                f"candidate mechanical baseline is unavailable: {exc}"
            ) from exc
    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else (
            root / "references" / "blueprint"
            if expected_schema_version == 6
            else root / "references" / "blueprint" / "migrations" / f"v{expected_schema_version}"
        )
    )
    policy_path = root / CANONICAL_NODE_HASH_POLICY

    def derive() -> tuple[
        RepositoryBlueprintGraph,
        dict[str, NodeHashState],
        str,
        tuple[Path, ...],
        dict[str, object],
    ]:
        """derive loads graph evidence for the private certificate writer.

        Intent
        ------
        Load the schema-versioned graph, reject incomplete v4 material, resolve basis paths, compute hashes, and derive certifier identity together.

        Rationale
        ---------
        The writer signs evidence that must come from one coherent graph/basis/identity bundle, so this closure computes those values in one place.

        Pseudocode
        ----------
        - set graph = loaded_repository_blueprint_graph
        - set basis = resolved_certification_basis
        - set states = computed_node_hash_states
        - return graph states basis identity

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._verify_executing_candidate_certifier:
          why:
            validates: "When candidate execution is required, confirms the running certifier belongs to the derived graph state."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Graph, completeness, basis, hash, or identity derivation failures leave as typed certifier rejections."
        .v4_certification_completeness_findings:
          why:
            constructs: "Completeness findings are inspected before hash evidence is accepted."
        officina.common.blueprint_graph.load_repository_blueprint_graph:
          why:
            constructs: "The loaded graph is returned with all derived certification evidence."
        officina.common.certification_hashing.compute_certification_basis_hash:
          why:
            serializes: "The basis hash is returned and fed into node-state computation."
        officina.common.certification_hashing.compute_node_hash_states:
          why:
            constructs: "Node hash states are returned for gate checks and payload construction."
        officina.common.certification_hashing.derive_certifier_identity:
          why:
            constructs: "Certifier identity is returned for snapshots and payloads."
        officina.common.certification_hashing.resolve_certification_basis_paths:
          why:
            transforms: "Resolved basis paths are returned for hashing and later freeze checks."
        """
        try:
            graph = load_repository_blueprint_graph(
                root,
                schema_root=selected_schema_root,
                expected_schema_version=expected_schema_version,
            )
            if not graph.nodes or any(
                node.declaration.get("schema_version") != expected_schema_version
                for node in graph.nodes.values()
            ):
                raise CertificationError(
                    "private certificate writer accepts only a closed "
                    f"all-v{expected_schema_version} repository"
                )
            completeness = v4_certification_completeness_findings(graph)
            if completeness:
                first = completeness[0]
                raise CertificationError(
                    f"v{expected_schema_version} certification completeness failed: "
                    f"{first.subject_id}:{first.field} "
                    f"({len(completeness)} finding(s))"
                )
            basis_paths = resolve_certification_basis_paths(
                root,
                expected_schema_version=expected_schema_version,
                allow_non_atomic=allow_non_atomic,
            )
            basis_hash = compute_certification_basis_hash(
                root,
                expected_schema_version=expected_schema_version,
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
    try:
        expanded_target_ids = set(target_node_ids)
        for node_id in tuple(expanded_target_ids):
            node = graph.nodes.get(node_id)
            if node is not None and node.node_type == "module":
                expanded_target_ids.update(graph.module_sources[node_id])
        order = certification_target_postorder(
            graph,
            states,
            tuple(expanded_target_ids),
        )
    except CertificationHashError as exc:
        raise CertificationError(str(exc)) from exc
    initial_route_smoke_audit = _v4_route_smoke_audit(
        graph,
        states,
        repo_root=root,
        certification_basis_paths=basis_paths,
        certification_node_ids=order,
        schema_root=selected_schema_root,
    )
    repeated_route_smoke_audit = _v4_route_smoke_audit(
        graph,
        states,
        repo_root=root,
        certification_basis_paths=basis_paths,
        certification_node_ids=order,
        schema_root=selected_schema_root,
    )
    if repeated_route_smoke_audit != initial_route_smoke_audit:
        raise CertificationError(
            "route-smoke dependency audit changed during certification"
        )
    normalized_checks: dict[str, tuple[dict[str, object], ...]] = {}

    tracked_paths: set[Path] = {
        root / repository_relative_path(path, root)
        for path in (
            *basis_paths,
            *(node.blueprint_path for node in graph.nodes.values()),
        )
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
        repository_relative_path(
            pooled_review_path(node.module_root),
            root,
        )
        for node in graph.nodes.values()
        if node.node_type == "module"
    }

    def require_commit_readiness(current_snapshot: object, phase: str) -> None:
        """require_commit_readiness enforces that tracked inputs match the reviewed commit.

        Intent
        ------
        Compare readiness findings with expected input hashes before allowing certificate writes.

        Rationale
        ---------
        Signing must stop if tracked files changed after review, because otherwise the certificate would attest to bytes different from the reviewed basis.

        Pseudocode
        ----------
                - set findings = commit_readiness_findings
                - if findings_are_not_clean:
                  - raise %.CertificationError(tracked_input_mismatch)
                - return readiness_passed

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._expected_file_hashes:
          why:
            computes: "expected_file_hashes computes comparison material used by this certifier branch."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Dirty tracked inputs reject signing before certificate logs are opened."
        officina.common.git_provenance.check_commit_readiness:
          why:
            constructs: "officina.common.git_provenance.check_commit_readiness supplies a carried value for this certification step."
        """
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
        """require_local_claims validates declared local input evidence.

        Intent
        ------
        Check untracked manifest entries against local file bytes and reject missing or mismatched claims.

        Rationale
        ---------
        Local inputs are outside Git history, so the certificate writer must compare their declared digests immediately before signing.

        Pseudocode
        ----------
                - set local_claims = untracked_manifest_entries
                - if local_claims_mismatch:
                  - raise %.CertificationError(local_input_mismatch)
                - return local_claims_valid

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._v4_hash_bytes:
          why:
            computes: "v4_hash_bytes computes comparison material used by this certifier branch."
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            orchestrates: "The bounded file read obtains local input bytes for direct digest comparison."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Missing or mismatched local evidence rejects signing for untracked input claims."
        """
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
        public_key_relative = repository_relative_path(
            Path(os.path.abspath(public_key_root)),
            root,
        )
    except BlueprintGraphError as exc:
        raise CertificationError("certificate public-key root is outside repository") from exc

    def is_pooled_review_temp(relative: Path) -> bool:
        """is_pooled_review_temp identifies generated pooled-review artifacts.

        Intent
        ------
        Return whether a path is one of the temporary review files tolerated during freeze checks.

        Rationale
        ---------
        The writer may create pooled review artifacts while still rejecting unrelated dirtiness in the reviewed repository.

        Pseudocode
        ----------
        - set tolerated = path_matches_pooled_review_area
        - return tolerated

        Wraps
        -----
        - none
        """
        name = relative.name
        if not name.startswith("..pooled-blueprint-review.yaml.tmp-"):
            return False
        final = relative.parent / ".pooled-blueprint-review.yaml"
        return final in pooled_review_relatives

    def require_frozen_tracked_inputs(phase: str) -> None:
        """require_frozen_tracked_inputs rejects tracked-input drift during signing.

        Intent
        ------
        Read expected tracked inputs and compare their current bytes to the frozen reviewed snapshot.

        Rationale
        ---------
        Certificate logs should never be appended after a tracked input mutates, even if the mutation occurs between earlier readiness checks and signing.

        Pseudocode
        ----------
                - set frozen_hashes = expected_tracked_input_hashes
                - if tracked_bytes_changed:
                  - raise %.CertificationError(frozen_input_drift)
                - return tracked_inputs_frozen

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._v4_hash_bytes:
          why:
            computes: "v4_hash_bytes computes comparison material used by this certifier branch."

        InstantiationsFromRepo
        ----------------------
        .CertificationError:
          why:
            raises: "Tracked-byte drift rejects signing at the final frozen-input gate."
        officina.common.atomic_files.read_regular_file_bytes:
          why:
            serializes: "The bounded byte read feeds the frozen-input hash check immediately before signing."
        officina.common.git_provenance.run_git:
          why:
            constructs: "officina.common.git_provenance.run_git supplies a carried value for this certification step."
        """
        current_preexisting_records: set[bytes] = set()
        for record in porcelain_status_records(phase):
            if not record:
                continue
            if not record.startswith(b"?? "):
                raise CertificationError(f"tracked repository state changed {phase}")
            try:
                relative = Path(os.fsdecode(record[3:]))
            except UnicodeError as exc:
                raise CertificationError(f"untracked repository state changed {phase}") from exc
            if record in initial_untracked_records:
                current_preexisting_records.add(record)
                continue
            if relative.is_relative_to(public_key_relative) or (
                ".certificates" in relative.parts
                and relative.suffix == ".jsonl"
            ) or (
                relative.as_posix() in local_claims
            ) or (
                relative in pooled_review_relatives
            ) or (
                is_pooled_review_temp(relative)
            ):
                continue
            raise CertificationError(
                f"untracked repository state changed {phase}: {relative}"
            )
        if current_preexisting_records != initial_untracked_records:
            raise CertificationError(f"untracked repository state changed {phase}")
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
            certificate_root.resolve().relative_to(graph.nodes[node_id].module_root.resolve())
        except (OSError, ValueError) as exc:
            raise CertificationError(f"unsafe certificate output root: {certificate_root}") from exc
        if certificate_root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise CertificationError(f"unsafe certificate output root: {certificate_root}")
        old_bytes: bytes | None = None
        previous_hash = None
        if log_path.exists():
            old_bytes = read_regular_file_bytes(
                log_path,
                allowed_root=graph.nodes[node_id].module_root,
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
            _v4_deterministic_check(
                gate_snapshot,
                graph=graph,
                states=states,
                expected_schema_version=expected_schema_version,
            ),
            _passed_v4_check(
                "route-smoke",
                expected_schema_version=expected_schema_version,
            ),
            _v4_semantic_attestation(
                gate_snapshot,
                reviewed_commit=reviewed_commit,
                expected_schema_version=expected_schema_version,
            ),
        )
        normalized_checks[node_id] = normalize_node_checks(gate_records)
        if normalized_checks[node_id] != expected_certifier_checks(
            expected_schema_version
        ):
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
                    allowed_root=graph.nodes[node_id].module_root,
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
            expected_schema_version=expected_schema_version,
        )
        envelope = sign_certificate_payload(payload, key)
        frame = canonical_certificate_envelope_bytes(envelope) + b"\n"
        try:
            atomic_compare_and_append_bytes(
                log_path,
                frame,
                expected_previous_bytes=old_bytes,
                allowed_root=graph.nodes[node_id].module_root,
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
        if normalized_checks[node_id] != expected_certifier_checks(
            expected_schema_version
        ):
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
                    allowed_root=graph.nodes[node_id].module_root,
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
        certification_basis_paths=final_basis_paths,
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
        path = pooled_review_path(module.module_root)
        try:
            rendered = render_pooled_review(
                final_graph,
                pooled_view,
                root_id=module_id,
            ).encode("utf-8")
            atomic_replace_bytes(
                path,
                rendered,
                allowed_root=module.module_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            )
            if read_regular_file_bytes(
                path,
                allowed_root=module.module_root,
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


@dataclass(frozen=True)
class CommandResult:
    """CommandResult stores one mechanical command outcome.

    Intent
    ------
    Keep command name, argv, exit status, stdout, and stderr together for certification evidence rendering.

    Rationale
    ---------
    Mechanical checks are evidence even when they fail, so callers need a stable object that can report both status and captured output.

    Pseudocode
    ----------
    - set command_fields = name argv exit_code stdout stderr
    - return command_fields

    Wraps
    -----
    - none
    """
    name: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        """passed reports whether the command exited successfully.

        Intent
        ------
        Convert the stored process exit code into the boolean used by certification summaries.

        Rationale
        ---------
        Rendering and certification flow should not duplicate the success convention for local command evidence.

        Pseudocode
        ----------
        - set success = exit_code_is_zero
        - return success

        Wraps
        -----
        - none
        """
        return self.exit_code == 0

    def as_payload(self) -> dict[str, Any]:
        """as_payload serializes command evidence for JSON output.

        Intent
        ------
        Return the command name, argv, exit code, stdout, and stderr as primitive payload fields.

        Rationale
        ---------
        The CLI JSON path needs mechanical evidence without dataclass instances or process objects leaking into the response.

        Pseudocode
        ----------
        - set payload = command_evidence_mapping
        - return payload

        Wraps
        -----
        - none
        """
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


@dataclass(frozen=True)
class NodeCertificationOutcome:
    """NodeCertificationOutcome records the certificate path for one node.

    Intent
    ------
    Store the certified node id and log path belonging to a module-level certification result.

    Rationale
    ---------
    Public reporting groups node outcomes by module but still needs the exact certificate log written for each node.

    Pseudocode
    ----------
    - set node_fields = node_id certificate_path
    - return node_fields

    Wraps
    -----
    - none
    """
    node_id: str
    certificate_path: Path

    def as_payload(self) -> dict[str, str]:
        """as_payload serializes one node certification outcome.

        Intent
        ------
        Return the node id and certificate path as primitive JSON-ready fields.

        Rationale
        ---------
        The CLI should expose node-level certificate locations without requiring callers to understand dataclass internals.

        Pseudocode
        ----------
        - set payload = node_certificate_mapping
        - return payload

        Wraps
        -----
        - none
        """
        return {
            "node_id": self.node_id,
            "certificate_path": self.certificate_path.as_posix(),
        }


@dataclass(frozen=True)
class CertificationOutcome:
    """CertificationOutcome groups certification results for one source module.

    Intent
    ------
    Store the module id, source name, module root, and node outcomes returned by certification.

    Rationale
    ---------
    The public API reports by module because a user target can expand into several certified node logs.

    Pseudocode
    ----------
    - set outcome_fields = module source root nodes
    - return outcome_fields

    Wraps
    -----
    - none
    """
    module: str
    source: str
    module_root: Path
    nodes: tuple[NodeCertificationOutcome, ...]

    def as_payload(self) -> dict[str, Any]:
        """as_payload serializes a module certification outcome.

        Intent
        ------
        Return module metadata and node payloads as a JSON-ready mapping.

        Rationale
        ---------
        The JSON renderer needs a stable primitive shape while text rendering can still use the typed outcome object.

        Pseudocode
        ----------
        - set node_payloads = serialized_node_outcomes
        - return module_payload

        Wraps
        -----
        - none
        """
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
    """run_local_command executes a named local command and captures its result.

    Intent
    ------
    Run the provided argv in the requested repository root without raising on nonzero exit, then package the captured process output.

    Rationale
    ---------
    Mechanical checks must remain visible in certification output even when they fail, so command evidence is returned rather than thrown away.

    Pseudocode
    ----------
    - set completed = completed_subprocess_result
    - return %.CommandResult(completed)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CommandResult:
      why:
        constructs: "The returned command result carries argv, exit status, stdout, and stderr into check evidence."
    """
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


def run_v4_mechanical_checks(
    repo_root: Path = REPO_ROOT,
) -> CommandResult:
    """run_v4_mechanical_checks runs the local validators required before certification.

    Intent
    ------
    Execute the configured command checks in the reviewed repository and return their captured results.

    Rationale
    ---------
    Certification should include the validator evidence that justified issuance, not only the final certificate paths.

    Pseudocode
    ----------
        - set results = executed_mechanical_checks
        - return command_evidence

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Unavailable local validators reject mechanical evidence collection for certification."
    .run_local_command:
      why:
        constructs: "Captured command results become the mechanical evidence returned to callers."
    """

    result = run_local_command(
        "validators",
        [sys.executable, "repo_checks.py", "--suite", "validators"],
        repo_root=repo_root,
    )
    if not result.passed:
        raise CertificationError(
            f"mechanical certification checks failed: {result.name}"
        )
    return result


def resolve_reviewed_repository_targets(
    graph: RepositoryBlueprintGraph,
    requests: Sequence[str],
) -> tuple[BlueprintNode, ...]:
    """resolve_reviewed_repository_targets maps user target names to reviewed graph nodes.

    Intent
    ------
    Resolve module names, source ids, and node ids against the reviewed blueprint graph while rejecting ambiguity.

    Rationale
    ---------
    The public CLI accepts user-facing target strings, but the private writer needs concrete behavioral-source nodes.

    Pseudocode
    ----------
        - set resolved = matching_reviewed_targets
        - return resolved

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Unknown or ambiguous target names reject public certification before signing."
    """

    module_nodes = tuple(
        sorted(
            (
                node
                for node in graph.nodes.values()
                if node.node_type == "module"
            ),
            key=lambda node: (node.node_id, node.module_root.as_posix()),
        )
    )
    if not module_nodes:
        raise CertificationError("reviewed repository contains no v4 modules")
    if not requests:
        return module_nodes

    resolved: list[BlueprintNode] = []
    seen: set[tuple[str, Path]] = set()
    for request in requests:
        path_like = (
            "/" in request
            or "\\" in request
            or request.startswith((".", "~"))
        )
        if path_like:
            candidate = Path(request).expanduser().resolve()
            matches = [
                node
                for node in module_nodes
                if node.module_root.resolve() == candidate
            ]
        else:
            matches = [
                node
                for node in module_nodes
                if node.node_id == request
                or (
                    graph.schema_version == 4
                    and node.module_root.name == request
                )
            ]
        if len(matches) != 1:
            raise CertificationError(
                f"target {request!r} resolves to {len(matches)} modules "
                "in the reviewed repository"
            )
        target = matches[0]
        identity = (target.node_id, target.module_root.resolve())
        if identity not in seen:
            seen.add(identity)
            resolved.append(target)
    return tuple(resolved)


def certify(
    *,
    targets: Sequence[str],
    timestamp: str | None = None,
    reviewed_repository: Path | None = None,
    reviewed_commit: str | None = None,
    allow_non_atomic: bool = False,
) -> tuple[list[CommandResult], list[CertificationOutcome]]:
    """certify runs the public reviewed-repository certification flow.

    Intent
    ------
    Require reviewed repository inputs, load the v6 graph, resolve requested modules, run mechanical checks, invoke the writer, and shape outcomes.

    Rationale
    ---------
    The dispatcher-facing API keeps argument validation and report construction outside the private signing routine while preserving commit-specific evidence.

    Pseudocode
    ----------
        - raise %.CertificationError(missing_or_invalid_reviewed_input)
        - set graph = reviewed_blueprint_graph
        - set resolved = concrete_certification_targets
        - set evidence = mechanical_check_results
        - set issued = private_writer_result
        - return certification_evidence_and_outcomes

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.certificate_records.certificate_public_key_root:
      why:
        computes: "Supplies the public-key root passed into the private writer."
    officina.common.certification_view.certificate_log_path:
      why:
        computes: "Maps each certified node to the log path reported in public outcomes."

    InstantiationsFromRepo
    ----------------------
    .CertificationError:
      why:
        raises: "Missing reviewed inputs, non-v6 graphs, private-writer failure, or incomplete issuance leave as typed API rejections."
    .CertificationOutcome:
      why:
        constructs: "Module-level outcomes are returned to CLI rendering or JSON output."
    .NodeCertificationOutcome:
      why:
        constructs: "Node-level certificate paths are carried inside returned module outcomes."
    ._certify_v4_repository:
      why:
        constructs: "The private writer result determines which requested nodes were actually issued."
    .resolve_reviewed_repository_targets:
      why:
        transforms: "User target requests become concrete module nodes for expansion and reporting."
    .run_v4_mechanical_checks:
      why:
        constructs: "Validator command evidence is returned alongside certification outcomes."
    officina.common.blueprint_graph.load_repository_blueprint_graph:
      why:
        constructs: "The reviewed v6 graph drives target resolution and outcome path lookup."
    """

    if reviewed_repository is None or reviewed_commit is None:
        raise CertificationError(
            "certification requires the exact LLM-reviewed repository and commit"
        )
    repository = Path(reviewed_repository).resolve()

    graph = load_repository_blueprint_graph(
        repository,
        schema_root=repository / "references" / "blueprint",
        expected_schema_version=6,
    )
    if any(
        node.declaration.get("schema_version") != 6
        for node in graph.nodes.values()
    ):
        raise CertificationError("certification accepts only an all-v6 repository")

    resolved = resolve_reviewed_repository_targets(graph, targets)

    target_nodes_by_module: dict[str, tuple[str, ...]] = {}
    requested_node_ids: set[str] = set()
    for target in resolved:
        node_ids = tuple(
            sorted(
                node_id
                for node_id, node in graph.nodes.items()
                if node.module_root.resolve() == target.module_root.resolve()
            )
        )
        if not node_ids:
            raise CertificationError(f"{target.node_id}: module owns no certifiable nodes")
        target_nodes_by_module[target.node_id] = node_ids
        requested_node_ids.update(node_ids)

    evidence = [run_v4_mechanical_checks(repository)]
    result = _certify_v4_repository(
        repository,
        target_node_ids=tuple(sorted(requested_node_ids)),
        public_key_root=certificate_public_key_root(repository),
        secret_backend=None,
        reviewed_commit=reviewed_commit,
        certified_at=timestamp
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        allow_non_atomic=allow_non_atomic,
        require_candidate_execution=True,
        require_migration_review=False,
        expected_schema_version=6,
        schema_root=repository / "references" / "blueprint",
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
            module=target.node_id,
            source="reviewed-repository",
            module_root=target.module_root.resolve(),
            nodes=tuple(
                NodeCertificationOutcome(
                    node_id=node_id,
                    certificate_path=certificate_log_path(graph.nodes[node_id]),
                )
                for node_id in result.node_ids
                if node_id in target_nodes_by_module[target.node_id]
            ),
        )
        for target in resolved
    ]
    return evidence, outcomes


def render_text(outcomes: Sequence[CertificationOutcome]) -> str:
    """render_text formats certification outcomes for terminal output.

    Intent
    ------
    Convert module and node outcomes into stable human-readable lines.

    Rationale
    ---------
    The CLI text path should summarize certificate locations without changing the JSON payload contract.

    Pseudocode
    ----------
    - set lines = rendered_outcome_lines
    - return lines

    Wraps
    -----
    - none
    """
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
    """build_parser constructs the skill-certifier CLI parser.

    Intent
    ------
    Declare reviewed-repository, target, timestamp, JSON, and atomicity options for the command entrypoint.

    Rationale
    ---------
    Parser construction is isolated so tests and the runtime interface share the same argument contract.

    Pseudocode
    ----------
    - set parser = configured_argument_parser
    - return parser

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(
        description="Issue signed certificates for blueprint-backed modules."
    )
    parser.add_argument("command", choices=["certify"])
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-non-atomic", action="store_true")
    parser.add_argument("--timestamp", help=argparse.SUPPRESS)
    parser.add_argument("--reviewed-repository", type=Path, required=True)
    parser.add_argument("--reviewed-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """main coordinates CLI parsing, certification, rendering, and exit status.

    Intent
    ------
    Parse argv, call the public certification API, render JSON or text output, and translate certification failures into process status.

    Rationale
    ---------
    The entrypoint is the boundary where structured certification exceptions become user-facing command results.

    Pseudocode
    ----------
        - set args = parsed_cli_arguments
        - set outcomes = certification_api_result
        - return process_exit_status

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .build_parser:
      why:
        orchestrates: "The parser defines the CLI argument contract consumed by the entrypoint."
    .render_text:
      why:
        serializes: "render_text formats certification evidence without becoming the returned product of main."

    InstantiationsFromRepo
    ----------------------
    .certify:
      why:
        constructs: "The certification API result supplies evidence and outcomes for CLI rendering."
    """
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        evidence, outcomes = certify(
            targets=args.targets,
            timestamp=args.timestamp,
            reviewed_repository=args.reviewed_repository,
            reviewed_commit=args.reviewed_commit,
            allow_non_atomic=args.allow_non_atomic,
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
    """Interface exposes the skill-certifier argv machine boundary.

    Intent
    ------
    Provide the dispatcher-visible runtime object that forwards argv into the module CLI entrypoint.

    Rationale
    ---------
    The class keeps the interface contract explicit while leaving command semantics in the module-level parser and main routine.

    Pseudocode
    ----------
    - set interface = argv_machine_boundary
    - return interface

    Wraps
    -----
    - none
    """

    def run(self, argv: list[str]) -> int:
        """run delegates dispatcher argv to the module entrypoint.

        Intent
        ------
        Forward the received argument vector to the CLI main function and return its process status unchanged.

        Rationale
        ---------
        This method is intentionally a thin runtime adapter; documenting the wrap edge makes the handoff visible in graphs.

        Pseudocode
        ----------
        - return @.main(argv)

        Wraps
        -----
        .main -> preprocess: receives dispatcher argv without translating command semantics; postprocess: returns the module entrypoint exit status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

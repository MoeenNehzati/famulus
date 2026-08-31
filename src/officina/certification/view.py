"""Read-only certification boundary consumed by dispatch and projection."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import jsonschema

from .hashing import (
    CertificationHashError,
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_NODE_ID,
    NodeHashState,
    certification_facet_claims,
    certification_target_postorder,
    compute_certification_basis_hash,
    compute_node_hash_states,
    derive_certifier_identity,
    EVIDENCE_ONLY_RELATIONS,
    expected_certifier_checks,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from ..common.atomic_files import AtomicWriteError, read_regular_file_bytes
from .records import (
    CertificateLogError,
    certificate_public_key_root,
    parse_certificate_log,
)
from ..blueprints.graph import (
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from ..blueprints.authorization import AuthorizationResult
from ..blueprints.template import load_schema, schema_validator
from ..git.provenance import capture_git_snapshot, check_commit_readiness
from ..common.repository_paths import RepositoryPathError, repository_relative_posix


@dataclass(frozen=True)
class CertificationDecision:
    certified: bool
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("certification decisions require nonempty code and message")


@dataclass(frozen=True)
class CurrentCertificate:
    node_id: str
    version: int | None
    node_hash: str
    certificate_hash: str
    certified_at: str | None = None


@dataclass(frozen=True)
class CertificateInputDelta:
    """Represent one exact canonical input-manifest change.

    Intent
    ------
    Preserve the repository-relative path, change class, and both signed and
    current manifest entries needed to explain file drift without rereading it.

    Rationale
    ---------
    A hash mismatch says that content changed; certification consumers also
    need to distinguish added, removed, and modified inputs deterministically.

    Pseudocode
    ----------
    - store change, path, certified entry, and current entry

    Wraps
    -----
    - none
    """

    change: str
    path: str
    certified: Mapping[str, str] | None
    current: Mapping[str, str] | None


@dataclass(frozen=True)
class CertificateDependencyDelta:
    """Represent one exact canonical dependency-claim change.

    Intent
    ------
    Retain the canonical dependency identity and both dependency records for
    an added, removed, or modified dependency.

    Rationale
    ---------
    Node-wide dependency mismatch concerns cannot identify which provider or
    used interface changed, or whether its pin appeared, disappeared, or
    drifted.

    Pseudocode
    ----------
    - store change, relation, target, optional interface, and both claims

    Wraps
    -----
    - none
    """

    change: str
    relation: str
    target: str
    interface: str | None
    certified: Mapping[str, object] | None
    current: Mapping[str, object] | None


@dataclass(frozen=True)
class CertificateFacetDrift:
    """Group structured drift under one interface, remainder, or node facet.

    Intent
    ------
    Attribute local-hash, declaration, input-file, and dependency
    changes to the smallest canonical certification facet available.

    Rationale
    ---------
    Facet ownership lets drift readers identify the exact audit unit while a
    concrete blueprint path explains declaration-only hash changes.

    Pseudocode
    ----------
    - store facet identity and local-hash/declaration flags
    - attach exact file and dependency deltas

    Wraps
    -----
    - none
    """

    facet_id: str
    facet_type: str
    local_hash_changed: bool = False
    declaration_changed: bool = False
    blueprint_path: str | None = None
    input_files: tuple[CertificateInputDelta, ...] = ()
    dependencies: tuple[CertificateDependencyDelta, ...] = ()


@dataclass(frozen=True)
class CertificateNodeCurrentness:
    """Describe certificate currentness and exact drift for one node.

    Intent
    ------
    Preserve the existing currentness verdict and concerns while exposing the
    smallest available node- or facet-level causes of certificate drift.

    Rationale
    ---------
    Callers need a stable compatibility surface for current/stale checks and
    structured evidence for selective bottom-up recertification.

    Pseudocode
    ----------
    - store the legacy verdict, concerns, and certificate record
    - attach node-level file, declaration, and dependency changes
    - attach facet-level drift when schema-v6 evidence is available

    Wraps
    -----
    - none
    """

    node_id: str
    current: bool
    concerns: tuple[str, ...]
    certificate: Mapping[str, object] | None
    local_hash_changed: bool = False
    declaration_changed: bool = False
    blueprint_path: str | None = None
    input_files: tuple[CertificateInputDelta, ...] = ()
    dependencies: tuple[CertificateDependencyDelta, ...] = ()
    facet_drift: tuple[CertificateFacetDrift, ...] = ()


@dataclass(frozen=True)
class CertificateCurrentnessReport:
    """Collect scoped currentness plus its stale dependency closure.

    Intent
    ------
    Keep requested-node results under ``nodes`` while retaining external stale
    dependencies and their canonical dependency-first worklist.

    Rationale
    ---------
    Drift callers must preserve exact-scope reporting and still audit stale
    providers before consumers that depend on them.

    Pseudocode
    ----------
    - store requested node results
    - store the dependency-first stale worklist
    - store external stale dependency results separately

    Wraps
    -----
    - none
    """

    nodes: Mapping[str, CertificateNodeCurrentness]
    stale_worklist: tuple[str, ...] = ()
    dependency_nodes: Mapping[str, CertificateNodeCurrentness] = field(
        default_factory=dict
    )

    @property
    def current(self) -> bool:
        return bool(self.nodes) and all(status.current for status in self.nodes.values())


class CertificationView(Protocol):
    def check_authorization(
        self,
        authorization: AuthorizationResult,
    ) -> CertificationDecision: ...

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str,
    ) -> CertificationDecision: ...

    def certificate_for(self, node_id: str) -> CurrentCertificate | None: ...


class CertificateRecordView:
    """Read-only adapter over already verified current certificate records."""

    def __init__(
        self,
        records: Mapping[str, Mapping[str, object]],
        *,
        expected_node_hashes: Mapping[str, str] | None = None,
    ) -> None:
        self._certificates: dict[str, CurrentCertificate] = {}
        expected = expected_node_hashes or {}
        for node_id, record in records.items():
            payload = record.get("payload")
            subject = payload.get("subject") if isinstance(payload, Mapping) else None
            node_hash = payload.get("node_hash") if isinstance(payload, Mapping) else None
            version = subject.get("version") if isinstance(subject, Mapping) else None
            certified_at = payload.get("certified_at") if isinstance(payload, Mapping) else None
            if (
                not isinstance(subject, Mapping)
                or subject.get("id") != node_id
                or (
                    version is not None
                    and (
                        not isinstance(version, int)
                        or isinstance(version, bool)
                    )
                )
                or not isinstance(node_hash, str)
            ):
                continue
            expected_hash = expected.get(node_id)
            if expected_hash is not None and expected_hash != node_hash:
                continue
            canonical = json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            self._certificates[node_id] = CurrentCertificate(
                node_id=node_id,
                version=version,
                node_hash=node_hash,
                certificate_hash="sha256:" + hashlib.sha256(canonical).hexdigest(),
                certified_at=certified_at if isinstance(certified_at, str) else None,
            )

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        return self._certificates.get(node_id)

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str,
    ) -> CertificationDecision:
        del interface_version
        certificate = self.certificate_for(module_id)
        if certificate is None:
            return CertificationDecision(
                False,
                "certification-unavailable",
                f"{module_id}: no current certificate for {interface_id}",
            )
        source_certificate = self.certificate_for(source_node_id)
        if source_certificate is None:
            return CertificationDecision(
                False,
                "source-certification-unavailable",
                f"{source_node_id}: no current certificate for {interface_id}",
            )
        return CertificationDecision(True, "current", "Current certificate.")

    def check_authorization(
        self,
        authorization: AuthorizationResult,
    ) -> CertificationDecision:
        """Check exactly the resolver-owned v6 certificate requirement set."""

        if not authorization.allowed:
            return CertificationDecision(
                False,
                "authorization-denied",
                authorization.diagnostic,
            )
        missing = tuple(
            requirement
            for requirement in sorted(authorization.required_certificates)
            if (
                (certificate := self.certificate_for(requirement.node_id))
                is None
                or certificate.version != requirement.version
            )
        )
        if missing:
            required = ", ".join(
                f"{item.node_id}@{item.version}" for item in missing
            )
            return CertificationDecision(
                False,
                "authorization-certification-unavailable",
                f"required current certificate unavailable: {required}",
            )
        return CertificationDecision(
            True,
            "current",
            "All resolver-required certificates are current.",
        )


def certificate_log_path(node: BlueprintNode) -> Path:
    """Return the sole append-only certificate log path for one v6 node."""

    if any(separator in node.node_id for separator in ("/", "\\")):
        raise ValueError(f"certificate node ID contains a path separator: {node.node_id}")
    return node.module_root / ".certificates" / f"{node.node_id}.jsonl"


def _default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "references" / "blueprint-schema"


def _relative_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    try:
        return repository_relative_posix(path, repo_root)
    except RepositoryPathError as exc:
        raise ValueError(f"certificate subject path is outside repository: {path}") from exc


def _expected_subject(node: BlueprintNode, repo_root: Path) -> dict[str, object]:
    return {
        "id": node.node_id,
        "node_type": node.node_type,
        "version": node.version,
        "blueprint_path": _relative_path(node.blueprint_path, repo_root),
        "gateway_path": _relative_path(node.gateway_path, repo_root),
    }


def _expected_checks(
    node_id: str,
    checks_by_node: Mapping[str, Sequence[Mapping[str, object]]],
) -> list[dict[str, object]]:
    try:
        return [dict(check) for check in normalize_node_checks(checks_by_node.get(node_id, ()))]
    except CertificationHashError as exc:
        raise ValueError(f"{node_id}: invalid expected certification checks: {exc}") from exc


def _certifier_currentness_identity(
    certifier_identity: Mapping[str, object],
    *,
    structured: bool = False,
) -> dict[str, object]:
    return {
        key: value
        for key, value in certifier_identity.items()
        if key != "source_commit" and (key != "node_hash" or not structured)
    }


def _input_file_deltas(
    certified_manifest: object,
    current_manifest: object,
) -> tuple[CertificateInputDelta, ...]:
    """Compare canonical manifest entries by repository-relative path.

    Intent
    ------
    Produce stable added, removed, and modified file records from two signed
    manifest projections.

    Rationale
    ---------
    Paths are the persistent input identity; digests and provenance are values
    whose differences make an existing path modified.

    Pseudocode
    ----------
    - index valid entries by path
    - compare the sorted union of paths
    - return one exact delta for every unequal entry

    Wraps
    -----
    - none
    """

    def indexed(value: object) -> dict[str, Mapping[str, str]]:
        if not isinstance(value, (list, tuple)):
            return {}
        return {
            str(entry["path"]): dict(entry)
            for entry in value
            if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
        }

    certified = indexed(certified_manifest)
    current = indexed(current_manifest)
    deltas: list[CertificateInputDelta] = []
    for path in sorted(certified.keys() | current.keys()):
        before = certified.get(path)
        after = current.get(path)
        if before == after:
            continue
        change = "added" if before is None else "removed" if after is None else "modified"
        deltas.append(
            CertificateInputDelta(
                change=change,
                path=path,
                certified=before,
                current=after,
            )
        )
    return tuple(deltas)


def _dependency_deltas(
    certified_dependencies: object,
    current_dependencies: object,
) -> tuple[CertificateDependencyDelta, ...]:
    """Compare dependency records by canonical dependency identity.

    Intent
    ------
    Report exact additions, removals, and modifications for both interface-
    hash and node-hash dependency claims.

    Rationale
    ---------
    Relation, target, and optional interface identify the logical dependency;
    version and hash changes remain values and are reported as modifications.

    Pseudocode
    ----------
    - index records by relation, target, and optional interface
    - compare the sorted union of dependency identities
    - retain both certified and current records for every delta

    Wraps
    -----
    - none
    """

    DependencyIdentity = tuple[str, str, str | None]

    def indexed(value: object) -> dict[DependencyIdentity, Mapping[str, object]]:
        if not isinstance(value, (list, tuple)):
            return {}
        return {
            (
                str(entry["relation"]),
                str(entry["target"]),
                str(entry["interface"])
                if isinstance(entry.get("interface"), str)
                else None,
            ): dict(entry)
            for entry in value
            if isinstance(entry, Mapping)
            and isinstance(entry.get("relation"), str)
            and isinstance(entry.get("target"), str)
        }

    certified = indexed(certified_dependencies)
    current = indexed(current_dependencies)
    deltas: list[CertificateDependencyDelta] = []
    for relation, target, interface in sorted(
        certified.keys() | current.keys(),
        key=lambda identity: (identity[0], identity[1], identity[2] or ""),
    ):
        identity = (relation, target, interface)
        before = certified.get(identity)
        after = current.get(identity)
        if before == after:
            continue
        change = "added" if before is None else "removed" if after is None else "modified"
        deltas.append(
            CertificateDependencyDelta(
                change=change,
                relation=relation,
                target=target,
                interface=interface,
                certified=before,
                current=after,
            )
        )
    return tuple(deltas)


def _facet_drift(
    payload_facets: object,
    state: NodeHashState,
    *,
    blueprint_path: str | None,
) -> tuple[CertificateFacetDrift, ...]:
    """Derive exact v3 facet deltas without guessing declaration changes.

    Intent
    ------
    Compare signed and current facet claims, retaining exact file and
    dependency changes plus confirmed declaration-only causes.

    Rationale
    ---------
    A changed local hash with an unchanged manifest proves that the canonical
    declaration projection changed; a simultaneous manifest change does not.

    Pseudocode
    ----------
    - index certified and current claims by facet type and ID
    - derive file and dependency deltas for every changed facet
    - attach the source blueprint only when declaration drift is confirmed

    Wraps
    -----
    - none
    """

    expected_claims = certification_facet_claims(state)
    expected = {
        (claim["type"], claim["id"]): claim for claim in expected_claims
    }
    certified_claims = payload_facets if isinstance(payload_facets, list) else []
    certified = {
        (claim.get("type"), claim.get("id")): claim
        for claim in certified_claims
        if isinstance(claim, Mapping)
        and isinstance(claim.get("type"), str)
        and isinstance(claim.get("id"), str)
    }
    drift: list[CertificateFacetDrift] = []
    keys = sorted(
        certified.keys() | expected.keys(),
        key=lambda item: (item[0] != "remainder", str(item[1])),
    )
    for facet_type, facet_id in keys:
        before = certified.get((facet_type, facet_id))
        after = expected.get((facet_type, facet_id))
        certified_manifest = before.get("input_manifest", []) if before else []
        current_manifest = after.get("input_manifest", []) if after else []
        certified_dependencies = before.get("dependencies", []) if before else []
        current_dependencies = after.get("dependencies", []) if after else []
        input_files = _input_file_deltas(certified_manifest, current_manifest)
        dependencies = _dependency_deltas(
            certified_dependencies,
            current_dependencies,
        )
        local_hash_changed = (
            before is None
            or after is None
            or before.get("local_hash") != after.get("local_hash")
        )
        declaration_changed = (
            before is None
            or after is None
            or (
                local_hash_changed
                and certified_manifest == current_manifest
            )
        )
        if not local_hash_changed and not input_files and not dependencies:
            continue
        drift.append(
            CertificateFacetDrift(
                facet_id=str(facet_id),
                facet_type=str(facet_type),
                local_hash_changed=local_hash_changed,
                declaration_changed=declaration_changed,
                blueprint_path=blueprint_path if declaration_changed else None,
                input_files=input_files,
                dependencies=dependencies,
            )
        )
    return tuple(drift)


def _facet_currentness_concerns(
    payload_facets: object,
    state: NodeHashState,
) -> tuple[str, ...]:
    """Name the exact schema-v6 certification facets that have drifted."""

    expected = {
        (claim["type"], claim["id"]): claim
        for claim in certification_facet_claims(state)
    }
    if not isinstance(payload_facets, list):
        return ("facet-set-mismatch",)

    actual: dict[tuple[object, object], Mapping[str, object]] = {}
    duplicate = False
    for claim in payload_facets:
        if not isinstance(claim, Mapping):
            return ("facet-set-mismatch",)
        key = (claim.get("type"), claim.get("id"))
        if key in actual:
            duplicate = True
        actual[key] = claim

    concerns: list[str] = []
    if duplicate or actual.keys() != expected.keys():
        concerns.append("facet-set-mismatch")
    elif tuple(actual) != tuple(expected):
        concerns.append("facet-order-mismatch")

    fields = (
        ("local_hash", "hash-mismatch"),
        ("input_manifest", "input-manifest-mismatch"),
        ("dependencies", "dependency-mismatch"),
    )
    for key in sorted(expected):
        certified = actual.get(key)
        if certified is None:
            continue
        facet_type, facet_id = key
        for field, suffix in fields:
            if certified.get(field) == expected[key][field]:
                continue
            if facet_type == "interface":
                concerns.append(f"interface-{suffix}:{facet_id}")
            else:
                concerns.append(f"remainder-{suffix}")
    return tuple(concerns)


def certificate_requires_renewal(status: CertificateNodeCurrentness) -> bool:
    """Return whether one stale status needs a new certificate entry.

    Intent
    ------
    Distinguish a node's own stale certificate evidence from currentness that
    is false only because a dependency's certificate is not current.

    Rationale
    ---------
    Once an unchanged provider is renewed, a consumer whose signed inputs and
    dependency claims still match becomes current without another log append.

    Pseudocode
    ----------
    - reject current statuses
    - ignore propagated ``dependency-not-current`` concerns
    - require renewal when any node-owned concern remains

    Wraps
    -----
    - none
    """

    return not status.current and any(
        not concern.startswith("dependency-not-current:")
        for concern in status.concerns
    )


def certificate_stale_worklist(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    report: CertificateCurrentnessReport,
    requested: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Return stale requested nodes and stale dependencies in canonical postorder.

    Intent
    ------
    Turn a currentness report and requested node set into the exact bottom-up
    worklist required before new certificates may be issued.

    Rationale
    ---------
    A consumer can be stale only because a provider is stale; listing the
    provider first prevents audit and issuance from skipping its dependency.

    Pseudocode
    ----------
    - select stale requested roots
    - compute their canonical certification dependency postorder
    - retain only stale nodes present in the report or dependency closure

    Wraps
    -----
    - ``CertificationHashError`` from invalid canonical state becomes a stable
      root-only worklist so drift reporting remains read-only and available

    CallsFromRepo
    -------------
    ``officina.certification.hashing.certification_target_postorder``:
      why: orders stale dependencies before their consumers
    """

    statuses = {
        **getattr(report, "dependency_nodes", {}),
        **report.nodes,
    }
    selected = tuple(sorted(set(requested or report.nodes)))
    stale_roots = tuple(
        node_id
        for node_id in selected
        if (status := statuses.get(node_id)) is not None and not status.current
    )
    if not stale_roots:
        return ()
    try:
        ordered = certification_target_postorder(graph, states, stale_roots)
    except CertificationHashError:
        ordered = stale_roots
    return tuple(
        node_id
        for node_id in ordered
        if (status := statuses.get(node_id)) is not None
        and certificate_requires_renewal(status)
    )


def evaluate_certificate_currentness(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    repo_root: Path,
    public_key_root: Path,
    source_commit: str,
    certifier_identity: Mapping[str, object],
    checks_by_node: Mapping[str, Sequence[Mapping[str, object]]],
    certification_basis_paths: Sequence[Path] | None = None,
    schema_root: Path | None = None,
    allow_non_atomic: bool = False,
) -> CertificateCurrentnessReport:
    """Evaluate the final entry of every certificate log against the v6 graph state."""

    if graph.schema_version != 6:
        raise CertificationHashError(
            "certification currentness requires a schema v6 graph"
        )

    root = Path(repo_root).resolve()
    selected_schema_root = Path(schema_root) if schema_root is not None else _default_schema_root()
    validator = schema_validator(load_schema(selected_schema_root / "certificate.schema.json"))
    local: dict[str, CertificateNodeCurrentness] = {}
    node_tracked_inputs_clean = {
        node_id: False for node_id in graph.nodes
    }
    try:
        snapshot = capture_git_snapshot(root)
        selected_basis_paths = (
            tuple(certification_basis_paths)
            if certification_basis_paths is not None
            else resolve_certification_basis_paths(
                root,
                allow_non_atomic=allow_non_atomic,
            )
        )
        global_tracked_paths = {
            *selected_basis_paths,
            *(node.blueprint_path for node in graph.nodes.values()),
        }
        certifier = graph.nodes.get(CERTIFIER_NODE_ID)
        if certifier is not None:
            for node_id, state in states.items():
                node = graph.nodes.get(node_id)
                if (
                    node is None
                    or node.module_root != certifier.module_root
                    or not isinstance(state, NodeHashState)
                ):
                    continue
                global_tracked_paths.update(
                    root / entry["path"]
                    for entry in state.input_manifest
                    if entry.get("git_provenance") == "tracked"
                )
        global_readiness = check_commit_readiness(
            snapshot,
            tuple(sorted(global_tracked_paths)),
            {},
            allow_non_atomic=allow_non_atomic,
        )
        global_inputs_current = (
            snapshot is not None
            and global_readiness.stamp_worthy
        )
        for node_id, state in states.items():
            if node_id not in node_tracked_inputs_clean or not isinstance(
                state, NodeHashState
            ):
                continue
            node_paths = tuple(
                sorted(
                    root / entry["path"]
                    for entry in state.input_manifest
                    if entry.get("git_provenance") == "tracked"
                )
            )
            node_tracked_inputs_clean[node_id] = (
                global_inputs_current
                and check_commit_readiness(
                    snapshot,
                    node_paths,
                    {},
                    allow_non_atomic=allow_non_atomic,
                ).stamp_worthy
            )
    except (CertificationHashError, OSError, TypeError, ValueError):
        pass

    for node_id, node in sorted(graph.nodes.items()):
        concerns: list[str] = []
        local_hash_changed = False
        declaration_changed = False
        blueprint_path: str | None = None
        input_files: tuple[CertificateInputDelta, ...] = ()
        dependencies: tuple[CertificateDependencyDelta, ...] = ()
        facet_drift: tuple[CertificateFacetDrift, ...] = ()
        if not node_tracked_inputs_clean[node_id]:
            concerns.append("source-commit-input-mismatch")
        certificate: Mapping[str, object] | None = None
        state = states.get(node_id)
        if not isinstance(state, NodeHashState):
            local[node_id] = CertificateNodeCurrentness(
                node_id,
                False,
                ("derived-state-unavailable",),
                None,
            )
            continue
        path = certificate_log_path(node)
        if not path.exists():
            local[node_id] = CertificateNodeCurrentness(
                node_id,
                False,
                ("missing-certificate-log",),
                None,
            )
            continue
        try:
            entries = parse_certificate_log(
                read_regular_file_bytes(
                    path,
                    allowed_root=node.module_root,
                    allow_non_atomic=allow_non_atomic,
                ),
                public_key_root,
                allow_non_atomic=allow_non_atomic,
            )
        except (CertificateLogError, AtomicWriteError, OSError, TypeError, ValueError):
            local[node_id] = CertificateNodeCurrentness(
                node_id,
                False,
                ("suspect-certificate-log",),
                None,
            )
            continue
        certificate = entries[-1]
        try:
            for entry in entries:
                validator.validate(entry)
        except jsonschema.ValidationError:
            concerns.append("invalid-certificate-schema")
        payload = certificate.get("payload")
        if not isinstance(payload, Mapping):
            concerns.append("invalid-certificate-schema")
        else:
            current_manifest = [dict(entry) for entry in state.input_manifest]
            current_dependencies = [dict(entry) for entry in state.dependency_hashes]
            certified_manifest = payload.get("input_manifest", [])
            certified_dependencies = payload.get("dependencies", [])
            facet_capable = (
                payload.get("certificate_schema_version") == 3
                and bool(state.facets)
            )
            if not facet_capable:
                input_files = _input_file_deltas(
                    certified_manifest,
                    current_manifest,
                )
                dependencies = _dependency_deltas(
                    certified_dependencies,
                    current_dependencies,
                )
                local_hash_changed = payload.get("node_hash") != state.node_hash
                current_blueprint_path = _relative_path(
                    node.blueprint_path,
                    root,
                )
                blueprint_input_changed = any(
                    delta.path == current_blueprint_path for delta in input_files
                )
                declaration_changed = (
                    blueprint_input_changed
                    or (
                        local_hash_changed
                        and certified_manifest == current_manifest
                        and certified_dependencies == current_dependencies
                    )
                )
                blueprint_path = (
                    current_blueprint_path
                    if declaration_changed
                    else None
                )
            if payload.get("certificate_schema_version") != 3:
                concerns.append("legacy-certificate-payload")
            else:
                concerns.extend(
                    _facet_currentness_concerns(payload.get("facets"), state)
                )
                facet_drift = _facet_drift(
                    payload.get("facets"),
                    state,
                    blueprint_path=_relative_path(node.blueprint_path, root),
                )
            if payload.get("subject") != _expected_subject(node, root):
                concerns.append("subject-mismatch")
            if payload.get("input_manifest") != [dict(entry) for entry in state.input_manifest]:
                concerns.append("input-manifest-mismatch")
            if payload.get("node_hash") != state.node_hash:
                concerns.append("node-hash-mismatch")
            if payload.get("dependencies") != [dict(entry) for entry in state.dependency_hashes]:
                concerns.append("dependency-mismatch")
            if payload.get("certification_basis_hash") != state.certification_basis_hash:
                concerns.append("certification-basis-mismatch")
            payload_certifier = payload.get("certifier")
            structured_certifier = any(
                dependency.get("relation") in EVIDENCE_ONLY_RELATIONS
                for dependency in state.dependency_hashes
            )
            currentness_identity = lambda identity: _certifier_currentness_identity(
                identity, structured=structured_certifier
            )
            if not isinstance(payload_certifier, Mapping) or (
                currentness_identity(payload_certifier)
                != currentness_identity(certifier_identity)
            ):
                concerns.append("certifier-mismatch")
            if payload.get("checks") != _expected_checks(node_id, checks_by_node):
                concerns.append("checks-mismatch")
        local[node_id] = CertificateNodeCurrentness(
            node_id=node_id,
            current=not concerns,
            concerns=tuple(dict.fromkeys(concerns)),
            certificate=certificate,
            local_hash_changed=local_hash_changed,
            declaration_changed=declaration_changed,
            blueprint_path=blueprint_path,
            input_files=input_files,
            dependencies=dependencies,
            facet_drift=facet_drift,
        )

    children: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    for node_id, state in states.items():
        if node_id not in children or not isinstance(state, NodeHashState):
            continue
        for dependency in state.dependency_hashes:
            if dependency.get("relation") in EVIDENCE_ONLY_RELATIONS:
                continue
            target = dependency.get("target") if isinstance(dependency, Mapping) else None
            if isinstance(target, str) and target in children:
                children[node_id].add(target)

    resolved: dict[str, CertificateNodeCurrentness] = {}
    visiting: set[str] = set()

    def resolve(node_id: str) -> CertificateNodeCurrentness:
        if node_id in resolved:
            return resolved[node_id]
        status = local[node_id]
        concerns = list(status.concerns)
        if node_id in visiting:
            concerns.append("dependency-cycle")
        else:
            visiting.add(node_id)
            for child_id in sorted(children[node_id]):
                if not resolve(child_id).current:
                    concerns.append(f"dependency-not-current:{child_id}")
            visiting.remove(node_id)
        result = CertificateNodeCurrentness(
            node_id=node_id,
            current=not concerns,
            concerns=tuple(dict.fromkeys(concerns)),
            certificate=status.certificate,
            local_hash_changed=status.local_hash_changed,
            declaration_changed=status.declaration_changed,
            blueprint_path=status.blueprint_path,
            input_files=status.input_files,
            dependencies=status.dependencies,
            facet_drift=status.facet_drift,
        )
        resolved[node_id] = result
        return result

    for node_id in sorted(graph.nodes):
        resolve(node_id)
    report = CertificateCurrentnessReport(nodes=resolved)
    return CertificateCurrentnessReport(
        nodes=resolved,
        stale_worklist=certificate_stale_worklist(graph, states, report),
    )


class CertificateCurrentnessView:
    """CertificationView adapter over the authoritative currentness report."""

    def __init__(self, report: CertificateCurrentnessReport) -> None:
        records = {
            node_id: status.certificate
            for node_id, status in report.nodes.items()
            if status.current and isinstance(status.certificate, Mapping)
        }
        self.report = report
        self._record_view = CertificateRecordView(records)

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        return self._record_view.certificate_for(node_id)

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str,
    ) -> CertificationDecision:
        return self._record_view.check_export(
            module_id,
            interface_id,
            interface_version,
            source_node_id,
        )

    def check_authorization(
        self,
        authorization: AuthorizationResult,
    ) -> CertificationDecision:
        return self._record_view.check_authorization(authorization)


@dataclass(frozen=True)
class RepositoryCertificationState:
    """One canonical repository graph, hash, identity, and certificate snapshot."""

    graph: RepositoryBlueprintGraph
    states: Mapping[str, NodeHashState]
    source_commit: str
    certification_basis_hash: str
    certifier_identity: Mapping[str, object]
    currentness: CertificateCurrentnessReport


class RepositoryCertificationError(ValueError):
    """Raised when the canonical repository certification state cannot be derived."""


def derive_repository_certification_state(
    repo_root: Path,
    *,
    public_key_root: Path | None = None,
    schema_root: Path | None = None,
    allow_non_atomic: bool = False,
) -> RepositoryCertificationState:
    """Derive the sole repository-backed certification state used by readers."""

    root = Path(repo_root).resolve()
    selected_schema_root = (
        Path(schema_root)
        if schema_root is not None
        else root / "references" / "blueprint-schema"
    )
    try:
        graph = load_repository_blueprint_graph(
            root,
            schema_root=selected_schema_root,
        )
        if any(
            node.declaration.get("schema_version") != 6
            for node in graph.nodes.values()
        ):
            raise RepositoryCertificationError(
                "repository certification requires one closed schema-version 6 repository"
            )
        snapshot = capture_git_snapshot(root)
        if snapshot is None or snapshot.repo_root != root:
            raise RepositoryCertificationError(
                "repository certification requires the exact Git repository root"
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
            policy_path=root / CANONICAL_NODE_HASH_POLICY,
            certification_basis_hash=basis_hash,
            certification_basis_paths=basis_paths,
            allow_non_atomic=allow_non_atomic,
        )
        certifier_identity = derive_certifier_identity(
            graph,
            states,
            snapshot.commit,
        )
        checks_by_node = {
            node_id: expected_certifier_checks()
            for node_id in graph.nodes
        }
        currentness = evaluate_certificate_currentness(
            graph,
            states,
            repo_root=root,
            public_key_root=(
                certificate_public_key_root(root)
                if public_key_root is None
                else Path(public_key_root)
            ),
            source_commit=snapshot.commit,
            certifier_identity=certifier_identity,
            checks_by_node=checks_by_node,
            certification_basis_paths=basis_paths,
            schema_root=selected_schema_root,
            allow_non_atomic=allow_non_atomic,
        )
    except RepositoryCertificationError:
        raise
    except (
        CertificationHashError,
        BlueprintGraphError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise RepositoryCertificationError(str(exc)) from exc
    return RepositoryCertificationState(
        graph=graph,
        states=states,
        source_commit=snapshot.commit,
        certification_basis_hash=basis_hash,
        certifier_identity=certifier_identity,
        currentness=currentness,
    )


def _certifier_target_postorder(
    state: RepositoryCertificationState,
) -> tuple[str, ...] | None:
    """Return the exact dependency-first order for the certifier module target."""

    module_ids = [CERTIFIER_NODE_ID]
    runtime_node_id = f"{CERTIFIER_NODE_ID}._rtx"
    if runtime_node_id in state.graph.nodes:
        module_ids.append(runtime_node_id)
    roots = {
        *module_ids,
        *(
            source_id
            for module_id in module_ids
            for source_id in state.graph.module_sources.get(module_id, ())
        ),
    }
    try:
        return certification_target_postorder(
            state.graph,
            state.states,
            tuple(roots),
        )
    except CertificationHashError:
        return None


def _initial_certificate_state_admissible(
    state: RepositoryCertificationState,
) -> bool:
    """Return whether initial certification is pristine or a valid resumable prefix."""

    order = _certifier_target_postorder(state)
    if order is None:
        return False
    allowed_concerns = {
        "missing-certificate-log",
        *(
            f"dependency-not-current:{node_id}"
            for node_id in state.graph.nodes
        ),
    }
    existing: set[str] = set()
    for node_id, node in state.graph.nodes.items():
        if certificate_log_path(node).exists():
            existing.add(node_id)
        status = state.currentness.nodes.get(node_id)
        if status is None:
            return False
        if node_id in existing:
            if not status.current:
                return False
        elif any(concern not in allowed_concerns for concern in status.concerns):
            return False

    if not existing <= set(order):
        return False
    return existing == set(order[: len(existing)])


def _certifier_renewal_state_admissible(
    state: RepositoryCertificationState,
    *,
    repo_root: Path,
    allow_non_atomic: bool = False,
) -> bool:
    """Return whether the exact certifier closure has appendable signed history."""

    order = _certifier_target_postorder(state)
    if order is None:
        return False
    if any(
        "source-commit-input-mismatch" in status.concerns
        for status in state.currentness.nodes.values()
    ):
        return False
    root = Path(repo_root).resolve()
    try:
        schema_root = root / "references" / "blueprint-schema"
        validator = schema_validator(
            load_schema(schema_root / "certificate.schema.json")
        )
        public_key_root = certificate_public_key_root(root)
        existing: set[str] = set()
        for node_id in order:
            node = state.graph.nodes[node_id]
            path = certificate_log_path(node)
            if not path.exists():
                continue
            existing.add(node_id)
            entries = parse_certificate_log(
                read_regular_file_bytes(
                    path,
                    allowed_root=node.module_root,
                    allow_non_atomic=allow_non_atomic,
                ),
                public_key_root,
                require_active_final=False,
                allow_non_atomic=allow_non_atomic,
            )
            for entry in entries:
                validator.validate(entry)
                payload = entry.get("payload")
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("subject") != _expected_subject(node, root)
                ):
                    return False
        if existing != set(order[: len(existing)]):
            return False
    except (
        AtomicWriteError,
        CertificateLogError,
        jsonschema.SchemaError,
        jsonschema.ValidationError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False
    return True


def _flag_value(argv: Sequence[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        return None
    if flag in argv[index + 2 :]:
        return None
    return argv[index + 1]


class RepositoryCertificationView(CertificateCurrentnessView):
    """Current-certificate admission plus the sole bounded certifier fallback."""

    def __init__(
        self,
        report: CertificateCurrentnessReport,
        *,
        repo_root: Path,
        source_commit: str,
        bootstrap_allowed: bool,
    ) -> None:
        super().__init__(report)
        self.repo_root = Path(repo_root).resolve()
        self.source_commit = source_commit
        self.bootstrap_allowed = bootstrap_allowed

    def check_bootstrap(
        self,
        *,
        caller_module_id: str,
        target_module_id: str,
        terminal_module_id: str,
        interface_id: str,
        pattern_name: str | None,
        argv: Sequence[str],
    ) -> CertificationDecision:
        """Admit only exact certifier self-certification and its read-only check."""

        rejected = CertificationDecision(
            False,
            "certification-unavailable",
            "no current certificate admits this invocation",
        )
        if not self.bootstrap_allowed or caller_module_id != CERTIFIER_NODE_ID:
            return rejected
        tokens = tuple(argv)
        if interface_id == "skill-maker._rtx.interface.sync-blueprints":
            if (
                target_module_id == "skill-maker"
                and terminal_module_id == "skill-maker"
                and pattern_name == "check"
                and tokens == ("--check",)
            ):
                return CertificationDecision(
                    True,
                    "certifier-self-certification",
                    "Bounded blueprint check for certifier self-certification.",
                )
            return rejected
        if (
            target_module_id != CERTIFIER_NODE_ID
            or terminal_module_id != CERTIFIER_NODE_ID
            or interface_id != "node-certify._rtx.interface.certify"
        ):
            return rejected
        if not tokens or tokens[0] != "certify":
            return rejected
        positionals: list[str] = []
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token in {"--reviewed-repository", "--reviewed-commit"}:
                index += 2
                continue
            if token == "--json":
                index += 1
                continue
            if token.startswith("--"):
                return rejected
            positionals.append(token)
            index += 1
        reviewed_repository = _flag_value(tokens, "--reviewed-repository")
        reviewed_commit = _flag_value(tokens, "--reviewed-commit")
        if (
            positionals == [CERTIFIER_NODE_ID]
            and reviewed_repository is not None
            and Path(reviewed_repository).resolve() == self.repo_root
            and reviewed_commit == self.source_commit
        ):
            return CertificationDecision(
                True,
                "certifier-self-certification",
                "Bounded self-certification of the canonical certifier.",
            )
        return rejected


def repository_certification_view(
    repo_root: Path,
    *,
    schema_root: Path | None = None,
    allow_non_atomic: bool = False,
) -> RepositoryCertificationView:
    """Construct the canonical production view from current repository state."""

    state = derive_repository_certification_state(
        repo_root,
        schema_root=schema_root,
        allow_non_atomic=allow_non_atomic,
    )
    return RepositoryCertificationView(
        state.currentness,
        repo_root=repo_root,
        source_commit=state.source_commit,
        bootstrap_allowed=(
            _initial_certificate_state_admissible(state)
            or _certifier_renewal_state_admissible(
                state,
                repo_root=repo_root,
                allow_non_atomic=allow_non_atomic,
            )
        ),
    )


class RejectingCertificationView:
    """Fail-closed view used when canonical repository state is unavailable."""

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str,
    ) -> CertificationDecision:
        del module_id, interface_id, interface_version, source_node_id
        return CertificationDecision(
            False,
            "certification-unavailable",
            "repository certification state is unavailable",
        )

    def check_authorization(
        self,
        authorization: AuthorizationResult,
    ) -> CertificationDecision:
        del authorization
        return CertificationDecision(
            False,
            "certification-unavailable",
            "repository certification state is unavailable",
        )

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        del node_id
        return None

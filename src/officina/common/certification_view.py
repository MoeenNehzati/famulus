"""Read-only certification boundary consumed by dispatch and projection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import jsonschema

from .artifact_health import (
    ArtifactHealthError,
    CERTIFIER_NODE_ID,
    NodeHashState,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from .atomic_files import AtomicWriteError, read_regular_file_bytes
from .audit_records import CertificateLogError, parse_certificate_log
from .blueprint_graph import BlueprintNode, RepositoryBlueprintGraph
from .blueprint_template import load_schema, schema_validator
from .git_provenance import capture_git_snapshot, check_commit_readiness


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
    node_hash: str
    certificate_hash: str
    certified_at: str | None = None


@dataclass(frozen=True)
class CertificateNodeCurrentness:
    node_id: str
    current: bool
    concerns: tuple[str, ...]
    certificate: Mapping[str, object] | None


@dataclass(frozen=True)
class CertificateCurrentnessReport:
    nodes: Mapping[str, CertificateNodeCurrentness]

    @property
    def current(self) -> bool:
        return bool(self.nodes) and all(status.current for status in self.nodes.values())


class CertificationView(Protocol):
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str | None,
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
            certified_at = payload.get("certified_at") if isinstance(payload, Mapping) else None
            if (
                not isinstance(subject, Mapping)
                or subject.get("id") != node_id
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
        source_node_id: str | None,
    ) -> CertificationDecision:
        del interface_version
        certificate = self.certificate_for(module_id)
        if certificate is None:
            return CertificationDecision(
                False,
                "certification-unavailable",
                f"{module_id}: no current certificate for {interface_id}",
            )
        if source_node_id is not None:
            source_certificate = self.certificate_for(source_node_id)
            if source_certificate is None:
                return CertificationDecision(
                    False,
                    "source-certification-unavailable",
                    f"{source_node_id}: no current certificate for {interface_id}",
                )
        return CertificationDecision(True, "current", "Current certificate.")


def certificate_log_path(node: BlueprintNode) -> Path:
    """Return the sole append-only certificate log path for one v4 node."""

    if any(separator in node.node_id for separator in ("/", "\\")):
        raise ValueError(f"certificate node ID contains a path separator: {node.node_id}")
    return node.skill_root / ".certificates" / f"{node.node_id}.jsonl"


def _default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "references" / "blueprint"


def _relative_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError as exc:
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
    except ArtifactHealthError as exc:
        raise ValueError(f"{node_id}: invalid expected certification checks: {exc}") from exc


def evaluate_certificate_currentness(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    repo_root: Path,
    public_key_root: Path,
    source_commit: str,
    certifier_identity: Mapping[str, object],
    checks_by_node: Mapping[str, Sequence[Mapping[str, object]]],
    schema_root: Path | None = None,
    allow_non_atomic: bool = False,
) -> CertificateCurrentnessReport:
    """Evaluate the final entry of every v4 log against one derived graph state."""

    root = Path(repo_root).resolve()
    selected_schema_root = Path(schema_root) if schema_root is not None else _default_schema_root()
    validator = schema_validator(load_schema(selected_schema_root / "certificate.schema.json"))
    local: dict[str, CertificateNodeCurrentness] = {}
    node_source_commit_inputs_current = {
        node_id: False for node_id in graph.nodes
    }
    try:
        snapshot = capture_git_snapshot(root)
        global_tracked_paths = {
            *resolve_certification_basis_paths(
                root,
                allow_non_atomic=allow_non_atomic,
            ),
            *(node.blueprint_path for node in graph.nodes.values()),
        }
        certifier = graph.nodes.get(CERTIFIER_NODE_ID)
        if certifier is not None:
            for node_id, state in states.items():
                node = graph.nodes.get(node_id)
                if (
                    node is None
                    or node.skill_root != certifier.skill_root
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
            and snapshot.commit == source_commit
            and global_readiness.stamp_worthy
        )
        for node_id, state in states.items():
            if node_id not in node_source_commit_inputs_current or not isinstance(
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
            node_source_commit_inputs_current[node_id] = (
                global_inputs_current
                and check_commit_readiness(
                    snapshot,
                    node_paths,
                    {},
                    allow_non_atomic=allow_non_atomic,
                ).stamp_worthy
            )
    except (ArtifactHealthError, OSError, TypeError, ValueError):
        pass

    for node_id, node in sorted(graph.nodes.items()):
        concerns: list[str] = []
        if not node_source_commit_inputs_current[node_id]:
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
                    allowed_root=node.skill_root,
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
            if payload.get("subject") != _expected_subject(node, root):
                concerns.append("subject-mismatch")
            if payload.get("source_commit") != source_commit:
                concerns.append("source-commit-mismatch")
            if payload.get("input_manifest") != [dict(entry) for entry in state.input_manifest]:
                concerns.append("input-manifest-mismatch")
            if payload.get("node_hash") != state.node_hash:
                concerns.append("node-hash-mismatch")
            if payload.get("dependencies") != [dict(entry) for entry in state.dependency_hashes]:
                concerns.append("dependency-mismatch")
            if payload.get("certification_basis_hash") != state.certification_basis_hash:
                concerns.append("certification-basis-mismatch")
            if payload.get("certifier") != dict(certifier_identity):
                concerns.append("certifier-mismatch")
            if payload.get("checks") != _expected_checks(node_id, checks_by_node):
                concerns.append("checks-mismatch")
        local[node_id] = CertificateNodeCurrentness(
            node_id=node_id,
            current=not concerns,
            concerns=tuple(dict.fromkeys(concerns)),
            certificate=certificate,
        )

    children: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    for node_id, state in states.items():
        if node_id not in children or not isinstance(state, NodeHashState):
            continue
        for dependency in state.dependency_hashes:
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
        )
        resolved[node_id] = result
        return result

    for node_id in sorted(graph.nodes):
        resolve(node_id)
    return CertificateCurrentnessReport(nodes=resolved)


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
        source_node_id: str | None,
    ) -> CertificationDecision:
        return self._record_view.check_export(
            module_id,
            interface_id,
            interface_version,
            source_node_id,
        )


class RejectingCertificationView:
    """Phase-2 production placeholder; Phase 4 supplies the backed view."""

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str | None,
    ) -> CertificationDecision:
        del module_id, interface_id, interface_version, source_node_id
        return CertificationDecision(
            False,
            "certification-unavailable",
            "machine-module certification is not available until Phase 4",
        )

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        del node_id
        return None

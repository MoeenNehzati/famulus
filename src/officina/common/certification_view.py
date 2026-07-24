"""Read-only certification boundary consumed by dispatch and projection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import jsonschema

from .certification_hashing import (
    CertificationHashError,
    CANONICAL_NODE_HASH_POLICY,
    CERTIFIER_NODE_ID,
    NodeHashState,
    compute_certification_basis_hash,
    compute_node_hash_states,
    derive_certifier_identity,
    expected_certifier_checks,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from .atomic_files import AtomicWriteError, read_regular_file_bytes
from .certificate_records import (
    CertificateLogError,
    certificate_public_key_root,
    parse_certificate_log,
)
from .blueprint_graph import (
    BlueprintGraphError,
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
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
    except CertificationHashError as exc:
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
    except (CertificationHashError, OSError, TypeError, ValueError):
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
        source_node_id: str,
    ) -> CertificationDecision:
        return self._record_view.check_export(
            module_id,
            interface_id,
            interface_version,
            source_node_id,
        )


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
    allow_non_atomic: bool = False,
) -> RepositoryCertificationState:
    """Derive the sole repository-backed certification state used by readers."""

    root = Path(repo_root).resolve()
    schema_root = root / "references" / "blueprint"
    try:
        graph = load_repository_blueprint_graph(root, schema_root=schema_root)
        if any(
            node.declaration.get("schema_version") != 4
            for node in graph.nodes.values()
        ):
            raise RepositoryCertificationError(
                "repository certification accepts only all-v4 repositories"
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
            node_id: expected_certifier_checks() for node_id in graph.nodes
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
            schema_root=schema_root,
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


def _initial_certificate_state_admissible(
    state: RepositoryCertificationState,
) -> bool:
    """Return whether initial certification is pristine or a valid resumable prefix."""

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

    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visited:
            return True
        if node_id in visiting:
            return False
        state_for_node = state.states.get(node_id)
        if not isinstance(state_for_node, NodeHashState):
            return False
        visiting.add(node_id)
        for dependency in state_for_node.dependency_hashes:
            target = (
                dependency.get("target")
                if isinstance(dependency, Mapping)
                else None
            )
            if isinstance(target, str) and target in state.graph.nodes:
                if not visit(target):
                    return False
        visiting.remove(node_id)
        visited.add(node_id)
        order.append(node_id)
        return True

    if CERTIFIER_NODE_ID not in state.graph.nodes or not visit(CERTIFIER_NODE_ID):
        return False
    if not existing <= set(order):
        return False
    return existing == set(order[: len(existing)])


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
    """Current-certificate admission plus the sole bounded initial route."""

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
        interface_id: str,
        pattern_name: str | None,
        argv: Sequence[str],
    ) -> CertificationDecision:
        """Admit only the certifier's exact initial mechanical/certification calls."""

        rejected = CertificationDecision(
            False,
            "certification-unavailable",
            "no current certificate admits this invocation",
        )
        if not self.bootstrap_allowed or caller_module_id != CERTIFIER_NODE_ID:
            return rejected
        tokens = tuple(argv)
        if interface_id == "skill-maker.interface.sync-blueprints":
            if (
                (pattern_name == "check" and tokens == ("--check",))
                or (pattern_name == "sync" and not tokens)
            ):
                return CertificationDecision(
                    True,
                    "initial-certification",
                    "Bounded blueprint synchronization for initial certification.",
                )
            return rejected
        if interface_id != "skill-certifier.interface.certify":
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
                "initial-certification",
                "Bounded initial certification of the canonical certifier.",
            )
        return rejected


def repository_certification_view(
    repo_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> RepositoryCertificationView:
    """Construct the canonical production view from current repository state."""

    state = derive_repository_certification_state(
        repo_root,
        allow_non_atomic=allow_non_atomic,
    )
    return RepositoryCertificationView(
        state.currentness,
        repo_root=repo_root,
        source_commit=state.source_commit,
        bootstrap_allowed=_initial_certificate_state_admissible(state),
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

    def certificate_for(self, node_id: str) -> CurrentCertificate | None:
        del node_id
        return None

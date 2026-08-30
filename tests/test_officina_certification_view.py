from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import officina.certification.view as certification_view_module
from officina.certification.hashing import (
    CertificationFacetHashState,
    NodeHashState,
    certification_facet_claims,
    compute_node_hash_states,
)
from officina.certification.records import (
    canonical_certificate_envelope_bytes,
    certificate_public_key_root,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    rotate_certificate_signing_key,
    sign_certificate_payload,
)
from officina.blueprints.graph import (
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.blueprints.authorization import (
    AuthorizationResult,
    CertificateRequirement,
)
from officina.certification.view import (
    CertificateNodeCurrentness,
    CertificateCurrentnessReport,
    CertificateCurrentnessView,
    CertificateRecordView,
    RepositoryCertificationView,
    RepositoryCertificationState,
    certificate_log_path,
    derive_repository_certification_state,
    evaluate_certificate_currentness,
    repository_certification_view,
    RejectingCertificationView,
)
from test_support.v4_certification_fixtures import (
    create_certified_fixture,
    create_v4_repository,
    payload as v4_payload,
)
from test_support.git_repository import GitTestRepository


CANONICAL_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[1] / "references" / "blueprint-schema"
)
SCHEMA_ROOT = (
    Path(__file__).parent
    / "fixtures"
    / "blueprint_schemas"
    / "v4"
)
CERTIFIER = {
    "interface": "node-certify.interface.certify",
    "version": 1,
    "node_hash": "sha256:" + "c" * 64,
    "source_commit": "c" * 40,
}
CHECKS = (
    {
        "id": "blueprint-accuracy",
        "version": 1,
        "passed": True,
        "findings": [],
    },
)


def _authorization_result(
    *requirements: tuple[str, int],
) -> AuthorizationResult:
    return AuthorizationResult(
        caller_module_id="caller",
        caller_source_id=None,
        requested_interface_id="target.interface.run",
        requested_version=1,
        requested_owner_module_id="target",
        terminal_interface_id="target.interface.run",
        terminal_version=1,
        terminal_module_id="target",
        implementing_source_id="target.source.gateway",
        caller_ancestry=("caller",),
        target_ancestry=("target",),
        terminal_ancestry=("target",),
        lca_module_id=None,
        crossed_namespace_gates=(),
        resolved_callers=(),
        effective_filters=(),
        allowed=True,
        diagnostic="authorized",
        relations=(),
        required_certificates=frozenset(
            CertificateRequirement(node_id, version)
            for node_id, version in requirements
        ),
    )


def _certificate_record(node_id: str, version: int) -> dict[str, object]:
    return {
        "payload": {
            "subject": {"id": node_id, "version": version},
            "node_hash": "sha256:" + "a" * 64,
        },
        "signature": {},
    }


class MemorySecretBackend:
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        self.values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.values.pop((namespace, key), None) is not None


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_authorization_check_consumes_exact_required_certificates_only() -> None:
    authorization = _authorization_result(("route-owner", 2), ("target", 3))
    view = CertificateRecordView(
        {
            "route-owner": _certificate_record("route-owner", 2),
            "target": _certificate_record("target", 3),
        }
    )

    assert view.check_authorization(authorization).certified

    wrong_version = CertificateRecordView(
        {
            "route-owner": _certificate_record("route-owner", 1),
            "target": _certificate_record("target", 3),
            "unrelated-sibling": _certificate_record(
                "unrelated-sibling",
                99,
            ),
        }
    )
    decision = wrong_version.check_authorization(authorization)

    assert not decision.certified
    assert decision.code == "authorization-certification-unavailable"
    assert "route-owner@2" in decision.message


def test_authorization_currentness_ignores_unrelated_stale_nodes_only() -> None:
    authorization = _authorization_result(("route-owner", 2), ("target", 3))

    def report(*, route_current: bool) -> CertificateCurrentnessReport:
        return CertificateCurrentnessReport(
            nodes={
                "route-owner": CertificateNodeCurrentness(
                    node_id="route-owner",
                    current=route_current,
                    concerns=() if route_current else ("node-hash-mismatch",),
                    certificate=_certificate_record("route-owner", 2),
                ),
                "target": CertificateNodeCurrentness(
                    node_id="target",
                    current=True,
                    concerns=(),
                    certificate=_certificate_record("target", 3),
                ),
                "unrelated-sibling": CertificateNodeCurrentness(
                    node_id="unrelated-sibling",
                    current=False,
                    concerns=("node-hash-mismatch",),
                    certificate=_certificate_record("unrelated-sibling", 1),
                ),
            }
        )

    assert CertificateCurrentnessView(
        report(route_current=True)
    ).check_authorization(authorization).certified
    assert not CertificateCurrentnessView(
        report(route_current=False)
    ).check_authorization(authorization).certified


def test_rejecting_view_has_an_explicit_v5_authorization_seam() -> None:
    decision = RejectingCertificationView().check_authorization(
        _authorization_result(("target", 1))
    )

    assert not decision.certified
    assert decision.code == "certification-unavailable"


def test_v5_certifier_bootstrap_roots_parent_runtime_child_and_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []

    def capture_postorder(graph, states, requested):
        observed.append((graph, states, tuple(requested)))
        return ("ordered",)

    monkeypatch.setattr(
        certification_view_module,
        "certification_target_postorder",
        capture_postorder,
    )
    graph = SimpleNamespace(
        schema_version=5,
        nodes={
            "node-certify": object(),
            "node-certify-rtx": object(),
            "node-certify.source.gateway": object(),
            "node-certify-rtx.source.certifier": object(),
            "unrelated": object(),
        },
        module_sources={
            "node-certify": ("node-certify.source.gateway",),
            "node-certify-rtx": (
                "node-certify-rtx.source.certifier",
            ),
        },
    )
    state = SimpleNamespace(graph=graph, states={"state": object()})

    assert certification_view_module._certifier_target_postorder(state) == (
        "ordered",
    )
    assert observed[0][0] is graph
    assert observed[0][1] is state.states
    assert set(observed[0][2]) == {
        "node-certify",
        "node-certify-rtx",
        "node-certify.source.gateway",
        "node-certify-rtx.source.certifier",
    }


def test_v6_certifier_bootstrap_uses_the_runtime_child_node_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []

    def capture_postorder(graph, states, requested):
        observed.append(tuple(requested))
        return ("ordered",)

    monkeypatch.setattr(
        certification_view_module,
        "certification_target_postorder",
        capture_postorder,
    )
    graph = SimpleNamespace(
        schema_version=6,
        nodes={
            "node-certify": object(),
            "node-certify._rtx": object(),
            "node-certify.source.gateway": object(),
            "node-certify._rtx.source.rtx-certifier": object(),
        },
        module_sources={
            "node-certify": ("node-certify.source.gateway",),
            "node-certify._rtx": (
                "node-certify._rtx.source.rtx-certifier",
            ),
        },
    )

    assert certification_view_module._certifier_target_postorder(
        SimpleNamespace(graph=graph, states={})
    ) == ("ordered",)
    assert set(observed[0]) == set(graph.nodes)


def _contract() -> dict[str, object]:
    return {
        "arguments": {},
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [
            {
                "id": "result",
                "audience": "machine",
                "description": "Result.",
                "type": {"kind": "string"},
                "direct_io_ref": "stdout",
                "cardinality": {"minimum": 1, "maximum": 1},
                "ordering": "stable",
                "pagination": {"kind": "none"},
                "truncation": {"kind": "none"},
                "empty": "Never empty.",
            }
        ],
        "outcomes": [
            {
                "id": "success",
                "class": "success",
                "outputs": ["result"],
                "effects": [],
                "caller_action": "Continue.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [],
        "direct_io": {
            "reads": [],
            "writes": [
                {
                    "id": "stdout",
                    "medium": "stdout",
                    "access": "write",
                    "content": "Result.",
                    "formats": ["text"],
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _repository(root: Path) -> tuple[object, dict[str, object], str]:
    repository = GitTestRepository.initialize_existing_empty(root)
    module = root / "skills" / "demo-skill"
    module.mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    source_id = "demo-skill.source.gateway"
    source_interface = f"{source_id}.interface.run"
    _write_yaml(
        module / "blueprints" / "gateway.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "description": "Gateway source.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                source_interface: {
                    "version": 1,
                    "description": "Run.",
                    "contract": _contract(),
                }
            },
        },
    )
    _write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": "demo-skill",
            "version": 1,
            "description": "Module.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "authority": {"owns_filesystem": []},
            "sources": {
                source_id: {
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/gateway.yaml",
                    }
                }
            },
            "exports": {
                "demo-skill.interface.run": {
                    "source_interface": source_interface,
                    "access": {"allow_all_modules": True, "allowed_callers": []},
                }
            },
        },
    )
    policy = root / "node-hash-policy.yaml"
    _write_yaml(
        policy,
        {
            "policy_version": 1,
            "path_syntax": "gitignore",
            "starting_set": "git-tracked-directly-owned-regular-files",
            "rules": [
                {"action": "exclude", "pattern": "**/.certificates/**"},
            ],
        },
    )
    basis_manifest = (
        root
        / "skills"
        / "node-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    basis_manifest.parent.mkdir(parents=True)
    basis_manifest.write_text('["node-hash-policy.yaml"]\n', encoding="utf-8")
    repository.git("add", ".")
    repository.git("commit", "-qm", "fixture")
    commit = repository.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
    graph = load_repository_blueprint_graph(
        root,
        schema_root=SCHEMA_ROOT,
        expected_schema_version=4,
    )
    states = compute_node_hash_states(
        graph,
        repo_root=root,
        policy_path=policy,
        certification_basis_hash="sha256:" + "b" * 64,
    )
    return graph, states, commit


def _postorder(graph: object) -> tuple[str, ...]:
    children = {node_id: [] for node_id in graph.nodes}
    for edge in graph.certification_edges:
        children[edge.source_node_id].append(edge.target_node_id)
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for child in sorted(children[node_id]):
            visit(child)
        ordered.append(node_id)

    for node_id in sorted(graph.nodes):
        visit(node_id)
    return tuple(ordered)


def _payload(
    root: Path,
    graph: object,
    states: dict[str, object],
    node_id: str,
    commit: str,
    key_id: str,
) -> dict:
    node = graph.nodes[node_id]
    state = states[node_id]
    return {
        "certificate_schema_version": 1,
        "subject": {
            "id": node.node_id,
            "node_type": node.node_type,
            "version": node.version,
            "blueprint_path": node.blueprint_path.relative_to(root).as_posix(),
            "gateway_path": node.gateway_path.relative_to(root).as_posix(),
        },
        "node_hash": state.node_hash,
        "source_commit": commit,
        "input_manifest": [dict(entry) for entry in state.input_manifest],
        "dependencies": [dict(entry) for entry in state.dependency_hashes],
        "certification_basis_hash": state.certification_basis_hash,
        "certifier": deepcopy(CERTIFIER),
        "checks": [deepcopy(check) for check in CHECKS],
        "key_id": key_id,
        "previous_entry_hash": None,
        "certified_at": "2026-07-20T12:00:00Z",
    }


def _write_log(graph: object, node_id: str, entries: list[dict]) -> None:
    path = certificate_log_path(graph.nodes[node_id])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(canonical_certificate_envelope_bytes(entry) + b"\n" for entry in entries)
    )


def _fixture(root: Path) -> tuple[object, dict[str, object], str, Path, MemorySecretBackend, object]:
    graph, states, commit = _repository(root)
    public_key_root = root / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    key = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    for node_id in _postorder(graph):
        _write_log(
            graph,
            node_id,
            [
                sign_certificate_payload(
                    _payload(root, graph, states, node_id, commit, key.key_id), key
                )
            ],
        )
    return graph, states, commit, public_key_root, backend, key


def _rewrite_payload_version_chain(
    root: Path,
    graph: object,
    states: dict[str, object],
    commit: str,
    public_key_root: Path,
    key: object,
    versions: tuple[int, ...],
) -> None:
    for node_id in _postorder(graph):
        first = parse_certificate_log(
            certificate_log_path(graph.nodes[node_id]).read_bytes(),
            public_key_root,
        )[0]
        entries = [first]
        previous_hash = certificate_entry_hash(first)
        for version in versions[1:]:
            payload = _payload(
                root,
                graph,
                states,
                node_id,
                commit,
                key.key_id,
            )
            payload["certificate_schema_version"] = version
            payload["previous_entry_hash"] = previous_hash
            envelope = sign_certificate_payload(payload, key)
            entries.append(envelope)
            previous_hash = certificate_entry_hash(envelope)
        _write_log(graph, node_id, entries)


def _evaluate_as_v5(
    root: Path,
    graph: object,
    states: dict[str, object],
    commit: str,
    public_key_root: Path,
) -> CertificateCurrentnessReport:
    return evaluate_certificate_currentness(
        replace(graph, schema_version=5),
        states,
        repo_root=root,
        public_key_root=public_key_root,
        source_commit=commit,
        certifier_identity=CERTIFIER,
        checks_by_node={node_id: CHECKS for node_id in graph.nodes},
        certification_basis_paths=(),
        schema_root=CANONICAL_SCHEMA_ROOT,
    )


def test_v5_currentness_handles_legacy_and_closed_v1_v2_entries_without_monotonicity(
    tmp_path: Path,
) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)

    legacy_report = _evaluate_as_v5(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
    )

    assert not legacy_report.current
    assert all(
        "legacy-certificate-payload" in status.concerns
        for status in legacy_report.nodes.values()
    )

    _rewrite_payload_version_chain(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
        key,
        (1, 2, 1, 2),
    )

    report = _evaluate_as_v5(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
    )

    assert report.current, {
        node_id: status.concerns
        for node_id, status in report.nodes.items()
    }


def _certifier_repository_with_provider_source(
    root: Path,
) -> RepositoryCertificationState:
    create_v4_repository(root, extra_modules=("provider",))
    source_id = "node-certify.source.provider-client"
    source_path = root / "skills" / "node-certify" / "_rtx" / "provider_client.py"
    source_path.parent.mkdir()
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    _write_yaml(
        root / "skills" / "node-certify" / "blueprints" / "provider-client.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "description": "Certifier provider client.",
            "gateway": {
                "path": "_rtx/provider_client.py",
                "language": "Python",
            },
            "content": [r"_rtx/provider_client\.py"],
            "dependencies": [
                {
                    "source": "provider.source.gateway",
                    "version": 1,
                    "reason": "Uses the provider source.",
                    "blueprint": {
                        "base": "repository-root",
                        "path": "skills/provider/blueprints/gateway.yaml",
                    },
                }
            ],
            "uses_interfaces": [],
            "interfaces": {
                f"{source_id}.interface.run": {
                    "version": 1,
                    "description": "Run.",
                    "contract": _contract(),
                }
            },
        },
    )
    module_path = root / "skills" / "node-certify" / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["content"].append(r"_rtx/provider_client\.py")
    module["sources"][source_id] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/provider-client.yaml",
        }
    }
    _write_yaml(module_path, module)
    repository = GitTestRepository(root)
    repository.git("add", ".")
    repository.git("commit", "-qm", "add provider client")
    return derive_repository_certification_state(root, expected_schema_version=4, schema_root=SCHEMA_ROOT)


def _evaluate(
    root: Path,
    graph: object,
    states: dict[str, object],
    commit: str,
    public_key_root: Path,
):
    return evaluate_certificate_currentness(
        graph,
        states,
        repo_root=root,
        public_key_root=public_key_root,
        source_commit=commit,
        certifier_identity=CERTIFIER,
        checks_by_node={node_id: CHECKS for node_id in graph.nodes},
        schema_root=SCHEMA_ROOT,
    )


def test_certificate_currentness_accepts_recursive_state_adapter_and_non_atomic_forwarding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, states, commit, public_key_root, _backend, _key = _fixture(tmp_path)
    real_read = certification_view_module.read_regular_file_bytes
    real_parse = certification_view_module.parse_certificate_log
    real_basis = certification_view_module.resolve_certification_basis_paths
    observed = {"basis": 0, "reads": 0, "parses": 0}

    def basis_with_fallback(*args: object, **kwargs: object):
        assert kwargs["allow_non_atomic"] is True
        observed["basis"] += 1
        return real_basis(*args, **kwargs)

    def read_with_fallback(*args: object, **kwargs: object) -> bytes:
        assert kwargs["allow_non_atomic"] is True
        observed["reads"] += 1
        return real_read(*args, **kwargs)

    def parse_with_fallback(*args: object, **kwargs: object):
        assert kwargs["allow_non_atomic"] is True
        observed["parses"] += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(
        certification_view_module,
        "resolve_certification_basis_paths",
        basis_with_fallback,
    )
    monkeypatch.setattr(
        certification_view_module, "read_regular_file_bytes", read_with_fallback
    )
    monkeypatch.setattr(
        certification_view_module, "parse_certificate_log", parse_with_fallback
    )

    report = evaluate_certificate_currentness(
        graph,
        states,
        repo_root=tmp_path,
        public_key_root=public_key_root,
        source_commit=commit,
        certifier_identity=CERTIFIER,
        checks_by_node={node_id: CHECKS for node_id in graph.nodes},
        schema_root=SCHEMA_ROOT,
        allow_non_atomic=True,
    )

    view = CertificateCurrentnessView(report)
    assert all(status.current for status in report.nodes.values())
    assert view.certificate_for("demo-skill") is not None
    assert view.check_export(
        "demo-skill",
        "demo-skill.interface.run",
        1,
        "demo-skill.source.gateway",
    ).certified
    assert observed == {
        "basis": 1,
        "reads": len(graph.nodes),
        "parses": len(graph.nodes),
    }


def test_repository_certification_state_accepts_later_head_with_unchanged_certified_inputs(
    tmp_path: Path,
) -> None:
    _graph, _states, certified_commit, public_key_root, _backend, _key = (
        create_certified_fixture(tmp_path)
    )
    repository = GitTestRepository(tmp_path)
    (tmp_path / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    repository.git("add", "unrelated.txt")
    repository.git("commit", "-qm", "unrelated later commit")
    assert (
        repository.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
        != certified_commit
    )

    state = derive_repository_certification_state(
        tmp_path,
        public_key_root=public_key_root,
        expected_schema_version=4,
        schema_root=SCHEMA_ROOT,
    )

    assert all(status.current for status in state.currentness.nodes.values())


def test_certificate_currentness_rejects_each_mismatched_projection(
    tmp_path: Path,
) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill"
    payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    payload["subject"] = {**payload["subject"], "id": "wrong"}
    payload["input_manifest"] = [
        {
            **payload["input_manifest"][0],
            "digest": "sha256:" + "d" * 64,
        }
    ]
    payload["node_hash"] = "sha256:" + "d" * 64
    payload["dependencies"] = [
        {
            "relation": "contains-source",
            "target": "demo-skill.source.gateway",
            "version": 1,
            "node_hash": "sha256:" + "d" * 64,
        }
    ]
    payload["certification_basis_hash"] = "sha256:" + "d" * 64
    payload["certifier"] = {**CERTIFIER, "version": 2}
    payload["checks"] = []
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    status = _evaluate(
        tmp_path, graph, states, commit, public_key_root
    ).nodes[node_id]

    assert not status.current
    assert status.concerns == (
        "subject-mismatch",
        "input-manifest-mismatch",
        "node-hash-mismatch",
        "dependency-mismatch",
        "certification-basis-mismatch",
        "certifier-mismatch",
        "checks-mismatch",
    )


def _v6_facet_fixture(
    root: Path,
) -> tuple[object, dict[str, object], str, Path, object, str, str]:
    graph, states, commit = _repository(root)
    graph = replace(graph, schema_version=6)
    public_key_root = root / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    node_id = "demo-skill.source.gateway"
    interface_id = f"{node_id}.interface.run"
    state = states[node_id]
    entries = tuple(state.input_manifest)
    states[node_id] = replace(
        state,
        facets=(
            CertificationFacetHashState(
                facet_id=node_id,
                facet_type="remainder",
                local_hash="sha256:" + "1" * 64,
                input_manifest=entries[1:],
                dependency_hashes=(),
            ),
            CertificationFacetHashState(
                facet_id=interface_id,
                facet_type="interface",
                local_hash="sha256:" + "2" * 64,
                input_manifest=entries[:1],
                dependency_hashes=(),
            ),
        ),
    )
    for current_id in _postorder(graph):
        payload = _payload(
            root,
            graph,
            states,
            current_id,
            commit,
            key.key_id,
        )
        payload["certificate_schema_version"] = 3
        payload["facets"] = [
            dict(claim)
            for claim in certification_facet_claims(states[current_id])
        ]
        _write_log(
            graph,
            current_id,
            [sign_certificate_payload(payload, key)],
        )
    return graph, states, commit, public_key_root, key, node_id, interface_id


def _v6_structured_certifier_fixture(root: Path):
    fixture = _v6_facet_fixture(root)
    graph, states, _commit, _public_key_root, _key, node_id, interface_id = fixture
    audit_interface = {
        "relation": "certified-under",
        "target": "node-certify.source.audit-interface",
        "interface": "node-certify.source.audit-interface.interface.audit",
        "version": 1,
        "interface_hash": "sha256:" + "5" * 64,
    }
    state = states[node_id]
    states[node_id] = replace(
        state,
        dependency_hashes=(audit_interface,),
        facets=tuple(
            replace(
                facet,
                dependency_hashes=(audit_interface,)
                if facet.facet_id == interface_id
                else (),
            )
            for facet in state.facets
        ),
    )
    return fixture


def _v6_structured_payload(
    root: Path,
    fixture,
) -> dict[str, object]:
    graph, states, commit, _public_key_root, key, node_id, _interface_id = fixture
    payload = _payload(root, graph, states, node_id, commit, key.key_id)
    payload["certificate_schema_version"] = 3
    payload["facets"] = [
        dict(claim) for claim in certification_facet_claims(states[node_id])
    ]
    return payload


def test_v6_structured_certifier_evidence_is_authoritative(
    tmp_path: Path,
) -> None:
    fixture = _v6_structured_certifier_fixture(tmp_path)
    graph, states, commit, public_key_root, _key, node_id, interface_id = fixture

    def write_and_evaluate(payload, target_id=node_id):
        _write_log(graph, target_id, [sign_certificate_payload(payload, _key)])
        return evaluate_certificate_currentness(
            graph,
            states,
            repo_root=tmp_path,
            public_key_root=public_key_root,
            source_commit=commit,
            certifier_identity={**CERTIFIER, "node_hash": "sha256:" + "d" * 64},
            checks_by_node={current_id: CHECKS for current_id in graph.nodes},
            certification_basis_paths=(),
            schema_root=CANONICAL_SCHEMA_ROOT,
            allow_non_atomic=True,
        ).nodes[target_id]

    payload = _v6_structured_payload(tmp_path, fixture)
    facet = next(item for item in payload["facets"] if item["id"] == interface_id)
    dependency = next(
        item
        for item in facet["dependencies"]
        if item["interface"]
        == "node-certify.source.audit-interface.interface.audit"
    )
    dependency["interface_hash"] = "sha256:" + "7" * 64
    top_level = next(
        item
        for item in payload["dependencies"]
        if item["interface"] == dependency["interface"]
    )
    top_level["interface_hash"] = dependency["interface_hash"]
    status = write_and_evaluate(payload)
    drift = next(item for item in status.facet_drift if item.facet_id == interface_id)
    delta = next(
        item
        for item in drift.dependencies
        if item.interface == "node-certify.source.audit-interface.interface.audit"
    )

    assert delta.relation == "certified-under"
    assert delta.certified["interface_hash"] == "sha256:" + "7" * 64
    assert delta.current["interface_hash"] == "sha256:" + "5" * 64
    assert not status.current
    assert "dependency-mismatch" in status.concerns
    assert "certifier-mismatch" not in status.concerns

    payload = _v6_structured_payload(tmp_path, fixture)
    payload["dependencies"] = []
    for facet in payload["facets"]:
        facet["dependencies"] = []
    status = write_and_evaluate(payload)

    assert "dependency-mismatch" in status.concerns
    assert "certifier-mismatch" not in status.concerns
    assert any(
        dependency.relation == "certified-under"
        for facet in status.facet_drift
        for dependency in facet.dependencies
    )

    module_id = "demo-skill"
    module_dependency = {
        "relation": "certified-under",
        "target": "node-certify.source.audit-module",
        "interface": "node-certify.source.audit-module.interface.audit",
        "version": 1,
        "interface_hash": "sha256:" + "5" * 64,
    }
    states[module_id] = replace(
        states[module_id],
        dependency_hashes=(module_dependency,),
    )
    payload = _payload(tmp_path, graph, states, module_id, commit, _key.key_id)
    payload["certificate_schema_version"] = 3
    payload["dependencies"][0]["interface_hash"] = "sha256:" + "7" * 64
    status = write_and_evaluate(payload, module_id)
    delta = status.dependencies[0]

    assert delta.interface == module_dependency["interface"]
    assert delta.relation == "certified-under"
    assert delta.certified["interface_hash"] == "sha256:" + "7" * 64
    assert delta.current["interface_hash"] == "sha256:" + "5" * 64
    assert not status.current
    assert "dependency-mismatch" in status.concerns
    assert "certifier-mismatch" not in status.concerns


def test_v6_currentness_reports_exact_facet_and_payload_shape_mismatches(
    tmp_path: Path,
) -> None:
    (
        graph,
        states,
        commit,
        public_key_root,
        key,
        node_id,
        interface_id,
    ) = _v6_facet_fixture(tmp_path)
    payload = _payload(
        tmp_path,
        graph,
        states,
        node_id,
        commit,
        key.key_id,
    )
    payload["certificate_schema_version"] = 3
    payload["facets"] = [
        dict(claim) for claim in certification_facet_claims(states[node_id])
    ]
    interface = next(
        facet for facet in payload["facets"] if facet["type"] == "interface"
    )
    remainder = next(
        facet for facet in payload["facets"] if facet["type"] == "remainder"
    )
    replacement_dependencies = [
        {
            "relation": "uses-source",
            "target": "demo-skill.source.gateway",
            "version": 1,
            "node_hash": "sha256:" + "4" * 64,
        }
    ]
    for facet in (interface, remainder):
        facet["local_hash"] = "sha256:" + "3" * 64
        facet["input_manifest"] = []
        facet["dependencies"] = replacement_dependencies
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    report = evaluate_certificate_currentness(
        graph,
        states,
        repo_root=tmp_path,
        public_key_root=public_key_root,
        source_commit=commit,
        certifier_identity=CERTIFIER,
        checks_by_node={current_id: CHECKS for current_id in graph.nodes},
        certification_basis_paths=(),
        schema_root=CANONICAL_SCHEMA_ROOT,
        allow_non_atomic=True,
    )

    assert report.nodes[node_id].concerns == (
        f"interface-hash-mismatch:{interface_id}",
        f"interface-input-manifest-mismatch:{interface_id}",
        f"interface-dependency-mismatch:{interface_id}",
        "remainder-hash-mismatch",
        "remainder-input-manifest-mismatch",
        "remainder-dependency-mismatch",
    )

    reversed_facets = [
        dict(claim)
        for claim in reversed(certification_facet_claims(states[node_id]))
    ]
    assert certification_view_module._facet_currentness_concerns(
        reversed_facets,
        states[node_id],
    ) == ("facet-order-mismatch",)

    payload = _payload(
        tmp_path,
        graph,
        states,
        node_id,
        commit,
        key.key_id,
    )
    payload["certificate_schema_version"] = 2
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    report = evaluate_certificate_currentness(
        graph,
        states,
        repo_root=tmp_path,
        public_key_root=public_key_root,
        source_commit=commit,
        certifier_identity=CERTIFIER,
        checks_by_node={current_id: CHECKS for current_id in graph.nodes},
        certification_basis_paths=(),
        schema_root=CANONICAL_SCHEMA_ROOT,
        allow_non_atomic=True,
    )

    assert "legacy-certificate-payload" in report.nodes[node_id].concerns


def test_v6_currentness_reports_exact_structured_facet_deltas() -> None:
    node_id = "demo-skill.source.gateway"
    interface_id = f"{node_id}.interface.run"
    blueprint_path = "skills/demo-skill/blueprints/gateway.yaml"
    current_manifest = (
        {
            "path": "skills/demo-skill/current.txt",
            "digest": "sha256:" + "b" * 64,
            "git_provenance": "tracked",
        },
        {
            "path": "skills/demo-skill/added.txt",
            "digest": "sha256:" + "c" * 64,
            "git_provenance": "tracked",
        },
    )
    current_dependency = {
        "relation": "uses-export",
        "target": "demo-skill.source.gateway",
        "interface": "demo-skill.interface.provider",
        "version": 1,
        "interface_hash": "sha256:" + "d" * 64,
    }
    current_contract_dependency = {
        "relation": "references-cross-owner-contract",
        "target": "demo-skill.source.contract-owner",
        "version": 1,
        "node_hash": "sha256:" + "3" * 64,
    }
    interface = CertificationFacetHashState(
        facet_id=interface_id,
        facet_type="interface",
        local_hash="sha256:" + "e" * 64,
        input_manifest=current_manifest,
        dependency_hashes=(current_contract_dependency, current_dependency),
    )
    state = NodeHashState(
        facets=(
            CertificationFacetHashState(
                facet_id=node_id,
                facet_type="remainder",
                local_hash="sha256:" + "f" * 64,
            ),
            interface,
        ),
    )
    payload_facets = [
        dict(claim) for claim in certification_facet_claims(state)
    ]
    certified = next(
        facet for facet in payload_facets if facet["id"] == interface_id
    )
    certified["input_manifest"] = [
        {
            "path": "skills/demo-skill/current.txt",
            "digest": "sha256:" + "a" * 64,
            "git_provenance": "tracked",
        },
        {
            "path": "skills/demo-skill/removed.txt",
            "digest": "sha256:" + "f" * 64,
            "git_provenance": "tracked",
        },
    ]
    certified["dependencies"] = [
        {
            **current_contract_dependency,
            "node_hash": "sha256:" + "2" * 64,
        },
        {
            **current_dependency,
            "interface_hash": "sha256:" + "1" * 64,
        }
    ]
    drift = certification_view_module._facet_drift(
        payload_facets,
        state,
        blueprint_path=blueprint_path,
    )

    assert len(drift) == 1
    assert drift[0].facet_id == interface_id
    assert drift[0].facet_type == "interface"
    assert [
        (delta.change, delta.path)
        for delta in drift[0].input_files
    ] == [
        ("added", "skills/demo-skill/added.txt"),
        ("modified", "skills/demo-skill/current.txt"),
        ("removed", "skills/demo-skill/removed.txt"),
    ]
    assert [
        (delta.change, delta.relation, delta.target, delta.interface)
        for delta in drift[0].dependencies
    ] == [
        (
            "modified",
            "references-cross-owner-contract",
            "demo-skill.source.contract-owner",
            None,
        ),
        (
            "modified",
            "uses-export",
            "demo-skill.source.gateway",
            "demo-skill.interface.provider",
        ),
    ]

    payload_facets = [
        dict(claim) for claim in certification_facet_claims(state)
    ]
    certified = next(
        facet for facet in payload_facets if facet["id"] == interface_id
    )
    certified["local_hash"] = "sha256:" + "9" * 64
    drift = certification_view_module._facet_drift(
        payload_facets,
        state,
        blueprint_path=blueprint_path,
    )

    assert len(drift) == 1
    assert drift[0].facet_id == interface_id
    assert drift[0].local_hash_changed
    assert drift[0].declaration_changed
    assert drift[0].blueprint_path == blueprint_path
    assert drift[0].input_files == ()
    assert drift[0].dependencies == ()


@pytest.fixture
def stale_worklist_fixture():
    dependency = "dependency"
    requested = "requested"
    graph = SimpleNamespace(nodes={dependency: object(), requested: object()})
    states = {
        dependency: NodeHashState(node_hash="sha256:" + "1" * 64),
        requested: NodeHashState(
            dependency_hashes=(
                {
                    "relation": "contains-source",
                    "target": dependency,
                    "version": 1,
                    "node_hash": "sha256:" + "1" * 64,
                },
            ),
        ),
    }
    return graph, states, dependency, requested


@pytest.mark.parametrize(
    ("requested_concerns", "expected_requested"),
    [
        (("node-hash-mismatch",), True),
        (("dependency-not-current:dependency",), False),
        (("dependency-mismatch", "dependency-not-current:dependency"), True),
    ],
)
def test_stale_worklist_retains_only_nodes_requiring_renewal(
    stale_worklist_fixture,
    requested_concerns: tuple[str, ...],
    expected_requested: bool,
) -> None:
    graph, states, dependency, requested = stale_worklist_fixture
    report = CertificateCurrentnessReport(
        nodes={
            dependency: CertificateNodeCurrentness(
                node_id=dependency,
                current=False,
                concerns=("checks-mismatch",),
                certificate=None,
            ),
            requested: CertificateNodeCurrentness(
                node_id=requested,
                current=False,
                concerns=requested_concerns,
                certificate=None,
            ),
        }
    )

    worklist = certification_view_module.certificate_stale_worklist(
        graph,
        states,
        report,
        (requested,),
    )

    assert worklist == (
        (dependency, requested) if expected_requested else (dependency,)
    )


def test_node_level_drift_reports_input_delta_and_blueprint_cause(
    tmp_path: Path,
) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill"
    original_state = states[node_id]
    states[node_id] = original_state
    payload = _payload(
        tmp_path,
        graph,
        states,
        node_id,
        commit,
        key.key_id,
    )
    blueprint_path = graph.nodes[node_id].blueprint_path.relative_to(
        tmp_path
    ).as_posix()
    entry_index, certified_entry = next(
        (index, dict(entry))
        for index, entry in enumerate(payload["input_manifest"])
        if entry["path"] != blueprint_path
    )
    payload["input_manifest"][entry_index] = {
        **certified_entry,
        "digest": "sha256:" + "9" * 64,
    }
    payload["node_hash"] = "sha256:" + "8" * 64
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    status = _evaluate(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
    ).nodes[node_id]

    assert [
        (delta.change, delta.path) for delta in status.input_files
    ] == [("modified", certified_entry["path"])]
    assert status.local_hash_changed
    assert not status.declaration_changed
    assert status.blueprint_path is None

    states[node_id] = original_state
    payload = _payload(
        tmp_path,
        graph,
        states,
        node_id,
        commit,
        key.key_id,
    )
    payload["node_hash"] = "sha256:" + "7" * 64
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    declaration_status = _evaluate(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
    ).nodes[node_id]

    assert declaration_status.input_files == ()
    assert declaration_status.declaration_changed
    assert declaration_status.blueprint_path == (
        graph.nodes[node_id].blueprint_path.relative_to(tmp_path).as_posix()
    )

    states[node_id] = original_state
    payload = _payload(
        tmp_path,
        graph,
        states,
        node_id,
        commit,
        key.key_id,
    )
    blueprint_entry = next(
        entry for entry in payload["input_manifest"] if entry["path"] == blueprint_path
    )
    blueprint_entry["digest"] = "sha256:" + "9" * 64
    payload["node_hash"] = "sha256:" + "8" * 64
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    blueprint_input_status = _evaluate(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
    ).nodes[node_id]

    assert [(delta.change, delta.path) for delta in blueprint_input_status.input_files] == [
        ("modified", blueprint_path)
    ]
    assert blueprint_input_status.declaration_changed
    assert blueprint_input_status.blueprint_path == blueprint_path

    states[node_id] = original_state
    current_dependency = {
        "relation": "uses-export",
        "target": "provider",
        "interface": "provider.interface.run",
        "version": 1,
        "interface_hash": "sha256:" + "2" * 64,
    }
    states[node_id] = replace(
        states[node_id],
        dependency_hashes=(current_dependency,),
    )
    payload = _payload(
        tmp_path,
        graph,
        states,
        node_id,
        commit,
        key.key_id,
    )
    payload["dependencies"] = [
        {
            **current_dependency,
            "interface_hash": "sha256:" + "1" * 64,
        }
    ]
    payload["node_hash"] = "sha256:" + "8" * 64
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    dependency_status = _evaluate(
        tmp_path,
        graph,
        states,
        commit,
        public_key_root,
    ).nodes[node_id]

    assert [
        (delta.change, delta.relation, delta.target, delta.interface)
        for delta in dependency_status.dependencies
    ] == [
        ("modified", "uses-export", "provider", "provider.interface.run")
    ]
    assert dependency_status.local_hash_changed
    assert not dependency_status.declaration_changed
    assert dependency_status.blueprint_path is None
    states[node_id] = original_state


def test_certificate_provenance_and_export_source_currentness(
    tmp_path: Path,
) -> None:
    graph, states, current_commit, public_key_root, _backend, key = _fixture(tmp_path)
    certified_commit = "d" * 40
    certified_certifier = {**CERTIFIER, "source_commit": certified_commit}

    for node_id in graph.nodes:
        payload = _payload(tmp_path, graph, states, node_id, certified_commit, key.key_id)
        payload["certifier"] = certified_certifier
        _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    report = evaluate_certificate_currentness(
        graph,
        states,
        repo_root=tmp_path,
        public_key_root=public_key_root,
        source_commit=current_commit,
        certifier_identity=CERTIFIER,
        checks_by_node={node_id: CHECKS for node_id in graph.nodes},
        schema_root=SCHEMA_ROOT,
        allow_non_atomic=True,
    )

    assert all(status.current for status in report.nodes.values())
    certificate_log_path(graph.nodes["demo-skill.source.gateway"]).unlink()

    report = _evaluate(
        tmp_path,
        graph,
        states,
        current_commit,
        public_key_root,
    )
    decision = CertificateCurrentnessView(report).check_export(
        "demo-skill",
        "demo-skill.interface.run",
        1,
        "demo-skill.source.gateway",
    )

    assert "missing-certificate-log" in report.nodes["demo-skill.source.gateway"].concerns
    assert report.nodes["demo-skill"].current
    assert not decision.certified
    assert decision.code == "source-certification-unavailable"


def test_rotation_and_history_integrity_select_only_a_valid_final_entry(
    tmp_path: Path,
) -> None:
    graph, states, commit, public_key_root, backend, old_key = _fixture(tmp_path)
    new_key = rotate_certificate_signing_key(public_key_root, secret_backend=backend)
    for node_id in _postorder(graph):
        old = sign_certificate_payload(
            _payload(tmp_path, graph, states, node_id, commit, old_key.key_id), old_key
        )
        new_payload = _payload(tmp_path, graph, states, node_id, commit, new_key.key_id)
        new_payload["previous_entry_hash"] = certificate_entry_hash(old)
        _write_log(graph, node_id, [old, sign_certificate_payload(new_payload, new_key)])

    report = _evaluate(tmp_path, graph, states, commit, public_key_root)

    assert all(status.current for status in report.nodes.values())
    rotate_certificate_signing_key(public_key_root, secret_backend=backend)

    inactive = _evaluate(tmp_path, graph, states, commit, public_key_root)
    assert "suspect-certificate-log" in inactive.nodes["demo-skill"].concerns

    new_key = rotate_certificate_signing_key(public_key_root, secret_backend=backend)
    node_id = "demo-skill.source.gateway"
    old = sign_certificate_payload(
        _payload(tmp_path, graph, states, node_id, commit, old_key.key_id), old_key
    )
    broken_payload = _payload(tmp_path, graph, states, node_id, commit, new_key.key_id)
    broken_payload["previous_entry_hash"] = "sha256:" + "0" * 64
    _write_log(graph, node_id, [old, sign_certificate_payload(broken_payload, new_key)])

    broken = _evaluate(tmp_path, graph, states, commit, public_key_root)
    assert "suspect-certificate-log" in broken.nodes[node_id].concerns


def test_schema_and_history_validation_rejects_every_invalid_entry_position(
    tmp_path: Path,
) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill.source.gateway"

    payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    payload["unexpected_field"] = []
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    status = _evaluate(tmp_path, graph, states, commit, public_key_root).nodes[node_id]

    assert not status.current
    assert "invalid-certificate-schema" in status.concerns

    historical_payload = _payload(
        tmp_path, graph, states, node_id, commit, key.key_id
    )
    historical_payload["unexpected_field"] = []
    historical = sign_certificate_payload(historical_payload, key)
    final_payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    final_payload["previous_entry_hash"] = certificate_entry_hash(historical)
    final = sign_certificate_payload(final_payload, key)
    _write_log(graph, node_id, [historical, final])

    status = _evaluate(tmp_path, graph, states, commit, public_key_root).nodes[node_id]

    assert not status.current
    assert "invalid-certificate-schema" in status.concerns

    current = sign_certificate_payload(
        _payload(tmp_path, graph, states, node_id, commit, key.key_id), key
    )
    stale_payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    stale_payload["certification_basis_hash"] = "sha256:" + "d" * 64
    stale_payload["previous_entry_hash"] = certificate_entry_hash(current)
    stale = sign_certificate_payload(stale_payload, key)
    _write_log(graph, node_id, [current, stale])

    status = _evaluate(tmp_path, graph, states, commit, public_key_root).nodes[node_id]

    assert not status.current
    assert status.certificate == stale
    assert "certification-basis-mismatch" in status.concerns


def test_zero_certificate_view_allows_only_exact_read_only_sync_fallback(
    tmp_path: Path,
) -> None:
    view = RepositoryCertificationView(
        CertificateCurrentnessReport(nodes={}),
        repo_root=tmp_path,
        source_commit="a" * 40,
        bootstrap_allowed=True,
        schema_version=4,
    )

    assert view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="node-certify",
        terminal_module_id="node-certify",
        interface_id="node-certify.interface.certify",
        pattern_name=None,
        argv=(
            "certify",
            "node-certify",
            "--reviewed-repository",
            str(tmp_path),
            "--reviewed-commit",
            "a" * 40,
        ),
    ).certified
    assert view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="skill-maker",
        terminal_module_id="skill-maker",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="check",
        argv=("--check",),
    ).certified
    assert not view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="skill-maker",
        terminal_module_id="skill-maker",
        interface_id="node-certify.interface.certify",
        pattern_name=None,
        argv=(
            "certify",
            "node-certify",
            "--reviewed-repository",
            str(tmp_path),
            "--reviewed-commit",
            "a" * 40,
        ),
    ).certified
    assert not view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="node-certify",
        terminal_module_id="node-certify",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="check",
        argv=("--check",),
    ).certified

    rejected = (
        (
            "daily-plan",
            "node-certify.interface.certify",
            None,
            (
                "certify",
                "node-certify",
                "--reviewed-repository",
                str(tmp_path),
                "--reviewed-commit",
                "a" * 40,
            ),
        ),
        (
            "node-certify",
            "node-certify.interface.certify",
            None,
            ("certify",),
        ),
        (
            "node-certify",
            "node-certify.interface.certify",
            None,
            (
                "certify",
                "other-module",
                "--reviewed-repository",
                str(tmp_path),
                "--reviewed-commit",
                "a" * 40,
            ),
        ),
        (
            "node-certify",
            "skill-maker.interface.sync-blueprints",
            "sync",
            ("--check",),
        ),
        (
            "node-certify",
            "skill-maker.interface.sync-blueprints",
            "sync",
            (),
        ),
        (
            "daily-plan",
            "skill-maker.interface.sync-blueprints",
            "sync",
            (),
        ),
        (
            "node-certify",
            "node-drift.interface.compute-hashes",
            None,
            ("compute-hashes", "--json"),
        ),
        (
            "node-certify",
            "node-drift.interface.drift-status",
            None,
            ("status", "--json"),
        ),
        (
            "node-certify",
            "unrelated.interface.run",
            None,
            (),
        ),
    )
    for caller, interface_id, pattern_name, argv in rejected:
        assert not view.check_bootstrap(
            caller_module_id=caller,
            target_module_id=(
                "skill-maker"
                if interface_id == "skill-maker.interface.sync-blueprints"
                else "node-certify"
            ),
            terminal_module_id=(
                "skill-maker"
                if interface_id == "skill-maker.interface.sync-blueprints"
                else "node-certify"
            ),
            interface_id=interface_id,
            pattern_name=pattern_name,
            argv=argv,
        ).certified


def test_v5_bootstrap_mutation_requires_runtime_child_terminal(
    tmp_path: Path,
) -> None:
    view = RepositoryCertificationView(
        CertificateCurrentnessReport(nodes={}),
        repo_root=tmp_path,
        source_commit="a" * 40,
        bootstrap_allowed=True,
        schema_version=5,
    )
    request = {
        "caller_module_id": "node-certify",
        "target_module_id": "node-certify",
        "interface_id": "node-certify.interface.certify",
        "pattern_name": None,
        "argv": (
            "certify",
            "node-certify",
            "--reviewed-repository",
            str(tmp_path),
            "--reviewed-commit",
            "a" * 40,
        ),
    }

    assert view.check_bootstrap(
        terminal_module_id="node-certify-rtx",
        **request,
    ).certified
    assert not view.check_bootstrap(
        terminal_module_id="node-certify",
        **request,
    ).certified
    sync_request = {
        "caller_module_id": "node-certify",
        "target_module_id": "skill-maker",
        "interface_id": "skill-maker.interface.sync-blueprints",
        "pattern_name": "check",
        "argv": ("--check",),
    }
    assert view.check_bootstrap(
        terminal_module_id="skill-maker-rtx",
        **sync_request,
    ).certified
    assert not view.check_bootstrap(
        terminal_module_id="skill-maker",
        **sync_request,
    ).certified


def test_repository_view_never_bootstraps_when_initial_state_is_not_clean(
    tmp_path: Path,
) -> None:
    view = RepositoryCertificationView(
        CertificateCurrentnessReport(nodes={}),
        repo_root=tmp_path,
        source_commit="a" * 40,
        bootstrap_allowed=False,
        schema_version=4,
    )

    decision = view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="node-drift",
        terminal_module_id="node-drift",
        interface_id="node-drift.interface.compute-hashes",
        pattern_name=None,
        argv=("compute-hashes", "--json"),
    )

    assert not decision.certified
    assert decision.code == "certification-unavailable"


def test_repository_view_admits_only_valid_exact_self_recertification(
    tmp_path: Path,
) -> None:
    graph, states, commit = create_v4_repository(tmp_path)
    public_key_root = certificate_public_key_root(tmp_path)
    public_key_root.mkdir(parents=True)
    backend = MemorySecretBackend()
    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    certifier_root = graph.nodes["node-certify"].module_root
    certifier_targets = tuple(
        sorted(
            node_id
            for node_id, node in graph.nodes.items()
            if node.module_root == certifier_root
        )
    )
    signed: dict[str, dict] = {}
    for node_id in certifier_targets:
        signed[node_id] = sign_certificate_payload(
            v4_payload(
                tmp_path,
                graph,
                states,
                node_id,
                commit,
                key.key_id,
            ),
            key,
        )
        _write_log(graph, node_id, [signed[node_id]])
    active_key = rotate_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )

    view = repository_certification_view(tmp_path, expected_schema_version=4, schema_root=SCHEMA_ROOT)
    assert all(
        "suspect-certificate-log" in view.report.nodes[node_id].concerns
        for node_id in certifier_targets
    )
    exact = (
        "certify",
        "node-certify",
        "--reviewed-repository",
        str(tmp_path),
        "--reviewed-commit",
        commit,
    )
    assert view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="node-certify",
        terminal_module_id="node-certify",
        interface_id="node-certify.interface.certify",
        pattern_name=None,
        argv=exact,
    ).certified
    assert not view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="node-certify",
        terminal_module_id="node-certify",
        interface_id="node-certify.interface.certify",
        pattern_name=None,
        argv=(
            "certify",
            "demo-skill",
            "--reviewed-repository",
            str(tmp_path),
            "--reviewed-commit",
            commit,
        ),
    ).certified

    corrupt_node_id = certifier_targets[-1]
    corrupt = deepcopy(signed[corrupt_node_id])
    corrupt["signature"]["value"] = "base64:" + base64.b64encode(
        b"\0" * 64
    ).decode("ascii")
    _write_log(graph, corrupt_node_id, [corrupt])

    assert not repository_certification_view(tmp_path, expected_schema_version=4, schema_root=SCHEMA_ROOT).check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="node-certify",
        terminal_module_id="node-certify",
        interface_id="node-certify.interface.certify",
        pattern_name=None,
        argv=exact,
    ).certified

    for node_id in certifier_targets:
        _write_log(
            graph,
            node_id,
            [
                sign_certificate_payload(
                    v4_payload(
                        tmp_path,
                        graph,
                        states,
                        node_id,
                        commit,
                        active_key.key_id,
                    ),
                    active_key,
                )
            ],
        )
    log_node_id = "node-certify.source.gateway"
    _write_log(
        graph,
        log_node_id,
        [
            sign_certificate_payload(
                v4_payload(
                    tmp_path,
                    graph,
                    states,
                    "node-certify",
                    commit,
                    active_key.key_id,
                ),
                active_key,
            )
        ],
    )
    state = derive_repository_certification_state(
        tmp_path,
        expected_schema_version=4,
        schema_root=SCHEMA_ROOT,
    )

    assert "subject-mismatch" in state.currentness.nodes[log_node_id].concerns
    assert not certification_view_module._certifier_renewal_state_admissible(
        state,
        repo_root=tmp_path,
    )


def test_partial_certifier_multi_root_closure_keeps_only_read_only_sync_fallback(
    tmp_path: Path,
) -> None:
    certifier_root = tmp_path / "skills" / "node-certify"
    nodes = {
        node_id: BlueprintNode(
            node_id=node_id,
            node_type=(
                "module"
                if node_id == "node-certify"
                else "behavioral_source"
            ),
            version=1,
            module_root=(
                certifier_root
                if node_id.startswith("node-certify")
                else tmp_path / "skills" / "skill-maker"
            ),
            blueprint_path=tmp_path / f"{node_id}.yaml",
            gateway_path=None,
            declaration={"schema_version": 4},
        )
        for node_id in (
            "node-certify",
            "node-certify.source.gateway",
            "node-certify.source.runtime",
            "skill-maker.source.sync-blueprints",
        )
    }
    graph = RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources={
            "node-certify": (
                "node-certify.source.gateway",
                "node-certify.source.runtime",
            )
        },
        direct_file_owners={},
    )
    states = {
        "node-certify": NodeHashState(),
        "node-certify.source.gateway": NodeHashState(
            dependency_hashes=(
                {
                    "relation": "uses-source",
                    "target": "skill-maker.source.sync-blueprints",
                    "version": 1,
                },
            )
        ),
        "node-certify.source.runtime": NodeHashState(),
        "skill-maker.source.sync-blueprints": NodeHashState(),
    }

    def state(existing: set[str]) -> RepositoryCertificationState:
        for node_id, node in nodes.items():
            path = certificate_log_path(node)
            if node_id in existing:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            elif path.exists():
                path.unlink()
        report = CertificateCurrentnessReport(
            nodes={
                node_id: CertificateNodeCurrentness(
                    node_id=node_id,
                    current=node_id in existing,
                    concerns=(
                        ()
                        if node_id in existing
                        else ("missing-certificate-log",)
                    ),
                    certificate={} if node_id in existing else None,
                )
                for node_id in nodes
            }
        )
        return RepositoryCertificationState(
            graph=graph,
            states=states,
            source_commit="a" * 40,
            certification_basis_hash="sha256:" + "b" * 64,
            certifier_identity={},
            currentness=report,
        )

    partial = state(
        {
            "node-certify",
            "skill-maker.source.sync-blueprints",
        }
    )
    view = RepositoryCertificationView(
        partial.currentness,
        repo_root=tmp_path,
        source_commit=partial.source_commit,
        bootstrap_allowed=certification_view_module._initial_certificate_state_admissible(
            partial
        ),
        schema_version=4,
    )

    assert view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="skill-maker",
        terminal_module_id="skill-maker",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="check",
        argv=("--check",),
    ).certified
    assert not view.check_bootstrap(
        caller_module_id="node-certify",
        target_module_id="skill-maker",
        terminal_module_id="skill-maker",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="sync",
        argv=(),
    ).certified


def test_renewal_accepts_only_valid_migrated_and_multi_root_prefixes(
    tmp_path: Path,
) -> None:
    state = _certifier_repository_with_provider_source(tmp_path)
    migrated_state = replace(
        state,
        graph=replace(state.graph, schema_version=5),
    )

    assert certification_view_module._certifier_renewal_state_admissible(
        migrated_state,
        repo_root=tmp_path,
    )

    migrated_order = certification_view_module._certifier_target_postorder(
        migrated_state
    )
    assert migrated_order
    migrated_path = certificate_log_path(
        migrated_state.graph.nodes[migrated_order[0]]
    )
    migrated_path.parent.mkdir(parents=True, exist_ok=True)
    migrated_path.write_text("{}\n", encoding="utf-8")

    assert not certification_view_module._certifier_renewal_state_admissible(
        migrated_state,
        repo_root=tmp_path,
    )
    migrated_path.unlink()

    order = certification_view_module._certifier_target_postorder(state)
    assert order == (
        "node-certify",
        "node-certify.source.gateway",
        "provider.source.gateway",
        "node-certify.source.provider-client",
    )
    public_key_root = certificate_public_key_root(tmp_path)
    public_key_root.mkdir(parents=True)
    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=MemorySecretBackend(),
    )
    for node_id in (
        "node-certify",
        "node-certify.source.gateway",
        "node-certify.source.provider-client",
    ):
        _write_log(
            state.graph,
            node_id,
            [
                sign_certificate_payload(
                    v4_payload(
                        tmp_path,
                        state.graph,
                        state.states,
                        node_id,
                        state.source_commit,
                        key.key_id,
                    ),
                    key,
                )
            ],
        )
    state = derive_repository_certification_state(tmp_path, expected_schema_version=4, schema_root=SCHEMA_ROOT)

    assert not certification_view_module._certifier_renewal_state_admissible(
        state,
        repo_root=tmp_path,
    )

from __future__ import annotations

import base64
from copy import deepcopy
import subprocess
from pathlib import Path

import pytest
import yaml

import officina.common.certification_view as certification_view_module
from officina.common.certification_hashing import NodeHashState, compute_node_hash_states
from officina.common.certificate_records import (
    canonical_certificate_envelope_bytes,
    certificate_public_key_root,
    certificate_entry_hash,
    load_or_create_certificate_signing_key,
    rotate_certificate_signing_key,
    sign_certificate_payload,
)
from officina.common.blueprint_graph import (
    BlueprintNode,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.certification_view import (
    CertificateNodeCurrentness,
    CertificateCurrentnessReport,
    CertificateCurrentnessView,
    RepositoryCertificationView,
    RepositoryCertificationState,
    certificate_log_path,
    derive_repository_certification_state,
    evaluate_certificate_currentness,
    repository_certification_view,
)
from v4_certification_fixtures import (
    create_v4_repository,
    payload as v4_payload,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"
CERTIFIER = {
    "interface": "skill-certifier.interface.certify",
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
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    basis_manifest.parent.mkdir(parents=True)
    basis_manifest.write_text('["node-hash-policy.yaml"]\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Tests"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    graph = load_repository_blueprint_graph(root, schema_root=SCHEMA_ROOT)
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


def _certifier_repository_with_provider_source(
    root: Path,
) -> RepositoryCertificationState:
    create_v4_repository(root, extra_modules=("provider",))
    source_id = "skill-certifier.source.provider-client"
    source_path = root / "skills" / "skill-certifier" / "_rtx" / "provider_client.py"
    source_path.parent.mkdir()
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    _write_yaml(
        root / "skills" / "skill-certifier" / "blueprints" / "provider-client.yaml",
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
    module_path = root / "skills" / "skill-certifier" / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["content"].append(r"_rtx/provider_client\.py")
    module["sources"][source_id] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/provider-client.yaml",
        }
    }
    _write_yaml(module_path, module)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "add provider client"],
        check=True,
    )
    return derive_repository_certification_state(root)


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


def test_certificate_currentness_accepts_exact_recursive_state_and_adapter(tmp_path: Path) -> None:
    graph, states, commit, public_key_root, _backend, _key = _fixture(tmp_path)

    report = _evaluate(tmp_path, graph, states, commit, public_key_root)
    view = CertificateCurrentnessView(report)

    assert all(status.current for status in report.nodes.values())
    assert view.certificate_for("demo-skill") is not None
    assert view.check_export(
        "demo-skill",
        "demo-skill.interface.run",
        1,
        "demo-skill.source.gateway",
    ).certified


def test_certificate_currentness_propagates_explicit_non_atomic_fallback(
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

    assert all(status.current for status in report.nodes.values())
    assert observed == {
        "basis": 1,
        "reads": len(graph.nodes),
        "parses": len(graph.nodes),
    }


@pytest.mark.parametrize(
    ("field", "replacement", "concern"),
    [
        ("subject", {"id": "wrong"}, "subject-mismatch"),
        ("source_commit", "d" * 40, "source-commit-mismatch"),
        ("input_manifest", [], "input-manifest-mismatch"),
        ("node_hash", "sha256:" + "d" * 64, "node-hash-mismatch"),
        (
            "dependencies",
            [
                {
                    "relation": "contains-source",
                    "target": "demo-skill.source.gateway",
                    "version": 1,
                    "node_hash": "sha256:" + "d" * 64,
                }
            ],
            "dependency-mismatch",
        ),
        ("certification_basis_hash", "sha256:" + "d" * 64, "certification-basis-mismatch"),
        ("certifier", {**CERTIFIER, "version": 2}, "certifier-mismatch"),
        ("checks", [], "checks-mismatch"),
    ],
)
def test_certificate_currentness_rejects_each_mismatched_projection(
    tmp_path: Path,
    field: str,
    replacement: object,
    concern: str,
) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill"
    payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    if field == "subject":
        payload[field] = {**payload[field], **replacement}
    else:
        payload[field] = replacement
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    status = _evaluate(tmp_path, graph, states, commit, public_key_root).nodes[node_id]

    assert not status.current
    assert concern in status.concerns


def test_export_requires_its_exact_source_but_containment_does_not_stale_module(
    tmp_path: Path,
) -> None:
    graph, states, commit, public_key_root, _backend, _key = _fixture(tmp_path)
    certificate_log_path(graph.nodes["demo-skill.source.gateway"]).unlink()

    report = _evaluate(tmp_path, graph, states, commit, public_key_root)
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


def test_rotation_with_linked_new_final_entries_remains_current(tmp_path: Path) -> None:
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


def test_broken_history_and_inactive_final_key_are_suspect(tmp_path: Path) -> None:
    graph, states, commit, public_key_root, backend, old_key = _fixture(tmp_path)
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


def test_history_never_restores_an_older_matching_entry(tmp_path: Path) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill.source.gateway"
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


def test_schema_rejects_extra_certificate_data(tmp_path: Path) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill.source.gateway"
    payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    payload["unexpected_field"] = []
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    status = _evaluate(tmp_path, graph, states, commit, public_key_root).nodes[node_id]

    assert not status.current
    assert "invalid-certificate-schema" in status.concerns


def test_schema_rejects_invalid_historical_certificate_data(tmp_path: Path) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill.source.gateway"
    historical_payload = _payload(
        tmp_path, graph, states, node_id, commit, key.key_id
    )
    historical_payload["unexpected_field"] = []
    historical = sign_certificate_payload(historical_payload, key)
    final_payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    final_payload["previous_entry_hash"] = certificate_entry_hash(historical)
    final = sign_certificate_payload(final_payload, key)
    _write_log(graph, node_id, [historical, final])

    status = _evaluate(
        tmp_path, graph, states, commit, public_key_root
    ).nodes[node_id]

    assert not status.current
    assert "invalid-certificate-schema" in status.concerns


def test_zero_certificate_view_allows_only_exact_read_only_sync_fallback(
    tmp_path: Path,
) -> None:
    view = RepositoryCertificationView(
        CertificateCurrentnessReport(nodes={}),
        repo_root=tmp_path,
        source_commit="a" * 40,
        bootstrap_allowed=True,
    )

    assert view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-certifier.interface.certify",
        pattern_name=None,
        argv=(
            "certify",
            "skill-certifier",
            "--reviewed-repository",
            str(tmp_path),
            "--reviewed-commit",
            "a" * 40,
        ),
    ).certified
    assert view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="check",
        argv=("--check",),
    ).certified

    rejected = (
        (
            "daily-plan",
            "skill-certifier.interface.certify",
            None,
            (
                "certify",
                "skill-certifier",
                "--reviewed-repository",
                str(tmp_path),
                "--reviewed-commit",
                "a" * 40,
            ),
        ),
        (
            "skill-certifier",
            "skill-certifier.interface.certify",
            None,
            ("certify",),
        ),
        (
            "skill-certifier",
            "skill-certifier.interface.certify",
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
            "skill-certifier",
            "skill-maker.interface.sync-blueprints",
            "sync",
            ("--check",),
        ),
        (
            "skill-certifier",
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
            "skill-certifier",
            "skill-drift.interface.compute-hashes",
            None,
            ("compute-hashes", "--json"),
        ),
        (
            "skill-certifier",
            "skill-drift.interface.drift-status",
            None,
            ("status", "--json"),
        ),
        (
            "skill-certifier",
            "unrelated.interface.run",
            None,
            (),
        ),
    )
    for caller, interface_id, pattern_name, argv in rejected:
        assert not view.check_bootstrap(
            caller_module_id=caller,
            interface_id=interface_id,
            pattern_name=pattern_name,
            argv=argv,
        ).certified


def test_repository_view_never_bootstraps_when_initial_state_is_not_clean(
    tmp_path: Path,
) -> None:
    view = RepositoryCertificationView(
        CertificateCurrentnessReport(nodes={}),
        repo_root=tmp_path,
        source_commit="a" * 40,
        bootstrap_allowed=False,
    )

    decision = view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-drift.interface.compute-hashes",
        pattern_name=None,
        argv=("compute-hashes", "--json"),
    )

    assert not decision.certified
    assert decision.code == "certification-unavailable"


def test_repository_view_admits_only_exact_self_recertification_for_valid_stale_history(
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
    certifier_root = graph.nodes["skill-certifier"].skill_root
    certifier_targets = tuple(
        sorted(
            node_id
            for node_id, node in graph.nodes.items()
            if node.skill_root == certifier_root
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
    rotate_certificate_signing_key(public_key_root, secret_backend=backend)

    view = repository_certification_view(tmp_path)
    assert all(
        "suspect-certificate-log" in view.report.nodes[node_id].concerns
        for node_id in certifier_targets
    )
    exact = (
        "certify",
        "skill-certifier",
        "--reviewed-repository",
        str(tmp_path),
        "--reviewed-commit",
        commit,
    )
    assert view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-certifier.interface.certify",
        pattern_name=None,
        argv=exact,
    ).certified
    assert not view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-certifier.interface.certify",
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

    assert not repository_certification_view(tmp_path).check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-certifier.interface.certify",
        pattern_name=None,
        argv=exact,
    ).certified


def test_partial_certifier_multi_root_closure_keeps_only_read_only_sync_fallback(
    tmp_path: Path,
) -> None:
    certifier_root = tmp_path / "skills" / "skill-certifier"
    nodes = {
        node_id: BlueprintNode(
            node_id=node_id,
            node_type=(
                "module"
                if node_id == "skill-certifier"
                else "behavioral_source"
            ),
            version=1,
            skill_root=(
                certifier_root
                if node_id.startswith("skill-certifier")
                else tmp_path / "skills" / "skill-maker"
            ),
            blueprint_path=tmp_path / f"{node_id}.yaml",
            gateway_path=None,
            declaration={"schema_version": 4},
        )
        for node_id in (
            "skill-certifier",
            "skill-certifier.source.gateway",
            "skill-certifier.source.runtime",
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
            "skill-certifier": (
                "skill-certifier.source.gateway",
                "skill-certifier.source.runtime",
            )
        },
        direct_file_owners={},
    )
    states = {
        "skill-certifier": NodeHashState(),
        "skill-certifier.source.gateway": NodeHashState(
            dependency_hashes=(
                {
                    "relation": "uses-source",
                    "target": "skill-maker.source.sync-blueprints",
                    "version": 1,
                },
            )
        ),
        "skill-certifier.source.runtime": NodeHashState(),
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
            "skill-certifier",
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
    )

    assert view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="check",
        argv=("--check",),
    ).certified
    assert not view.check_bootstrap(
        caller_module_id="skill-certifier",
        interface_id="skill-maker.interface.sync-blueprints",
        pattern_name="sync",
        argv=(),
    ).certified


def test_renewal_rejects_nonprefix_second_root_provider_history(
    tmp_path: Path,
) -> None:
    state = _certifier_repository_with_provider_source(tmp_path)
    order = certification_view_module._certifier_target_postorder(state)
    assert order == (
        "skill-certifier",
        "skill-certifier.source.gateway",
        "provider.source.gateway",
        "skill-certifier.source.provider-client",
    )
    public_key_root = certificate_public_key_root(tmp_path)
    public_key_root.mkdir(parents=True)
    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=MemorySecretBackend(),
    )
    for node_id in (
        "skill-certifier",
        "skill-certifier.source.gateway",
        "skill-certifier.source.provider-client",
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
    state = derive_repository_certification_state(tmp_path)

    assert not certification_view_module._certifier_renewal_state_admissible(
        state,
        repo_root=tmp_path,
    )


def test_renewal_rejects_signed_entry_for_different_log_subject(
    tmp_path: Path,
) -> None:
    create_v4_repository(tmp_path)
    state = derive_repository_certification_state(tmp_path)
    public_key_root = certificate_public_key_root(tmp_path)
    public_key_root.mkdir(parents=True)
    key = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=MemorySecretBackend(),
    )
    log_node_id = "skill-certifier.source.gateway"
    _write_log(
        state.graph,
        log_node_id,
        [
            sign_certificate_payload(
                v4_payload(
                    tmp_path,
                    state.graph,
                    state.states,
                    "skill-certifier",
                    state.source_commit,
                    key.key_id,
                ),
                key,
            )
        ],
    )
    state = derive_repository_certification_state(tmp_path)
    assert "subject-mismatch" in state.currentness.nodes[log_node_id].concerns

    assert not certification_view_module._certifier_renewal_state_admissible(
        state,
        repo_root=tmp_path,
    )

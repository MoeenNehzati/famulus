"""Keep preparation reuse physical at Git, signature and certificate boundaries."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import officina.certification.records as records
import officina.certification.view as currentness
import officina.repository.checks.runner as checks
from officina.certification.hashing import (
    certification_input_scope, certification_target_postorder, expected_certifier_checks,
)
from officina.rutter import RutterRegistry
from test_support.git_repository import GitTestRepository

from .. import _certification_preparation as preparation
from .. import _certification_support as support
from .._certification_voyage import CERTIFICATION_RUTTER


def test_preparation_reuses_evidence_but_keeps_fresh_guards_and_raw_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reuse the existing small physical repository, not a second fixture builder.
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "preparation_view_fixture", root / "tests/test_officina_certification_view.py",
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    graph, states, _commit = fixture._repository(tmp_path)
    repository = GitTestRepository(tmp_path)
    schema = tmp_path / "references/blueprint-schema"
    schema.mkdir()
    for name in ("certificate.schema.json", "common.schema.json"):
        (schema / name).write_bytes((fixture.CANONICAL_SCHEMA_ROOT / name).read_bytes())
    (tmp_path / "skills/node-certify").mkdir()
    repository.git("add", ".")
    repository.git("commit", "-qm", "certificate schemas")
    commit = preparation.capture_git_snapshot(tmp_path).commit
    backend = fixture.MemorySecretBackend()
    key = records.provision_certificate_signing_material(tmp_path, secret_backend=backend)
    public_keys = records.certificate_public_key_root(tmp_path)
    monkeypatch.setattr(preparation, "provision_certificate_signing_material",
        lambda repository, **_kwargs: records.provision_certificate_signing_material(repository, secret_backend=backend))
    monkeypatch.setattr(preparation, "load_certificate_signing_key",
        lambda public_keys, **_kwargs: records.load_certificate_signing_key(public_keys, secret_backend=backend))
    calls = {"validators": 0, "graphs": 0}
    gates = []
    local_failure = None

    def validators(repository, suite, **kwargs):
        assert repository == tmp_path and suite == "validators"
        selected = kwargs["validation_node_ids"]
        assert kwargs["prepared_graph"] is (graph if kwargs["graph_checks"] else None)
        assert kwargs["graph_checks"] or len(selected) == 1
        assert kwargs["validation_paths"] == tuple(sorted({
            entry["path"] for node in selected for entry in states[node].input_manifest
        }))
        gates.append((kwargs["graph_checks"], selected))
        calls["validators"] += 1
        if not kwargs["graph_checks"]:
            if local_failure == "rejected":
                print("selected node validator finding")
                return 1
            if local_failure == "inputs-changed":
                (tmp_path / "added-during-local-check.txt").write_text("changed input")
        return 0

    load_graph = currentness.load_repository_blueprint_graph

    def count_graph(*args, **kwargs):
        calls["graphs"] += 1
        return load_graph(*args, **kwargs)

    monkeypatch.setattr(checks, "run_suite", validators)
    monkeypatch.setattr(currentness, "load_repository_blueprint_graph", count_graph)
    monkeypatch.setattr(preparation.certifier, "load_repository_blueprint_graph", count_graph)
    monkeypatch.setattr(preparation.certifier.RouteSmokeAuditor, "require_stable_dependencies", lambda _self: None)
    identity = {**fixture.CERTIFIER, "source_commit": commit}
    observed = SimpleNamespace(graph=graph, states=states, source_commit=commit,
        certifier_identity=identity, currentness=currentness.evaluate_certificate_currentness(
            graph, states, repo_root=tmp_path, public_key_root=public_keys,
            source_commit=commit, certifier_identity=identity,
            checks_by_node={node: expected_certifier_checks() for node in states},
            schema_root=schema,
        ))
    target, source = "demo-skill", "demo-skill.source.gateway"
    order = certification_target_postorder(graph, states, (target,))
    scope = certification_input_scope(graph, states, repo_root=tmp_path, requested=(target,),
        certification_basis_paths=preparation.resolve_certification_basis_paths(tmp_path))
    dag = support.build_dependency_dag(graph, states, tmp_path)
    data = {
        "reviewed_repository": str(tmp_path), "reviewed_commit": commit,
        "certification_scope": scope.identity, "whole_graph": False,
        "requested_targets": [target], "run_id": "preparation-test", "worker_capacity": 1,
        "retry_interval_seconds": 10, "automatic_transition_limit": 100,
        "dag": dag, "node_order": list(order), "required": [node["id"] for node in dag["nodes"]],
        "audited_inputs": {node: preparation.certifier.audited_inputs(states[node]) for node in order},
        "preparation": None,
    }
    context = SimpleNamespace(charter=SimpleNamespace(data=data))
    packets = {node["id"]: support._packet(context, observed, node["id"], {}, {}, static_only=True)
               for node in dag["nodes"]}
    data["preparation"] = preparation.prepare(
        data, observed, scope, packets, preparation.input_fingerprint(tmp_path),
    )
    envelope = data["preparation"]
    assert envelope["payload"]["mechanical"]["validation_node_ids"] == list(order)
    assert envelope["payload"]["mechanical"]["validation_paths"] == list(
        preparation.certifier.mechanical_validation_paths(states, order)
    )
    reopened = preparation.open_preparation(data, envelope)
    assert reopened is not None and not reopened.currentness.current
    assert envelope["payload"]["node_mechanical"] is None
    assert gates == [(True, order)]
    with pytest.raises(preparation.certifier.CertificationError, match="this node's mechanical checks"):
        preparation.issue(data, reopened, source)
    with pytest.raises(preparation.certifier.CertificationError, match="current prerequisites"):
        preparation.issue(data, reopened, target)
    assert not currentness.certificate_log_path(graph.nodes[target]).exists()

    forged = deepcopy(envelope)
    forged["payload"]["inputs"] = "forged"
    with pytest.raises(preparation.certifier.CertificationError, match="signature"):
        preparation.open_preparation(data, forged)
    with pytest.raises(preparation.certifier.CertificationError, match="binding"):
        preparation.open_preparation({**data, "run_id": "different-run"}, envelope)
    records.rotate_certificate_signing_key(public_keys, secret_backend=backend)
    with pytest.raises(preparation.certifier.CertificationError, match="signing key changed"):
        preparation.issue(data, reopened, source)
    with pytest.raises(preparation.certifier.CertificationError, match="binding"):
        preparation.open_preparation(data, envelope)
    (public_keys / "active-key-id").write_text(key.key_id + "\n")

    added = tmp_path / "new-blueprint.yaml"
    added.write_text("new discovery input\n")
    assert preparation.open_preparation(data, envelope) is None
    added.unlink()
    for name in ("bad", "_build", ".certificates"):
        added_directory = tmp_path / "skills" / name
        added_directory.mkdir()
        assert preparation.open_preparation(data, envelope) is None
        added_directory.rmdir()
    gateway = graph.nodes[source].gateway_path
    original = gateway.read_bytes()
    gateway.write_bytes(original + b"changed audited input\n")
    with pytest.raises(preparation.certifier.CertificationError):
        preparation.open_preparation(data, envelope)
    gateway.write_bytes(original)
    assert preparation.open_preparation(data, envelope) is not None

    registry = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path)
    # The two failure probes share the same untouched signing inputs. Failed
    # Voyages retain separate Reckonings and must neither dispatch nor append.
    for local_failure in ("rejected", "inputs-changed"):
        failed_voyage = registry.create(
            "certification", Path(f"_build/{local_failure}.reckoning.json"), data,
        )
        failure = failed_voyage.next()
        assert failure.kind == "terminal"
        assert failure.status.terminal_result.outcome == "failed"
        assert all(not currentness.certificate_log_path(graph.nodes[node]).exists() for node in order)
        assert gates[-1] == (False, (source,))
        (tmp_path / "added-during-local-check.txt").unlink(missing_ok=True)
    local_failure = None
    assert preparation.open_preparation(data, envelope) is not None
    voyage = registry.create(
        "certification", Path("_build/test.reckoning.json"), data,
    )
    message = voyage.next()
    audited = []
    for _ in data["required"]:
        packet = message.status.instruction.payload["packets"][0]
        # The local gate must precede every audit, while repeated facet turns
        # reuse its receipt. Later nodes have not been checked yet.
        owner = packet["owner_node_id"] or packet["target_id"]
        assert gates[-1] == (False, (owner,))
        audited.append(packet["target_id"])
        event = {"outcome": "worker-completed", "task_id": packet["task_id"], "raw_output": json.dumps({
            "schema_version": "node-certify.semantic-audit-result/v1",
            "task_id": packet["task_id"], "verdict": "pass", "summary": "checked",
            "evidence": ["read selected declaration and source"], "findings": [],
            "consumed_dependencies": [{"task_id": report["task_id"], "verdict": "pass"}
                                      for report in packet["prerequisite_reports"]],
        })}
        message = voyage.next(event, responding_to=message.status.current_evolution.evolution_entry_id)
    assert message.status.instruction is None
    assert set(audited) == set(data["required"])
    assert calls == {"validators": 5, "graphs": 0}
    assert gates == [(True, order), *[(False, (source,))] * 3, (False, (target,))]
    logs = {node: currentness.certificate_log_path(graph.nodes[node]).read_bytes() for node in order}
    for content in logs.values():
        assert len(records.parse_certificate_log(content, public_keys)) == 1
    # Published pooled reviews are new validator inputs; fresh preparation must be requested.
    assert preparation.open_preparation(data, envelope) is None
    review = graph.nodes[target].module_root / ".pooled-blueprint-review.yaml"
    # A crash after review publication but before its machine receipt must resume
    # even when all nodes are already current and the review changed broad inputs.
    fresh_observation = SimpleNamespace(**{**observed.__dict__, "currentness":
        currentness.evaluate_certificate_currentness(graph, states, repo_root=tmp_path,
            public_key_root=public_keys, source_commit=commit, certifier_identity=identity,
            checks_by_node={node: expected_certifier_checks() for node in states}, schema_root=schema)})
    recovery_context = SimpleNamespace(charter=context.charter,
        history=SimpleNamespace(machines=lambda: ()))
    with monkeypatch.context() as patch:
        patch.setattr(support, "observe", lambda *_args: fresh_observation)
        recovered = support._observation(recovery_context)
    assert recovered.preparation_changed and recovered.currentness.current
    assert calls["validators"] == 6
    assert gates[-1] == (True, ())
    preparation.publish_reviews(data, recovered, set())
    assert review.exists()
    review.unlink()
    reopened = preparation.open_preparation(data, envelope)
    assert reopened.currentness.current
    assert preparation.issue(data, reopened, source) is False
    assert logs == {node: currentness.certificate_log_path(graph.nodes[node]).read_bytes() for node in order}

    # A valid replacement prerequisite still changes the exact evidence consumed.
    source_log = currentness.certificate_log_path(graph.nodes[source])
    target_log = currentness.certificate_log_path(graph.nodes[target])
    target_log.unlink()
    observed.currentness = currentness.evaluate_certificate_currentness(
        graph, states, repo_root=tmp_path, public_key_root=public_keys,
        source_commit=commit, certifier_identity=identity,
        checks_by_node={node: expected_certifier_checks() for node in states}, schema_root=schema,
    )
    renewed = preparation.prepare(data, observed, scope, packets, preparation.input_fingerprint(tmp_path))
    # The now-current source is context only, even for the upfront graph gate.
    assert gates[-1] == (True, (target,))
    reopened = preparation.open_preparation(data, renewed)
    assert reopened.preparation["payload"]["node_mechanical"] is None
    previous_node_receipt = records.sign_certificate_payload({**renewed["payload"],
        "node_mechanical": {**renewed["payload"]["mechanical"], "validation_node_ids": [source]}}, key)
    with pytest.raises(preparation.certifier.CertificationError, match="this node's mechanical checks"):
        preparation.issue(data, preparation.open_preparation(data, previous_node_receipt), target)
    assert not target_log.exists()
    preparation.ensure_node_checks(data, reopened, target)
    checked = preparation.open_preparation(data, reopened.preparation)
    preparation.ensure_node_checks(data, checked, target)
    assert gates[-1] == (False, (target,))
    assert gates.count((False, (target,))) == 2
    gate_records = preparation.certifier.CertificateBatchIssuer._gate_records

    def renew_prerequisite(issuer, node_id):
        result = gate_records(issuer, node_id)
        previous = records.parse_certificate_log(logs[source], public_keys)[-1]
        replacement = {**previous["payload"],
            "previous_entry_hash": records.certificate_entry_hash(previous),
            "certified_at": "2026-09-14T00:00:00+00:00"}
        frame = records.canonical_certificate_envelope_bytes(records.sign_certificate_payload(replacement, key))
        source_log.write_bytes(logs[source] + frame + b"\n")
        assert len(records.parse_certificate_log(source_log.read_bytes(), public_keys)) == 2
        return result

    with monkeypatch.context() as patch:
        patch.setattr(preparation.certifier.CertificateBatchIssuer, "_gate_records", renew_prerequisite)
        with pytest.raises(preparation.certifier.CertificationError, match="prerequisite certificate changed"):
            preparation.issue(data, reopened, target)
    assert not target_log.exists()
    source_log.write_bytes(logs[source])
    target_log.write_bytes(logs[target])

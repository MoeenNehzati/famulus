"""Exercise dispatch-only certification over the real Rutter state machine."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from officina.certification.hashing import NodeHashState, CertificationFacetHashState
from officina.certification.view import (
    CertificateNodeCurrentness, CertificateCurrentnessReport, CertificateDependencyDelta,
    CertificateFacetDrift,
)
from officina.rutter import RutterRegistry, RutterValidationError, voyage_dispenser_cli

from .. import _certification_support as support
from .. import _certification_voyage as certification_cli
from .._certification_voyage import CERTIFICATION_RUTTER, make_voyage_dispenser


def _run(tmp_path, monkeypatch, capacity=2):
    nodes = {
        "source": SimpleNamespace(
            node_id="source", node_type="behavioral_source", version=1,
            declaration={"interfaces": {"source.one": {}, "source.two": {}}},
            blueprint_path=tmp_path / "source.yaml", gateway_path=tmp_path / "source.py",
            module_root=tmp_path,
        ),
        "module": SimpleNamespace(
            node_id="module", node_type="module", version=1,
            declaration={"id": "module"}, blueprint_path=tmp_path / "module.yaml",
            gateway_path=None,
        ),
    }
    graph = SimpleNamespace(
        nodes=nodes, module_sources={"module": ("source",)}, module_children={},
        module_parents={"module": None}, source_modules={"source": "module"},
        source_interfaces={
            key: SimpleNamespace(source_node_id="source", declaration={"id": key})
            for key in ("source.one", "source.two")
        }, exports={},
    )
    states = {
        "source": NodeHashState(
            node_hash="source-hash", facets=(
                CertificationFacetHashState("source.one", "interface", "one-hash"),
                CertificationFacetHashState("source.two", "interface", "two-hash"),
                CertificationFacetHashState("source", "remainder", "remainder-hash"),
            ),
        ),
        "module": NodeHashState(
            node_hash="module-hash", dependency_hashes=(
                {"relation": "contains-source", "target": "source", "version": 1},
            ),
        ),
    }
    for node in nodes.values():
        node.blueprint_path.write_text(yaml.safe_dump(node.declaration))
        if node.gateway_path:
            node.gateway_path.write_text('"""Selected interface implementation."""\n')
    statuses = {
        key: CertificateNodeCurrentness(key, False, ("missing-certificate",), None)
        for key in nodes
    }
    observed = SimpleNamespace(
        graph=graph, states=states, source_commit="a" * 40,
        currentness=CertificateCurrentnessReport(statuses),
        requested_observations=[],
    )
    signed = []

    def sign(data, observation, target):
        assert observation is observed
        assert data["audited_inputs"][target] == support.certifier.audited_inputs(states[target])
        assert data["certification_scope"] == "scope"
        assert "module" in data["requested_targets"]
        assert target != "module" or statuses["source"].current
        signed.append(target)
        statuses[target] = replace(statuses[target], current=True, concerns=())
        return True

    def open_preparation(data, envelope):
        # Simulate invalidated physical preparation so the real canonical
        # fallback still diagnoses the fixture's input and authority changes.
        if (data["reviewed_commit"] != observed.source_commit
                or any(expected != support.certifier.audited_inputs(states[node])
                       for node, expected in data["audited_inputs"].items())
                or support._ready_inputs(None, None, None).identity != data["certification_scope"]):
            return None
        observed.preparation = envelope
        observed.__dict__.pop("preparation_changed", None)
        return observed

    def observe(_root, *, requested):
        observed.requested_observations.append(
            None if requested is None else list(requested),
        )
        observed.__dict__.pop("preparation", None)
        observed.__dict__.pop("preparation_changed", None)
        return observed

    monkeypatch.setattr(support, "derive_repository_certification_state", observe)
    monkeypatch.setattr(support, "_ready_inputs", lambda *_args: SimpleNamespace(identity="scope"))
    monkeypatch.setattr(support, "_prepare", lambda *_args: {"fixture": "preparation"})
    monkeypatch.setattr(support.preparation, "open_preparation", open_preparation)
    monkeypatch.setattr(support.preparation, "ensure_node_checks", lambda *_args: None)
    monkeypatch.setattr(support.preparation, "publish_reviews", lambda *_args: None)
    monkeypatch.setattr(support.preparation, "input_fingerprint", lambda *_args: "fixture-inputs")
    monkeypatch.setattr(support.certifier, "_verify_executing_candidate_certifier", lambda *_args: None)
    monkeypatch.setattr(support.preparation, "issue", sign)
    charter = support.make_charter(tmp_path, ["module"], capacity, "run-one")
    registry = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path)
    voyage = registry.create("certification", Path("test.reckoning.json"), charter)
    return voyage, observed, signed


def _payload(result):
    return result.status.instruction.payload


def _event(packet, *, verdict="pass"):
    return {
        "outcome": "worker-completed", "task_id": packet["task_id"],
        "raw_output": json.dumps({
            "schema_version": "node-certify.semantic-audit-result/v1",
            "task_id": packet["task_id"], "verdict": verdict,
            "summary": "checked", "evidence": ["selected declaration and behavior"],
            "consumed_dependencies": [
                {"task_id": report["task_id"], "verdict": "pass"}
                for report in packet["prerequisite_reports"]
            ],
            "findings": [] if verdict == "pass" else ["does not agree"],
        }),
    }


def _submit(voyage, message, event):
    return voyage.next(
        event, responding_to=message.status.current_evolution.evolution_entry_id,
    )


def test_dispatches_parallel_facets_then_signs_each_exact_node(tmp_path, monkeypatch):
    voyage, observed, signed = _run(tmp_path, monkeypatch)
    message = voyage.next()
    first, second = _payload(message)["packets"]
    assert first["instruction_interface"]["id"].endswith("audit-interface.interface.audit")
    message = _submit(voyage, message, _event(second))
    assert _payload(message)["packets"] == ()
    before = (tmp_path / "test.reckoning.json").read_bytes()
    with pytest.raises(RutterValidationError):
        _submit(voyage, message, _event(second))
    assert (tmp_path / "test.reckoning.json").read_bytes() == before
    message = _submit(voyage, message, _event(first))
    source = _payload(message)["packets"][0]
    assert source["target_id"] == "source"
    assert {r["task_id"] for r in source["prerequisite_reports"]} == {
        first["task_id"], second["task_id"],
    }
    assert all(r["evidence"] for r in source["prerequisite_reports"])
    message = _submit(voyage, message, _event(source))
    assert signed == ["source"]
    module = _payload(message)["packets"][0]
    assert module["target_id"] == "module"
    terminal = _submit(voyage, message, _event(module))
    assert terminal.kind == "terminal"
    assert terminal.status.terminal_result.outcome == "complete"
    assert signed == ["source", "module"]
    assert observed.requested_observations
    assert all(requested == ["module"] for requested in observed.requested_observations)


@pytest.mark.parametrize("failure", [
    "malformed", "reject", "abort", "wrong-report", "dependencies", "lost",
    "missing-dependencies", "duplicate-dependencies",
])
def test_machine_classifies_raw_worker_failure_without_signing(tmp_path, monkeypatch, failure):
    voyage, _observed, signed = _run(tmp_path, monkeypatch)
    message = voyage.next()
    if failure in {"missing-dependencies", "duplicate-dependencies"}:
        for interface in _payload(message)["packets"]:
            message = _submit(voyage, message, _event(interface))
    packet = _payload(message)["packets"][0]
    event = _event(packet)
    if failure == "malformed":
        event["raw_output"] = "not json"
    elif failure in {"reject", "abort"}:
        event = _event(packet, verdict=failure)
    elif failure == "lost":
        event = {"outcome": "worker-failed", "task_id": packet["task_id"], "reason": "worker lost"}
    else:
        report = json.loads(event["raw_output"])
        if failure == "wrong-report":
            report["task_id"] = "old-run:" + packet["target_id"]
        elif failure == "missing-dependencies":
            assert len(report["consumed_dependencies"]) == 2
            report["consumed_dependencies"].pop()
        elif failure == "duplicate-dependencies":
            assert len(report["consumed_dependencies"]) == 2
            report["consumed_dependencies"].append(report["consumed_dependencies"][0])
        else:
            report["consumed_dependencies"] = [{"task_id": "invented", "verdict": "pass"}]
        event["raw_output"] = json.dumps(report)
    terminal = _submit(voyage, message, event)
    assert terminal.kind == "terminal"
    assert terminal.status.terminal_result.outcome == "failed"
    assert terminal.status.terminal_result.value["task_id"] == packet["task_id"]
    assert terminal.status.terminal_result.value["node_id"] == "source"
    assert signed == []


def test_old_assignment_rejected_without_reckoning_mutation(tmp_path, monkeypatch):
    voyage, _observed, signed = _run(tmp_path, monkeypatch)
    message = voyage.next()
    packet = _payload(message)["packets"][0]
    event = _event(packet)
    event["task_id"] = "old-run:" + packet["target_id"]
    path = tmp_path / "test.reckoning.json"
    before = path.read_bytes()
    with pytest.raises(RutterValidationError):
        _submit(voyage, message, event)
    assert path.read_bytes() == before
    assert signed == []


@pytest.mark.parametrize("when", ["worker-completion", "sign-return"])
def test_drift_stops_progress_before_next_dispatch(tmp_path, monkeypatch, when):
    voyage, observed, signed = _run(tmp_path, monkeypatch)
    message = voyage.next()
    if when == "sign-return":
        for interface in _payload(message)["packets"]:
            message = _submit(voyage, message, _event(interface))
        sign = support.preparation.issue

        def sign_then_drift(*args):
            result = sign(*args)
            observed.states["module"] = replace(observed.states["module"], node_hash="changed")
            return result

        monkeypatch.setattr(support.preparation, "issue", sign_then_drift)
    else:
        observed.states["source"] = replace(observed.states["source"], node_hash="changed")
    packet = _payload(message)["packets"][0]
    terminal = _submit(voyage, message, _event(packet))
    assert terminal.status.terminal_result.outcome == "failed"
    assert "audited inputs changed" in terminal.status.terminal_result.value["reason"]
    assert signed == (["source"] if when == "sign-return" else [])


def test_authority_scope_drift_after_worker_completion_fails_without_signing(tmp_path, monkeypatch):
    voyage, _observed, signed = _run(tmp_path, monkeypatch)
    message = voyage.next()
    packet = _payload(message)["packets"][0]
    monkeypatch.setattr(support, "_ready_inputs", lambda *_args: SimpleNamespace(identity="changed"))
    terminal = _submit(voyage, message, _event(packet))
    assert terminal.status.terminal_result.outcome == "failed"
    assert "certification scope changed" in terminal.status.terminal_result.value["reason"]
    assert signed == []


def test_capacity_and_fresh_run_skip_current_nodes(tmp_path, monkeypatch):
    voyage, observed, signed = _run(tmp_path, monkeypatch, capacity=1)
    observed.currentness.nodes["module"] = replace(
        observed.currentness.nodes["module"], concerns=("dependency-not-current:source",),
        certificate={"payload": {"subject": {"id": "module"}}},
    )
    sign = support.preparation.issue

    def renew_dependency(*args):
        result = sign(*args)
        # The observer can restore a propagated-stale parent after source renewal.
        observed.currentness.nodes["module"] = replace(
            observed.currentness.nodes["module"], current=True, concerns=(),
        )
        return result

    monkeypatch.setattr(support.preparation, "issue", renew_dependency)
    message = voyage.next()
    for target in ("source.one", "source.two", "source"):
        packet, = _payload(message)["packets"]
        assert packet["target_id"] == target
        message = _submit(voyage, message, _event(packet))
    assert message.status.terminal_result.outcome == "complete"
    assert signed == ["source"]
    assert message.status.terminal_result.value["nodes_already_current"] == ("module",)
    charter = support.make_charter(tmp_path, ["module"], 1, "fresh-run")
    assert charter["preparation"] is None
    assert not hasattr(observed, "preparation")
    fresh = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path).create(
        "certification", Path("fresh.reckoning.json"), charter,
    )
    terminal = fresh.next()
    assert not hasattr(observed, "preparation")
    assert terminal.status.terminal_result.outcome == "complete"
    assert terminal.status.terminal_result.value["semantic_audit_count"] == 0
    assert signed == ["source"]


def test_public_dispenser_initializes_defaults_and_correlates_next(tmp_path, monkeypatch, capsys):
    _voyage, _observed, signed = _run(tmp_path, monkeypatch)
    dispenser = make_voyage_dispenser(tmp_path / "runs")
    assert voyage_dispenser_cli(dispenser, [
        "initiate", "--run-prefix", "audit", "--repository", str(tmp_path),
        "--worker-capacity", "2",
    ]) == 0
    voyage_id, = json.loads(capsys.readouterr().out)["voyage_ids"]
    assert voyage_id.startswith("audit/r-") and voyage_id.endswith("/1")
    assert make_voyage_dispenser(tmp_path / "runs").get_voyage_ids("audit") == (voyage_id,)
    assert voyage_dispenser_cli(dispenser, ["next", voyage_id]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "rutter.voyage-next/v1"
    assert result["kind"] == "message"
    packet = result["instruction"]["data"]["payload"]["packets"][0]
    path = tmp_path / "event.json"
    path.write_text(json.dumps(_event(packet)))
    argv = ["next", voyage_id, "--response-file", str(path),
            "--responding-to", result["evolution"]["evolution_entry_id"]]
    assert voyage_dispenser_cli(dispenser, argv) == 0
    capsys.readouterr()
    before = next((tmp_path / "runs").rglob("*.reckoning.json")).read_bytes()
    assert voyage_dispenser_cli(dispenser, argv) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid-response"
    assert next((tmp_path / "runs").rglob("*.reckoning.json")).read_bytes() == before
    assert signed == []


def test_candidate_relay_preserves_arguments_output_and_exit(tmp_path, monkeypatch, capsys):
    arguments = ["next", "run/1", "--repository", str(tmp_path),
                 "--response-file", str(tmp_path / "event.json"), "--responding-to", "entry"]
    calls = []

    def relay(repository, **kwargs):
        assert repository == tmp_path
        assert kwargs == {
            "caller_module_id": "node-certify", "interface_id": "node-certify._rtx.interface.certification-voyage",
            "version": 2, "argv": arguments,
            "expected_gateway": Path("skills/node-certify/_rtx/_certification_voyage.py"),
        }
        calls.append(repository)
        return SimpleNamespace(returncode=4, stdout='{"raw":"result"}\n', stderr="diagnostic\n")

    monkeypatch.setattr(certification_cli, "relay_candidate_interface", relay)
    assert certification_cli.main(arguments) == 4
    output = capsys.readouterr()
    assert output.out == '{"raw":"result"}\n'
    assert output.err == "diagnostic\n"
    assert calls == [tmp_path]
    assert certification_cli.main(["next", "run/1"]) == 2
    assert calls == [tmp_path]


def test_candidate_ownership_failure_precedes_voyage_initialization(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch)

    def reject(*_args):
        raise support.certifier.CertificationError("executing certifier bytes are outside the candidate")

    monkeypatch.setattr(support.certifier, "_verify_executing_candidate_certifier", reject)
    run_root = tmp_path / "must-not-be-created"
    assert voyage_dispenser_cli(make_voyage_dispenser(run_root), [
        "initiate", "--repository", str(tmp_path), "--worker-capacity", "1",
    ]) == 2
    assert not run_root.exists()


def test_structural_child_without_namespace_route_precedes_parent(tmp_path, monkeypatch):
    _voyage, observed, _signed = _run(tmp_path, monkeypatch)
    observed.graph.nodes["child"] = SimpleNamespace(
        node_id="child", node_type="module", version=1,
        declaration={"id": "child"}, blueprint_path=tmp_path / "child.yaml",
        gateway_path=None,
    )
    observed.graph.module_children["module"] = ("child",)
    observed.graph.module_parents["child"] = "module"
    observed.states["child"] = NodeHashState(node_hash="child-hash")
    observed.currentness.nodes["child"] = CertificateNodeCurrentness(
        "child", False, ("missing-certificate",), None,
    )
    charter = support.make_charter(tmp_path, ["module"], 2, "children")
    assert charter["node_order"].index("child") < charter["node_order"].index("module")
    parent = next(node for node in charter["dag"]["nodes"] if node["id"] == "module")
    assert "child" in parent["dependencies"]


def test_reusable_facet_requires_valid_evidence_and_survives_own_signing(tmp_path, monkeypatch):
    _voyage, observed, _signed = _run(tmp_path, monkeypatch)
    facets = list(support.certification_facet_claims(observed.states["source"]))
    envelope = {"payload": {"subject": {}, "facets": facets, "dependencies": [],
                            "input_manifest": [], "checks": []}}
    status = CertificateNodeCurrentness(
        "source", False, ("remainder-hash-mismatch", "node-hash-mismatch"), envelope,
        facet_drift=(CertificateFacetDrift("source", "remainder", local_hash_changed=True),),
    )
    observed.currentness.nodes["source"] = status
    evidence = support._certificate(observed, "source.one")
    assert evidence["facets"] == [next(f for f in facets if f["id"] == "source.one")]
    assert support.make_charter(tmp_path, ["module"], 2, "selective")["required"] == ["module", "source"]
    for concern in (
        "certification-basis-mismatch", "invalid-certificate-schema", "legacy-certificate-payload",
        "subject-mismatch", "checks-mismatch", "certifier-mismatch", "suspect-certificate-log",
        "unknown-concern", "node-hash-mismatch:unexpected", "interface-hash-mismatch",
        "interface-hash-mismatch:",
    ):
        observed.currentness.nodes["source"] = replace(status, concerns=(*status.concerns, concern))
        charter = support.make_charter(tmp_path, ["module"], 2, "full")
        assert charter["required"] == ["module", "source", "source.one", "source.two"], concern
        with pytest.raises(ValueError, match="not current"):
            support._certificate(observed, "source.one")
    observed.currentness.nodes["source"] = replace(status, certificate=None)
    assert support.make_charter(tmp_path, ["module"], 2, "missing")["required"] == [
        "module", "source", "source.one", "source.two",
    ]
    # A prerequisite may renew before this source; planning stays selective but
    # packet consumption must wait for its current certificate.
    observed.currentness.nodes["source"] = replace(
        status, concerns=(*status.concerns, "dependency-not-current:provider"),
    )
    assert support.make_charter(tmp_path, ["module"], 2, "pending")["required"] == ["module", "source"]
    with pytest.raises(ValueError, match="not current"):
        support._certificate(observed, "source.one")
    observed.currentness.nodes["source"] = replace(status, concerns=("invalid-certificate-schema",))
    with pytest.raises(ValueError, match="not current"):
        support._certificate(observed, "source.one")
    packet = {"owner_node_id": "source", "prerequisite_certificates": [evidence]}
    support._verify_packet_evidence(observed, {"task": packet}, {"source"})
    with pytest.raises(ValueError, match="not current"):
        support._verify_packet_evidence(observed, {"task": packet}, set())
    observed.currentness.nodes["source"] = replace(
        status, current=True, concerns=(), certificate={**envelope, "signature": "renewed"},
    )
    support._verify_packet_evidence(observed, {"task": packet}, set())
    with pytest.raises(ValueError, match="prerequisite evidence changed"):
        support._verify_packet_evidence(
            observed, {"task": {**packet, "owner_node_id": "module"}}, set(),
        )


def test_current_prerequisite_packet_contains_readable_semantic_declaration(tmp_path, monkeypatch):
    _voyage, observed, signed = _run(tmp_path, monkeypatch)
    source = observed.graph.nodes["source"]
    source.declaration["interfaces"]["source.one"] = {
        "description": "Returns a selected value.", "contract": {"outputs": ["value"]},
    }
    source.blueprint_path.write_text(yaml.safe_dump(source.declaration))
    observed.currentness.nodes["source"] = CertificateNodeCurrentness(
        "source", True, (), {"payload": {
            "subject": {"id": "source"},
            "facets": list(support.certification_facet_claims(observed.states["source"])),
            "dependencies": [], "input_manifest": [], "checks": [],
        }},
    )
    charter = support.make_charter(tmp_path, ["module"], 1, "reuse")
    voyage = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path).create(
        "certification", Path("reuse.reckoning.json"), charter,
    )
    message = voyage.next()
    packet = support.plain(_payload(message)["packets"][0])
    prerequisite, = packet["prerequisite_declarations"]
    assert prerequisite["declaration"] == yaml.safe_load(Path(prerequisite["blueprint_path"]).read_text())
    assert prerequisite["declaration"]["interfaces"]["source.one"]["contract"]["outputs"] == ["value"]
    assert packet["prerequisite_certificates"][0]["target_id"] == "source"
    assert _submit(voyage, message, _event(packet)).status.terminal_result.outcome == "complete"
    assert signed == ["module"]


def test_replay_after_signing_reconciles_replaced_own_facet_evidence(tmp_path, monkeypatch):
    _voyage, observed, signed = _run(tmp_path, monkeypatch)
    certificate = {"payload": {
        "subject": {"id": "source"},
        "facets": list(support.certification_facet_claims(observed.states["source"])),
        "dependencies": [], "input_manifest": [], "checks": [],
    }}
    observed.currentness.nodes["source"] = CertificateNodeCurrentness(
        "source", False, ("remainder-hash-mismatch",), certificate,
        facet_drift=(CertificateFacetDrift("source", "remainder", local_hash_changed=True),),
    )
    charter = support.make_charter(tmp_path, ["module"], 1, "replay")
    registry = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path)
    path = Path("replay.reckoning.json")
    voyage = registry.create("certification", path, charter)
    message = voyage.next()
    packet, = _payload(message)["packets"]
    assert packet["target_id"] == "source"
    assert len(packet["prerequisite_certificates"]) == 2
    sign = support.preparation.issue

    class InterruptedAppend(BaseException):
        pass

    def interrupt_after_append(data, observation, target):
        if observed.currentness.nodes[target].current:
            return False
        result = sign(data, observation, target)
        if target == "source":
            observed.currentness.nodes[target] = replace(
                observed.currentness.nodes[target],
                certificate={**certificate, "signature": "replacement-certificate"},
            )
            raise InterruptedAppend
        return result

    monkeypatch.setattr(support.preparation, "issue", interrupt_after_append)
    with pytest.raises(InterruptedAppend):
        _submit(voyage, message, _event(packet))
    assert signed == ["source"]
    recovered = registry.open(path)
    message = recovered.next()
    module, = _payload(message)["packets"]
    assert module["target_id"] == "module"
    terminal = _submit(recovered, message, _event(module))
    assert terminal.status.terminal_result.outcome == "complete"
    assert signed == ["source", "module"]
    assert terminal.status.terminal_result.value["nodes_already_current"] == ("source",)


def test_current_certificate_loss_cannot_authorize_an_unplanned_audit_skip(tmp_path, monkeypatch):
    _voyage, observed, signed = _run(tmp_path, monkeypatch)
    for target, status in observed.currentness.nodes.items():
        observed.currentness.nodes[target] = replace(status, current=True, concerns=())
    charter = support.make_charter(tmp_path, ["module"], 1, "lost-evidence")
    voyage = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path).create(
        "certification", Path("lost-evidence.reckoning.json"), charter,
    )
    observed.currentness.nodes["source"] = CertificateNodeCurrentness(
        "source", False, ("missing-certificate-log",), None,
    )
    terminal = voyage.next()
    assert terminal.status.terminal_result.outcome == "failed"
    assert "additional semantic audits" in terminal.status.terminal_result.value["reason"]
    assert signed == []


@pytest.mark.parametrize("flags, reason", [
    (["--worker-capacity", "0"], "between 1 and 64"),
    (["--worker-capacity", "1", "--targets", "unknown"], "registered modules"),
])
def test_init_returns_actionable_domain_errors(tmp_path, monkeypatch, capsys, flags, reason):
    _run(tmp_path, monkeypatch)
    dispenser = make_voyage_dispenser(tmp_path / "runs")
    assert voyage_dispenser_cli(dispenser, ["initiate", "--repository", str(tmp_path), *flags]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "usage-error" and reason in error["message"]
    assert not (tmp_path / "runs").exists()


def test_large_mechanical_only_renewal_completes_without_an_llm_turn(tmp_path, monkeypatch):
    _voyage, observed, signed = _run(tmp_path, monkeypatch)
    # 51 total nodes retain over 100 machine records without excess fixture work.
    for index in range(49):
        target = f"child-{index:02}"
        observed.graph.nodes[target] = SimpleNamespace(
            node_id=target, node_type="module", version=1,
        )
        observed.graph.module_parents[target] = "module"
        observed.states[target] = NodeHashState(node_hash=target + "-hash")
        observed.currentness.nodes[target] = CertificateNodeCurrentness(target, False, (), None)
    observed.graph.module_children["module"] = tuple(
        key for key in observed.graph.nodes if key.startswith("child-")
    )
    for target, status in observed.currentness.nodes.items():
        observed.currentness.nodes[target] = replace(
            status, certificate={"payload": {}}, concerns=("dependency-mismatch",), dependencies=(
                CertificateDependencyDelta("changed", "certified-under", "certifier", None, {}, {}),
            ),
        )
    charter = support.make_charter(tmp_path, ["module"], 2, "mechanical")
    assert charter["required"] == []
    source_status = observed.currentness.nodes["source"]
    observed.currentness.nodes["source"] = replace(
        source_status, concerns=(*source_status.concerns, "certification-basis-mismatch"),
    )
    assert support.make_charter(tmp_path, ["module"], 2, "old-basis")["required"] == [
        "module", "source", "source.one", "source.two",
    ]
    observed.currentness.nodes["source"] = source_status
    voyage = RutterRegistry({"certification": CERTIFICATION_RUTTER}, tmp_path).create(
        "certification", Path("mechanical.reckoning.json"), charter,
    )
    terminal = voyage.next()
    assert terminal.kind == "terminal"
    assert terminal.status.terminal_result.outcome == "complete"
    assert terminal.status.terminal_result.value["semantic_audit_count"] == 0
    assert set(signed) == set(observed.graph.nodes)
    assert len(voyage._store.read().root.history) > 100

"""Deterministic certification scheduling and evidence checks for one Voyage."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError
from officina.certification.dependency_dag import build_dependency_dag
from officina.certification.hashing import (
    certification_facet_claims, certification_input_scope, certification_target_postorder,
    resolve_certification_basis_paths,
)
from officina.certification.records import certificate_entry_hash
from officina.certification.view import (
    certificate_log_path, certificate_requires_renewal, certificate_semantic_evidence_reusable,
    derive_repository_certification_state, semantic_stale_vertices,
)
from officina.rutter import (
    EvolutionContext, LLMResponseContext, MachineContext, MachineResult,
    ValidationIssue, ValidationReport, VoyageResult,
)

from . import _node_certifier as certifier
from . import _semantic_audit_scheduler as scheduler
from . import _certification_preparation as preparation

SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas"
PACKET_VERSION = "node-certify.semantic-audit-task/v1"
INSTRUCTIONS = {
    "interface": ("audit-interface", "node-certify.source.audit-interface.interface.audit"),
    "behavioral-source": ("audit-behavioral-source", "node-certify.source.audit-behavioral-source.interface.audit"),
    "module": ("audit-module", "node-certify.source.audit-module.interface.audit"),
}
EVENT_SCHEMA = {
    "type": "object",
    "oneOf": [
        {
            "type": "object", "additionalProperties": False,
            "required": ["outcome", "task_id", "raw_output"],
            "properties": {
                "outcome": {"const": "worker-completed"},
                "task_id": {"type": "string", "minLength": 1},
                "raw_output": {"type": "string"},
            },
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["outcome", "task_id", "reason"],
            "properties": {
                "outcome": {"const": "worker-failed"},
                "task_id": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
        },
    ],
}


def plain(value: object) -> object:
    """Materialize immutable Rutter JSON without changing worker content."""

    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def observe(repository: Path, requested: Sequence[str] | None = None):
    """Read canonical inputs and authenticate the requested dependency closure."""

    return derive_repository_certification_state(repository, requested=requested)


def _ready_inputs(repository: Path, observation, requested: Sequence[str], whole_graph: bool = False):
    snapshot = certifier.capture_git_snapshot(repository)
    if snapshot is None or snapshot.commit != observation.source_commit:
        raise certifier.CertificationError("repository commit changed during preparation")
    scope = certification_input_scope(
        observation.graph, observation.states, repo_root=repository, requested=requested,
        whole_graph=whole_graph,
        certification_basis_paths=(observation.certification_basis_paths
            if hasattr(observation, "certification_basis_paths") else resolve_certification_basis_paths(repository)),
    )
    guard = certifier.RepositoryFreezeGuard(
        repo_root=repository, snapshot=snapshot, allow_non_atomic=False, scoped=True,
    )
    guard.configure_inputs(scope.tracked_paths, scope.local_claims)
    guard.capture_initial_state()
    findings = certifier.certification_completeness_findings(observation.graph, scope.node_ids)
    if findings:
        raise certifier.CertificationError("certification scope is incomplete")
    return scope


def make_charter(
    repository: Path, targets: Sequence[str], worker_capacity: int,
    run_id: str, retry_interval_seconds: int = 10,
) -> dict[str, object]:
    """Bind one signable repository closure before any worker is dispatched.

    InstantiationsFromRepo
    ----------------------
    ._ready_inputs:
      why:
        constructs: "Builds the ready evidence scope bound into the charter."
    """

    if type(worker_capacity) is not int or not 1 <= worker_capacity <= 64:
        raise ValueError("worker_capacity must be an integer between 1 and 64")
    if type(retry_interval_seconds) is not int or retry_interval_seconds < 1:
        raise ValueError("retry_interval_seconds must be a positive integer")
    repository = repository.expanduser().resolve()
    initial_inputs = preparation.input_fingerprint(repository)
    observation = observe(repository, targets or None)
    graph = observation.graph
    requested = tuple(sorted(set(targets))) if targets else tuple(sorted(graph.nodes))
    if any(target not in graph.nodes for target in requested):
        raise ValueError("targets must be registered modules or behavioral sources")
    scope = _ready_inputs(repository, observation, requested, not targets)
    certifier._verify_executing_candidate_certifier(repository, graph, observation.states)
    order = certification_target_postorder(graph, observation.states, requested)
    dag = build_dependency_dag(graph, observation.states, repository)
    indexes = {node["id"]: node for node in dag["nodes"]}
    positions = {node_id: index for index, node_id in enumerate(order)}
    selected = {
        task_id for task_id, node in indexes.items()
        if (node["owner_node_id"] or task_id) in positions
    }
    for task_id in selected:
        node = indexes[task_id]
        owner = node["owner_node_id"] or task_id
        for dependency in node["dependencies"]:
            provider = indexes[dependency]["owner_node_id"] or dependency
            if provider not in positions or (
                provider != owner and positions[provider] >= positions[owner]
            ):
                raise ValueError("canonical ordering omits an audit prerequisite")
    scoped_dag = {**dag, "nodes": [indexes[key] for key in sorted(selected)]}
    stale = tuple(
        node_id for node_id in order
        if certificate_requires_renewal(observation.currentness.nodes[node_id])
    )
    required = set(semantic_stale_vertices(graph, observation.currentness, stale))
    data = {
        "reviewed_repository": str(repository),
        "reviewed_commit": observation.source_commit,
        "certification_scope": scope.identity,
        "whole_graph": not targets,
        "requested_targets": list(requested),
        "run_id": run_id,
        "worker_capacity": worker_capacity,
        "retry_interval_seconds": retry_interval_seconds,
        # Two machine evolutions per node, plus reconciliation and terminal entry.
        "automatic_transition_limit": max(100, 3 * len(order) + 3),
        "dag": scoped_dag, "node_order": list(order),
        "required": sorted(required & selected),
        "audited_inputs": {
            node_id: certifier.audited_inputs(observation.states[node_id])
            for node_id in order
        },
    }
    data["preparation"] = None
    if stale:
        data["preparation"] = _prepare(data, observation, scope, initial_inputs)
    return data


def _prepare(data, observation, scope, initial_inputs):
    context = SimpleNamespace(charter=SimpleNamespace(data=data))
    packets = {task["id"]: _packet(context, observation, task["id"], {}, {}, static_only=True)
               for task in data["dag"]["nodes"]}
    return preparation.prepare(data, observation, scope, packets, initial_inputs)


def _observation(context: EvolutionContext):
    data = context.charter.data
    envelope = data.get("preparation")
    for record in context.history.machines():
        if record.result.value.get("preparation") is not None:
            envelope = record.result.value["preparation"]
    if envelope is not None:
        observed = preparation.open_preparation(plain(data), plain(envelope))
        if observed is not None:
            _require_audit_scope(data, observed)
            return observed
    initial_inputs = preparation.input_fingerprint(Path(data["reviewed_repository"]))
    observed = observe(
        Path(data["reviewed_repository"]),
        None if data["whole_graph"] else data["requested_targets"],
    )
    if observed.source_commit != data["reviewed_commit"]:
        raise ValueError("reviewed repository commit changed")
    for node_id, expected in data["audited_inputs"].items():
        state = observed.states.get(node_id)
        if state is None or certifier.audited_inputs(state) != plain(expected):
            raise ValueError(f"audited inputs changed: {node_id}")
    scope = _ready_inputs(
        Path(data["reviewed_repository"]), observed, data["requested_targets"], data["whole_graph"],
    )
    if scope.identity != data["certification_scope"]:
        raise ValueError("certification scope changed after preparation")
    _require_audit_scope(data, observed)
    if envelope is not None or any(not observed.currentness.nodes[node].current for node in data["node_order"]):
        fresh = _prepare(plain(data), observed, scope, initial_inputs)
        refreshed = preparation.open_preparation(plain(data), fresh)
        if refreshed is None:
            raise ValueError("inputs changed after refreshed preparation")
        refreshed.preparation_changed = True
        return refreshed
    return observed


def _require_audit_scope(data, observed):
    stale = tuple(
        node_id for node_id in data["node_order"]
        if certificate_requires_renewal(observed.currentness.nodes[node_id])
    )
    required = set(semantic_stale_vertices(observed.graph, observed.currentness, stale))
    selected = {node["id"] for node in data["dag"]["nodes"]}
    if additional := sorted((required & selected) - set(data["required"])):
        raise ValueError("certificate evidence changed: additional semantic audits required: " + ", ".join(additional))


def progress(context: EvolutionContext):
    """Reconstruct assignments and accepted reports from the sole run history."""

    # ponytail: scan bounded run history; add an index if long runs become slow.
    packets, reports, issued, current = {}, {}, set(), set()
    for record in context.history.machines():
        value = record.result.value
        if record.evolution_id == "prepare-and-reconcile":
            for packet in value.get("packets", ()):
                packets[packet["task_id"]] = plain(packet)
            current.update(value.get("current", ()))
        elif record.evolution_id == "accept-audit-and-certify":
            report = value.get("report")
            if report is not None:
                reports[report["task_id"]] = plain(report)
            issued.update(value.get("issued", ()))
            current.update(value.get("current", ()))
    return packets, reports, issued, current


def _certificate(observation, target: str) -> dict[str, object]:
    graph = observation.graph
    interface = graph.source_interfaces.get(target)
    owner = interface.source_node_id if interface else target
    status = observation.currentness.nodes[owner]
    if status.certificate is None:
        raise ValueError(f"prerequisite certificate is not current: {target}")
    envelope = plain(status.certificate)
    payload = envelope["payload"]
    facets = [
        facet for facet in payload.get("facets", ())
        if interface is None or facet["id"] == target
    ]
    if interface is not None and not facets:
        raise ValueError(f"prerequisite facet evidence is missing: {target}")
    if not status.current:
        # Reuse only an unchanged facet in an otherwise stale source.
        expected = next(
            (facet for facet in certification_facet_claims(observation.states[owner])
             if facet["id"] == target),
            None,
        )
        if (interface is None or facets != [expected]
                or not certificate_semantic_evidence_reusable(status)
                or any(concern.startswith("dependency-not-current:") for concern in status.concerns)):
            raise ValueError(f"prerequisite certificate is not current: {target}")
    return {
        "target_id": target, "owner_node_id": owner,
        "certificate_identity": certificate_entry_hash(envelope),
        "certificate_path": str(certificate_log_path(graph.nodes[owner])),
        "subject": payload["subject"],
        "facets": facets,
        "dependencies": payload["dependencies"],
        "input_manifest": payload["input_manifest"] if interface is None else facets[0]["input_manifest"],
        "checks": payload["checks"],
    }


def _packet(context, observation, target, packets, reports, *, static_only=False):
    data = context.charter.data
    nodes = {node["id"]: node for node in data["dag"]["nodes"]}
    task = nodes[target]
    if hasattr(observation, "prepared_packets"):
        packet = plain(observation.prepared_packets[target])
        target_reports = {packets[key]["target_id"]: report for key, report in reports.items()}
        packet["prerequisite_reports"] = [target_reports[dependency] for dependency in task["dependencies"] if dependency in target_reports]
        packet["prerequisite_certificates"] = [_certificate(observation, dependency) for dependency in task["dependencies"] if dependency not in target_reports]
        _validate_packet(packet)
        return packet
    owner = task["owner_node_id"] or target
    state = observation.states[owner]
    node = observation.graph.nodes[owner]
    target_reports = {packets[key]["target_id"]: report for key, report in reports.items()}
    prerequisites, certificates = [], []
    declarations = []
    for dependency in task["dependencies"]:
        dependency_interface = observation.graph.source_interfaces.get(dependency)
        dependency_owner = (
            dependency_interface.source_node_id if dependency_interface else dependency
        )
        dependency_node = observation.graph.nodes[dependency_owner]
        declarations.append({
            "target_id": dependency,
            "blueprint_path": str(dependency_node.blueprint_path),
            "declaration": (
                dependency_interface.declaration if dependency_interface
                else dependency_node.declaration
            ),
        })
        if static_only:
            continue
        if dependency in target_reports:
            prerequisites.append(target_reports[dependency])
        else:
            certificates.append(_certificate(observation, dependency))
    kind, instruction = INSTRUCTIONS[task["kind"]]
    manifest = state.input_manifest
    content = node.declaration
    identity = state.node_hash
    if task["kind"] == "interface":
        facet = next(f for f in state.facets if f.facet_id == target)
        manifest = facet.input_manifest
        identity = facet.local_hash
        content = observation.graph.source_interfaces[target].declaration
    elif task["kind"] == "behavioral-source":
        facet = next(f for f in state.facets if f.facet_type == "remainder")
        manifest = facet.input_manifest
    packet = {
        "schema_version": PACKET_VERSION,
        "task_id": data["run_id"] + ":" + target,
        "kind": kind, "instruction_interface": {"id": instruction, "version": 3},
        "owner_node_id": owner, "target_id": target,
        "reviewed_repository": data["reviewed_repository"],
        "reviewed_commit": data["reviewed_commit"],
        "audited_input_identity": identity,
        "input_manifest": list(manifest),
        "selected_content": {
            "blueprint_path": str(node.blueprint_path),
            "declaration": content,
            "gateway": str(node.gateway_path) if node.gateway_path else None,
        },
        "prerequisite_reports": prerequisites,
        "prerequisite_certificates": certificates,
        "prerequisite_declarations": declarations,
    }
    _validate_packet(packet)
    return packet


def _validate_packet(packet):
    schema = json.loads((SCHEMA_ROOT / "semantic-audit-task.schema.json").read_text())
    schema["properties"]["prerequisite_reports"]["items"] = json.loads(
        (SCHEMA_ROOT / "semantic-audit-result.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(packet)


def _verify_packet_evidence(observation, packets, issued) -> None:
    """Reject dependency evidence replaced after it was supplied to a worker."""

    for packet in packets.values():
        if packet["owner_node_id"] in issued:
            continue
        for evidence in packet["prerequisite_certificates"]:
            # A completed append can precede its Reckoning result on replay.
            if (evidence["owner_node_id"] == packet["owner_node_id"]
                    and observation.currentness.nodes[packet["owner_node_id"]].current):
                continue
            current = _certificate(observation, evidence["target_id"])
            if current != evidence:
                raise ValueError(f"prerequisite evidence changed: {evidence['target_id']}")


def prepare(context: MachineContext) -> MachineResult:
    """Select ready audits using canonical order and retained passing evidence."""

    try:
        evolution = context.evolution
        data = evolution.charter.data
        observed = _observation(evolution)
        packets, reports, issued, _current = progress(evolution)
        _verify_packet_evidence(observed, packets, issued)
        current = [
            node_id for node_id in data["node_order"]
            if observed.currentness.nodes[node_id].current and node_id not in issued
        ]
        root = next(
            (node_id for node_id in data["node_order"] if not observed.currentness.nodes[node_id].current),
            None,
        )
        if root is None:
            if hasattr(observed, "preparation"):
                preparation.publish_reviews(plain(data), observed, issued)
            return MachineResult("complete", {"current": current, "packets": []})
        preparation.ensure_node_checks(plain(data), observed, root)
        retained = {"preparation": observed.preparation} if getattr(observed, "preparation_changed", False) else {}
        nodes = {node["id"]: node for node in data["dag"]["nodes"]}
        root_tasks = {
            target for target in data["required"]
            if (nodes[target]["owner_node_id"] or target) == root
        }
        audited = {packets[key]["target_id"] for key in reports}
        if root_tasks <= audited:
            return MachineResult("sign", {"root": root, "current": current, "packets": [], **retained})
        assigned = {packet["target_id"] for packet in packets.values()}
        outstanding = sorted(set(packets) - set(reports))
        ready = scheduler.ready_tasks(
            nodes, root_tasks, audited, assigned, data["worker_capacity"],
        )
        selected = [
            _packet(evolution, observed, target, packets, reports)
            for target in ready
        ]
        if not selected and not outstanding:
            raise ValueError(f"no ready semantic audit for {root}")
        return MachineResult("dispatch", {
            **retained,
            "root": root, "packets": selected, "current": current,
            "outstanding": outstanding + [packet["task_id"] for packet in selected],
            "worker_capacity": data["worker_capacity"],
        })
    except (ValueError, ValidationError, OSError, certifier.CertificationError) as error:
        return MachineResult("failed", {"reason": str(error)})


def dispatch_data(context: EvolutionContext):
    """Expose the machine-selected packets without graph or signing decisions."""

    value = context.history.machines("prepare-and-reconcile")[-1].result.value
    return {key: item for key, item in value.items() if key != "preparation"}


def assess_event(context: LLMResponseContext) -> ValidationReport:
    """Reject stale or unknown host assignments without accepting a worker event."""

    packets, reports, _issued, _current = progress(context.evolution)
    task_id = context.response.get("task_id")
    if task_id not in packets or task_id in reports:
        return ValidationReport(False, (
            ValidationIssue(("task_id",), "invalid-assignment", "assignment is not outstanding"),
        ))
    return ValidationReport(True)


def accept_and_certify(context: MachineContext) -> MachineResult:
    """Validate raw reports and sign only the audited exact node."""

    report = None
    root = task_id = None
    try:
        evolution = context.evolution
        data = evolution.charter.data
        packets, reports, issued, _current = progress(evolution)
        prepared = evolution.history.machines("prepare-and-reconcile")[-1]
        root = prepared.result.value["root"]
        if prepared.result.outcome == "dispatch":
            event = plain(evolution.history.turns("assign-audit")[-1].response)
            task_id = event["task_id"]
            if event["outcome"] == "worker-failed":
                raise ValueError(f"{task_id}: {event['reason']}")
            report = json.loads(event["raw_output"])
            scheduler._validate_report(report, task_id)
            expected = sorted(item["task_id"] for item in packets[task_id]["prerequisite_reports"])
            actual = sorted(item["task_id"] for item in report["consumed_dependencies"])
            if expected != actual:
                raise ValueError(f"{task_id}: consumed dependencies do not match packet")
            if report["verdict"] != "pass":
                raise ValueError(f"{task_id}: {report['summary']}")
            reports[task_id] = report
        observed = _observation(evolution)
        _verify_packet_evidence(observed, packets, issued)
        preparation.ensure_node_checks(plain(data), observed, root)
        nodes = {node["id"]: node for node in data["dag"]["nodes"]}
        required = {
            target for target in data["required"]
            if (nodes[target]["owner_node_id"] or target) == root
        }
        audited = {packets[key]["target_id"] for key in reports}
        result = {"report": report, "issued": [], "current": []}
        if getattr(observed, "preparation_changed", False):
            result["preparation"] = observed.preparation
        if required <= audited:
            field = "issued" if preparation.issue(plain(data), observed, root) else "current"
            result[field] = [root]
        return MachineResult("accepted", result)
    except (ValueError, TypeError, KeyError, OSError, certifier.CertificationError) as error:
        return MachineResult("failed", {"node_id": root, "task_id": task_id, "reason": str(error)})


def complete(context: EvolutionContext) -> VoyageResult:
    """Return the machine's certification receipt without an LLM judgment."""

    _packets, reports, issued, current = progress(context)
    return VoyageResult("complete", {
        "requested_targets": context.charter.data["requested_targets"],
        "nodes_issued": sorted(issued),
        "nodes_already_current": sorted(current - issued),
        "semantic_audit_count": len(reports),
    })


def failed(context: EvolutionContext) -> VoyageResult:
    """Return the exact deterministic failure retained by the machine."""

    record = context.history.machines()[-1]
    return VoyageResult("failed", record.result.value)

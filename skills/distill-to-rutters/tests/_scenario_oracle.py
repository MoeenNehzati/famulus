"""Fixture-specific fact oracle for the checked-in distillation scenarios.

This test helper extracts a deliberately small set of assignment, graph, and
enforcement facts.  It neither imports the production artifact validator nor
claims to execute a Rutter.  The literals in each ``oracle.yaml`` are the
independently authored expectation for that one fixture family.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml


_CONTRACT_RE = re.compile(
    r"^```distill-scenario-contract[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


class ScenarioOracleError(ValueError):
    """Raised when a scenario fixture cannot be inspected."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioOracleError(f"{label} must be a mapping")
    return value


def load_scenario_contract(path: Path) -> Mapping[str, Any]:
    """Parse the one fixture-only bundle of production-shaped contract bodies."""
    matches = list(_CONTRACT_RE.finditer(path.read_text(encoding="utf-8")))
    if len(matches) != 1:
        raise ScenarioOracleError(
            "scenario fixture must contain exactly one contract block, "
            f"found {len(matches)}"
        )
    match = matches[0]
    loaded = yaml.safe_load(match.group("body"))
    document = _mapping(loaded, path.name)
    if set(document) != {"scenario", "assignment", "graph", "logic"}:
        raise ScenarioOracleError(
            f"{path.name} must define scenario, assignment, graph, and logic"
        )
    return document


def _load_oracle(path: Path) -> Mapping[str, Any]:
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), path.name)


def _indexed(rows: Any, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, list):
        raise ScenarioOracleError(f"{label} must be a list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for position, value in enumerate(rows):
        row = _mapping(value, f"{label}[{position}]")
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier:
            raise ScenarioOracleError(f"{label}[{position}].{key} must be a string")
        if identifier in indexed:
            raise ScenarioOracleError(f"duplicate {label} {identifier}")
        indexed[identifier] = row
    return indexed


def _found(value: Any) -> str:
    return "missing" if value is None else repr(value)


def evaluate_scenario(contract_path: Path, oracle_path: Path) -> tuple[str, ...]:
    """Compare one known fixture with its hand-authored obligation/trace facts."""
    contract = load_scenario_contract(contract_path)
    oracle = _load_oracle(oracle_path)
    findings: list[str] = []

    expected_scenario = oracle.get("scenario")
    if contract.get("scenario") != expected_scenario:
        findings.append(
            f"scenario expected {_found(expected_scenario)}, "
            f"found {_found(contract.get('scenario'))}"
        )

    graph = _mapping(contract["graph"], "graph")
    graph_rutters = _indexed(graph.get("rutters"), "rutter_id", "rutters")
    graph_evolutions = {
        rutter_id: _indexed(
            rutter.get("evolutions"),
            "evolution_id",
            f"rutter {rutter_id} evolutions",
        )
        for rutter_id, rutter in graph_rutters.items()
    }

    assignment = _mapping(contract["assignment"], "assignment")
    assignments = _indexed(assignment.get("assignments"), "part_id", "assignments")
    expected_assignments = _mapping(
        oracle.get("assignments", {}), "oracle assignments"
    )
    missing_assignments = sorted(set(expected_assignments) - set(assignments))
    unexpected_assignments = sorted(set(assignments) - set(expected_assignments))
    if missing_assignments:
        findings.append(f"assignments missing={missing_assignments}")
    if unexpected_assignments:
        findings.append(f"assignments unexpected={unexpected_assignments}")
    for part_id, expected_value in expected_assignments.items():
        expected = _mapping(expected_value, f"oracle assignment {part_id}")
        actual = assignments.get(part_id)
        if actual is None:
            findings.append(f"assignment {part_id} is missing")
            continue
        for field in ("voyage_id", "rutter_definition_id"):
            if actual.get(field) != expected.get(field):
                findings.append(
                    f"assignment {part_id}.{field} expected "
                    f"{_found(expected.get(field))}, found {_found(actual.get(field))}"
                )
        actual_inseparability = _mapping(
            actual.get("inseparability"), f"assignment {part_id}.inseparability"
        ).get("status")
        if actual_inseparability != expected.get("inseparability"):
            findings.append(
                f"assignment {part_id}.inseparability expected "
                f"{_found(expected.get('inseparability'))}, "
                f"found {_found(actual_inseparability)}"
            )
        actual_workflows = {
            voyage_id: workflow["join_transition"]
            for voyage_id, workflow in _indexed(
                actual.get("independent_workflows"),
                "voyage_id",
                f"assignment {part_id}.independent_workflows",
            ).items()
        }
        expected_workflows = dict(
            _mapping(
                expected.get("independent_workflows", {}),
                f"oracle assignment {part_id}.independent_workflows",
            )
        )
        if actual_workflows != expected_workflows:
            findings.append(
                f"assignment {part_id}.independent_workflows expected "
                f"{expected_workflows!r}, found {actual_workflows!r}"
            )
        rutter_definition_id = actual.get("rutter_definition_id")
        actual_rutter = graph_rutters.get(rutter_definition_id)
        if actual_rutter is None:
            findings.append(
                f"assignment {part_id} rutter_definition_id "
                f"{rutter_definition_id!r} has no graph Rutter"
            )
        else:
            expected_evolutions = set(expected.get("graph_evolutions", ()))
            actual_evolutions = set(graph_evolutions[str(rutter_definition_id)])
            if actual_evolutions != expected_evolutions:
                findings.append(
                    f"assignment {part_id} graph evolutions expected "
                    f"{sorted(expected_evolutions)}, found {sorted(actual_evolutions)}"
                )

    for rutter_id, expected_value in _mapping(
        oracle.get("rutter_definitions", {}), "oracle rutter_definitions"
    ).items():
        expected = _mapping(expected_value, f"oracle Rutter {rutter_id}")
        if rutter_id not in graph_rutters:
            findings.append(f"Rutter {rutter_id} is missing from graph")
            continue
        expected_evolutions = set(expected.get("evolutions", ()))
        actual_evolutions = set(graph_evolutions[rutter_id])
        if actual_evolutions != expected_evolutions:
            findings.append(
                f"Rutter {rutter_id} evolutions expected "
                f"{sorted(expected_evolutions)}, found {sorted(actual_evolutions)}"
            )

    orchestration = _mapping(assignment.get("orchestration"), "orchestration")
    expected_orchestration = _mapping(
        oracle.get("orchestration", {}), "oracle orchestration"
    )
    for field in ("mode", "coordinator_rutter_id", "retry_owner"):
        if (
            field in expected_orchestration
            and orchestration.get(field) != expected_orchestration.get(field)
        ):
            findings.append(
                f"orchestration.{field} expected "
                f"{_found(expected_orchestration.get(field))}, "
                f"found {_found(orchestration.get(field))}"
            )
    for group, expected_rules in _mapping(
        expected_orchestration.get("required_rules", {}),
        "oracle orchestration.required_rules",
    ).items():
        expected_rule_map = _mapping(
            expected_rules, f"oracle orchestration.{group}"
        )
        actual_rules = _indexed(
            orchestration.get(group),
            "obligation_id",
            f"orchestration.{group}",
        )
        unexpected_rules = sorted(set(actual_rules) - set(expected_rule_map))
        if unexpected_rules:
            findings.append(
                f"orchestration.{group} has unexpected obligations "
                f"{unexpected_rules}"
            )
        for obligation_id, expected_value in expected_rule_map.items():
            expected = _mapping(
                expected_value,
                f"oracle orchestration.{group}.{obligation_id}",
            )
            actual = actual_rules.get(obligation_id)
            if actual is None:
                findings.append(
                    f"orchestration.{group} is missing obligation {obligation_id}"
                )
                continue
            for field in ("owning_transition", "evidence"):
                if actual.get(field) != expected.get(field):
                    findings.append(
                        f"orchestration.{group}.{obligation_id}.{field} expected "
                        f"{_found(expected.get(field))}, "
                        f"found {_found(actual.get(field))}"
                    )

    graph_rows: dict[str, tuple[str, Mapping[str, Any], Mapping[str, str]]] = {}
    for rutter_id, rutter in graph_rutters.items():
        transitions: dict[tuple[str, str], str] = {}
        for raw_edge in rutter.get("transitions", []):
            edge = _mapping(raw_edge, f"rutter {rutter_id} transition")
            transitions[(str(edge.get("from")), str(edge.get("outcome")))] = str(
                edge.get("to")
            )
        for evolution_id, evolution in graph_evolutions[rutter_id].items():
            for obligation_id in evolution.get("obligation_ids", []):
                if obligation_id in graph_rows:
                    raise ScenarioOracleError(
                        f"obligation {obligation_id} has multiple graph owners"
                    )
                successors = {
                    outcome: target
                    for (source, outcome), target in transitions.items()
                    if source == evolution_id
                }
                graph_rows[str(obligation_id)] = (
                    f"{rutter_id}/{evolution_id}",
                    evolution,
                    successors,
                )

    logic = _mapping(contract["logic"], "logic")
    logic_rows = _indexed(
        logic.get("enforcement_matrix"),
        "obligation_id",
        "logic.enforcement_matrix",
    )
    expected_obligations = _mapping(
        oracle.get("obligations", {}), "oracle obligations"
    )
    for label, actual_ids in (
        ("graph", set(graph_rows)),
        ("logic", set(logic_rows)),
    ):
        missing = sorted(set(expected_obligations) - actual_ids)
        unexpected = sorted(actual_ids - set(expected_obligations))
        if missing:
            findings.append(f"{label} obligations missing={missing}")
        if unexpected:
            findings.append(f"{label} obligations unexpected={unexpected}")

    capability_honesty = _mapping(
        oracle.get("capability_honesty", {}), "oracle capability_honesty"
    )
    for obligation_id, expected_value in expected_obligations.items():
        expected = _mapping(expected_value, f"oracle obligation {obligation_id}")
        graph_row = graph_rows.get(obligation_id)
        logic_row = logic_rows.get(obligation_id)
        if graph_row is None:
            findings.append(f"obligation {obligation_id} is missing from graph")
            continue
        if logic_row is None:
            findings.append(f"obligation {obligation_id} is missing from logic")
            continue
        owner, evolution, successors = graph_row
        mechanism = _mapping(
            logic_row.get("exact_mechanism"), f"obligation {obligation_id}.exact_mechanism"
        )
        validation = _mapping(
            mechanism.get("validation"), f"obligation {obligation_id}.validation"
        )
        request = mechanism.get("request")
        request_owner = (
            _mapping(request, f"obligation {obligation_id}.request").get("owner")
            if request is not None
            else None
        )
        actual_fields = {
            "owning_evolution": owner,
            "decision_owner": evolution.get("decision_owner"),
            "validator": evolution.get("validator"),
            "logic_validator": validation.get("validator_ref"),
            "automation_permission": logic_row.get("automation_permission"),
            "request_owner": request_owner,
        }
        for field, actual_value in actual_fields.items():
            if field in expected and actual_value != expected.get(field):
                findings.append(
                    f"obligation {obligation_id}.{field} expected "
                    f"{_found(expected.get(field))}, found {_found(actual_value)}"
                )
        if logic_row.get("owning_evolution") != expected.get("owning_evolution"):
            findings.append(
                f"obligation {obligation_id}.logic_owning_evolution expected "
                f"{_found(expected.get('owning_evolution'))}, "
                f"found {_found(logic_row.get('owning_evolution'))}"
            )
        if logic_row.get("original_decision_owner") != expected.get(
            "decision_owner"
        ):
            findings.append(
                f"obligation {obligation_id}.logic_decision_owner expected "
                f"{_found(expected.get('decision_owner'))}, "
                f"found {_found(logic_row.get('original_decision_owner'))}"
            )

        capability_gap = logic_row.get("capability_gap")
        gap = capability_gap if isinstance(capability_gap, Mapping) else {}
        capability_fields = {
            "public_runtime_capability": logic_row.get(
                "public_runtime_capability"
            ),
            "public_runtime_version": logic_row.get("public_runtime_version"),
            "public_binding_contract_version": logic_row.get(
                "public_binding_contract_version"
            ),
            "capability_verified": logic_row.get("capability_verified"),
            "enforcement_class": mechanism.get("enforcement_class"),
            "absent_binding_contract": gap.get("absent_binding_contract"),
            "exact_repair": gap.get("exact_repair"),
        }
        for field, actual_value in capability_fields.items():
            expected_value = capability_honesty.get(field)
            if actual_value != expected_value:
                findings.append(
                    f"obligation {obligation_id}.{field} expected "
                    f"{_found(expected_value)}, found {_found(actual_value)}"
                )

        positive = _mapping(
            logic_row.get("positive_trace"),
            f"obligation {obligation_id}.positive_trace",
        )
        positive_expected = _mapping(
            positive.get("expected"),
            f"obligation {obligation_id}.positive_trace.expected",
        )
        expected_positive = _mapping(
            expected.get("positive"), f"oracle obligation {obligation_id}.positive"
        )
        actual_positive = {
            "outcome": positive.get("outcome"),
            "state": positive_expected.get("state"),
        }
        graph_successor = successors.get(str(actual_positive["outcome"]))
        if graph_successor != actual_positive["state"]:
            findings.append(
                f"obligation {obligation_id}.positive graph successor expected "
                f"{_found(graph_successor)}, found {_found(actual_positive['state'])}"
            )
        for field, actual_value in actual_positive.items():
            if actual_value != expected_positive.get(field):
                findings.append(
                    f"obligation {obligation_id}.positive.{field} expected "
                    f"{_found(expected_positive.get(field))}, found {_found(actual_value)}"
                )

        negative = _mapping(
            logic_row.get("negative_trace"),
            f"obligation {obligation_id}.negative_trace",
        )
        negative_expected = _mapping(
            negative.get("expected"),
            f"obligation {obligation_id}.negative_trace.expected",
        )
        expected_negative = _mapping(
            expected.get("negative"), f"oracle obligation {obligation_id}.negative"
        )
        actual_negative = {
            "outcome": negative.get("outcome"),
            "rejection_code": negative_expected.get("rejection_code"),
        }
        for field, actual_value in actual_negative.items():
            if actual_value != expected_negative.get(field):
                findings.append(
                    f"obligation {obligation_id}.negative.{field} expected "
                    f"{_found(expected_negative.get(field))}, found {_found(actual_value)}"
                )

    return tuple(findings)

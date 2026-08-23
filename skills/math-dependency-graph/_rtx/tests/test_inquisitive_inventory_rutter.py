#!/usr/bin/env python3
"""Behavioral tests for the inventory diagnostic Rutter example."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
from officina import rutter


inventory = importlib.import_module(
    "skills.math-dependency-graph._rtx._inquisitive_inventory_rutter"
)


def _single_added_node(alias: str) -> dict:
    return {
        "delta_nodes": {
            "added": [
                {
                    "alias": alias,
                    "after": {
                        "description": "A compactness hypothesis.",
                        "locations": [
                            {
                                "file": "paper.md",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                        "properties": {
                            "provenance": "explicit",
                            "type_hint": "setup",
                        },
                    },
                }
            ],
            "changed": [],
            "deleted": [],
        },
        "delta_edges": {"added": [], "changed": [], "deleted": []},
        "endpoint_context": {},
    }


def _recovered_edge(
    *, edge_alias: str, source_alias: str, target_alias: str
) -> dict:
    def context(description: str, line: int) -> dict:
        return {
            "after": {
                "description": description,
                "locations": [
                    {"file": "paper.md", "start_line": line, "end_line": line}
                ],
            }
        }

    return {
        "delta_nodes": {"added": [], "changed": [], "deleted": []},
        "delta_edges": {
            "added": [
                {
                    "alias": edge_alias,
                    "after": {
                        "from": source_alias,
                        "to": target_alias,
                        "description": "Compactness supports existence.",
                        "locations": [
                            {
                                "file": "paper.md",
                                "start_line": 3,
                                "end_line": 3,
                            }
                        ],
                        "properties": {"type": "supports"},
                    },
                }
            ],
            "changed": [],
            "deleted": [],
        },
        "endpoint_context": {
            source_alias: context("A compactness hypothesis.", 1),
            target_alias: context("An existence claim.", 3),
        },
    }


def _empty_graph() -> dict:
    return {
        "delta_nodes": {"added": [], "changed": [], "deleted": []},
        "delta_edges": {"added": [], "changed": [], "deleted": []},
        "endpoint_context": {},
    }


def _unresolved_edge(
    *, edge_alias: str, source_alias: str, target_alias: str
) -> dict:
    value = _recovered_edge(
        edge_alias=edge_alias,
        source_alias=source_alias,
        target_alias=target_alias,
    )
    value["endpoint_context"][source_alias]["after"]["properties"] = {
        "resolution_kind": "remote-label",
        "title": "Measurable selection theorem",
    }
    return value


def _chain(prefix: str, *, complete: bool) -> dict:
    first = _recovered_edge(
        edge_alias=f"{prefix}-edge-1",
        source_alias=f"{prefix}-node-a",
        target_alias=f"{prefix}-node-b",
    )
    first["endpoint_context"][f"{prefix}-node-b"]["after"][
        "description"
    ] = "An intermediate claim."
    first["delta_edges"]["added"][0]["after"][
        "description"
    ] = "Compactness supports the intermediate claim."
    if not complete:
        return first
    first["endpoint_context"][f"{prefix}-node-c"] = {
        "after": {
            "description": "A terminal claim.",
            "locations": [
                {"file": "paper.md", "start_line": 5, "end_line": 5}
            ],
        }
    }
    first["delta_edges"]["added"].append(
        {
            "alias": f"{prefix}-edge-2",
            "after": {
                "from": f"{prefix}-node-b",
                "to": f"{prefix}-node-c",
                "description": "The intermediate claim supports the terminal claim.",
                "locations": [
                    {"file": "paper.md", "start_line": 5, "end_line": 5}
                ],
                "properties": {"type": "supports"},
            },
        }
    )
    return first


def _frozen_verdict_cases() -> list[tuple[str, dict, dict, str]]:
    expected_node = _single_added_node("gold-node")
    wrong_node = copy.deepcopy(expected_node)
    wrong_node["delta_nodes"]["added"][0]["after"][
        "description"
    ] = "A boundedness hypothesis."

    expected_edge = _recovered_edge(
        edge_alias="gold-edge",
        source_alias="gold-source",
        target_alias="gold-target",
    )
    wrong_edge = copy.deepcopy(expected_edge)
    wrong_edge["delta_edges"]["added"][0]["after"]["properties"][
        "type"
    ] = "contradicts"
    return [
        (
            "semantic-node-renaming",
            _single_added_node("actual-node"),
            expected_node,
            "equal",
        ),
        ("unequal-node", wrong_node, expected_node, "different"),
        (
            "recovered-edge-renaming",
            _recovered_edge(
                edge_alias="actual-edge",
                source_alias="actual-source",
                target_alias="actual-target",
            ),
            expected_edge,
            "equal",
        ),
        ("unequal-edge", wrong_edge, expected_edge, "different"),
        (
            "unresolved-endpoint-renaming",
            _unresolved_edge(
                edge_alias="actual-unresolved-edge",
                source_alias="actual-unresolved-source",
                target_alias="actual-local-target",
            ),
            _unresolved_edge(
                edge_alias="gold-unresolved-edge",
                source_alias="gold-unresolved-source",
                target_alias="gold-local-target",
            ),
            "equal",
        ),
        ("omitted-node", _empty_graph(), expected_node, "different"),
        (
            "partial-chain",
            _chain("actual", complete=False),
            _chain("gold", complete=True),
            "different",
        ),
    ]


def _inventory_snapshot(*, nodes: list[dict], edges: list[dict]) -> dict:
    return {
        "ir_version": 3,
        "chunk_id": "iterator-worker-001",
        "files": ["paper.md"],
        "nodes": nodes,
        "edges": edges,
        "gaps": [],
    }


def _node(local_id: str, line: int, summary: str) -> dict:
    return {
        "local_id": local_id,
        "location": [0, line, line],
        "provenance": "explicit",
        "type_hint": "setup",
        "summary": summary,
    }


def _edge(local_id: str, source: str, target: str, line: int) -> dict:
    return {
        "local_id": local_id,
        "from": {"local_node": source},
        "to": {"local_node": target},
        "type": "supports",
        "basis": "explicit-prose",
        "assertion": "explicit",
        "location": [0, line, line],
        "description": "The compactness hypothesis supports the existence claim.",
        "confidence": "High",
    }


def _source_cases() -> list[dict]:
    compactness = _node("n2", 1, "A compactness hypothesis.")
    return [
        {
            "sequence_id": 1,
            "before": _inventory_snapshot(nodes=[], edges=[]),
            "after": _inventory_snapshot(nodes=[compactness], edges=[]),
        },
        {
            "sequence_id": 2,
            "before": _inventory_snapshot(nodes=[compactness], edges=[]),
            "after": _inventory_snapshot(
                nodes=[
                    compactness,
                    _node("n1", 3, "An existence claim."),
                ],
                edges=[_edge("d1", "n2", "n1", 3)],
            ),
        },
    ]


def _gold_cases() -> list[dict]:
    first = _single_added_node("gold-compactness")
    second = {
        "delta_nodes": {
            "added": [
                {
                    "alias": "gold-existence",
                    "after": {
                        "description": "An existence claim.",
                        "locations": [
                            {
                                "file": "paper.md",
                                "start_line": 3,
                                "end_line": 3,
                            }
                        ],
                        "properties": {
                            "provenance": "explicit",
                            "type_hint": "setup",
                        },
                    },
                }
            ],
            "changed": [],
            "deleted": [],
        },
        "delta_edges": {
            "added": [
                {
                    "alias": "gold-dependency",
                    "after": {
                        "from": "gold-prior-node",
                        "to": "gold-existence",
                        "description": (
                            "The compactness hypothesis is required by the "
                            "existence claim."
                        ),
                        "locations": [
                            {
                                "file": "paper.md",
                                "start_line": 3,
                                "end_line": 3,
                            }
                        ],
                        "properties": {
                            "assertion": "explicit",
                            "basis": "explicit-prose",
                            "confidence": "High",
                            "type": "requires",
                        },
                    },
                }
            ],
            "changed": [],
            "deleted": [],
        },
        "endpoint_context": {
            "gold-prior-node": {
                "before": {
                    "description": "A compactness hypothesis.",
                    "locations": [
                        {"file": "paper.md", "start_line": 1, "end_line": 1}
                    ],
                },
                "after": {
                    "description": "A compactness hypothesis.",
                    "locations": [
                        {"file": "paper.md", "start_line": 1, "end_line": 1}
                    ],
                },
            },
            "gold-existence": {
                "after": {
                    "description": "An existence claim.",
                    "locations": [
                        {"file": "paper.md", "start_line": 3, "end_line": 3}
                    ],
                }
            },
        },
    }
    return [
        {"sequence_id": 1, **first},
        {"sequence_id": 2, **second},
    ]


def _report_from_message(message: rutter.Message) -> dict:
    sequence = message.data["payload"]["sequence"]

    def changes(kind: str) -> dict:
        before = {record["local_id"]: record for record in sequence["before"][kind]}
        after = {record["local_id"]: record for record in sequence["after"][kind]}
        return {
            "added": sorted(after.keys() - before.keys()),
            "changed": sorted(
                local_id
                for local_id in before.keys() & after.keys()
                if before[local_id] != after[local_id]
            ),
            "deleted": sorted(before.keys() - after.keys()),
        }

    return {
        "revision": message.data["state"]["revision"],
        "outcome": "reported",
        "evidence": {
            "sequence_id": sequence["sequence_id"],
            "node_ids": changes("nodes"),
            "edge_ids": changes("edges"),
        },
    }


def _materialized_json(value: object) -> object:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {
            str(key): _materialized_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_materialized_json(item) for item in value]
    return value


def test_semantic_inventory_equality_ignores_local_alias_renaming() -> None:
    """Comparing aliases directly would reject the same reported entity."""

    assert inventory.semantic_inventory_equal(
        _single_added_node("actual-node-001"),
        _single_added_node("gold-compactness"),
    )


def test_recovered_edge_equality_uses_endpoint_semantics_not_local_labels() -> None:
    """Requiring reported endpoint nodes would misclassify a recovered edge."""

    assert inventory.semantic_inventory_equal(
        _recovered_edge(
            edge_alias="actual-edge-001",
            source_alias="actual-node-001",
            target_alias="actual-node-002",
        ),
        _recovered_edge(
            edge_alias="gold-dependency",
            source_alias="gold-compactness",
            target_alias="gold-existence",
        ),
    )


@pytest.mark.parametrize(
    ("_case", "actual", "expected", "verdict"),
    _frozen_verdict_cases(),
    ids=[case[0] for case in _frozen_verdict_cases()],
)
def test_frozen_semantic_cases_classify_inventory_differences(
    _case: str,
    actual: dict,
    expected: dict,
    verdict: str,
) -> None:
    """A label-sensitive or record-count evaluator misclassifies these cases."""

    assert inventory.inventory_verdict(actual, expected) == verdict


def test_adjudicated_second_gold_case_remains_unchanged() -> None:
    """Tuning the frozen adjudication to the current report would invalidate evidence."""

    edge = _gold_cases()[1]["delta_edges"]["added"][0]["after"]

    assert edge["from"] == "gold-prior-node"
    assert edge["description"] == (
        "The compactness hypothesis is required by the existence claim."
    )
    assert edge["properties"]["type"] == "requires"


def test_equal_report_composes_child_ledger_and_next_parent_prompt(
    tmp_path: Path,
) -> None:
    """Skipping the attached child or ledger would lose one accepted iteration."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    first_message = voyage.get_instruction()

    assert isinstance(first_message, rutter.Message)
    rendered = json.dumps(_materialized_json(first_message.to_json()), sort_keys=True)
    assert "gold-compactness" not in rendered
    first_response = _report_from_message(first_message)

    next_node = voyage.next(first_response, continue_=True)
    second_message = voyage.get_instruction()
    ledger = inventory.validated_inventory_ledger(experiment_dir)

    assert next_node.rutter_id == inventory.InquisitiveInventoryRutter.rutter_id
    assert next_node.state_id == "report"
    assert isinstance(second_message, rutter.Message)
    assert second_message.data["payload"]["sequence"]["sequence_id"] == 2
    assert len(ledger) == 1
    row = ledger[0]
    assert row["message"] == _materialized_json(first_message.to_json())
    assert row["response"] == first_response
    assert row["verdict"] == "equal"
    assert row["child_result"]["outcome"] == "equal"
    assert row["maker_id"] == "inventory-diagnosis"
    assert (experiment_dir / "ledger" / f"{row['action_id']}.json").is_file()


def test_different_report_reveals_gold_only_in_attached_diagnosis(
    tmp_path: Path,
) -> None:
    """Gold exposure before acceptance or skipped diagnosis would invalidate the trace."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    parent_message = voyage.get_instruction()
    wrong_response = _report_from_message(parent_message)
    wrong_response["evidence"]["node_ids"]["added"] = []

    explain = voyage.next(wrong_response, continue_=True)
    diagnosis_message = voyage.get_instruction()

    assert explain.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert explain.state_id == "explain"
    assert "gold-compactness" in json.dumps(
        _materialized_json(diagnosis_message.to_json()), sort_keys=True
    )

    diagnosis_response = {
        "revision": diagnosis_message.data["state"]["revision"],
        "outcome": "diagnosed",
        "evidence": {
            "mistake": "The compactness node was omitted.",
            "reason": "The after snapshot introduces that explicit setup.",
            "minimal_fix": "Add the missing node to inventory.md without paper-specific tuning.",
        },
    }
    next_parent = voyage.next(diagnosis_response, continue_=True)
    row = inventory.validated_inventory_ledger(experiment_dir)[0]

    assert next_parent.rutter_id == inventory.InquisitiveInventoryRutter.rutter_id
    assert next_parent.state_id == "report"
    assert row["message"] == _materialized_json(parent_message.to_json())
    assert row["response"] == wrong_response
    assert row["verdict"] == "different"
    assert row["child_result"]["outcome"] == "different"
    assert row["child_result"]["value"]["detail"] == diagnosis_response["evidence"]


def test_reopen_at_each_inventory_boundary_neither_repeats_nor_skips(
    tmp_path: Path,
) -> None:
    """A volatile position or non-idempotent ledger would duplicate sequence one."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    parent_message = voyage.get_instruction()
    wrong_response = _report_from_message(parent_message)
    wrong_response["evidence"]["node_ids"]["added"] = []

    child_route = voyage.next(wrong_response, continue_=False)
    assert child_route.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert child_route.state_id == "route"

    voyage = inventory.open_experiment(experiment_dir)
    explain = voyage.next(continue_=False)
    assert explain.state_id == "explain"
    diagnosis_message = inventory.open_experiment(experiment_dir).get_instruction()
    diagnosis_response = {
        "revision": diagnosis_message.data["state"]["revision"],
        "outcome": "diagnosed",
        "evidence": {
            "mistake": "The compactness node was omitted.",
            "reason": "The after snapshot introduces it.",
            "minimal_fix": "Add it to inventory.md without paper-specific tuning.",
        },
    }

    child_done = inventory.open_experiment(experiment_dir).next(
        diagnosis_response, continue_=False
    )
    assert child_done.state_id == "complete-different"
    child_terminal = inventory.open_experiment(experiment_dir).next(
        continue_=False
    )
    assert child_terminal.condition == "terminal"

    ledger_action = inventory.open_experiment(experiment_dir).next(
        continue_=False
    )
    assert ledger_action.rutter_id == inventory.InquisitiveInventoryRutter.rutter_id
    assert ledger_action.state_id == "record"
    action_instruction = inventory.open_experiment(experiment_dir).get_instruction()
    action_result = action_instruction.run()
    first_rows = inventory.validated_inventory_ledger(experiment_dir)

    reopened = inventory.open_experiment(experiment_dir)
    recovered_instruction = reopened.get_instruction()
    assert recovered_instruction.action_id == action_instruction.action_id
    assert recovered_instruction.run() == action_result
    assert inventory.validated_inventory_ledger(experiment_dir) == first_rows
    next_prompt = reopened.next(action_result, continue_=False)
    next_message = inventory.open_experiment(experiment_dir).get_instruction()

    assert next_prompt.state_id == "report"
    assert next_message.data["payload"]["sequence"]["sequence_id"] == 2
    assert inventory.validated_inventory_ledger(experiment_dir) == first_rows
    assert len(first_rows) == 1


def test_two_sequence_trace_preserves_adjudicated_edge_difference(
    tmp_path: Path,
) -> None:
    """Rewriting gold to mirror the current source edge would erase the frozen miss."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    first_message = voyage.get_instruction()
    voyage.next(_report_from_message(first_message), continue_=True)
    second_message = voyage.get_instruction()

    explain = voyage.next(_report_from_message(second_message), continue_=True)
    diagnosis_message = voyage.get_instruction()
    terminal = voyage.next(
        {
            "revision": diagnosis_message.data["state"]["revision"],
            "outcome": "diagnosed",
            "evidence": {
                "mistake": "The edge semantics differ from the adjudication.",
                "reason": "The report says supports while gold says requires.",
                "minimal_fix": "Correct the edge semantics in inventory.md without paper tuning.",
            },
        },
        continue_=True,
    )
    rows = {
        row["sequence_id"]: row
        for row in inventory.validated_inventory_ledger(experiment_dir)
    }
    actual = json.loads(rows[2]["child_result"]["value"]["actual_answer"])
    expected = json.loads(rows[2]["child_result"]["value"]["expected_answer"])

    assert terminal.state_id == "complete"
    assert terminal.condition == "terminal"
    assert {sequence: row["verdict"] for sequence, row in rows.items()} == {
        1: "equal",
        2: "different",
    }
    actual_type = actual["delta_edges"]["added"][0]["after"]["properties"]["type"]
    expected_type = expected["delta_edges"]["added"][0]["after"]["properties"]["type"]
    assert actual_type == "supports"
    assert expected_type == "requires"

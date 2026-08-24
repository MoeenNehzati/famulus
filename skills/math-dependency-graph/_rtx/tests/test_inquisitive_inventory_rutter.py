#!/usr/bin/env python3
"""Behavioral tests for the inventory diagnostic Rutter example."""

from __future__ import annotations

import copy
import importlib
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from officina import rutter


inventory = importlib.import_module(
    "skills.math-dependency-graph._rtx._inquisitive_inventory_rutter"
)
iterator = importlib.import_module(
    "skills.math-dependency-graph._rtx._inventory_unit_iterator"
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


def _duplicate_semantic_node_graph(
    *,
    aliases: tuple[str, ...],
    edges: tuple[tuple[str, str, str], ...],
) -> dict:
    node_record = {
        "description": "An indistinguishable claim.",
        "locations": [
            {"file": "paper.md", "start_line": 1, "end_line": 1}
        ],
        "properties": {"provenance": "explicit", "type_hint": "setup"},
    }
    return {
        "delta_nodes": {
            "added": [
                {"alias": alias, "after": copy.deepcopy(node_record)}
                for alias in aliases
            ],
            "changed": [],
            "deleted": [],
        },
        "delta_edges": {
            "added": [
                {
                    "alias": edge_alias,
                    "after": {
                        "from": source_alias,
                        "to": target_alias,
                        "description": "One claim supports another.",
                        "locations": [
                            {
                                "file": "paper.md",
                                "start_line": 2,
                                "end_line": 2,
                            }
                        ],
                        "properties": {"type": "supports"},
                    },
                }
                for edge_alias, source_alias, target_alias in edges
            ],
            "changed": [],
            "deleted": [],
        },
        "endpoint_context": {},
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
            "text": "A compactness hypothesis.",
            "before": _inventory_snapshot(nodes=[], edges=[]),
            "after": _inventory_snapshot(nodes=[compactness], edges=[]),
        },
        {
            "sequence_id": 2,
            "text": "An existence claim follows from compactness.",
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
                            "The compactness hypothesis supports the "
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
                            "type": "supports",
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
    interaction = message.data["payload"]["interaction"]
    source = next(
        case
        for case in _source_cases()
        if case["sequence_id"] == interaction["sequence_id"]
    )

    return {
        "outcome": "reported",
        "sequence_id": interaction["sequence_id"],
        "inventory": source["after"],
    }


def _completed_iterator(tmp_path: Path) -> Path:
    packet = tmp_path / "source-packet.txt"
    packet.write_text(
        "@@ source: paper.md\n0001 | A compactness hypothesis.\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "iterator"
    iterator.setup_inventory_iterator(
        packet, state_dir, requested_workers=1, window_chars=80
    )
    leased = iterator.next_inventory_unit(state_dir, 1)
    inventory_path = state_dir / "workers" / "worker-1" / "inventory.json"
    inventory_path.write_text(
        json.dumps(
            _inventory_snapshot(
                nodes=[_node("n2", 1, "A compactness hypothesis.")],
                edges=[],
            )
        ),
        encoding="utf-8",
    )
    assert iterator.next_inventory_unit(
        state_dir, 1, ack=leased["unit"]["id"]
    ) == {"state": "complete"}
    return state_dir


def test_iterator_backed_setup_uses_authenticated_closed_sequence(tmp_path: Path) -> None:
    """Accepting caller-built source cases would bypass the iterator's durable trace."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_iterator_experiment(
        _completed_iterator(tmp_path),
        1,
        _gold_cases()[:1],
        experiment_dir,
    )
    message = voyage.get_status().instruction

    assert isinstance(message, rutter.Message)
    interaction = message.data["payload"]["interaction"]
    assert interaction["sequence_id"] == 1
    assert interaction["prior_inventory"]["nodes"] == ()
    interaction = message.data["payload"]["interaction"]
    assert interaction["text"] == "A compactness hypothesis."
    assert "after" not in interaction
    assert "gold-compactness" not in json.dumps(
        _materialized_json(message.to_json()), sort_keys=True
    )


def test_report_message_embeds_loaded_inventory_instructions_and_frozen_schema(
    tmp_path: Path,
) -> None:
    """Omitting the governing inventory contract recreates an underspecified task."""

    voyage = inventory.setup_experiment(
        _source_cases(), _gold_cases(), tmp_path / "experiment"
    )
    message = voyage.get_status().instruction
    instruction_text = inventory._INVENTORY_INSTRUCTION_PATH.read_text(
        encoding="utf-8"
    )
    schema_text = inventory._INVENTORY_SCHEMA_PATH.read_text(encoding="utf-8")

    assert isinstance(message, rutter.Message)
    charter = voyage._reckoning.root.charter.data
    assert charter["inventory_instruction_text"] == instruction_text
    assert charter["inventory_instruction_sha256"] == hashlib.sha256(
        instruction_text.encode("utf-8")
    ).hexdigest()
    assert charter["inventory_schema_text"] == schema_text
    assert charter["inventory_schema_sha256"] == hashlib.sha256(
        schema_text.encode("utf-8")
    ).hexdigest()
    assert message.instructions["text"] == instruction_text + "\n\n" + inventory._REPORT_REQUEST
    assert _materialized_json(message.data["payload"]["output_schema"]) == json.loads(
        schema_text
    )


@pytest.mark.parametrize("schema_bytes", (b"", b"\xff", b"{"))
def test_setup_contract_validation_fails_before_creating_experiment_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema_bytes: bytes
) -> None:
    """Creating an experiment before validating its frozen worker contract must fail."""

    instruction_path = tmp_path / "inventory.md"
    schema_path = tmp_path / "inventory.schema.json"
    experiment_dir = tmp_path / "experiment"
    instruction_path.write_text("Inventory contract.", encoding="utf-8")
    schema_path.write_bytes(schema_bytes)
    monkeypatch.setattr(inventory, "_INVENTORY_INSTRUCTION_PATH", instruction_path)
    monkeypatch.setattr(inventory, "_INVENTORY_SCHEMA_PATH", schema_path)

    with pytest.raises(ValueError):
        inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)

    assert not experiment_dir.exists()
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    assert experiment_dir.is_dir()


def test_unreadable_setup_contract_fails_before_creating_experiment_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving a partial experiment when the canonical schema cannot be read must fail."""

    instruction_path = tmp_path / "inventory.md"
    schema_path = tmp_path / "inventory.schema.json"
    experiment_dir = tmp_path / "experiment"
    instruction_path.write_text("Inventory contract.", encoding="utf-8")
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    monkeypatch.setattr(inventory, "_INVENTORY_INSTRUCTION_PATH", instruction_path)
    monkeypatch.setattr(inventory, "_INVENTORY_SCHEMA_PATH", schema_path)
    read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == schema_path:
            raise OSError("schema is unreadable")
        return read_bytes(path)

    monkeypatch.setattr(inventory.Path, "read_bytes", unreadable)

    with pytest.raises(ValueError, match="not readable UTF-8"):
        inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)

    assert not experiment_dir.exists()


def test_frozen_gold_setup_reconstructs_legacy_sequence_and_maps_annotation(
    tmp_path: Path,
) -> None:
    """Using only the final fragment without recorded hashes would fake history."""

    state_dir = tmp_path / "iterator"
    state_dir.mkdir()
    before = _inventory_snapshot(nodes=[], edges=[])
    after = _inventory_snapshot(
        nodes=[_node("n1", 7, "A compactness hypothesis.")], edges=[]
    )
    database = state_dir / "iterator.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE units (id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, "
            "text TEXT NOT NULL, metadata_json TEXT NOT NULL);"
            "CREATE TABLE attention_sequences (id INTEGER PRIMARY KEY, "
            "worker_index INTEGER NOT NULL, first_unit_id TEXT NOT NULL, "
            "last_unit_id TEXT NOT NULL, before_sha256 TEXT NOT NULL, "
            "before_counts_json TEXT NOT NULL, after_sha256 TEXT NOT NULL, "
            "after_counts_json TEXT NOT NULL);"
        )
        connection.execute(
            "INSERT INTO units VALUES (?, ?, ?, ?)",
            (
                "u000001",
                1,
                "A compactness hypothesis.",
                json.dumps(
                    {
                        "coordinates": [
                            {"source": "paper.md", "line": 7, "packet_index": 1}
                        ]
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO attention_sequences VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                1,
                "u000001",
                "u000001",
                hashlib.sha256(inventory._canonical_bytes(before)).hexdigest(),
                json.dumps({"nodes": 0, "edges": 0, "gaps": 0}),
                hashlib.sha256(inventory._canonical_bytes(after)).hexdigest(),
                json.dumps({"nodes": 1, "edges": 0, "gaps": 0}),
            ),
        )
    fragment = tmp_path / "worker-1.json"
    fragment.write_text(json.dumps(after), encoding="utf-8")
    gold = {
        "benchmark_version": 1,
        "nodes": [
            {
                "id": "gold:compactness",
                "description": "A compactness hypothesis.",
                "locations": [
                    {"file": "paper.md", "start_line": 7, "end_line": 7}
                ],
                "kind": "assumption",
            }
        ],
        "edges": [],
    }
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(
        json.dumps(
            {
                "base_benchmark_version": 1,
                "base_gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
                "status": "accepted-adjudicated-overlay",
                "operations": [],
                "derived_counts": {"nodes": 1, "edges": 0},
            }
        ),
        encoding="utf-8",
    )

    voyage = inventory.setup_frozen_gold_experiment(
        state_dir,
        1,
        [fragment],
        gold_path,
        overlay_path,
        tmp_path / "experiment",
    )
    message = voyage.get_status().instruction

    assert isinstance(message, rutter.Message)
    interaction = message.data["payload"]["interaction"]
    assert interaction["sequence_id"] == 1
    assert interaction["prior_inventory"]["nodes"] == ()
    assert interaction["text"] == "A compactness hypothesis."
    assert "after" not in interaction
    assert "gold:compactness" not in json.dumps(
        _materialized_json(message.to_json()), sort_keys=True
    )


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


def test_semantic_inventory_equality_distinguishes_loop_from_cross_edge() -> None:
    """Replacing duplicate-valued endpoints independently would erase incidence."""

    assert not inventory.semantic_inventory_equal(
        _duplicate_semantic_node_graph(
            aliases=("actual-a", "actual-b"),
            edges=(("actual-edge", "actual-a", "actual-a"),),
        ),
        _duplicate_semantic_node_graph(
            aliases=("gold-x", "gold-y"),
            edges=(("gold-edge", "gold-x", "gold-y"),),
        ),
    )


def test_semantic_inventory_equality_maps_renamed_duplicate_nodes_one_to_one() -> None:
    """Requiring unique node semantics would reject an isomorphic renamed graph."""

    assert inventory.semantic_inventory_equal(
        _duplicate_semantic_node_graph(
            aliases=("actual-a", "actual-b", "actual-c"),
            edges=(
                ("actual-edge-1", "actual-a", "actual-b"),
                ("actual-edge-2", "actual-b", "actual-c"),
                ("actual-edge-3", "actual-c", "actual-a"),
            ),
        ),
        _duplicate_semantic_node_graph(
            aliases=("gold-x", "gold-y", "gold-z"),
            edges=(
                ("gold-edge-1", "gold-x", "gold-z"),
                ("gold-edge-2", "gold-z", "gold-y"),
                ("gold-edge-3", "gold-y", "gold-x"),
            ),
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


def test_adjudicated_second_gold_case_uses_inventory_edge_vocabulary() -> None:
    """Using a relationship type unavailable to inventory workers creates a false miss."""

    edge = _gold_cases()[1]["delta_edges"]["added"][0]["after"]

    assert edge["from"] == "gold-prior-node"
    assert edge["description"] == (
        "The compactness hypothesis supports the existence claim."
    )
    assert edge["properties"]["type"] == "supports"


def test_equal_report_composes_child_ledger_and_next_parent_prompt(
    tmp_path: Path,
) -> None:
    """Skipping the attached child or ledger would lose one accepted iteration."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    first_message = voyage.get_status().instruction

    assert isinstance(first_message, rutter.Message)
    rendered = json.dumps(_materialized_json(first_message.to_json()), sort_keys=True)
    assert "gold-compactness" not in rendered
    first_response = _report_from_message(first_message)

    next_evolution = voyage.advance(
        first_response,
        responding_to=first_message.evolution_entry_id,
        continue_=True,
    )
    second_message = voyage.get_status().instruction
    ledger = inventory.validated_inventory_ledger(experiment_dir)

    assert next_evolution.rutter_id == inventory.InquisitiveInventoryRutter.rutter_id
    assert next_evolution.evolution_id == "report"
    assert isinstance(second_message, rutter.Message)
    assert second_message.data["payload"]["interaction"]["sequence_id"] == 2
    assert len(ledger) == 1
    row = ledger[0]
    assert row["message"] == _materialized_json(first_message.to_json())
    assert row["response"] == first_response
    assert row["verdict"] == "equal"
    assert row["child_result"]["outcome"] == "equal"
    assert row["transition_hook_id"] == "inventory-diagnosis"
    assert (experiment_dir / "ledger" / f"{row['machine_id']}.json").is_file()


def test_different_report_reveals_gold_only_in_attached_diagnosis(
    tmp_path: Path,
) -> None:
    """Gold exposure before acceptance or skipped diagnosis would invalidate the trace."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    parent_message = voyage.get_status().instruction
    wrong_response = _report_from_message(parent_message)
    wrong_response["inventory"]["nodes"] = []

    explain = voyage.advance(
        wrong_response,
        responding_to=parent_message.evolution_entry_id,
        continue_=True,
    )
    diagnosis_message = voyage.get_status().instruction

    assert explain.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert explain.evolution_id == "explain"
    assert (
        "adjust your subsequent reasoning and work path"
        in diagnosis_message.instructions["text"]
    )
    assert "Do not return that adjustment" in diagnosis_message.instructions["text"]
    assert "corrected answer" not in diagnosis_message.instructions["text"]
    assert (
        "Gold truth does not represent a proof environment separately from the "
        "result environment it proves. Do not diagnose the worker's separate "
        "proof node or its proves edge as a mistake, and do not adjust the "
        "worker's path to remove them."
        in diagnosis_message.data["payload"]["metadata"]["diagnosis_guidance"]
    )
    assert "gold-compactness" in json.dumps(
        _materialized_json(diagnosis_message.to_json()), sort_keys=True
    )

    diagnosis_response = {
        "outcome": "diagnosed",
        "mistake": "The compactness node was omitted.",
        "reason": "The after snapshot introduces that explicit setup.",
        "minimal_fix": "Add the missing node to inventory.md without paper-specific tuning.",
    }
    next_parent = voyage.advance(
        diagnosis_response,
        responding_to=diagnosis_message.evolution_entry_id,
        continue_=True,
    )
    row = inventory.validated_inventory_ledger(experiment_dir)[0]

    assert next_parent.rutter_id == inventory.InquisitiveInventoryRutter.rutter_id
    assert next_parent.evolution_id == "report"
    assert row["message"] == _materialized_json(parent_message.to_json())
    assert row["response"] == wrong_response
    assert row["verdict"] == "different"
    assert row["child_result"]["outcome"] == "different"
    assert row["child_result"]["value"]["detail"] == {
        key: value for key, value in diagnosis_response.items() if key != "outcome"
    }


def test_reopen_at_each_inventory_boundary_neither_repeats_nor_skips(
    tmp_path: Path,
) -> None:
    """A volatile position or non-idempotent ledger would duplicate sequence one."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    parent_message = voyage.get_status().instruction
    wrong_response = _report_from_message(parent_message)
    wrong_response["inventory"]["nodes"] = []

    child_route = voyage.advance(
        wrong_response,
        responding_to=parent_message.evolution_entry_id,
        continue_=False,
    )
    assert child_route.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert child_route.evolution_id == "route"

    voyage = inventory.open_experiment(experiment_dir)
    explain = voyage.advance(continue_=False)
    assert explain.evolution_id == "explain"
    diagnosis_message = inventory.open_experiment(experiment_dir).get_status().instruction
    diagnosis_response = {
        "outcome": "diagnosed",
        "mistake": "The compactness node was omitted.",
        "reason": "The after snapshot introduces it.",
        "minimal_fix": "Add it to inventory.md without paper-specific tuning.",
    }

    child_done = inventory.open_experiment(experiment_dir).advance(
        diagnosis_response,
        responding_to=diagnosis_message.evolution_entry_id,
        continue_=False,
    )
    assert child_done.evolution_id == "complete-different"
    child_terminal = inventory.open_experiment(experiment_dir).advance(
        continue_=False
    )
    assert child_terminal.condition == "terminal"

    ledger_action = inventory.open_experiment(experiment_dir).advance(
        continue_=False
    )
    assert ledger_action.rutter_id == inventory.InquisitiveInventoryRutter.rutter_id
    assert ledger_action.evolution_id == "record"
    machine_instruction = inventory.open_experiment(experiment_dir).get_status().instruction
    machine_result = machine_instruction.run()
    first_rows = inventory.validated_inventory_ledger(experiment_dir)

    reopened = inventory.open_experiment(experiment_dir)
    recovered_instruction = reopened.get_status().instruction
    assert recovered_instruction.machine_id == machine_instruction.machine_id
    assert recovered_instruction.run() == machine_result
    assert inventory.validated_inventory_ledger(experiment_dir) == first_rows
    next_llm_step = reopened.advance(machine_result, continue_=False)
    next_message = inventory.open_experiment(experiment_dir).get_status().instruction

    assert next_llm_step.evolution_id == "report"
    assert next_message.data["payload"]["interaction"]["sequence_id"] == 2
    assert inventory.validated_inventory_ledger(experiment_dir) == first_rows
    assert len(first_rows) == 1


def test_two_sequence_trace_preserves_schema_valid_edge_difference(
    tmp_path: Path,
) -> None:
    """A valid but wrong worker relationship type remains diagnosable."""

    experiment_dir = tmp_path / "experiment"
    voyage = inventory.setup_experiment(_source_cases(), _gold_cases(), experiment_dir)
    first_message = voyage.get_status().instruction
    voyage.advance(
        _report_from_message(first_message),
        responding_to=first_message.evolution_entry_id,
        continue_=True,
    )
    second_message = voyage.get_status().instruction
    wrong_response = _report_from_message(second_message)
    wrong_edge = wrong_response["inventory"]["edges"][0]
    wrong_edge["type"] = "illustrated-by"
    wrong_edge["description"] = (
        "The compactness hypothesis illustrates the existence claim."
    )

    explain = voyage.advance(
        wrong_response,
        responding_to=second_message.evolution_entry_id,
        continue_=True,
    )
    diagnosis_message = voyage.get_status().instruction
    terminal = voyage.advance(
        {
            "outcome": "diagnosed",
            "mistake": "The edge semantics differ from the adjudication.",
            "reason": "The report says illustrated-by while gold says supports.",
            "minimal_fix": "Correct the edge semantics in inventory.md without paper tuning.",
        },
        responding_to=diagnosis_message.evolution_entry_id,
        continue_=True,
    )
    rows = {
        row["sequence_id"]: row
        for row in inventory.validated_inventory_ledger(experiment_dir)
    }
    actual = json.loads(rows[2]["child_result"]["value"]["actual_answer"])
    expected = json.loads(rows[2]["child_result"]["value"]["expected_answer"])

    assert terminal.evolution_id == "complete"
    assert terminal.condition == "terminal"
    assert {sequence: row["verdict"] for sequence, row in rows.items()} == {
        1: "equal",
        2: "different",
    }
    actual_type = actual["delta_edges"]["added"][0]["after"]["properties"]["type"]
    expected_type = expected["delta_edges"]["added"][0]["after"]["properties"]["type"]
    assert actual_type == "illustrated-by"
    assert expected_type == "supports"

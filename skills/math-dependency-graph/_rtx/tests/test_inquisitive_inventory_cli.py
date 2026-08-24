#!/usr/bin/env python3
"""Finite JSON adapter tests for the inquisitive inventory voyage."""

from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
import sqlite3

import yaml


cli = importlib.import_module(
    "skills.math-dependency-graph._rtx._inquisitive_inventory_cli"
)
iterator = importlib.import_module(
    "skills.math-dependency-graph._rtx._inventory_unit_iterator"
)
RTX_ROOT = Path(__file__).resolve().parents[1]


def _snapshot(nodes: list[dict]) -> dict:
    return {
        "ir_version": 3,
        "chunk_id": "frozen-appendix-001",
        "files": ["paper.md"],
        "nodes": nodes,
        "edges": [],
        "gaps": [],
    }


def _node() -> dict:
    return {
        "local_id": "n1",
        "location": [0, 1, 1],
        "provenance": "explicit",
        "type_hint": "setup",
        "summary": "A compactness hypothesis.",
    }


def _source_cases() -> list[dict]:
    return [
        {
            "sequence_id": 1,
            "text": "A compactness hypothesis.",
            "before": _snapshot([]),
            "after": _snapshot([_node()]),
        }
    ]


def _gold_cases() -> list[dict]:
    return [
        {
            "sequence_id": 1,
            "delta_nodes": {
                "added": [
                    {
                        "alias": "sealed-gold-node",
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
    ]


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _invoke(capsys, *args: object) -> tuple[int, dict, str]:
    status = cli.main([str(arg) for arg in args])
    captured = capsys.readouterr()
    return status, json.loads(captured.out), captured.err


def _setup(tmp_path: Path, capsys) -> tuple[Path, dict]:
    source_file = _write_json(tmp_path / "source.json", _source_cases())
    gold_file = _write_json(tmp_path / "gold.json", _gold_cases())
    experiment_dir = tmp_path / "experiment"
    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--source-cases-file",
        source_file,
        "--gold-cases-file",
        gold_file,
        "--experiment-dir",
        experiment_dir,
    )
    assert status == 0
    assert stderr == ""
    return experiment_dir, payload


def test_setup_accepts_completed_iterator_instead_of_caller_built_source_cases(
    tmp_path: Path, capsys
) -> None:
    """The public adapter must bind the Rutter to authenticated iterator history."""

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
    snapshot = _snapshot([_node()])
    snapshot["chunk_id"] = "iterator-worker-001"
    inventory_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert iterator.next_inventory_unit(
        state_dir, 1, ack=leased["unit"]["id"]
    ) == {"state": "complete"}
    gold_file = _write_json(tmp_path / "gold.json", _gold_cases())
    experiment_dir = tmp_path / "experiment"

    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--iterator-state-dir",
        state_dir,
        "--worker-index",
        "1",
        "--gold-cases-file",
        gold_file,
        "--experiment-dir",
        experiment_dir,
    )

    assert status == 0
    assert stderr == ""
    assert payload["evolution"]["evolution_id"] == "report"
    assert payload["instruction"]["data"]["payload"]["interaction"]["sequence_id"] == 1


def test_setup_accepts_frozen_gold_annotation_for_legacy_iterator(
    tmp_path: Path, capsys
) -> None:
    """Requiring hand-authored per-sequence cases would keep the appendix unusable."""

    state_dir = tmp_path / "iterator"
    state_dir.mkdir()
    before = _snapshot([])
    after = _snapshot([_node()])
    after["chunk_id"] = before["chunk_id"] = "iterator-worker-001"
    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
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
                    {"coordinates": [{"source": "paper.md", "line": 1}]}
                ),
            ),
        )
        canonical = lambda value: json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode()
        connection.execute(
            "INSERT INTO attention_sequences VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                1,
                "u000001",
                "u000001",
                hashlib.sha256(canonical(before)).hexdigest(),
                json.dumps({"nodes": 0, "edges": 0, "gaps": 0}),
                hashlib.sha256(canonical(after)).hexdigest(),
                json.dumps({"nodes": 1, "edges": 0, "gaps": 0}),
            ),
        )
    fragment = _write_json(tmp_path / "fragment.json", after)
    gold = _write_json(
        tmp_path / "annotation.json",
        {
            "benchmark_version": 1,
            "nodes": [
                {
                    "id": "gold:compactness",
                    "description": "A compactness hypothesis.",
                    "locations": [
                        {"file": "paper.md", "start_line": 1, "end_line": 1}
                    ],
                }
            ],
            "edges": [],
        },
    )
    overlay = _write_json(
        tmp_path / "overlay.json",
        {
            "base_benchmark_version": 1,
            "base_gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
            "status": "accepted-adjudicated-overlay",
            "operations": [],
            "derived_counts": {"nodes": 1, "edges": 0},
        },
    )

    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--iterator-state-dir",
        state_dir,
        "--worker-index",
        "1",
        "--inventory-fragment-file",
        fragment,
        "--gold-annotation-file",
        gold,
        "--gold-overlay-file",
        overlay,
        "--experiment-dir",
        tmp_path / "experiment",
    )

    assert status == 0
    assert stderr == ""
    assert payload["evolution"]["evolution_id"] == "report"
    assert payload["instruction"]["data"]["payload"]["interaction"]["sequence_id"] == 1


def test_setup_and_show_project_only_public_bound_operations(
    tmp_path: Path, capsys
) -> None:
    """Dumping the bound voyage would leak the separately sealed gold cases."""

    experiment_dir, setup_payload = _setup(tmp_path, capsys)
    status, shown, stderr = _invoke(
        capsys, "show", "--experiment-dir", experiment_dir
    )

    assert status == 0
    assert stderr == ""
    assert shown == setup_payload
    assert shown["evolution"] == {
        "rutter_id": "math-graph-inquisitive-inventory",
        "definition_version": 5,
        "evolution_id": "report",
        "evolution_entry_id": shown["evolution"]["evolution_entry_id"],
        "depth": 0,
        "condition": "ready",
    }
    assert shown["terminal_result"] is None
    assert shown["fault"] is None
    assert shown["instruction"]["kind"] == "message"
    interaction = shown["instruction"]["data"]["payload"]["interaction"]
    assert interaction["sequence_id"] == 1
    assert interaction["text"] == "A compactness hypothesis."
    assert "after" not in interaction
    assert "sealed-gold-node" not in json.dumps(shown, sort_keys=True)


def test_cli_exposes_advance_without_next(tmp_path: Path, capsys) -> None:
    """The removed next alias must not remain callable at the CLI boundary."""

    experiment_dir, _payload = _setup(tmp_path, capsys)
    reckoning = experiment_dir / "inquisitive-inventory.reckoning.json"
    before = reckoning.read_bytes()

    status, payload, stderr = _invoke(
        capsys, "next", "--experiment-dir", experiment_dir
    )

    assert status == 2
    assert json.loads(stderr) == payload
    assert payload["error"]["code"] == "usage-error"
    assert reckoning.read_bytes() == before


def test_advance_rejects_invalid_response_without_mutating_reckoning(
    tmp_path: Path, capsys
) -> None:
    """Calling advance before public validation could persist malformed authority."""

    experiment_dir, setup_payload = _setup(tmp_path, capsys)
    reckoning = experiment_dir / "inquisitive-inventory.reckoning.json"
    before = reckoning.read_bytes()
    invalid = _write_json(
        tmp_path / "invalid.json",
        {
            "outcome": "reported",
            "sequence_id": 1,
            "unexpected": {},
        },
    )

    status, payload, stderr = _invoke(
        capsys,
        "advance",
        "--experiment-dir",
        experiment_dir,
        "--response-file",
        invalid,
        "--responding-to",
        setup_payload["evolution"]["evolution_entry_id"],
    )

    assert status == 4
    assert json.loads(stderr) == payload
    assert payload == {
        "error": {
            "code": "invalid-response",
            "message": "response validation failed",
            "report": {
                "valid": False,
                "issues": [
                    {
                        "path": [],
                        "code": "response-schema",
                        "message": "response does not satisfy the LLMStep response schema",
                    },
                    {
                        "path": [],
                        "code": "response-schema",
                        "message": "response does not satisfy the LLMStep response schema",
                    },
                ],
            },
        }
    }
    assert reckoning.read_bytes() == before


def test_equal_advance_and_ledger_persist_exact_public_trace(
    tmp_path: Path, capsys
) -> None:
    """An adapter that bypasses advance or re-renders data would lose exact evidence."""

    experiment_dir, setup_payload = _setup(tmp_path, capsys)
    response = {
        "outcome": "reported",
        "sequence_id": 1,
        "inventory": _snapshot([_node()]),
    }
    response_file = _write_json(tmp_path / "response.json", response)

    status, terminal, stderr = _invoke(
        capsys,
        "advance",
        "--experiment-dir",
        experiment_dir,
        "--response-file",
        response_file,
        "--responding-to",
        setup_payload["evolution"]["evolution_entry_id"],
    )
    ledger_status, ledger, ledger_stderr = _invoke(
        capsys, "ledger", "--experiment-dir", experiment_dir
    )

    assert status == ledger_status == 0
    assert stderr == ledger_stderr == ""
    assert terminal["evolution"]["evolution_id"] == "complete"
    assert terminal["evolution"]["condition"] == "terminal"
    assert terminal["instruction"] is None
    assert terminal["terminal_result"]["outcome"] == "complete"
    assert terminal["fault"] is None
    assert len(ledger["rows"]) == 1
    row = ledger["rows"][0]
    assert row["message"] == {
        key: value
        for key, value in setup_payload["instruction"].items()
        if key != "kind"
    }
    assert row["response"] == response
    assert row["verdict"] == "equal"
    assert row["child_result"]["outcome"] == "equal"


def test_source_blueprints_follow_live_v6_and_cli_pins_cutover_interfaces() -> None:
    """The public cutover must propagate exact Rutter interface versions."""

    lifecycle = yaml.safe_load(
        (RTX_ROOT / "blueprints/rtx-inquisitive-inventory-rutter.yaml").read_text(
            encoding="utf-8"
        )
    )
    adapter = yaml.safe_load(
        (RTX_ROOT / "blueprints/rtx-inquisitive-inventory-cli.yaml").read_text(
            encoding="utf-8"
        )
    )

    for blueprint in (lifecycle, adapter):
        assert blueprint["schema_version"] == 6
        assert blueprint["node_type"] == "behavioral_source"
        assert blueprint["maturity"] == "experimental"
        assert len(blueprint["content"]) == 1
        assert len(blueprint["interfaces"]) == 1
    assert {
        "interface": "rutter.interface.bound-operations",
        "version": 5,
    } in adapter["uses_interfaces"]
    assert {
        "interface": "rutter.interface.binding",
        "version": 3,
    } in lifecycle["uses_interfaces"]
    assert {"interface": "rutter.interface.model", "version": 2} in adapter[
        "uses_interfaces"
    ]
    assert lifecycle["version"] == adapter["version"] == 7
    assert adapter["interfaces"][
        "math-dependency-graph._rtx.source.rtx-inquisitive-inventory-cli.interface.experiment"
    ]["process_binding"]["entry"] == "Interface"
    lifecycle_contract = lifecycle["interfaces"][
        "math-dependency-graph._rtx.source.rtx-inquisitive-inventory-rutter."
        "interface.experiment-lifecycle"
    ]["contract"]
    assert {
        item["value"]
        for item in lifecycle_contract["arguments"]["operation"]["type"]["values"]
    } == {"setup", "setup-iterator", "setup-frozen-gold", "open", "ledger"}


def test_module_registers_only_the_two_owned_sources_and_cli_export() -> None:
    """Unregistered files or a private lifecycle export would break node ownership."""

    module = yaml.safe_load((RTX_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))

    assert module["schema_version"] == 6
    assert module["maturity"] == "stable"
    assert module["version"] == 66
    assert "_inquisitive_inventory_rutter\\.py" in module["content"]
    assert "_inquisitive_inventory_cli\\.py" in module["content"]
    assert {
        "math-dependency-graph._rtx.source.rtx-inquisitive-inventory-rutter",
        "math-dependency-graph._rtx.source.rtx-inquisitive-inventory-cli",
    }.issubset(module["sources"])
    assert module["exports"][
        "math-dependency-graph._rtx.interface.inquisitive-inventory-experiment"
    ] == {
        "source_interface": (
            "math-dependency-graph._rtx.source.rtx-inquisitive-inventory-cli."
            "interface.experiment"
        ),
        "access": {
            "allow_all_modules": False,
            "allowed_callers": ["math-dependency-graph"],
        },
    }
    commands = [
        item["command"] for item in module["authority"]["suggested_permissions"]["bash"]
    ]
    assert [
        "python3",
        "-m",
        "officina.runtime.python_machine_interface_runner",
        "_inquisitive_inventory_cli.py",
        "Interface",
    ] in commands


def test_usage_error_is_one_structured_json_object(capsys) -> None:
    """Default argparse prose and SystemExit would violate the finite adapter contract."""

    status, payload, stderr = _invoke(capsys, "show")

    assert status == 2
    assert json.loads(stderr) == payload
    assert payload["error"]["code"] == "usage-error"
    assert "Traceback" not in stderr


def test_missing_input_is_structured_without_publishing_private_path(
    tmp_path: Path, capsys
) -> None:
    """Raw OSError text would disclose a controller-owned filesystem coordinate."""

    missing = tmp_path / "private-source.json"
    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--source-cases-file",
        missing,
        "--gold-cases-file",
        missing,
        "--experiment-dir",
        tmp_path / "experiment",
    )

    assert status == 3
    assert json.loads(stderr) == payload
    assert payload == {
        "error": {
            "code": "input-error",
            "message": "filesystem input could not be read",
        }
    }
    assert str(tmp_path) not in json.dumps(payload, sort_keys=True)


def test_invalid_json_input_is_structured_without_traceback(
    tmp_path: Path, capsys
) -> None:
    """A decoder exception must remain behind the finite process boundary."""

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--source-cases-file",
        malformed,
        "--gold-cases-file",
        malformed,
        "--experiment-dir",
        tmp_path / "experiment",
    )

    assert status == 3
    assert json.loads(stderr) == payload
    assert payload["error"] == {
        "code": "input-error",
        "message": "input is not valid JSON",
    }
    assert "Traceback" not in stderr


def test_invalid_frozen_case_shape_is_structured_input_error(
    tmp_path: Path, capsys
) -> None:
    """Constructor rejection must not escape the JSON process boundary."""

    empty = _write_json(tmp_path / "empty.json", [])
    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--source-cases-file",
        empty,
        "--gold-cases-file",
        empty,
        "--experiment-dir",
        tmp_path / "experiment",
    )

    assert status == 3
    assert json.loads(stderr) == payload
    assert payload["error"] == {
        "code": "input-error",
        "message": "source cases must be a nonempty frozen JSON array",
    }


def test_duplicate_setup_is_a_structured_state_error(
    tmp_path: Path, capsys
) -> None:
    """Treating existing authority as ordinary input could invite unsafe overwrite."""

    experiment_dir, _payload = _setup(tmp_path, capsys)
    status, payload, stderr = _invoke(
        capsys,
        "setup",
        "--source-cases-file",
        tmp_path / "source.json",
        "--gold-cases-file",
        tmp_path / "gold.json",
        "--experiment-dir",
        experiment_dir,
    )

    assert status == 5
    assert json.loads(stderr) == payload
    assert payload["error"] == {
        "code": "state-error",
        "message": "experiment state already exists",
    }


def test_unexpected_adapter_failure_stays_inside_json_boundary(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """Unexpected exceptions must not turn the declared process into a traceback."""

    def fail(_path: Path) -> object:
        raise RuntimeError("private implementation detail")

    monkeypatch.setattr(cli, "open_experiment", fail)
    status, payload, stderr = _invoke(
        capsys, "show", "--experiment-dir", tmp_path / "experiment"
    )

    assert status == 1
    assert json.loads(stderr) == payload
    assert payload["error"] == {
        "code": "internal-error",
        "message": "unexpected RuntimeError",
    }
    assert "private implementation detail" not in stderr

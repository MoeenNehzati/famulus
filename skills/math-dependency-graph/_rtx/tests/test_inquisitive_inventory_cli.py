#!/usr/bin/env python3
"""Finite JSON adapter tests for the inquisitive inventory voyage."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import yaml


cli = importlib.import_module(
    "skills.math-dependency-graph._rtx._inquisitive_inventory_cli"
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
    assert shown["node"] == {
        "rutter_id": "math-graph-inquisitive-inventory",
        "definition_version": 1,
        "state_id": "report",
        "node_entry_id": shown["node"]["node_entry_id"],
        "depth": 0,
        "condition": "ready",
    }
    assert shown["instruction"]["kind"] == "message"
    assert shown["instruction"]["data"]["payload"]["sequence"]["sequence_id"] == 1
    assert "sealed-gold-node" not in json.dumps(shown, sort_keys=True)


def test_next_rejects_invalid_response_without_mutating_reckoning(
    tmp_path: Path, capsys
) -> None:
    """Calling next before public validation could persist malformed authority."""

    experiment_dir, _payload = _setup(tmp_path, capsys)
    reckoning = experiment_dir / "inquisitive-inventory.reckoning.json"
    before = reckoning.read_bytes()
    invalid = _write_json(
        tmp_path / "invalid.json",
        {
            "revision": 0,
            "outcome": "reported",
            "evidence": {
                "sequence_id": 1,
                "node_ids": {"added": [], "changed": [], "deleted": []},
            },
        },
    )

    status, payload, stderr = _invoke(
        capsys,
        "next",
        "--experiment-dir",
        experiment_dir,
        "--response-file",
        invalid,
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
                        "path": ["evidence"],
                        "code": "invalid-inventory-report",
                        "message": (
                            "evidence must contain sequence_id, node_ids, and edge_ids"
                        ),
                    }
                ],
            },
        }
    }
    assert reckoning.read_bytes() == before


def test_equal_next_and_ledger_persist_exact_public_trace(
    tmp_path: Path, capsys
) -> None:
    """An adapter that bypasses next or re-renders data would lose exact evidence."""

    experiment_dir, setup_payload = _setup(tmp_path, capsys)
    response = {
        "revision": setup_payload["instruction"]["data"]["state"]["revision"],
        "outcome": "reported",
        "evidence": {
            "sequence_id": 1,
            "node_ids": {"added": ["n1"], "changed": [], "deleted": []},
            "edge_ids": {"added": [], "changed": [], "deleted": []},
        },
    }
    response_file = _write_json(tmp_path / "response.json", response)

    status, terminal, stderr = _invoke(
        capsys,
        "next",
        "--experiment-dir",
        experiment_dir,
        "--response-file",
        response_file,
    )
    ledger_status, ledger, ledger_stderr = _invoke(
        capsys, "ledger", "--experiment-dir", experiment_dir
    )

    assert status == ledger_status == 0
    assert stderr == ledger_stderr == ""
    assert terminal["node"]["state_id"] == "complete"
    assert terminal["node"]["condition"] == "terminal"
    assert terminal["instruction"] is None
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


def test_source_blueprints_follow_live_v6_and_cli_pins_bound_operations_v3() -> None:
    """A copied prototype contract would retain bound operations v2."""

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
        "version": 3,
    } in adapter["uses_interfaces"]
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
    } == {"setup", "open", "ledger"}


def test_module_registers_only_the_two_owned_sources_and_cli_export() -> None:
    """Unregistered files or a private lifecycle export would break node ownership."""

    module = yaml.safe_load((RTX_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))

    assert module["schema_version"] == 6
    assert module["maturity"] == "stable"
    assert module["version"] == 54
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

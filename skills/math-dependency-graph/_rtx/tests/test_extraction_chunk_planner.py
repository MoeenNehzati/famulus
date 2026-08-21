#!/usr/bin/env python3
"""Test deterministic workload chunks for inventory and extract planning."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import yaml


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

import _extraction_chunk_planner as planner  # noqa: E402


def test_schema_accepts_only_extract_plans() -> None:
    """The chunk-plan contract must reject retired inventory traversal metadata."""

    schema = json.loads(
        (SKILL_DIR / "chunk-plan.schema.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["mode"] == {"const": "extract"}
    chunk_properties = schema["properties"]["chunks"]["items"]["properties"]
    assert not {
        "packet_sha256",
        "owned_bytes",
        "anchors",
        "spans",
    } & set(chunk_properties)


@pytest.mark.parametrize(
    "retired_mode", ("inventory", "entity", "dependency", "semantic")
)
def test_cli_rejects_retired_planning_modes(
    tmp_path: Path, retired_mode: str
) -> None:
    """Only inventory and extract may be scheduled through the planner CLI."""

    with pytest.raises(SystemExit) as failure:
        planner.main(
            [
                retired_mode,
                str(tmp_path / "entity-ir.json"),
                "--out-dir",
                str(tmp_path),
            ]
        )

    assert failure.value.code == 2


def test_planner_exports_only_extract_packet_builder() -> None:
    """Inventory traversal must remain owned exclusively by the durable iterator."""

    assert {
        name for name in dir(planner) if name.startswith("plan_")
    } == {"plan_extract_packet"}


def test_parent_blueprint_exposes_no_dependency_interface() -> None:
    """The parent catalog must not publish or package the retired dependency layer."""

    blueprint = yaml.safe_load((SKILL_DIR / "blueprint.yaml").read_text(encoding="utf-8"))

    assert "math-dependency-graph.interface.dependencies" not in blueprint["exports"]
    assert "math-dependency-graph.source.instructions-dependencies" not in blueprint["sources"]
    assert "dependencies" not in "\n".join(blueprint["content"])
    assert "candidate-ledger.schema.json" not in "\n".join(blueprint["content"])


def test_authored_runtime_blueprints_follow_live_extraction_routes() -> None:
    """Runtime declarations may expose only the live inventory-to-extract routes."""

    parent = yaml.safe_load((SKILL_DIR / "blueprint.yaml").read_text(encoding="utf-8"))
    runtime = yaml.safe_load(
        (SKILL_DIR / "_rtx" / "blueprint.yaml").read_text(encoding="utf-8")
    )
    planner = yaml.safe_load(
        (SKILL_DIR / "_rtx" / "blueprints" / "rtx-extraction-chunk-planner.yaml").read_text(
            encoding="utf-8"
        )
    )
    merger = yaml.safe_load(
        (SKILL_DIR / "_rtx" / "blueprints" / "rtx-batch-ir-merger.yaml").read_text(
            encoding="utf-8"
        )
    )
    driver = yaml.safe_load(
        (SKILL_DIR / "_rtx" / "blueprints" / "rtx-extraction-phase-driver.yaml").read_text(
            encoding="utf-8"
        )
    )
    gateway = yaml.safe_load(
        (SKILL_DIR / "blueprints" / "gateway.yaml").read_text(encoding="utf-8")
    )
    normalizer = "math-dependency-graph._rtx.source.rtx-candidate-ledger-normalizer"
    normalizer_interface = (
        "math-dependency-graph._rtx.interface.scripts-normalize-candidate-ledger"
    )

    namespace = parent["namespace_exports"]["_rtx"]
    assert all(normalizer not in identifier for identifier in namespace["surface"]["only"])
    assert all(normalizer not in identifier for identifier in namespace["interface_access"])
    assert normalizer not in runtime["sources"]
    assert all(normalizer not in identifier for identifier in runtime["exports"])
    assert "_candidate_ledger_normalizer\\.py" not in runtime["content"]
    assert all(
        "_candidate_ledger_normalizer.py" not in permission["command"]
        for permission in runtime["authority"]["suggested_permissions"]["bash"]
    )
    assert normalizer_interface not in {
        interface["interface"] for interface in gateway["uses_interfaces"]
    }

    planner_dependencies = {dependency["source"] for dependency in planner["dependencies"]}
    assert planner_dependencies == {
        "math-dependency-graph._rtx.source.rtx-init",
        "math-dependency-graph._rtx.source.rtx-semantic-graph-compiler",
        "math-dependency-graph._rtx.source.rtx-source-packet",
    }
    planner_interface = next(iter(planner["interfaces"].values()))
    assert planner_interface["usage"] == (
        "<inventory|extract> <source> --out-dir <path> "
        "[--source-packet <path>] [--entrypoint <path>] "
        "[--target-tokens <n>] [--hard-max-tokens <n>]"
    )
    planner_patterns = {
        pattern["name"]: pattern
        for pattern in planner_interface["process_binding"]["patterns"]
    }
    assert set(planner_patterns) == {"inventory", "extract"}
    assert planner_patterns["inventory"]["positional_patterns"] == {"0": "^inventory$"}
    assert planner_patterns["inventory"]["allowed_flags"] == [
        "--out-dir",
        "--target-tokens",
        "--hard-max-tokens",
    ]
    assert planner_patterns["inventory"]["required_flags"] == ["--out-dir"]
    assert planner_patterns["extract"]["positional_patterns"] == {"0": "^extract$"}
    assert planner_patterns["extract"]["allowed_flags"] == [
        "--source-packet",
        "--entrypoint",
        "--out-dir",
        "--target-tokens",
        "--hard-max-tokens",
    ]
    assert planner_patterns["extract"]["required_flags"] == [
        "--source-packet",
        "--entrypoint",
        "--out-dir",
    ]

    assert {dependency["source"] for dependency in merger["dependencies"]} == {
        "math-dependency-graph._rtx.source.rtx-init"
    }
    merger_interface = next(iter(merger["interfaces"].values()))
    assert set(merger_interface["contract"]["arguments"]) == {
        "fragment-manifest",
        "chunk-manifest",
        "out",
    }
    assert merger_interface["usage"] == (
        "<fragment-manifest.json> --chunk-manifest <inventory-chunks.json> "
        "--out <inventory-ir.json>"
    )
    merger_pattern = merger_interface["process_binding"]["patterns"][0]
    assert merger_pattern["min_positionals"] == 1
    assert merger_pattern["max_positionals"] == 1
    assert merger_pattern["allowed_flags"] == [
        "--chunk-manifest",
        "--out",
    ]
    merger_reads = {
        item["id"]: item for item in merger_interface["contract"]["direct_io"]["reads"]
    }
    packet_read = merger_reads["read-5"]
    assert packet_read["path"] == "packet_path values listed by <chunk-manifest>"
    assert "authenticated" in packet_read["content"]

    driver_dependencies = {dependency["source"] for dependency in driver["dependencies"]}
    assert normalizer not in driver_dependencies
    for dependency in driver["dependencies"]:
        dependency_blueprint = yaml.safe_load(
            (SKILL_DIR / "_rtx" / dependency["blueprint"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        assert dependency["version"] == dependency_blueprint["version"]
    assert next(iter(driver["interfaces"].values()))["usage"] == (
        "<prepare|advance-inventory|finalize-extract> "
        "<entrypoint-or-state-or-fragment> "
        "--run-dir <path> [--html <path>]"
    )
    driver_interface = next(iter(driver["interfaces"].values()))
    driver_patterns = {
        pattern["name"]: pattern
        for pattern in driver_interface["process_binding"]["patterns"]
    }
    assert set(driver_patterns) == {"prepare", "advance-inventory", "finalize-extract"}
    for operation in ("prepare", "advance-inventory"):
        assert driver_patterns[operation]["positional_patterns"] == {
            "0": f"^{operation}$"
        }
        assert driver_patterns[operation]["allowed_flags"] == ["--run-dir"]
        assert driver_patterns[operation]["required_flags"] == ["--run-dir"]
    assert driver_patterns["finalize-extract"]["positional_patterns"] == {
        "0": "^finalize-extract$"
    }
    assert driver_patterns["finalize-extract"]["allowed_flags"] == [
        "--run-dir",
        "--html",
    ]
    assert driver_patterns["finalize-extract"]["required_flags"] == ["--run-dir"]
    driver_input = driver_interface["contract"]["arguments"]["input"]
    assert "inventory iterator state directory" in driver_input["description"]
    assert "inventory-chunks" not in driver_input["description"]
    driver_reads = {
        item["id"]: item for item in driver_interface["contract"]["direct_io"]["reads"]
    }
    assert driver_reads["read-1"]["path"] == "<input>"
    assert driver_reads["read-1"]["path_match"] == "exact"
    assert "inventory iterator state directory" in driver_reads["read-1"]["content"]
    iterator_state_read = driver_reads["read-3"]
    assert iterator_state_read["path"] == "<input>/**"
    assert "controller-owned authentication data" in iterator_state_read["content"]

    gateway_versions = {
        interface["interface"]: interface["version"]
        for interface in gateway["uses_interfaces"]
    }
    driver_export = "math-dependency-graph._rtx.interface.scripts-advance-extraction-phases"
    assert namespace["surface"]["only"][driver_export] == driver_interface["version"]
    assert gateway_versions[driver_export] == driver_interface["version"]
    assert set(gateway_versions) == {
        "math-dependency-graph.interface.inventory",
        "math-dependency-graph.interface.extract",
        driver_export,
        "math-dependency-graph._rtx.interface.scripts-record-run-diagnostics",
        "math-dependency-graph._rtx.interface.scripts-setup-inventory-iterator",
        "math-dependency-graph._rtx.interface.scripts-next-inventory-unit",
    }


def test_extract_packet_contains_pooled_inventory_without_source_text(tmp_path: Path) -> None:
    """Extract must receive one pooled index and bounded source-reopening metadata."""

    assert hasattr(planner, "plan_extract_packet")
    source_path = tmp_path / "source-packet.txt"
    entrypoint = tmp_path / "main.tex"
    entrypoint.write_text("\\documentclass{article}\n", encoding="utf-8")
    source_text = "@@ source: section.tex\n0001 | This source text must stay outside the extract packet.\n"
    source_path.write_text(source_text, encoding="utf-8")
    inventory = {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["section.tex"],
        "evidence": [
            {
                "id": "inventory-001::e1",
                "location": [0, 1, 1],
                "role": "statement",
            }
        ],
        "references": [],
        "candidates": [
            {
                "id": "section.tex:1",
                "location": [0, 1, 1],
                "provenance": "explicit",
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
                "summary": "An admissible object is fixed.",
            }
        ],
        "unresolved_entities": [],
        "relationship_hints": [],
        "reference_decisions": [],
        "gaps": [],
    }

    manifest = planner.plan_extract_packet(
        inventory,
        source_packet_path=source_path,
        entrypoint_path=entrypoint,
        output_dir=tmp_path,
    )

    assert manifest["mode"] == "extract"
    assert [chunk["chunk_id"] for chunk in manifest["chunks"]] == ["extract-001"]
    chunk = manifest["chunks"][0]
    assert chunk["progress_path"] == str(
        (tmp_path / "progress" / "extract-001.progress.md").resolve()
    )
    packet = json.loads(Path(chunk["packet_path"]).read_text(encoding="utf-8"))
    assert packet["inventory"] == inventory
    assert chunk["entrypoint_path"] == str(entrypoint.resolve())
    assert packet["entrypoint_path"] == str(entrypoint.resolve())
    assert packet["source_packet_path"] == str(source_path.resolve())
    assert packet["coordinate_sidecar_path"] == chunk["sidecar_path"]
    assert packet["lookup_rules"] == {
        "max_context_lines": 20,
        "max_locations_per_request": 32,
        "require_registered_locations": True,
    }
    assert source_text not in json.dumps(packet)
    sidecar = json.loads(Path(chunk["sidecar_path"]).read_text(encoding="utf-8"))
    assert sidecar["files"] == ["section.tex"]
    assert sidecar["source_packet_path"] == str(source_path.resolve())

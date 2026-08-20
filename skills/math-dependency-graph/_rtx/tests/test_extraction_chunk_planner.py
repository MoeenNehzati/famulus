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
from _extraction_chunk_planner import (  # noqa: E402
    plan_inventory_chunks,
)


def test_schema_enumerates_only_inventory_and_extract_planning_modes() -> None:
    """The shared manifest contract must not advertise any semantic worker wave."""

    schema = json.loads(
        (SKILL_DIR / "chunk-plan.schema.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["mode"]["enum"] == ["inventory", "extract"]


@pytest.mark.parametrize("retired_mode", ("entity", "dependency", "semantic"))
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


def test_planner_exports_only_inventory_and_extract_packet_builders() -> None:
    """No public planner function may recreate an entity or semantic wave."""

    assert {
        name for name in dir(planner) if name.startswith("plan_")
    } == {"plan_extract_packet", "plan_inventory_chunks"}


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
        "<prepare|advance-inventory|finalize-extract> <entrypoint-or-fragment> "
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
    assert "ordered inventory-fragment manifest" in driver_input["description"]
    assert "inventory-chunks" not in driver_input["description"]
    driver_reads = {
        item["id"]: item for item in driver_interface["contract"]["direct_io"]["reads"]
    }
    assert driver_reads["read-1"]["path"] == "<input>"
    assert driver_reads["read-1"]["path_match"] == "exact"
    assert "ordered inventory-fragment manifest" in driver_reads["read-1"]["content"]
    inventory_chunks_read = driver_reads["read-3"]
    assert inventory_chunks_read["path"] == "<run-dir>/inventory-chunks.json"
    assert "distinct from <input>" in inventory_chunks_read["content"]

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
    }


def _large_packet(*, files: int = 12, paragraphs_per_file: int = 36) -> str:
    lines: list[str] = []
    sentence = "A mechanically repeated active-source paragraph. " * 10
    for file_index in range(files):
        lines.append(f"@@ source: sections/part-{file_index:02d}.tex")
        line_number = 1
        for _paragraph in range(paragraphs_per_file):
            lines.append(f"{line_number:04d} | {sentence}")
            line_number += 1
            lines.append(f"{line_number:04d} | ")
            line_number += 1
    return "\n".join(lines) + "\n"


def _packet() -> str:
    return (
        "# packet\n"
        "@@ source: sections/first.tex\n"
        "0001 | First paragraph with enough words to count.\n"
        "0002 | \n"
        "0003 | Second paragraph with enough words to count.\n"
        "0004 | \n"
        "@@ source: sections/second.tex\n"
        "0001 | A small adjacent file.\n"
        "0002 | More text.\n"
    )


def test_inventory_plan_combines_small_files_and_covers_lines_once(tmp_path: Path) -> None:
    manifest = plan_inventory_chunks(
        _packet(),
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
        target_tokens=100,
        hard_max_tokens=140,
    )

    assert len(manifest["chunks"]) == 1
    assert manifest["chunks"][0]["spans"] == [
        {"source_file": "sections/first.tex", "start_line": 1, "end_line": 4},
        {"source_file": "sections/second.tex", "start_line": 1, "end_line": 2},
    ]
    packet = Path(manifest["chunks"][0]["packet_path"]).read_text(encoding="utf-8")
    assert "# assigned-span: sections/first.tex:1-4" in packet
    assert "# assigned-span: sections/second.tex:1-2" in packet


def test_fifty_thousand_token_source_uses_one_inventory_job(tmp_path: Path) -> None:
    packet_text = _large_packet()
    manifest = plan_inventory_chunks(
        packet_text,
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
    )

    assert len(manifest["chunks"]) == 1
    assert manifest["chunks"][0]["projected_total_tokens"] <= 95_000
    planned = {
        (span["source_file"], line_number)
        for chunk in manifest["chunks"]
        for span in chunk["spans"]
        for line_number in range(span["start_line"], span["end_line"] + 1)
    }
    indexed = planner.index_source_packet(packet_text)
    assert planned == set(indexed)


def test_inventory_uses_minimum_chunks_above_projected_context_ceiling(
    tmp_path: Path,
) -> None:
    packet_text = _large_packet(files=24, paragraphs_per_file=45)
    manifest = plan_inventory_chunks(
        packet_text,
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
    )

    assert 1 < len(manifest["chunks"]) < 5
    assert all(
        chunk["projected_total_tokens"] <= 95_000
        for chunk in manifest["chunks"]
    )


def test_inventory_limits_dense_mathematics_to_sixteen_anchors_per_chunk(
    tmp_path: Path,
) -> None:
    lines = ["@@ source: dense.tex"]
    line_number = 1
    for index in range(32):
        lines.extend(
            [
                f"{line_number:04d} | \\begin{{theorem}}",
                f"{line_number + 1:04d} | Result {index} holds.",
                f"{line_number + 2:04d} | \\end{{theorem}}",
                f"{line_number + 3:04d} | ",
            ]
        )
        line_number += 4

    manifest = plan_inventory_chunks(
        "\n".join(lines) + "\n",
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
    )

    assert len(manifest["chunks"]) >= 2
    anchor_counts = [len(chunk["anchors"]) for chunk in manifest["chunks"]]
    assert sum(anchor_counts) == 32
    assert max(anchor_counts) <= 16
    assert all(
        "visible-environment-anchor" not in Path(chunk["packet_path"]).read_text(
            encoding="utf-8"
        )
        for chunk in manifest["chunks"]
    )
    assert all(
        chunk["progress_path"]
        == str((tmp_path / "progress" / f"{chunk['chunk_id']}.progress.md").resolve())
        for chunk in manifest["chunks"]
    )


def test_inventory_plan_splits_large_file_at_safe_blank_boundary(tmp_path: Path) -> None:
    manifest = plan_inventory_chunks(
        _packet(),
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
        target_tokens=8,
        hard_max_tokens=14,
    )

    first_file_spans = [
        span
        for chunk in manifest["chunks"]
        for span in chunk["spans"]
        if span["source_file"] == "sections/first.tex"
    ]
    covered = [
        line
        for span in first_file_spans
        for line in range(span["start_line"], span["end_line"] + 1)
    ]
    assert covered == [1, 2, 3, 4]
    assert len(first_file_spans) > 1


def test_inventory_packet_hides_anchors_and_manifest_retains_them_for_postcheck(
    tmp_path: Path,
) -> None:
    packet_text = (
        "@@ source: section.tex\n"
        "0001 | Introductory context.\n"
        "0002 | \n"
        "0003 | \\begin{theorem}\n"
        "0004 | The conclusion holds.\n"
        "0005 | \\end{theorem}\n"
        "0006 | \n"
        "0007 | Closing context.\n"
    )
    manifest = plan_inventory_chunks(
        packet_text,
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
        target_tokens=6,
        hard_max_tokens=20,
    )

    theorem_chunk = next(
        chunk
        for chunk in manifest["chunks"]
        if any(span["start_line"] == 3 for span in chunk["spans"])
    )
    packet = Path(theorem_chunk["packet_path"]).read_text(encoding="utf-8")
    assert "# Boundary-context lines are read-only" in packet
    assert "# boundary-context-before: section.tex:1-2" in packet
    assert "# boundary-context-after: section.tex:7-7" in packet
    assert "visible-environment-anchor" not in packet
    assert theorem_chunk["anchors"] == [
        {
            "source_file": "section.tex",
            "start_line": 3,
            "end_line": 5,
            "environment": "theorem",
        }
    ]


def test_inventory_plan_does_not_split_locally_declared_theorem_environment(
    tmp_path: Path,
) -> None:
    """A blank line inside a custom theorem stays within one owned chunk."""

    packet_text = (
        "@@ source: section.tex\n"
        "0001 | \\newtheorem{mainclaim}{Main Claim}\n"
        "0002 | Introductory text.\n"
        "0003 | \n"
        "0004 | \\begin{mainclaim}\n"
        "0005 | The first part of the claim.\n"
        "0006 | \n"
        "0007 | The second part of the claim.\n"
        "0008 | \\end{mainclaim}\n"
        "0009 | \n"
        "0010 | Closing text.\n"
    )
    manifest = plan_inventory_chunks(
        packet_text,
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
        target_tokens=5,
        hard_max_tokens=40,
    )

    owning = [
        span
        for chunk in manifest["chunks"]
        for span in chunk["spans"]
        if span["start_line"] <= 4 <= span["end_line"]
    ]
    assert owning == [
        {"source_file": "section.tex", "start_line": 4, "end_line": 9}
    ]
    assert owning[0]["end_line"] >= 8


def test_inventory_plan_does_not_split_theorem_declared_in_another_source_file(
    tmp_path: Path,
) -> None:
    """A preamble declaration must protect its included-file environment body."""

    packet_text = (
        "@@ source: main.tex\n"
        "0001 | \\newtheorem{mainclaim}{Main Claim}\n"
        "0002 | \\input{section}\n"
        "@@ source: section.tex\n"
        "0001 | \\begin{mainclaim}\n"
        "0002 | The first part of the claim.\n"
        "0003 | \n"
        "0004 | The second part of the claim.\n"
        "0005 | \\end{mainclaim}\n"
    )
    manifest = plan_inventory_chunks(
        packet_text,
        source_packet_path=tmp_path / "source-packet.txt",
        output_dir=tmp_path,
        target_tokens=5,
        hard_max_tokens=40,
    )

    owning = [
        span
        for chunk in manifest["chunks"]
        for span in chunk["spans"]
        if span["source_file"] == "section.tex"
        and span["start_line"] <= 1 <= span["end_line"]
    ]
    assert owning == [
        {"source_file": "section.tex", "start_line": 1, "end_line": 5}
    ]


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

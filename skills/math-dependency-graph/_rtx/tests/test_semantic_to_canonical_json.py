#!/usr/bin/env python3
"""Tests for semantic validation and deterministic canonical JSON conversion."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))
sys.path.insert(0, str(Path(__file__).parent))

from _semantic_test_data import pooled_inventory, semantic_graph  # noqa: E402
from _semantic_pipeline._to_canonical_json import validate_semantic_payload  # noqa: E402


@pytest.fixture
def compile_graph(canonical_base_payload: dict[str, object]):
    """Compile through one session-cached canonical presentation base."""

    from _semantic_pipeline._to_canonical_json import compile_semantic_graph

    def compile_(payload: dict | None = None, inventory: dict | None = None) -> dict:
        return compile_semantic_graph(
            payload or semantic_graph(),
            canonical_base_payload,
            inventory or pooled_inventory(),
        )

    return compile_


def reject_only_hint(payload: dict) -> None:
    """Account for the fixture hint after a test intentionally removes all edges."""

    payload["hint_decisions"] = [
        {
            "hint_id": "inventory-001::h1",
            "decision": "rejected",
            "reason": "The test intentionally removes this edge.",
        }
    ]


def test_validate_semantic_payload_requires_authoritative_inventory() -> None:
    """Shape-valid semantic IR cannot be certified without pooled reconciliation."""

    with pytest.raises(ValueError, match="pooled inventory is required"):
        validate_semantic_payload(semantic_graph())


def test_compiler_builds_canonical_graph_without_tex_access(
    compile_graph,
    canonical_base_payload: dict[str, object],
) -> None:
    canonical = compile_graph()

    assert canonical["graph_kind"] == "math-dependency"
    assert canonical["categories"] == canonical_base_payload["categories"]
    assert [entity["id"] for entity in canonical["entities"]] == [
        "admissible-object",
        "existence",
    ]
    assert canonical["entities"][0]["category"] == "setup:definition"
    assert canonical["entities"][1]["category"] == "result:theorem"
    assert canonical["entities"][1]["position"] == 1
    assert canonical["entities"][0]["connects_to"] == [
        {
            "to": "existence",
            "type": "supports",
            "description": "The theorem uses admissibility.",
            "evidence": "sections/appendix.tex:82-84",
            "implicit": False,
            "confidence": "Verified",
        }
    ]


def test_compiler_preserves_explicit_semantic_identity_over_candidate_values(
    compile_graph,
) -> None:
    """Deterministic fallback never replaces an extract worker's explicit identity."""

    inventory = pooled_inventory()
    inventory["candidates"][1]["visible_title"] = "Candidate title"
    payload = semantic_graph()
    payload["entities"][1]["title"] = "Semantic title"
    payload["entities"][1]["ref"] = "thm:semantic"

    canonical = compile_graph(payload, inventory)
    compiled = next(item for item in canonical["entities"] if item["id"] == "existence")

    assert compiled["title"] == "Semantic title"
    assert compiled["ref"] == "thm:semantic"
    source_fields = compiled["details"]["sections"][0]["fields"]
    by_label = {field["label"]: field["value"] for field in source_fields}
    assert by_label["Title"] == "Semantic title"
    assert by_label["Reference"] == "thm:semantic"


def test_compiler_ignores_provenance_from_nonretained_resolutions(
    compile_graph,
) -> None:
    """Only matched or created handles may contribute entity source locations."""

    inventory = pooled_inventory()
    inventory["unresolved_entities"] = [
        {
            "key": "inventory-001::u1",
            "title": "Rejected alias",
            "statement": "The alias is not retained.",
            "resolution_kind": "named-entity",
            "locators": [{"name": "Rejected alias"}],
            "type_hint": "setup",
            "evidence_ids": ["inventory-001::e4"],
        }
    ]
    semantic = semantic_graph()
    semantic["unresolved_resolutions"] = [
        {
            "unresolved_id": "inventory-001::u1",
            "disposition": "rejected",
            "reason": "It is only prose.",
        }
    ]

    from officina.visualization.base_renderer import BaseRenderer

    canonical = compile_graph(semantic, inventory)

    definition_locations = canonical["entities"][0]["details"]["sections"][0]["fields"]
    assert "sections/results.tex:30-31" not in {
        field["value"] for field in definition_locations if field["label"] == "Location"
    }
    source_fields = canonical["entities"][1]["details"]["sections"][0]["fields"]
    assert {field["value"] for field in source_fields if field["label"] == "Location"} == {
        "sections/results.tex:20-24",
        "sections/appendix.tex:82-84",
    }
    assert canonical["metadata"]["semantic_exclusions"][0]["candidate_id"] == "candidate-remark"
    BaseRenderer().validate(canonical)


def test_compiler_rejects_low_confidence_final_edge(compile_graph) -> None:
    payload = semantic_graph()
    payload["relationships"][0]["confidence"] = "Low"

    with pytest.raises(ValueError, match="low-confidence relationship"):
        compile_graph(payload)


def test_compiler_rejects_unjustified_multi_entity_edgeless_graph(
    compile_graph,
) -> None:
    payload = semantic_graph()
    payload["relationships"] = []
    reject_only_hint(payload)

    with pytest.raises(ValueError, match="edgeless multi-entity semantic graph"):
        compile_graph(payload)


def test_compiler_allows_justified_dependency_free_graph(compile_graph) -> None:
    payload = semantic_graph()
    payload["relationships"] = []
    reject_only_hint(payload)
    payload["edgeless_justification"] = (
        "The displayed statements are independent declarations with no direct use."
    )

    assert compile_graph(payload)["entities"]


def test_compiler_adds_deterministic_child_category_extension(compile_graph) -> None:
    payload = semantic_graph()
    payload["entities"][1]["kind"] = "claim"
    payload["entities"][1]["category_label"] = "Claim"

    first = compile_graph(payload)
    second = compile_graph(payload)
    extension = next(item for item in first["categories"] if item["id"] == "result:claim")

    assert extension["parent"] == "result"
    assert extension["shape"] == "roundrect"
    assert extension["color"] == next(
        item for item in second["categories"] if item["id"] == "result:claim"
    )["color"]


def test_cli_writes_canonical_json_and_reports_counts(tmp_path: Path) -> None:
    semantic_path = tmp_path / "semantic.json"
    inventory_path = tmp_path / "inventory.json"
    out_path = tmp_path / "canonical.json"
    semantic_path.write_text(json.dumps(semantic_graph()), encoding="utf-8")
    inventory_path.write_text(json.dumps(pooled_inventory()), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (env.get("PYTHONPATH"), str(REPO_SRC)) if part
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "_rtx" / "_semantic_pipeline" / "_to_canonical_json.py"),
            str(semantic_path),
            "--inventory",
            str(inventory_path),
            "--out",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    report = json.loads(result.stdout)
    assert report["entities"] == 2
    assert report["edges"] == 1
    assert Path(report["out"]).resolve() == out_path.resolve()
    canonical = json.loads(out_path.read_text(encoding="utf-8"))
    assert canonical["graph_kind"] == "math-dependency"
    assert canonical["metadata"]["semantic_ir_sha256"] == hashlib.sha256(
        semantic_path.read_bytes()
    ).hexdigest()

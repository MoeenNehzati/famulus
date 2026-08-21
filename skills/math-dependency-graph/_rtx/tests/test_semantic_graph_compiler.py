#!/usr/bin/env python3
"""Tests for whole-document extract validation and deterministic compilation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import jsonschema
import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

from _semantic_graph_compiler import validate_semantic_payload  # noqa: E402


def pooled_inventory() -> dict:
    """Return the authoritative pooled records needed to compile one graph."""

    return {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["sections/model.tex", "sections/results.tex", "sections/appendix.tex"],
        "evidence": [
            {"id": "inventory-001::e1", "location": [0, 10, 14], "role": "statement"},
            {"id": "inventory-001::e2", "location": [1, 20, 24], "role": "statement"},
            {"id": "inventory-001::e3", "location": [2, 82, 84], "role": "proof-use"},
            {"id": "inventory-001::e4", "location": [1, 30, 31], "role": "statement"},
        ],
        "references": [],
        "candidates": [
            {
                "id": "candidate-definition",
                "location": [0, 10, 14],
                "environment": "definition",
                "labels": ["def:object"],
                "provenance": "explicit",
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
                "summary": "An admissible object is defined.",
            },
            {
                "id": "candidate-theorem",
                "location": [1, 20, 24],
                "environment": "theorem",
                "labels": ["thm:existence"],
                "provenance": "explicit",
                "type_hint": "result",
                "evidence_ids": ["inventory-001::e2", "inventory-001::e3"],
                "summary": "An admissible object exists.",
            },
            {
                "id": "candidate-remark",
                "location": [1, 30, 31],
                "environment": "remark",
                "provenance": "explicit",
                "type_hint": "exposition",
                "evidence_ids": ["inventory-001::e4"],
                "summary": "A prose transition follows the theorem.",
            },
        ],
        "unresolved_entities": [],
        "relationship_hints": [
            {
                "id": "inventory-001::h1",
                "from": {"candidate_id": "candidate-definition"},
                "to": {"candidate_id": "candidate-theorem"},
                "type": "supports",
                "basis": "proof-use",
                "assertion": "explicit",
                "evidence_ids": ["inventory-001::e3"],
                "confidence": "Verified",
            }
        ],
        "reference_decisions": [],
        "gaps": [],
    }


def semantic_graph() -> dict:
    """Return one reconciled version-2 semantic graph with handle provenance."""

    return {
        "ir_version": 2,
        "document": {"title": "Example paper", "source_file": "main.tex"},
        "inventory": {
            "candidate_ids": [
                "candidate-definition",
                "candidate-theorem",
                "candidate-remark",
            ],
            "candidate_count": 3,
        },
        "entities": [
            {
                "candidate_ids": ["candidate-definition"],
                "id": "admissible-object",
                "type": "setup",
                "kind": "definition",
                "short_title": "Admissible object",
                "description": "An object is admissible when $A(x)$ holds.",
                "source": "explicit",
                "ref": "def:object",
            },
            {
                "candidate_ids": ["candidate-theorem"],
                "id": "existence",
                "type": "result",
                "kind": "theorem",
                "short_title": "Existence",
                "description": "An admissible object exists.",
                "source": "explicit",
                "ref": "thm:existence",
            },
        ],
        "exclusions": [
            {
                "candidate_id": "candidate-remark",
                "reason": "The remark is a prose transition with no dependency role.",
            }
        ],
        "unresolved_resolutions": [],
        "relationships": [
            {
                "from": "admissible-object",
                "to": "existence",
                "type": "supports",
                "description": "The theorem uses admissibility.",
                "hint_ids": ["inventory-001::h1"],
                "evidence_ids": ["inventory-001::e3"],
                "implicit": False,
                "confidence": "Verified",
            }
        ],
        "hint_decisions": [],
        "reference_decisions": [],
        "gap_decisions": [],
        "gaps": [],
    }


def load_schema(name: str) -> dict:
    return json.loads((SKILL_DIR / name).read_text(encoding="utf-8"))


def compile_graph(payload: dict | None = None, inventory: dict | None = None) -> dict:
    """Compile the test graph through the public inventory-aware boundary."""

    from _graph_builder import load_base_payload
    from _semantic_graph_compiler import compile_semantic_graph

    return compile_semantic_graph(
        payload or semantic_graph(),
        load_base_payload(),
        inventory or pooled_inventory(),
    )


def reject_only_hint(payload: dict) -> None:
    """Account for the fixture hint after a test intentionally removes all edges."""

    payload["hint_decisions"] = [
        {
            "hint_id": "inventory-001::h1",
            "decision": "rejected",
            "reason": "The test intentionally removes this edge.",
        }
    ]


def test_semantic_graph_schema_requires_reconciliation_fields() -> None:
    schema = load_schema("semantic-graph.schema.json")
    validator = jsonschema.validators.validator_for(schema)(schema)
    validator.validate(semantic_graph())

    for field in ("unresolved_resolutions", "hint_decisions", "gap_decisions"):
        invalid = semantic_graph()
        del invalid[field]
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid)
    for field in ("hint_ids", "evidence_ids"):
        invalid = semantic_graph()
        del invalid["relationships"][0][field]
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid)


def test_validate_semantic_payload_requires_authoritative_inventory() -> None:
    """Shape-valid semantic IR cannot be certified without pooled reconciliation."""

    with pytest.raises(ValueError, match="pooled inventory is required"):
        validate_semantic_payload(semantic_graph())


def test_compiler_builds_canonical_graph_without_tex_access() -> None:
    from _graph_builder import load_base_payload, validate_math_payload
    from officina.visualization.base_renderer import BaseRenderer

    canonical = compile_graph()

    assert canonical["graph_kind"] == "math-dependency"
    assert canonical["categories"] == load_base_payload()["categories"]
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


def test_compiler_derives_merged_entity_identity_from_first_source_candidate() -> None:
    """Source labels and visible titles survive even when extract omits duplicates."""

    inventory = pooled_inventory()
    inventory["evidence"].append(
        {"id": "inventory-001::e5", "location": [0, 5, 7], "role": "statement"}
    )
    inventory["candidates"].append(
        {
            "id": "candidate-source-first",
            "location": [0, 5, 7],
            "environment": "theorem",
            "labels": ["thm:source-first"],
            "visible_title": "Source-visible existence",
            "provenance": "explicit",
            "type_hint": "result",
            "evidence_ids": ["inventory-001::e5"],
            "summary": "An admissible object exists.",
        }
    )
    payload = semantic_graph()
    payload["inventory"]["candidate_ids"].append("candidate-source-first")
    payload["inventory"]["candidate_count"] += 1
    existence = payload["entities"][1]
    existence["candidate_ids"] = ["candidate-theorem", "candidate-source-first"]
    existence.pop("ref")

    canonical = compile_graph(payload, inventory)
    compiled = next(item for item in canonical["entities"] if item["id"] == "existence")

    assert compiled["ref"] == "thm:source-first"
    assert compiled["title"] == "Source-visible existence"
    source_fields = compiled["details"]["sections"][0]["fields"]
    by_label = {field["label"]: field["value"] for field in source_fields}
    assert by_label["Reference"] == "thm:source-first"
    assert by_label["Title"] == "Source-visible existence"


def test_compiler_preserves_explicit_semantic_identity_over_candidate_values() -> None:
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


def test_compiler_ignores_provenance_from_nonretained_resolutions() -> None:
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

    from _graph_builder import validate_math_payload
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
    validate_math_payload(canonical)
    BaseRenderer().validate(canonical)


def test_compiler_rejects_unknown_edge_evidence() -> None:
    payload = semantic_graph()
    payload["relationships"][0]["evidence_ids"] = ["inventory-001::e99"]

    with pytest.raises(ValueError, match="unknown evidence handle"):
        compile_graph(payload)


def test_compiler_rejects_low_confidence_final_edge() -> None:
    payload = semantic_graph()
    payload["relationships"][0]["confidence"] = "Low"

    with pytest.raises(ValueError, match="low-confidence relationship"):
        compile_graph(payload)


def test_compiler_rejects_raw_environment_wrapper_description() -> None:
    payload = semantic_graph()
    payload["entities"][0]["description"] = (
        "\\begin{definition}An object is admissible.\\end{definition}"
    )

    with pytest.raises(ValueError, match="raw TeX environment wrapper"):
        compile_graph(payload)


def test_compiler_rejects_unjustified_multi_entity_edgeless_graph() -> None:
    payload = semantic_graph()
    payload["relationships"] = []
    reject_only_hint(payload)

    with pytest.raises(ValueError, match="edgeless multi-entity semantic graph"):
        compile_graph(payload)


def test_compiler_allows_justified_dependency_free_graph() -> None:
    payload = semantic_graph()
    payload["relationships"] = []
    reject_only_hint(payload)
    payload["edgeless_justification"] = (
        "The displayed statements are independent declarations with no direct use."
    )

    assert compile_graph(payload)["entities"]


def test_compiler_adds_deterministic_child_category_extension() -> None:
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


def test_compiler_gives_distinct_colors_to_distinct_extension_kinds() -> None:
    from _semantic_graph_compiler import stable_extension_color

    by_color: dict[str, str] = {}
    colliding_kinds: tuple[str, str] | None = None
    for index in range(100):
        kind = f"custom-{index}"
        color = stable_extension_color(f"result:{kind}")
        if color in by_color:
            colliding_kinds = (by_color[color], kind)
            break
        by_color[color] = kind
    assert colliding_kinds is not None

    payload = semantic_graph()
    inventory = pooled_inventory()
    first_kind, second_kind = colliding_kinds
    payload["entities"][1]["kind"] = first_kind
    payload["inventory"]["candidate_ids"].append("candidate-second-extension")
    payload["inventory"]["candidate_count"] = 4
    payload["entities"].append(
        {
            "candidate_ids": ["candidate-second-extension"],
            "id": "second-extension",
            "type": "result",
            "kind": second_kind,
            "short_title": "Second extension",
            "description": "A second extension result.",
            "source": "explicit",
        }
    )
    inventory["candidates"].append(
        {
            "id": "candidate-second-extension",
            "location": [1, 30, 31],
            "provenance": "explicit",
            "type_hint": "result",
            "evidence_ids": ["inventory-001::e4"],
            "summary": "A second extension result.",
        }
    )
    payload["relationships"].append(
        {
            "from": "existence",
            "to": "second-extension",
            "type": "supports",
            "description": "The first result supports the second extension.",
            "hint_ids": [],
            "evidence_ids": ["inventory-001::e4"],
            "implicit": True,
            "confidence": "High",
        }
    )

    canonical = compile_graph(payload, inventory)
    colors = {
        item["id"]: item["color"]
        for item in canonical["categories"]
        if item["id"] in {f"result:{first_kind}", f"result:{second_kind}"}
    }
    assert len(set(colors.values())) == 2


def test_cli_writes_canonical_json_and_reports_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        semantic_path = root / "semantic.json"
        inventory_path = root / "inventory.json"
        out_path = root / "canonical.json"
        semantic_path.write_text(json.dumps(semantic_graph()), encoding="utf-8")
        inventory_path.write_text(json.dumps(pooled_inventory()), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (env.get("PYTHONPATH"), str(REPO_SRC)) if part
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SKILL_DIR / "_rtx" / "_semantic_graph_compiler.py"),
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
        assert Path(report["out"]) == out_path.resolve()
        canonical = json.loads(out_path.read_text(encoding="utf-8"))
        assert canonical["graph_kind"] == "math-dependency"
        assert canonical["metadata"]["semantic_ir_sha256"] == hashlib.sha256(
            semantic_path.read_bytes()
        ).hexdigest()

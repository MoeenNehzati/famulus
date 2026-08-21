"""Behavioral tests for deterministic proof-entity normalization."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

import pytest
from jsonschema import Draft202012Validator, ValidationError


RUNTIME_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = RUNTIME_DIR.parent
REPO_SRC = RUNTIME_DIR.parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RUNTIME_DIR))


def _semantic_validator() -> Draft202012Validator:
    schema = json.loads((SKILL_DIR / "semantic-graph.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _inventory_validator() -> Draft202012Validator:
    schema = json.loads((SKILL_DIR / "inventory.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _semantic_ir() -> dict:
    return {
        "ir_version": 2,
        "document": {"source_file": "main.tex"},
        "inventory": {"candidate_ids": [], "candidate_count": 0},
        "entities": [
            {
                "candidate_ids": [],
                "id": "assumption-a",
                "type": "assumption",
                "short_title": "A",
                "description": "Assumption A.",
                "source": "explicit",
            },
            {
                "candidate_ids": [],
                "id": "result-r",
                "type": "result",
                "short_title": "R",
                "description": "Result R.",
                "source": "explicit",
            },
            {
                "candidate_ids": [],
                "id": "proof-formal",
                "type": "proof",
                "kind": "formal",
                "short_title": "Proof of R",
                "description": "A formal argument for R.",
                "source": "explicit",
            },
            {
                "candidate_ids": [],
                "id": "proof-sketch",
                "type": "proof",
                "kind": "sketch",
                "short_title": "Sketch of R",
                "description": "A complementary sketch for R.",
                "source": "explicit",
            },
        ],
        "exclusions": [],
        "unresolved_resolutions": [],
        "relationships": [
            {
                "from": "assumption-a",
                "to": "proof-formal",
                "type": "supports",
                "description": "The formal proof uses A.",
                "hint_ids": ["pool::h1"],
                "evidence_ids": ["pool::e1"],
                "reference_ids": ["pool::r1"],
                "implicit": True,
                "confidence": "High",
            },
            {
                "from": "assumption-a",
                "to": "proof-sketch",
                "type": "supports",
                "description": "The sketch explicitly uses A.",
                "hint_ids": ["pool::h2"],
                "evidence_ids": ["pool::e2"],
                "implicit": False,
                "confidence": "Medium",
            },
            {
                "from": "proof-formal",
                "to": "result-r",
                "type": "proves",
                "description": "The formal proof proves R.",
                "hint_ids": ["pool::h3"],
                "evidence_ids": ["pool::e3"],
                "implicit": False,
                "confidence": "Verified",
            },
            {
                "from": "proof-sketch",
                "to": "result-r",
                "type": "proves",
                "description": "The sketch proves R.",
                "hint_ids": ["pool::h4"],
                "evidence_ids": ["pool::e4"],
                "implicit": False,
                "confidence": "Verified",
            },
            {
                "from": "assumption-a",
                "to": "result-r",
                "type": "supports",
                "description": "The result already records the direct use of A.",
                "hint_ids": ["pool::h5"],
                "evidence_ids": ["pool::e5"],
                "implicit": True,
                "confidence": "High",
            },
        ],
        "hint_decisions": [],
        "reference_decisions": [],
        "gap_decisions": [],
        "gaps": [],
    }


def _decisions() -> dict:
    return {
        "document_kind": "proof-normalization-decisions",
        "ir_version": 1,
        "decisions": [
            {
                "proof_id": "proof-formal",
                "disposition": "accepted",
                "bundle_id": "bundle-r-main",
                "target_id": "result-r",
                "reason": "The formal proof and sketch are one argument.",
            },
            {
                "proof_id": "proof-sketch",
                "disposition": "accepted",
                "bundle_id": "bundle-r-main",
                "target_id": "result-r",
                "reason": "The sketch introduces the formal argument.",
            },
        ],
    }


def test_inventory_schema_accepts_proof_hint_and_proves_edge() -> None:
    """Removing the transitional inventory vocabulary rejects proof discovery."""

    payload = {
        "ir_version": 3,
        "chunk_id": "proof-chunk",
        "files": ["main.tex"],
        "nodes": [
            {
                "local_id": "n1",
                "location": [0, 10, 14],
                "provenance": "explicit",
                "type_hint": "proof",
                "summary": "A proof of the theorem.",
            },
            {
                "local_id": "n2",
                "location": [0, 2, 8],
                "provenance": "explicit",
                "type_hint": "result",
                "summary": "The theorem.",
            },
        ],
        "edges": [
            {
                "local_id": "d1",
                "from": {"local_node": "n1"},
                "to": {"local_node": "n2"},
                "type": "proves",
                "basis": "explicit-prose",
                "assertion": "explicit",
                "location": [0, 10, 14],
                "description": "Proof of the theorem.",
                "confidence": "Verified",
            }
        ],
        "gaps": [],
    }

    _inventory_validator().validate(payload)


def test_transitional_semantic_schema_accepts_each_proof_kind_and_proves() -> None:
    """Removing a proof kind or proves relationship rejects transitional IR."""

    payload = _semantic_ir()
    _semantic_validator().validate(payload)
    for kind in ("formal", "informal", "sketch"):
        candidate = deepcopy(payload)
        candidate["entities"][2]["kind"] = kind
        _semantic_validator().validate(candidate)


def test_normalizer_redirects_and_deduplicates_proof_dependencies() -> None:
    """Dropping a proof route loses its evidence or leaves proof IR in canonical input."""

    from _proof_normalizer import normalize_proof_entities

    normalized, sidecar = normalize_proof_entities(_semantic_ir(), _decisions())

    assert [entity["id"] for entity in normalized["entities"]] == [
        "assumption-a",
        "result-r",
    ]
    assert normalized["relationships"] == [
        {
            "from": "assumption-a",
            "to": "result-r",
            "type": "supports",
            "description": "The result already records the direct use of A.",
            "hint_ids": ["pool::h1", "pool::h2", "pool::h5"],
            "evidence_ids": ["pool::e1", "pool::e2", "pool::e5"],
            "reference_ids": ["pool::r1"],
            "implicit": False,
            "confidence": "Medium",
        }
    ]
    assert sidecar["bundles"][0]["bundle_id"] == "bundle-r-main"
    assert sidecar["bundles"][0]["target_id"] == "result-r"
    assert sidecar["bundles"][0]["proof_ids"] == ["proof-formal", "proof-sketch"]
    assert [item["description"] for item in sidecar["bundles"][0]["proves_relationships"]] == [
        "The formal proof proves R.",
        "The sketch proves R.",
    ]
    assert sidecar["relationships"][0]["proof_ids"] == [
        "proof-formal",
        "proof-sketch",
    ]


def test_normalizer_rejects_unaccounted_proof_without_replacing_outputs() -> None:
    """Accepting incomplete decisions could silently replace a prior normalized artifact."""

    from _proof_normalizer import normalize_files

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        semantic_path = root / "semantic.json"
        decisions_path = root / "decisions.json"
        normalized_path = root / "normalized.json"
        provenance_path = root / "provenance.json"
        semantic_path.write_text(json.dumps(_semantic_ir()), encoding="utf-8")
        decisions = _decisions()
        decisions["decisions"].pop()
        decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
        normalized_path.write_text('{"previous":"normalized"}\n', encoding="utf-8")
        provenance_path.write_text('{"previous":"provenance"}\n', encoding="utf-8")

        with pytest.raises(ValueError, match="unaccounted proof"):
            normalize_files(
                semantic_path,
                decisions_path,
                normalized_path,
                provenance_path,
            )

        assert normalized_path.read_text(encoding="utf-8") == '{"previous":"normalized"}\n'
        assert provenance_path.read_text(encoding="utf-8") == '{"previous":"provenance"}\n'


def test_normalized_profile_rejects_transitional_records() -> None:
    """Letting proof records reach compilation would leak temporary entities."""

    from _proof_normalizer import validate_normalized_semantic_profile

    with pytest.raises(ValueError, match="transitional proof entity"):
        validate_normalized_semantic_profile(_semantic_ir())


def test_compiler_applies_normalized_profile_before_inventory_reconciliation() -> None:
    """A compiler path must reject proof IR even when its broad schema permits it."""

    from _semantic_graph_compiler import validate_semantic_payload

    with pytest.raises(ValueError, match="normalized semantic profile rejects transitional proof entity"):
        validate_semantic_payload(_semantic_ir(), {})


def test_normalizer_rejects_self_edges_before_writing_outputs() -> None:
    """A self dependency must fail instead of surviving proof normalization."""

    from _proof_normalizer import normalize_proof_entities

    payload = _semantic_ir()
    payload["relationships"].append(
        {
            "from": "assumption-a",
            "to": "assumption-a",
            "type": "supports",
            "description": "An invalid self dependency.",
            "hint_ids": ["pool::h6"],
            "evidence_ids": ["pool::e6"],
            "implicit": False,
            "confidence": "High",
        }
    )

    with pytest.raises(ValueError, match="self-edge"):
        normalize_proof_entities(payload, _decisions())

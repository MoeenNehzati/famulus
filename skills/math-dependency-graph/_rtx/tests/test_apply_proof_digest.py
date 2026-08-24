"""Behavioral tests for deterministic proof-digest application."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


RUNTIME_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = RUNTIME_DIR.parent
REPO_SRC = RUNTIME_DIR.parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RUNTIME_DIR))
sys.path.insert(0, str(Path(__file__).parent))


def _input_identity() -> dict:
    return {
        "semantic_ir_sha256": "0" * 64,
        "inventory_sha256": "1" * 64,
        "source_sha256": "2" * 64,
        "packet_payload_sha256": "3" * 64,
    }


def _semantic_ir() -> dict:
    return {
        "ir_version": 2,
        "files": ["assumptions.tex", "results.tex", "proofs.tex"],
        "document": {"source_file": "main.tex"},
        "inventory": {"candidate_ids": [], "candidate_count": 0},
        "entities": [
            {
                "candidate_ids": [],
                "id": "assumption-a",
                "type": "assumption",
                "statement_location": [0, 1, 1],
                "short_title": "A",
                "description": "Assumption A.",
                "source": "explicit",
            },
            {
                "candidate_ids": [],
                "id": "result-r",
                "type": "result",
                "statement_location": [0, 2, 2],
                "short_title": "R",
                "description": "Result R.",
                "source": "explicit",
            },
            {
                "candidate_ids": [],
                "id": "proof-formal",
                "type": "proof",
                "kind": "formal",
                "statement_location": [0, 3, 3],
                "short_title": "Proof of R",
                "description": "A formal argument for R.",
                "source": "explicit",
            },
            {
                "candidate_ids": [],
                "id": "proof-sketch",
                "type": "proof",
                "kind": "sketch",
                "statement_location": [0, 4, 4],
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
        "input_identity": _input_identity(),
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


def _compiler_proof_fixture(*, target_from_unresolved: bool = False) -> tuple[dict, dict, dict]:
    """Return a compiler-valid pooled inventory, proof IR, and one decision."""

    from _semantic_test_data import pooled_inventory, semantic_graph

    inventory = pooled_inventory()
    semantic = semantic_graph()
    inventory["candidates"].append(
        {
            "id": "candidate-proof",
            "location": [2, 82, 84],
            "environment": "proof",
            "provenance": "explicit",
            "type_hint": "proof",
            "evidence_ids": ["inventory-001::e3"],
            "summary": "A proof of existence.",
        }
    )
    inventory["relationship_hints"][0]["to"] = {"candidate_id": "candidate-proof"}
    inventory["relationship_hints"].append(
        {
            "id": "inventory-001::h2",
            "from": {"candidate_id": "candidate-proof"},
            "to": {"candidate_id": "candidate-theorem"},
            "type": "proves",
            "basis": "explicit-prose",
            "assertion": "explicit",
            "evidence_ids": ["inventory-001::e3"],
            "confidence": "Verified",
        }
    )
    semantic["inventory"]["candidate_ids"].append("candidate-proof")
    semantic["inventory"]["candidate_count"] += 1
    semantic["entities"].append(
        {
            "candidate_ids": ["candidate-proof"],
            "id": "proof-existence",
            "type": "proof",
            "kind": "formal",
            "statement_location": [2, 82, 84],
            "short_title": "Proof",
            "description": "A proof of existence.",
            "source": "explicit",
        }
    )
    semantic["relationships"] = [
        {**semantic["relationships"][0], "to": "proof-existence"},
        {
            "from": "proof-existence",
            "to": "existence",
            "type": "proves",
            "description": "The proof proves existence.",
            "hint_ids": ["inventory-001::h2"],
            "evidence_ids": ["inventory-001::e3"],
            "implicit": False,
            "confidence": "Verified",
        },
    ]
    if target_from_unresolved:
        inventory["candidates"] = [
            item for item in inventory["candidates"] if item["id"] != "candidate-theorem"
        ]
        inventory["unresolved_entities"] = [
            {
                "key": "inventory-001::u1",
                "title": "Existence",
                "statement": "An admissible object exists.",
                "resolution_kind": "implicit-entity",
                "type_hint": "result",
                "evidence_ids": ["inventory-001::e2"],
            }
        ]
        inventory["relationship_hints"][1]["to"] = {"unresolved_key": "inventory-001::u1"}
        semantic["inventory"]["candidate_ids"].remove("candidate-theorem")
        semantic["inventory"]["candidate_count"] -= 1
        semantic["entities"][1]["candidate_ids"] = []
        semantic["entities"][1]["source"] = "inferred"
        semantic["unresolved_resolutions"] = [
            {
                "unresolved_id": "inventory-001::u1",
                "disposition": "created",
                "entity_id": "existence",
            }
        ]
    decisions = {
        "document_kind": "proof-normalization-decisions",
        "ir_version": 1,
        "input_identity": _input_identity(),
        "decisions": [
            {
                "proof_id": "proof-existence",
                "disposition": "accepted",
                "bundle_id": "bundle-existence",
                "target_id": "existence",
                "reason": "One proof.",
            }
        ],
    }
    return semantic, decisions, inventory


def test_normalizer_redirects_and_deduplicates_proof_dependencies() -> None:
    """Dropping a proof route loses its evidence or leaves proof IR in canonical input."""

    from _semantic_pipeline._apply_proof_digest import normalize_proof_entities

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


def test_normalizer_rejects_unaccounted_proof_without_replacing_outputs(
    tmp_path: Path,
) -> None:
    """Accepting incomplete decisions could silently replace a prior normalized artifact."""

    from _semantic_pipeline._apply_proof_digest import normalize_files

    semantic_path = tmp_path / "semantic.json"
    decisions_path = tmp_path / "decisions.json"
    normalized_path = tmp_path / "normalized.json"
    provenance_path = tmp_path / "provenance.json"
    inventory_path = tmp_path / "inventory.json"
    inventory_out_path = tmp_path / "inventory-out.json"
    semantic, decisions, inventory = _compiler_proof_fixture()
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    decisions["decisions"].pop()
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    normalized_path.write_text('{"previous":"normalized"}\n', encoding="utf-8")
    provenance_path.write_text('{"previous":"provenance"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unaccounted proof"):
        normalize_files(
            semantic_path,
            decisions_path,
            normalized_path,
            provenance_path,
            inventory_path,
            inventory_out_path,
        )

    assert normalized_path.read_text(encoding="utf-8") == '{"previous":"normalized"}\n'
    assert provenance_path.read_text(encoding="utf-8") == '{"previous":"provenance"}\n'


def test_normalized_profile_rejects_transitional_records() -> None:
    """Letting proof records reach compilation would leak temporary entities."""

    from _semantic_pipeline._apply_proof_digest import validate_normalized_semantic_profile

    with pytest.raises(ValueError, match="transitional proof entity"):
        validate_normalized_semantic_profile(_semantic_ir())


def test_normalized_inventory_projection_compiles_proof_free_ir(
    canonical_base_payload: dict[str, object],
) -> None:
    """Leaving proof candidates and hint endpoints in compiler inventory breaks compilation."""

    from _semantic_pipeline._apply_proof_digest import normalize_with_inventory
    from _semantic_pipeline._to_canonical_json import compile_semantic_graph
    from _semantic_test_data import pooled_inventory, semantic_graph

    inventory = pooled_inventory()
    inventory["candidates"].append(
        {
            "id": "candidate-proof",
            "location": [2, 82, 84],
            "environment": "proof",
            "provenance": "explicit",
            "type_hint": "proof",
            "evidence_ids": ["inventory-001::e3"],
            "summary": "A proof of existence.",
        }
    )
    inventory["relationship_hints"][0]["to"] = {"candidate_id": "candidate-proof"}
    inventory["relationship_hints"].append(
        {
            "id": "inventory-001::h2",
            "from": {"candidate_id": "candidate-proof"},
            "to": {"candidate_id": "candidate-theorem"},
            "type": "proves",
            "basis": "explicit-prose",
            "assertion": "explicit",
            "evidence_ids": ["inventory-001::e3"],
            "confidence": "Verified",
        }
    )
    semantic = semantic_graph()
    semantic["inventory"]["candidate_ids"].append("candidate-proof")
    semantic["inventory"]["candidate_count"] += 1
    semantic["entities"].append(
        {
                "candidate_ids": ["candidate-proof"], "id": "proof-existence",
                "type": "proof", "kind": "formal", "short_title": "Proof",
                "statement_location": [2, 82, 84],
                "description": "A proof of existence.", "source": "explicit",
        }
    )
    semantic["relationships"] = [
        {**semantic["relationships"][0], "to": "proof-existence"},
        {"from": "proof-existence", "to": "existence", "type": "proves",
         "description": "The proof proves existence.", "hint_ids": ["inventory-001::h2"],
         "evidence_ids": ["inventory-001::e3"], "implicit": False, "confidence": "Verified"},
    ]
    decisions = {"document_kind": "proof-normalization-decisions", "ir_version": 1,
                 "input_identity": _input_identity(),
                 "decisions": [{"proof_id": "proof-existence", "disposition": "accepted",
                 "bundle_id": "bundle-existence", "target_id": "existence", "reason": "One proof."}]}

    normalized, _, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert "candidate-proof" not in {item["id"] for item in projected["candidates"]}
    assert compile_semantic_graph(normalized, canonical_base_payload, projected)[
        "entities"
    ]


def test_candidate_free_created_result_target_projects_inventory_and_compiles(
    canonical_base_payload: dict[str, object],
) -> None:
    """Rejecting a valid created target or leaving a proof endpoint breaks compilation."""

    from _semantic_pipeline._apply_proof_digest import normalize_with_inventory
    from _semantic_pipeline._to_canonical_json import compile_semantic_graph

    semantic, decisions, inventory = _compiler_proof_fixture(target_from_unresolved=True)
    normalized, report, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert projected["relationship_hints"] == [
        {
            **inventory["relationship_hints"][0],
            "to": {"unresolved_key": "inventory-001::u1"},
        }
    ]
    assert report["compiler_inventory"]["projected_candidate_ids"] == [
        "candidate-definition",
        "candidate-remark",
    ]
    assert compile_semantic_graph(normalized, canonical_base_payload, projected)[
        "entities"
    ]


def test_excluded_proof_removes_incident_hints_and_accounts_relationships() -> None:
    """An excluded proof must not leave dangling compiler hints or unaudited edges."""

    from _semantic_pipeline._apply_proof_digest import normalize_with_inventory

    semantic, decisions, inventory = _compiler_proof_fixture()
    semantic["edgeless_justification"] = (
        "The two retained statements have no direct source-grounded dependency once the prose is excluded."
    )
    decisions["decisions"][0] = {
        "proof_id": "proof-existence",
        "disposition": "excluded",
        "reason": "The passage is only motivational prose.",
    }
    normalized, report, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert normalized["relationships"] == []
    assert projected["relationship_hints"] == []
    exclusion = report["exclusions"][0]
    assert [item["type"] for item in exclusion["incident_relationships"]] == ["supports", "proves"]
    assert report["compiler_inventory"]["removed_proof_hint_ids"] == [
        "inventory-001::h1",
        "inventory-001::h2",
    ]


@pytest.mark.parametrize("alias_pair", [(0, 1), (0, 2), (1, 2)])
def test_three_output_publication_rejects_every_destination_alias(
    alias_pair: tuple[int, int], tmp_path: Path
) -> None:
    """Aliasing any output could overwrite one validated artifact with another."""

    from _semantic_pipeline._apply_proof_digest import write_normalized_outputs_atomic

    destinations = [
        tmp_path / "normalized.json",
        tmp_path / "provenance.json",
        tmp_path / "inventory.json",
    ]
    destinations[alias_pair[1]] = destinations[alias_pair[0]]
    with pytest.raises(ValueError, match="destinations must differ"):
        write_normalized_outputs_atomic(
            {}, {}, destinations[0], destinations[1], {}, destinations[2]
        )


@pytest.mark.parametrize("failed_destination", [1, 2])
def test_three_output_transaction_restores_all_existing_files_on_late_failure(
    monkeypatch: pytest.MonkeyPatch, failed_destination: int, tmp_path: Path
) -> None:
    """Failure replacing output two or three must restore every earlier destination."""

    from _semantic_pipeline import _apply_proof_digest as normalizer

    destinations = [
        tmp_path / "normalized.json",
        tmp_path / "provenance.json",
        tmp_path / "inventory.json",
    ]
    originals = [b'{"old":1}\n', b'{"old":2}\n', b'{"old":3}\n']
    for path, content in zip(destinations, originals, strict=True):
        path.write_bytes(content)
    real_replace = normalizer.os.replace
    failed = False

    def fail_once(source: object, destination: object) -> None:
        nonlocal failed
        if (
            not failed
            and Path(destination).resolve()
            == destinations[failed_destination].resolve()
        ):
            failed = True
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(normalizer.os, "replace", fail_once)
    with pytest.raises(OSError, match="injected"):
        normalizer.write_normalized_outputs_atomic(
            {"new": 1},
            {"new": 2},
            destinations[0],
            destinations[1],
            {"new": 3},
            destinations[2],
        )

    assert [path.read_bytes() for path in destinations] == originals


def test_alternative_bundles_remain_separate_while_endpoint_dependencies_union() -> None:
    """Collapsing same-target alternatives into one bundle loses proof-route semantics."""

    from _semantic_pipeline._apply_proof_digest import normalize_proof_entities

    decisions = _decisions()
    decisions["decisions"][1]["bundle_id"] = "bundle-r-alternative"
    decisions["decisions"][1]["reason"] = "The sketch is a materially different proof."
    normalized, report = normalize_proof_entities(_semantic_ir(), decisions)

    assert len(normalized["relationships"]) == 1
    assert normalized["relationships"][0]["evidence_ids"] == ["pool::e1", "pool::e2", "pool::e5"]
    assert [item["bundle_id"] for item in report["bundles"]] == [
        "bundle-r-main",
        "bundle-r-alternative",
    ]
    assert report["relationships"][0]["bundle_ids"] == [
        "bundle-r-main",
        "bundle-r-alternative",
    ]


def test_proof_free_input_is_an_exact_semantic_identity() -> None:
    """The normalization pass must leave every proof-free semantic collection untouched."""

    from _semantic_pipeline._apply_proof_digest import normalize_proof_entities

    semantic = _semantic_ir()
    semantic["entities"] = semantic["entities"][:2]
    semantic["relationships"] = semantic["relationships"][-1:]
    semantic["exclusions"] = [{"candidate_id": "pool::n9", "reason": "Irrelevant prose."}]
    semantic["unresolved_resolutions"] = [
        {"unresolved_id": "pool::u1", "disposition": "rejected", "reason": "No entity."}
    ]
    semantic["hint_decisions"] = [
        {"hint_id": "pool::h9", "decision": "rejected", "reason": "Not direct."}
    ]
    semantic["reference_decisions"] = [
        {
            "reference_id": "pool::r9",
            "decision": "navigation",
            "evidence_ids": ["pool::e9"],
        }
    ]
    semantic["gap_decisions"] = [
        {"gap_id": "pool::g9", "disposition": "rejected", "reason": "Resolved by inspection."}
    ]
    semantic["gaps"] = [
        {
            "id": "remaining-gap",
            "category": "evidence",
            "evidence_ids": ["pool::e9"],
            "description": "One source ambiguity remains.",
        }
    ]
    expected = deepcopy(semantic)

    normalized, report = normalize_proof_entities(
        semantic,
        {"document_kind": "proof-normalization-decisions", "ir_version": 1,
         "input_identity": _input_identity(), "decisions": []},
    )

    assert normalized == expected
    assert report["proof_entities"] == []

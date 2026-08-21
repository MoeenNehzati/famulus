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
sys.path.insert(0, str(Path(__file__).parent))


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


def _compiler_proof_fixture(*, target_from_unresolved: bool = False) -> tuple[dict, dict, dict]:
    """Return a compiler-valid pooled inventory, proof IR, and one decision."""

    from test_semantic_graph_compiler import pooled_inventory, semantic_graph

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
        inventory_path = root / "inventory.json"
        inventory_out_path = root / "inventory-out.json"
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


def test_normalized_inventory_projection_compiles_proof_free_ir() -> None:
    """Leaving proof candidates and hint endpoints in compiler inventory breaks compilation."""

    from _proof_normalizer import normalize_with_inventory
    from _semantic_graph_compiler import compile_semantic_graph
    from _graph_builder import load_base_payload
    from test_semantic_graph_compiler import pooled_inventory, semantic_graph

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
                 "decisions": [{"proof_id": "proof-existence", "disposition": "accepted",
                 "bundle_id": "bundle-existence", "target_id": "existence", "reason": "One proof."}]}

    normalized, _, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert "candidate-proof" not in {item["id"] for item in projected["candidates"]}
    assert compile_semantic_graph(normalized, load_base_payload(), projected)["entities"]


def test_normalizer_rejects_nonresult_proof_target() -> None:
    """Treating assumptions or setup as proved results would violate target eligibility."""
    from _proof_normalizer import normalize_proof_entities
    payload = _semantic_ir()
    payload["relationships"][2]["to"] = "assumption-a"
    decisions = _decisions()
    decisions["decisions"][0]["target_id"] = "assumption-a"
    with pytest.raises(ValueError, match="eligible result"):
        normalize_proof_entities(payload, decisions)


def test_normalizer_allows_excluded_proof_with_proves_in_sidecar() -> None:
    from _proof_normalizer import normalize_proof_entities
    decisions = _decisions()
    decisions["decisions"][1] = {"proof_id": "proof-sketch", "disposition": "excluded", "reason": "Irrelevant."}
    payload = _semantic_ir()
    normalized, report = normalize_proof_entities(payload, decisions)
    assert "proof-sketch" not in {item["id"] for item in normalized["entities"]}
    assert report["exclusions"][0]["incident_relationships"]


def test_candidate_free_created_result_target_projects_inventory_and_compiles() -> None:
    """Rejecting a valid created target or leaving a proof endpoint breaks compilation."""

    from _graph_builder import load_base_payload
    from _proof_normalizer import normalize_with_inventory
    from _semantic_graph_compiler import compile_semantic_graph

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
    assert compile_semantic_graph(normalized, load_base_payload(), projected)["entities"]


def test_excluded_proof_removes_incident_hints_and_accounts_relationships() -> None:
    """An excluded proof must not leave dangling compiler hints or unaudited edges."""

    from _proof_normalizer import normalize_with_inventory

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


def test_excluded_proof_removes_prior_decision_for_incident_dependency_hint() -> None:
    """Removing an excluded-proof hint must also remove its now-orphaned decision."""

    from _proof_normalizer import normalize_with_inventory

    semantic, decisions, inventory = _compiler_proof_fixture()
    semantic["edgeless_justification"] = (
        "The two retained statements have no direct source-grounded dependency once the prose is excluded."
    )
    semantic["relationships"][0]["hint_ids"] = []
    semantic["hint_decisions"] = [
        {
            "hint_id": "inventory-001::h1",
            "decision": "rejected",
            "reason": "The apparent dependency belongs only to irrelevant prose.",
        }
    ]
    decisions["decisions"][0] = {
        "proof_id": "proof-existence",
        "disposition": "excluded",
        "reason": "The passage is only motivational prose.",
    }

    normalized, report, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert normalized["hint_decisions"] == []
    assert projected["relationship_hints"] == []
    assert report["compiler_inventory"]["removed_proof_hint_ids"] == [
        "inventory-001::h1",
        "inventory-001::h2",
    ]


def test_provenance_schema_strictly_validates_proof_entity_provenance() -> None:
    """A report must preserve complete, schema-checked proof entity provenance."""

    schema = json.loads((SKILL_DIR / "proof-normalization.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    from _proof_normalizer import normalize_proof_entities

    _, report = normalize_proof_entities(_semantic_ir(), _decisions())
    validator.validate(report)

    missing_candidates = deepcopy(report)
    del missing_candidates["proof_entities"][0]["candidate_ids"]
    with pytest.raises(ValidationError):
        validator.validate(missing_candidates)
    unknown_provenance = deepcopy(report)
    unknown_provenance["proof_entities"][0]["invented"] = True
    with pytest.raises(ValidationError):
        validator.validate(unknown_provenance)


def test_final_inventory_provenance_validates_against_schema() -> None:
    """Malformed compiler-inventory accounting must not pass the report contract."""

    schema = json.loads((SKILL_DIR / "proof-normalization.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    from _proof_normalizer import normalize_with_inventory

    semantic, decisions, inventory = _compiler_proof_fixture()
    _, report, _ = normalize_with_inventory(semantic, decisions, inventory)
    validator.validate(report)

    malformed = deepcopy(report)
    del malformed["compiler_inventory"]["projected_candidate_ids"]
    with pytest.raises(ValidationError):
        validator.validate(malformed)


def test_normalize_files_requires_inventory_pair() -> None:
    """File normalization without compiler inventory cannot publish a compile-ready result."""

    from _proof_normalizer import normalize_files

    with pytest.raises(TypeError, match="inventory_path"):
        normalize_files(Path("semantic"), Path("decisions"), Path("normalized"), Path("provenance"))


@pytest.mark.parametrize("alias_pair", [(0, 1), (0, 2), (1, 2)])
def test_three_output_publication_rejects_every_destination_alias(alias_pair: tuple[int, int]) -> None:
    """Aliasing any output could overwrite one validated artifact with another."""

    from _proof_normalizer import write_normalized_outputs_atomic

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        destinations = [root / "normalized.json", root / "provenance.json", root / "inventory.json"]
        destinations[alias_pair[1]] = destinations[alias_pair[0]]
        with pytest.raises(ValueError, match="destinations must differ"):
            write_normalized_outputs_atomic(
                {}, {}, destinations[0], destinations[1], {}, destinations[2]
            )


@pytest.mark.parametrize("failed_destination", [1, 2])
def test_three_output_transaction_restores_all_existing_files_on_late_failure(
    monkeypatch: pytest.MonkeyPatch, failed_destination: int
) -> None:
    """Failure replacing output two or three must restore every earlier destination."""

    import _proof_normalizer as normalizer

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        destinations = [root / "normalized.json", root / "provenance.json", root / "inventory.json"]
        originals = [b'{"old":1}\n', b'{"old":2}\n', b'{"old":3}\n']
        for path, content in zip(destinations, originals, strict=True):
            path.write_bytes(content)
        real_replace = normalizer.os.replace
        failed = False

        def fail_once(source: object, destination: object) -> None:
            nonlocal failed
            if not failed and Path(destination).resolve() == destinations[failed_destination].resolve():
                failed = True
                raise OSError("injected replacement failure")
            real_replace(source, destination)

        monkeypatch.setattr(normalizer.os, "replace", fail_once)
        with pytest.raises(OSError, match="injected"):
            normalizer.write_normalized_outputs_atomic(
                {"new": 1}, {"new": 2}, destinations[0], destinations[1], {"new": 3}, destinations[2]
            )

        assert [path.read_bytes() for path in destinations] == originals


def test_alternative_bundles_remain_separate_while_endpoint_dependencies_union() -> None:
    """Collapsing same-target alternatives into one bundle loses proof-route semantics."""

    from _proof_normalizer import normalize_proof_entities

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


def test_normalizer_preserves_every_nonproof_entity_exactly() -> None:
    """Proof collapsing must not rewrite unrelated entity identity or metadata."""

    from _proof_normalizer import normalize_proof_entities

    semantic = _semantic_ir()
    semantic["entities"][0]["category_label"] = "Standing assumption"
    semantic["entities"][1]["title"] = "Full result title"
    expected = deepcopy(semantic["entities"][:2])

    normalized, _ = normalize_proof_entities(semantic, _decisions())

    assert normalized["entities"] == expected


def test_normalizer_fails_closed_when_proof_removal_would_invent_edgeless_prose() -> None:
    """Runtime must not synthesize a source-grounded justification absent from extraction."""

    from _proof_normalizer import normalize_with_inventory

    semantic, decisions, inventory = _compiler_proof_fixture()
    decisions["decisions"][0] = {
        "proof_id": "proof-existence",
        "disposition": "excluded",
        "reason": "The passage is only motivational prose.",
    }

    with pytest.raises(ValueError, match="preexisting source-grounded edgeless_justification"):
        normalize_with_inventory(semantic, decisions, inventory)


def test_accepted_proves_reference_is_removed_while_surviving_reference_is_retained() -> None:
    """A proves-only reference must disappear without deleting a redirected dependency reference."""

    from _graph_builder import load_base_payload
    from _proof_normalizer import normalize_with_inventory
    from _semantic_graph_compiler import compile_semantic_graph

    semantic, decisions, inventory = _compiler_proof_fixture()
    inventory["references"] = [
        {"id": "inventory-001::r1", "location": [2, 82, 84], "raw": "A", "kind": "label"},
        {"id": "inventory-001::r2", "location": [2, 82, 84], "raw": "R", "kind": "label"},
    ]
    inventory["relationship_hints"][0]["reference_ids"] = ["inventory-001::r1"]
    inventory["relationship_hints"][1]["reference_ids"] = ["inventory-001::r2"]
    semantic["relationships"][0]["reference_ids"] = ["inventory-001::r1"]
    semantic["relationships"][1]["reference_ids"] = ["inventory-001::r2"]

    normalized, report, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert [item["id"] for item in projected["references"]] == ["inventory-001::r1"]
    assert normalized["relationships"][0]["reference_ids"] == ["inventory-001::r1"]
    assert report["compiler_inventory"]["removed_proof_reference_ids"] == ["inventory-001::r2"]
    assert compile_semantic_graph(normalized, load_base_payload(), projected)["entities"]


def test_excluded_proof_removes_all_incident_references_and_still_compiles() -> None:
    """References carried only by excluded proof relationships must be projected out."""

    from _graph_builder import load_base_payload
    from _proof_normalizer import normalize_with_inventory
    from _semantic_graph_compiler import compile_semantic_graph

    semantic, decisions, inventory = _compiler_proof_fixture()
    semantic["edgeless_justification"] = (
        "The two retained statements have no direct source-grounded dependency once the prose is excluded."
    )
    inventory["references"] = [
        {"id": "inventory-001::r1", "location": [2, 82, 84], "raw": "A", "kind": "label"},
        {"id": "inventory-001::r2", "location": [2, 82, 84], "raw": "R", "kind": "label"},
    ]
    inventory["relationship_hints"][0]["reference_ids"] = ["inventory-001::r1"]
    inventory["relationship_hints"][1]["reference_ids"] = ["inventory-001::r2"]
    semantic["relationships"][0]["reference_ids"] = ["inventory-001::r1"]
    semantic["relationships"][1]["reference_ids"] = ["inventory-001::r2"]
    decisions["decisions"][0] = {
        "proof_id": "proof-existence",
        "disposition": "excluded",
        "reason": "The passage is only motivational prose.",
    }

    normalized, report, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert projected["references"] == []
    assert report["compiler_inventory"]["removed_proof_reference_ids"] == [
        "inventory-001::r1",
        "inventory-001::r2",
    ]
    assert compile_semantic_graph(normalized, load_base_payload(), projected)["entities"]


def test_removed_proof_hint_also_removes_matching_reference_decision() -> None:
    """A decision for a reference owned only by a removed proof hint must not become orphaned."""

    from _graph_builder import load_base_payload
    from _proof_normalizer import normalize_with_inventory
    from _semantic_graph_compiler import compile_semantic_graph

    semantic, decisions, inventory = _compiler_proof_fixture()
    inventory["references"] = [
        {"id": "inventory-001::r2", "location": [2, 82, 84], "raw": "R", "kind": "label"}
    ]
    inventory["relationship_hints"][1]["reference_ids"] = ["inventory-001::r2"]
    semantic["reference_decisions"] = [
        {
            "reference_id": "inventory-001::r2",
            "decision": "non-dependency",
            "evidence_ids": ["inventory-001::e3"],
            "reason": "The reference establishes proof ownership rather than a canonical dependency.",
        }
    ]

    normalized, report, projected = normalize_with_inventory(semantic, decisions, inventory)

    assert normalized["reference_decisions"] == []
    assert projected["references"] == []
    assert report["compiler_inventory"]["removed_proof_reference_ids"] == ["inventory-001::r2"]
    assert compile_semantic_graph(normalized, load_base_payload(), projected)["entities"]


def test_proof_free_input_is_an_exact_semantic_identity() -> None:
    """The normalization pass must leave every proof-free semantic collection untouched."""

    from _proof_normalizer import normalize_proof_entities

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
        {"document_kind": "proof-normalization-decisions", "ir_version": 1, "decisions": []},
    )

    assert normalized == expected
    assert report["proof_entities"] == []

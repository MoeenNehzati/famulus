"""Focused tests for semantic IR reconciliation and keyed repair."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

from _semantic_pipeline import _ir_validator as semantic  # noqa: E402

def _reconciliation_inventory() -> dict:
    """Return one pooled inventory covering every extract reconciliation route."""

    return {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["section.tex"],
        "evidence": [
            {"id": "inventory-001::e1", "location": [0, 10, 12], "role": "statement"},
            {"id": "inventory-001::e2", "location": [0, 20, 22], "role": "proof-use"},
            {
                "id": "inventory-001::e3",
                "location": [0, 30, 30],
                "role": "explicit-reference",
            },
        ],
        "references": [
            {
                "id": f"inventory-001::r{index}",
                "location": [0, 30 + index, 30 + index],
                "locator": {"label": f"ref:{index}"},
            }
            for index in range(1, 4)
        ],
        "candidates": [
            {
                "id": "section.tex:10",
                "location": [0, 10, 12],
                "provenance": "explicit",
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
                "summary": "A reusable definition.",
            },
            {
                "id": "section.tex:20",
                "location": [0, 20, 22],
                "provenance": "explicit",
                "type_hint": "result",
                "evidence_ids": ["inventory-001::e2"],
                "summary": "A result using the definition.",
            },
            {
                "id": "section.tex:40",
                "location": [0, 40, 40],
                "provenance": "explicit",
                "type_hint": "exposition",
                "evidence_ids": ["inventory-001::e3"],
                "summary": "A navigation remark excluded from the graph.",
            },
        ],
        "unresolved_entities": [
            {
                "key": "inventory-001::u1",
                "title": "Definition alias",
                "statement": "A remote name for the definition.",
                "resolution_kind": "remote-label",
                "locators": [{"label": "def:object"}],
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
            },
            {
                "key": "inventory-001::u2",
                "title": "Spurious name",
                "statement": "A name that is not a graph entity.",
                "resolution_kind": "named-entity",
                "locators": [{"name": "spurious"}],
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e3"],
            },
        ],
        "relationship_hints": [
            {
                "id": "inventory-001::h1",
                "from": {"unresolved_key": "inventory-001::u1"},
                "to": {"candidate_id": "section.tex:20"},
                "type": "supports",
                "basis": "explicit-reference",
                "assertion": "explicit",
                "reference_ids": ["inventory-001::r1"],
                "evidence_ids": ["inventory-001::e2"],
                "confidence": "Verified",
            },
            {
                "id": "inventory-001::h2",
                "from": {"candidate_id": "section.tex:20"},
                "to": {"candidate_id": "section.tex:40"},
                "type": "supports",
                "basis": "mathematical-inference",
                "assertion": "inferred",
                "evidence_ids": ["inventory-001::e3"],
                "confidence": "Medium",
                "reason": "The remark resembles a consequence.",
            },
        ],
        "reference_decisions": [],
        "gaps": [
            {
                "id": "inventory-001::g1",
                "category": "reference",
                "reference_id": "inventory-001::r3",
                "evidence_ids": ["inventory-001::e3"],
                "description": "The explicit reference could not be resolved in its chunk.",
            }
        ],
    }


def _semantic_extract() -> dict:
    """Return one schema-v2 extract partitioning every inventory handle."""

    return {
        "ir_version": 2,
        "files": ["section.tex"],
        "document": {"title": "Example", "source_file": "main.tex"},
        "inventory": {
            "candidate_ids": ["section.tex:10", "section.tex:20", "section.tex:40"],
            "candidate_count": 3,
        },
        "entities": [
            {
                "candidate_ids": ["section.tex:10"],
                "id": "definition",
                "type": "setup",
                "kind": "definition",
                "statement_location": [0, 10, 12],
                "short_title": "Definition",
                "description": "A reusable definition.",
                "source": "explicit",
            },
            {
                "candidate_ids": ["section.tex:20"],
                "id": "result",
                "type": "result",
                "kind": "theorem",
                "statement_location": [0, 20, 22],
                "short_title": "Result",
                "description": "The definition yields the result.",
                "source": "explicit",
            },
        ],
        "exclusions": [
            {"candidate_id": "section.tex:40", "reason": "Navigation only."}
        ],
        "unresolved_resolutions": [
            {
                "unresolved_id": "inventory-001::u1",
                "disposition": "matched",
                "entity_id": "definition",
            },
            {
                "unresolved_id": "inventory-001::u2",
                "disposition": "rejected",
                "reason": "The name does not denote a reusable mathematical object.",
            },
        ],
        "relationships": [
            {
                "from": "definition",
                "to": "result",
                "type": "supports",
                "description": "The result directly uses the definition.",
                "hint_ids": ["inventory-001::h1"],
                "evidence_ids": ["inventory-001::e2"],
                "reference_ids": ["inventory-001::r1"],
                "implicit": False,
                "confidence": "Verified",
            }
        ],
        "hint_decisions": [
            {
                "hint_id": "inventory-001::h2",
                "decision": "rejected",
                "reason": "The remark is not a mathematical consequence.",
            }
        ],
        "reference_decisions": [
            {
                "reference_id": "inventory-001::r2",
                "decision": "navigation",
                "evidence_ids": ["inventory-001::e3"],
            }
        ],
        "gap_decisions": [],
        "gaps": [
            {
                "id": "gap-reference",
                "category": "reference",
                "reference_id": "inventory-001::r3",
                "evidence_ids": ["inventory-001::e3"],
                "inventory_gap_ids": ["inventory-001::g1"],
                "description": "The bounded source does not resolve this reference.",
            }
        ],
    }


def _empty_semantic_repair() -> dict:
    """Return one schema-v2 repair that intentionally changes no keyed records."""

    return {
        "repair_version": 2,
        "remove_entity_ids": [],
        "upsert_entities": [],
        "remove_exclusion_candidate_ids": [],
        "upsert_exclusions": [],
        "remove_unresolved_ids": [],
        "upsert_unresolved_resolutions": [],
        "remove_relationships": [],
        "upsert_relationships": [],
        "remove_hint_ids": [],
        "upsert_hint_decisions": [],
        "remove_reference_ids": [],
        "upsert_reference_decisions": [],
        "remove_gap_ids": [],
        "upsert_gaps": [],
        "remove_inventory_gap_ids": [],
        "upsert_gap_decisions": [],
    }


def test_extract_reconciliation_accepts_exact_whole_document_partitions() -> None:
    """The complete candidate/u/h/r partition is the valid handoff to compilation."""

    semantic.validate_extract_reconciliation(
        _semantic_extract(), _reconciliation_inventory()
    )


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["exclusions"].clear(),
        "unreconciled candidate.*section.tex:40",
    ),
    (
        lambda payload: payload["exclusions"].append(
            {"candidate_id": "section.tex:10", "reason": "duplicate"}
        ),
        "candidate reconciled more than once.*section.tex:10",
    ),
    (
        lambda payload: payload["exclusions"].append(
            {"candidate_id": "section.tex:99", "reason": "unknown"}
        ),
        "unknown candidate.*section.tex:99",
    ),
])
def test_extract_reconciliation_rejects_inexact_candidate_partition(
    mutation, expected: str
) -> None:
    """Every inventory candidate has exactly one known entity/exclusion outcome."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["unresolved_resolutions"].pop(),
        "unreconciled unresolved handle.*inventory-001::u2",
    ),
    (
        lambda payload: payload["unresolved_resolutions"].append(
            deepcopy(payload["unresolved_resolutions"][0])
        ),
        "unresolved handle reconciled more than once.*inventory-001::u1",
    ),
    (
        lambda payload: payload["unresolved_resolutions"].append(
            {
                "unresolved_id": "inventory-001::u99",
                "disposition": "rejected",
                "reason": "unknown",
            }
        ),
        "unknown unresolved handle.*inventory-001::u99",
    ),
])
def test_extract_reconciliation_rejects_inexact_unresolved_partition(
    mutation, expected: str
) -> None:
    """Qualified unresolved handles cannot disappear, duplicate, or appear de novo."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("disposition", ["matched", "created"])
def test_extract_schema_requires_entity_for_retained_unresolved_handle(
    disposition: str,
) -> None:
    """A retained unresolved record without an entity cannot preserve its endpoint."""

    payload = _semantic_extract()
    payload["unresolved_resolutions"][0] = {
        "unresolved_id": "inventory-001::u1",
        "disposition": disposition,
    }

    with pytest.raises(ValueError, match="entity_id.*required"):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["hint_decisions"].clear(),
        "unreconciled hint handle.*inventory-001::h2",
    ),
    (
        lambda payload: payload["hint_decisions"].append(
            {
                "hint_id": "inventory-001::h1",
                "decision": "rejected",
                "reason": "duplicate",
            }
        ),
        "hint handle reconciled more than once.*inventory-001::h1",
    ),
    (
        lambda payload: payload["hint_decisions"].append(
            {
                "hint_id": "inventory-001::h99",
                "decision": "rejected",
                "reason": "unknown",
            }
        ),
        "unknown hint handle.*inventory-001::h99",
    ),
])
def test_extract_reconciliation_rejects_inexact_hint_partition(
    mutation, expected: str
) -> None:
    """Each qualified relationship hint becomes one edge input or one decision."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["gaps"].clear(),
        "unreconciled reference handle.*inventory-001::r3",
    ),
    (
        lambda payload: payload["reference_decisions"].append(
            {
                "reference_id": "inventory-001::r1",
                "decision": "navigation",
                "evidence_ids": ["inventory-001::e3"],
            }
        ),
        "reference handle reconciled more than once.*inventory-001::r1",
    ),
    (
        lambda payload: payload["reference_decisions"].append(
            {
                "reference_id": "inventory-001::r99",
                "decision": "navigation",
                "evidence_ids": ["inventory-001::e3"],
            }
        ),
        "unknown reference handle.*inventory-001::r99",
    ),
])
def test_extract_reconciliation_rejects_inexact_reference_partition(
    mutation, expected: str
) -> None:
    """References partition across edges, non-edge decisions, and reference gaps."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_extract_reconciliation_requires_exact_inventory_gap_partition() -> None:
    """Every pooled g* finding must survive as a final gap or an explicit decision."""

    payload = _semantic_extract()
    del payload["gaps"][0]["inventory_gap_ids"]

    with pytest.raises(ValueError, match="unreconciled inventory gap.*inventory-001::g1"):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())

    payload["gap_decisions"] = [
        {
            "gap_id": "inventory-001::g1",
            "disposition": "resolved",
            "reason": "The document-wide pass matched the reference.",
        }
    ]
    semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_extract_reconciliation_reports_independent_record_errors_together() -> None:
    """One correction job needs every independently detectable record failure."""

    payload = _semantic_extract()
    payload["relationships"][0]["evidence_ids"] = ["inventory-001::e99"]
    payload["relationships"][0]["to"] = "missing"

    with pytest.raises(ValueError) as raised:
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())

    diagnostic = str(raised.value)
    assert "unknown evidence handle" in diagnostic
    assert "unknown relationship target" in diagnostic


def test_extract_reconciliation_rejects_self_and_duplicate_direct_edges() -> None:
    """Final direct edges are unique ordered endpoint/type records and never loops."""

    payload = _semantic_extract()
    duplicate = deepcopy(payload["relationships"][0])
    duplicate["from"] = "result"
    duplicate["to"] = "result"
    duplicate["hint_ids"] = []
    duplicate["reference_ids"] = []
    payload["relationships"].append(duplicate)
    payload["relationships"].append(deepcopy(payload["relationships"][0]))

    with pytest.raises(ValueError) as raised:
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())

    diagnostic = str(raised.value)
    assert "self-edge" in diagnostic
    assert "duplicate direct relationship" in diagnostic


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload, _inventory: payload["entities"][1].update(
            {"type": "external-result"}
        ),
        "external-result entity 'result' has no outgoing supports edge",
    ),
    (
        lambda _payload, inventory: inventory["candidates"][2].update(
            {
                "type_hint": "external-result",
                "external_identity": {"name": "Named theorem"},
                "retention_reasons": ["named-indispensable-external-result"],
            }
        ),
        "required external-result candidate section.tex:40 is not retained",
    ),
    (
        lambda payload, _inventory: payload["entities"][1].update(
            {"type": "exposition", "kind": "example"}
        ),
        "example entity 'result' has no incoming illustrated-by edge",
    ),
    (
        lambda payload, inventory: (
            inventory["unresolved_entities"].append(
                {
                    "key": "inventory-001::u3",
                    "title": "Created object",
                    "statement": "An implicit object is required.",
                    "resolution_kind": "implicit-entity",
                    "type_hint": "setup",
                    "evidence_ids": ["inventory-001::e3"],
                }
            ),
            payload["unresolved_resolutions"].append(
                {
                    "unresolved_id": "inventory-001::u3",
                    "disposition": "created",
                    "entity_id": "isolated",
                }
            ),
            payload["entities"].append(
                {
                    "candidate_ids": [],
                        "id": "isolated",
                        "type": "setup",
                        "kind": "definition",
                        "statement_location": [0, 30, 30],
                    "short_title": "Isolated",
                    "description": "An implicit object.",
                    "source": "inferred",
                }
            ),
        ),
        "isolated semantic entity: isolated",
    ),
])
def test_extract_reconciliation_enforces_final_graph_retention_invariants(
    mutation, expected: str
) -> None:
    """External results, examples, and all retained entities remain graph-connected."""

    payload = _semantic_extract()
    inventory = _reconciliation_inventory()
    mutation(payload, inventory)

    with pytest.raises(ValueError, match=expected):
        semantic.validate_extract_reconciliation(payload, inventory)


def test_extract_reconciliation_allows_only_one_direct_type_per_endpoint_pair() -> None:
    """One ordered endpoint pair cannot masquerade as two different direct links."""

    payload = _semantic_extract()
    second = deepcopy(payload["relationships"][0])
    second["type"] = "illustrated-by"
    second["hint_ids"] = []
    second.pop("reference_ids")
    second["implicit"] = True
    payload["relationships"].append(second)

    with pytest.raises(ValueError, match="multiple relationship types"):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_semantic_repair_updates_new_records_without_touching_unaffected_records() -> None:
    """Narrow u/h corrections preserve every record outside their keyed upserts."""

    payload = _semantic_extract()
    original_relationships = deepcopy(payload["relationships"])
    original_reference_decisions = deepcopy(payload["reference_decisions"])
    original_gaps = deepcopy(payload["gaps"])
    repair = _empty_semantic_repair()
    repair["remove_unresolved_ids"] = ["inventory-001::u2"]
    repair["upsert_unresolved_resolutions"] = [
        {
            "unresolved_id": "inventory-001::u2",
            "disposition": "unresolved",
            "reason": "More source context is required.",
        }
    ]
    repair["remove_hint_ids"] = ["inventory-001::h2"]
    repair["upsert_hint_decisions"] = [
        {
            "hint_id": "inventory-001::h2",
            "decision": "unresolved",
            "reason": "The directness question remains open.",
        }
    ]
    repair["remove_inventory_gap_ids"] = ["inventory-001::g1"]
    repair["remove_gap_ids"] = ["gap-reference"]
    repair["upsert_gap_decisions"] = [
        {
            "gap_id": "inventory-001::g1",
            "disposition": "resolved",
            "reason": "The document-wide pass resolved the ambiguity.",
        }
    ]
    repair["upsert_reference_decisions"] = [
        {
            "reference_id": "inventory-001::r3",
            "decision": "non-dependency",
            "evidence_ids": ["inventory-001::e3"],
            "reason": "The document-wide pass resolved the ambiguity as a non-edge.",
        }
    ]

    repaired = semantic.apply_semantic_repair(
        payload, repair, _reconciliation_inventory()
    )

    assert repaired["relationships"] == original_relationships
    assert repaired["reference_decisions"][:1] == original_reference_decisions
    assert repaired["reference_decisions"][1]["reference_id"] == "inventory-001::r3"
    assert original_gaps[0]["inventory_gap_ids"] == ["inventory-001::g1"]
    assert repaired["gaps"] == []
    assert repaired["unresolved_resolutions"][1]["disposition"] == "unresolved"
    assert repaired["hint_decisions"][0]["decision"] == "unresolved"
    assert repaired["gap_decisions"][0]["disposition"] == "resolved"


def test_semantic_repair_requires_inventory_and_cannot_bypass_reconciliation() -> None:
    """Every correction returns only after exact pooled-inventory validation."""

    payload = _semantic_extract()
    with pytest.raises(TypeError, match="inventory"):
        semantic.apply_semantic_repair(payload, _empty_semantic_repair())

    incomplete = _empty_semantic_repair()
    incomplete["remove_unresolved_ids"] = ["inventory-001::u2"]
    with pytest.raises(ValueError, match="unreconciled unresolved handle"):
        semantic.apply_semantic_repair(
            payload, incomplete, _reconciliation_inventory()
        )


@pytest.mark.parametrize(
    "field, records",
    [
        ("upsert_entities", lambda payload: [payload["entities"][0]] * 2),
        ("upsert_exclusions", lambda payload: [payload["exclusions"][0]] * 2),
        (
            "upsert_unresolved_resolutions",
            lambda payload: [payload["unresolved_resolutions"][0]] * 2,
        ),
        ("upsert_relationships", lambda payload: [payload["relationships"][0]] * 2),
        ("upsert_hint_decisions", lambda payload: [payload["hint_decisions"][0]] * 2),
        (
            "upsert_reference_decisions",
            lambda payload: [payload["reference_decisions"][0]] * 2,
        ),
        ("upsert_gaps", lambda payload: [payload["gaps"][0]] * 2),
    ],
)
def test_semantic_repair_rejects_duplicate_logical_upsert_keys(
    field: str, records
) -> None:
    """Two upserts may not compete for the same stable logical record key."""

    payload = _semantic_extract()
    repair = _empty_semantic_repair()
    repair[field] = deepcopy(records(payload))

    with pytest.raises(ValueError, match="duplicate repair upsert key"):
        semantic.apply_semantic_repair(
            payload, repair, _reconciliation_inventory()
        )


def test_superseded_hint_decisions_require_a_reason_in_extract_and_repair() -> None:
    """Supersession is a semantic disposition and must remain auditable."""

    payload = _semantic_extract()
    payload["hint_decisions"][0] = {
        "hint_id": "inventory-001::h2",
        "decision": "superseded",
    }
    with pytest.raises(ValueError, match="reason is required"):
        semantic.validate_extract_reconciliation(payload, _reconciliation_inventory())

    repair = _empty_semantic_repair()
    repair["remove_hint_ids"] = ["inventory-001::h2"]
    repair["upsert_hint_decisions"] = [
        {"hint_id": "inventory-001::h2", "decision": "superseded"}
    ]
    with pytest.raises(ValueError, match="reason is required"):
        semantic.apply_semantic_repair(
            _semantic_extract(), repair, _reconciliation_inventory()
        )

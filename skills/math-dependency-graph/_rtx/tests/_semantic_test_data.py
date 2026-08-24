"""Reusable fresh payload factories for semantic runtime tests."""

from __future__ import annotations

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
        "files": ["sections/model.tex", "sections/results.tex", "sections/appendix.tex"],
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
                "statement_location": [0, 10, 14],
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
                "statement_location": [1, 20, 24],
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

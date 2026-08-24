"""Contract tests for the compact chunk-level inventory discovery schema."""

from __future__ import annotations

import copy
import pytest
from jsonschema import Draft202012Validator, ValidationError


def _inventory() -> dict[str, object]:
    """Return a valid discovery fragment with one local and one remote endpoint."""

    return {
        "ir_version": 3,
        "chunk_id": "inventory-003",
        "files": ["sections/model.tex"],
        "nodes": [
            {
                "local_id": "n1",
                "statement_location": [0, 128, 134],
                "environment": "proposition",
                "labels": ["prop:measurable"],
                "provenance": "explicit",
                "type_hint": "result",
                "summary": "The policy correspondence is measurable.",
            }
        ],
        "edges": [
            {
                "local_id": "d1",
                "from": {
                    "unresolved": {
                        "title": "Measurable selection theorem",
                        "statement": "A labeled theorem supplies the selection step.",
                        "resolution_kind": "remote-label",
                        "locators": [{"label": "thm:selection"}],
                        "type_hint": "result",
                    }
                },
                "to": {"local_node": "n1"},
                "type": "supports",
                "basis": "explicit-reference",
                "assertion": "explicit",
                "location": [0, 140, 143],
                "reference": {
                    "location": [0, 141, 141],
                    "locator": {"label": "thm:selection"},
                },
                "description": "The proof invokes the labeled selection theorem.",
                "confidence": "Verified",
            }
        ],
        "gaps": [],
    }


def test_inventory_schema_accepts_inline_discovery_records(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """Workers can report graph semantics without bookkeeping registries."""

    inventory_schema_validator.validate(_inventory())


def test_inventory_schema_requires_reason_for_inferred_edge(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """A worker inference must retain a concise mathematical rationale."""

    inventory = copy.deepcopy(_inventory())
    edge = inventory["edges"][0]
    edge["basis"] = "mathematical-inference"
    edge["assertion"] = "inferred"
    del edge["reference"]

    with pytest.raises(ValidationError):
        inventory_schema_validator.validate(inventory)


def test_inventory_schema_requires_inline_reference_for_reference_edge(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """An explicit-reference edge carries its locator at the point of use."""

    inventory = copy.deepcopy(_inventory())
    del inventory["edges"][0]["reference"]

    with pytest.raises(ValidationError):
        inventory_schema_validator.validate(inventory)


def test_inventory_schema_requires_scope_for_local_assumption(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """A local assumption cannot lose the boundary where it applies."""

    inventory = copy.deepcopy(_inventory())
    node = inventory["nodes"][0]
    node["type_hint"] = "assumption"
    node["kind_hint"] = "local"

    with pytest.raises(ValidationError):
        inventory_schema_validator.validate(inventory)


def test_inventory_schema_requires_external_result_identity(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """External results remain matchable by name or citation identity."""

    inventory = copy.deepcopy(_inventory())
    inventory["nodes"][0]["type_hint"] = "external-result"

    with pytest.raises(ValidationError):
        inventory_schema_validator.validate(inventory)


def test_inventory_schema_requires_reference_for_reference_gap(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """An unresolved explicit reference keeps its source-visible locator."""

    inventory = copy.deepcopy(_inventory())
    inventory["gaps"] = [
        {
            "local_id": "g1",
            "category": "reference",
            "location": [0, 141, 141],
            "description": "The referenced result is outside this chunk.",
        }
    ]

    with pytest.raises(ValidationError):
        inventory_schema_validator.validate(inventory)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("nodes", 0, "summary"), "s" * 241),
        (("edges", 0, "description"), "d" * 201),
        (("gaps", 0, "description"), "g" * 201),
    ],
)
def test_inventory_schema_bounds_worker_prose(
    path: tuple[object, ...],
    value: str,
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """Inline annotations remain compact rather than reproducing source text."""

    inventory = copy.deepcopy(_inventory())
    if path[0] == "gaps":
        inventory["gaps"] = [
            {
                "local_id": "g1",
                "category": "coverage",
                "location": [0, 128, 134],
                "description": "A relevant claim may be missing.",
            }
        ]
    target: object = inventory
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        inventory_schema_validator.validate(inventory)


def test_inventory_schema_accepts_empty_complete_fragment(
    inventory_schema_validator: Draft202012Validator,
) -> None:
    """A chunk with no graph findings still returns the complete v3 shape."""

    inventory_schema_validator.validate(
        {
            "ir_version": 3,
            "chunk_id": "inventory-empty",
            "files": ["sections/model.tex"],
            "nodes": [],
            "edges": [],
            "gaps": [],
        }
    )

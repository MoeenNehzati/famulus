"""Contract tests for the compact chunk-level inventory discovery schema."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


SCHEMA_PATH = Path(__file__).parents[2] / "inventory.schema.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _inventory() -> dict[str, object]:
    """Return a valid discovery fragment with one local and one remote endpoint."""

    return {
        "ir_version": 3,
        "chunk_id": "inventory-003",
        "files": ["sections/model.tex"],
        "nodes": [
            {
                "local_id": "n1",
                "location": [0, 128, 134],
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


def test_inventory_schema_accepts_inline_discovery_records() -> None:
    """Workers can report graph semantics without bookkeeping registries."""

    _validator().validate(_inventory())


@pytest.mark.parametrize(
    "field",
    [
        "evidence",
        "references",
        "candidates",
        "unresolved_entities",
        "relationship_hints",
        "reference_decisions",
    ],
)
def test_inventory_schema_rejects_retired_bookkeeping_arrays(field: str) -> None:
    """The worker contract cannot regress to normalized pooling registries."""

    inventory = _inventory()
    inventory[field] = []

    with pytest.raises(ValidationError):
        _validator().validate(inventory)


def test_inventory_schema_requires_reason_for_inferred_edge() -> None:
    """A worker inference must retain a concise mathematical rationale."""

    inventory = copy.deepcopy(_inventory())
    edge = inventory["edges"][0]
    edge["basis"] = "mathematical-inference"
    edge["assertion"] = "inferred"
    del edge["reference"]

    with pytest.raises(ValidationError):
        _validator().validate(inventory)


def test_inventory_schema_requires_inline_reference_for_reference_edge() -> None:
    """An explicit-reference edge carries its locator at the point of use."""

    inventory = copy.deepcopy(_inventory())
    del inventory["edges"][0]["reference"]

    with pytest.raises(ValidationError):
        _validator().validate(inventory)


def test_inventory_schema_requires_scope_for_local_assumption() -> None:
    """A local assumption cannot lose the boundary where it applies."""

    inventory = copy.deepcopy(_inventory())
    node = inventory["nodes"][0]
    node["type_hint"] = "assumption"
    node["kind_hint"] = "local"

    with pytest.raises(ValidationError):
        _validator().validate(inventory)


def test_inventory_schema_requires_external_result_identity() -> None:
    """External results remain matchable by name or citation identity."""

    inventory = copy.deepcopy(_inventory())
    inventory["nodes"][0]["type_hint"] = "external-result"

    with pytest.raises(ValidationError):
        _validator().validate(inventory)


def test_inventory_schema_requires_reference_for_reference_gap() -> None:
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
        _validator().validate(inventory)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("nodes", 0, "summary"), "s" * 241),
        (("edges", 0, "description"), "d" * 201),
        (("gaps", 0, "description"), "g" * 201),
    ],
)
def test_inventory_schema_bounds_worker_prose(
    path: tuple[object, ...], value: str
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
        _validator().validate(inventory)


def test_inventory_schema_accepts_empty_complete_fragment() -> None:
    """A chunk with no graph findings still returns the complete v3 shape."""

    _validator().validate(
        {
            "ir_version": 3,
            "chunk_id": "inventory-empty",
            "files": ["sections/model.tex"],
            "nodes": [],
            "edges": [],
            "gaps": [],
        }
    )

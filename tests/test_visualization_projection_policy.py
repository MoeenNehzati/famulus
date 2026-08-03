"""Mechanical checks for the generic relation-semantics contract."""

from copy import deepcopy

import pytest

from officina.common.visualization.from_blueprint.catalog import build_relation_semantics
from officina.common.visualization.payload import GraphPayloadProcessor


def _payload() -> dict:
    return {
        "schema_version": 2,
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [
            {"id": "refined", "label": "Refined"},
            {"id": "coarse", "label": "Coarse"},
        ],
        "relation_semantics": {
            "transformations": {"node_omission": {"rules": [{
                "id": "rewrite",
                "causes": ["user-hidden"],
                "left_types": ["refined"],
                "right_types": ["refined"],
                "outcomes": [
                    {"type": "refined", "fidelity": "exact"},
                    {"type": "coarse", "fidelity": "degraded"},
                ],
            }]}},
            "subsumptions": [{
                "stronger_type": "refined",
                "weaker_types": ["coarse"],
            }],
        },
        "entities": [
            {
                "id": "x", "type": "node", "category": "node",
                "short_title": "X", "position": 0,
                "connects_to": [{"to": "y", "type": "refined"}],
            },
            {
                "id": "y", "type": "node", "category": "node",
                "short_title": "Y", "position": 1, "connects_to": [],
            },
        ],
    }


def test_relation_semantics_accepts_multiple_truthful_outcomes() -> None:
    GraphPayloadProcessor().prepare(_payload())


def test_relation_semantics_rejects_duplicate_expanded_cell() -> None:
    payload = _payload()
    duplicate = deepcopy(
        payload["relation_semantics"]["transformations"]["node_omission"]["rules"][0]
    )
    duplicate["id"] = "duplicate"
    payload["relation_semantics"]["transformations"]["node_omission"]["rules"].append(duplicate)
    with pytest.raises(ValueError, match="Duplicate relation-transformation cell"):
        GraphPayloadProcessor().prepare(payload)


def test_relation_semantics_rejects_cyclic_subsumption() -> None:
    payload = _payload()
    payload["relation_semantics"]["subsumptions"].append({
        "stronger_type": "coarse", "weaker_types": ["refined"],
    })
    with pytest.raises(ValueError, match="must be acyclic"):
        GraphPayloadProcessor().prepare(payload)


def test_blueprint_semantics_specialize_rules_to_scoped_edge_types() -> None:
    semantics = build_relation_semantics({"depends-on-source", "binds-interface"})
    rules = semantics["transformations"]["node_omission"]["rules"]
    referenced = {
        edge_type
        for rule in rules
        for edge_type in [*rule["left_types"], *rule["right_types"]]
    }
    assert "helper-dependency" not in referenced
    assert any(
        outcome["type"] == "indirectly-depends-on"
        for rule in rules
        for outcome in rule["outcomes"]
    )

"""Regression tests for reusable visualization graph primitives."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from officina.common.visualization import render_module_artifacts
from officina.common.visualization.base_renderer import BaseRenderer
from officina.common.visualization.base_visualizer import BaseVisualizer, GraphSourceKind
from officina.common.visualization.graph import Graph


def _presentation_node_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "presentation_nodes": [
            {
                "id": "group.research",
                "type": "presentation-group",
                "short_title": "Research",
                "position": 0,
                "member_ids": ["alpha"],
                "presentation": {
                    "form": "supernode",
                    "tone": "subtle",
                    "default_visibility": "hidden",
                },
                "interaction": {
                    "selectable": True,
                    "inspectable": True,
                    "draggable": "members",
                    "collapse_effect": "self",
                },
            }
        ],
        "ui": {
            "presentation_node_controls": [
                {
                    "id": "grouping",
                    "label": "Grouping",
                    "selector_label": "Group by",
                    "default_facet": "domain",
                    "facets": [
                        {
                            "id": "domain",
                            "label": "Domain",
                            "activation": "all",
                            "node_ids": ["group.research"],
                        }
                    ],
                }
            ]
        },
        "entities": [
            {
                "id": "alpha",
                "type": "module",
                "short_title": "Alpha",
                "position": 0,
                "connects_to": [],
            },
            {
                "id": "alpha.child",
                "type": "module",
                "short_title": "Child",
                "position": 1,
                "container": "alpha",
                "connects_to": [],
            },
        ],
    }


def test_graph_validation_accepts_presentation_node_root_members() -> None:
    Graph().validate_graph(_presentation_node_payload())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate-node", "duplicate presentation node id"),
        ("canonical-collision", "presentation node id conflicts with canonical entity"),
        ("duplicate-member", "duplicate member.*group.research"),
        ("unknown-member", "unknown presentation node member"),
        ("contained-member", "presentation node member must be a root entity"),
        ("duplicate-control", "duplicate presentation node control id"),
        ("duplicate-facet", "duplicate presentation node facet id"),
        ("duplicate-facet-global", "duplicate presentation node facet id"),
        ("unknown-default", "unknown default presentation node facet"),
        ("unknown-node-reference", "unknown presentation node reference"),
        ("unreachable-inspector", "inspectable presentation node must be selectable"),
    ],
)
def test_graph_validation_rejects_invalid_presentation_nodes(
    mutation: str, message: str
) -> None:
    payload = deepcopy(_presentation_node_payload())
    nodes = payload["presentation_nodes"]
    controls = payload["ui"]["presentation_node_controls"]
    control = controls[0]
    facet = control["facets"][0]
    if mutation == "duplicate-node":
        nodes.append(deepcopy(nodes[0]))
    elif mutation == "canonical-collision":
        nodes[0]["id"] = "alpha"
        facet["node_ids"] = ["alpha"]
    elif mutation == "duplicate-member":
        nodes[0]["member_ids"] = ["alpha", "alpha"]
    elif mutation == "unknown-default":
        control["default_facet"] = "missing"
    elif mutation == "unknown-member":
        nodes[0]["member_ids"] = ["missing"]
    elif mutation == "contained-member":
        nodes[0]["member_ids"] = ["alpha.child"]
    elif mutation == "duplicate-control":
        controls.append(deepcopy(control))
    elif mutation == "duplicate-facet":
        control["facets"].append(deepcopy(facet))
    elif mutation == "duplicate-facet-global":
        second_node = deepcopy(nodes[0])
        second_node["id"] = "group.other"
        nodes.append(second_node)
        second_control = deepcopy(control)
        second_control["id"] = "secondary"
        second_control["facets"][0]["node_ids"] = ["group.other"]
        controls.append(second_control)
    elif mutation == "unknown-node-reference":
        facet["node_ids"] = ["missing"]
    elif mutation == "unreachable-inspector":
        nodes[0]["interaction"]["selectable"] = False

    with pytest.raises(ValueError, match=message):
        Graph().validate_graph(payload)


def test_graph_validation_rejects_presentation_node_owned_by_two_controls() -> None:
    payload = deepcopy(_presentation_node_payload())
    second = deepcopy(payload["ui"]["presentation_node_controls"][0])
    second["id"] = "secondary"
    second["default_facet"] = "secondary-domain"
    second["facets"][0]["id"] = "secondary-domain"
    payload["ui"]["presentation_node_controls"].append(second)

    with pytest.raises(ValueError, match="presentation node has multiple control owners"):
        Graph().validate_graph(payload)


def test_public_package_exports_docstring_artifact_facade() -> None:
    """Repository tooling can import the documented orchestration function."""
    assert callable(render_module_artifacts)


def test_visualizer_loads_precomputed_payload_without_extractor(tmp_path) -> None:
    """LLM-owned workflows can enter at canonical JSON without a fake extractor."""
    source = tmp_path / "graph.json"
    source.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "result",
                        "type": "theorem",
                        "short_title": "Result",
                        "position": 0,
                        "connects_to": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    visualizer = BaseVisualizer(extractor=None, renderer=BaseRenderer())
    resolved = visualizer.resolve_source(source)
    payload = visualizer.build_payload(resolved)

    assert resolved.kind is GraphSourceKind.PAYLOAD
    assert payload["schema_version"] == 2
    assert payload["entities"][0]["connects_to"] == []


def test_transitive_reduction_removes_same_type_redundant_edge() -> None:
    """A direct edge is redundant when another path of the same type reaches it."""
    payload = {
        "schema_version": 2,
        "entities": [
            {
                "id": "a",
                "type": "function",
                "short_title": "a",
                "position": 0,
                "connects_to": [
                    {"to": "b", "type": "call"},
                    {"to": "c", "type": "call"},
                ],
            },
            {
                "id": "c",
                "type": "function",
                "short_title": "c",
                "position": 1,
                "connects_to": [{"to": "b", "type": "call"}],
            },
            {
                "id": "b",
                "type": "function",
                "short_title": "b",
                "position": 2,
                "connects_to": [],
            },
        ],
    }

    reduced, removed = Graph().reduce_transitive_edges(payload)

    a_edges = reduced["entities"][0]["connects_to"]
    assert {"to": "b", "type": "call"} not in a_edges
    assert {"to": "c", "type": "call"} in a_edges
    assert removed[0]["source"] == "a"
    assert removed[0]["target"] == "b"


def test_transitive_reduction_keeps_edges_when_type_differs() -> None:
    """Reduction does not collapse semantically different edge types."""
    payload = {
        "schema_version": 2,
        "entities": [
            {
                "id": "a",
                "type": "function",
                "short_title": "a",
                "position": 0,
                "connects_to": [
                    {"to": "b", "type": "call"},
                    {"to": "c", "type": "instantiation"},
                ],
            },
            {
                "id": "c",
                "type": "function",
                "short_title": "c",
                "position": 1,
                "connects_to": [{"to": "b", "type": "instantiation"}],
            },
            {
                "id": "b",
                "type": "function",
                "short_title": "b",
                "position": 2,
                "connects_to": [],
            },
        ],
    }

    reduced, removed = Graph().reduce_transitive_edges(payload)

    assert reduced == payload
    assert removed == []

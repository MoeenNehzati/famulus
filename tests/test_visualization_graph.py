"""Regression tests for reusable visualization graph primitives."""

from __future__ import annotations

import json

from officina.common.visualization import render_module_artifacts
from officina.common.visualization.base_renderer import BaseRenderer
from officina.common.visualization.base_visualizer import BaseVisualizer, GraphSourceKind
from officina.common.visualization.graph import Graph


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

"""Regression tests for reusable visualization graph primitives."""

from __future__ import annotations

from officina.common.visualization import render_module_artifacts
from officina.common.visualization.graph import Graph


def test_public_package_exports_docstring_artifact_facade() -> None:
    """Repository tooling can import the documented orchestration function."""
    assert callable(render_module_artifacts)


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

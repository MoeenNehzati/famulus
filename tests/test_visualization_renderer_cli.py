from __future__ import annotations

from officina.common.visualization import base_renderer_cli


def test_core_renderer_prepares_math_dependency_presentation_defaults() -> None:
    payload = {
        "schema_version": 2,
        "graph_kind": "math-dependency",
        "entities": [
            {
                "id": "assumption",
                "type": "standing-assumption",
                "short_title": "Assumption",
                "position": 0,
                "connects_to": [
                    {"to": "result", "type": "assumption-for"}
                ],
            },
            {
                "id": "result",
                "type": "theorem",
                "short_title": "Result",
                "position": 1,
                "connects_to": [],
            },
        ],
    }

    prepared = base_renderer_cli.prepare_render_payload(
        payload, profile="math-dependency"
    )

    assert prepared["categories"] == [
        {
            "id": "standing-assumption",
            "label": "Standing Assumption",
            "shape": "hexagon",
            "color": "#c0392b",
        },
        {
            "id": "theorem",
            "label": "Theorem",
            "shape": "rect",
            "color": "#6c3483",
        },
    ]
    assert [entity["category"] for entity in prepared["entities"]] == [
        "standing-assumption",
        "theorem",
    ]
    assert prepared["edge_categories"] == [
        {
            "id": "assumption-for",
            "label": "Assumption For",
            "description": "A direct mathematical dependency classified by the LLM extractor.",
        }
    ]


def test_math_profile_preserves_caller_category_catalog() -> None:
    payload = {
        "schema_version": 2,
        "graph_kind": "math-dependency",
        "categories": [
            {"id": "main-results", "label": "Main results", "color": "#123456"}
        ],
        "entities": [
            {
                "id": "result",
                "type": "theorem",
                "category": "main-results",
                "short_title": "Result",
                "position": 0,
                "connects_to": [],
            }
        ],
    }

    prepared = base_renderer_cli.prepare_render_payload(
        payload, profile="math-dependency"
    )

    assert prepared["categories"] == payload["categories"]
    assert prepared["entities"][0]["category"] == "main-results"

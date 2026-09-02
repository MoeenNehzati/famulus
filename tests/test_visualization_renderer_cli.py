from __future__ import annotations

import json

import pytest

import officina.visualization.base_renderer_cli as base_renderer_cli
import officina.visualization.html_renderer.dependencies as renderer_dependencies
from officina.visualization.elk_html_renderer import build_html_with_elk


def test_mathjax_macro_adapter_normalizes_every_schema_tuple_encoding() -> None:
    macros = {
        "Scalar": r"\mathbb{R}",
        "Native": [r"#1+#2", 2],
        "Legacy": [2, r"#1+#2"],
        "NativeDefault": [r"#1+#2", 2, "x"],
        "LegacyDefault": [2, r"#1+#2", "x"],
    }

    normalized = renderer_dependencies.normalize_mathjax_macros(macros)

    assert normalized == {
        "Scalar": r"\mathbb{R}",
        "Native": [r"#1+#2", 2],
        "Legacy": [r"#1+#2", 2],
        "NativeDefault": [r"#1+#2", 2, "x"],
        "LegacyDefault": [r"#1+#2", 2, "x"],
    }
    assert macros["Legacy"] == [2, r"#1+#2"]


def test_schema_integral_macro_arities_reach_rendering_as_native_integers() -> None:
    payload = {
        "schema_version": 2,
        "renderer_dependencies": [
            {
                "id": "mathjax",
                "version": "3",
                "configuration": {
                    "macros": {
                        "Native": ["#1+#2", 2.0],
                        "Legacy": [2.0, "#1+#2"],
                        "NativeDefault": ["#1+#2", 2.0, "x"],
                        "LegacyDefault": [2.0, "#1+#2", "x"],
                    }
                },
            }
        ],
        "entities": [],
    }

    rendered = build_html_with_elk(payload)
    configuration_text = rendered.split("    window.MathJax = ", 1)[1].split(";\n", 1)[0]

    assert json.loads(configuration_text)["tex"]["macros"] == {
        "Native": ["#1+#2", 2],
        "Legacy": ["#1+#2", 2],
        "NativeDefault": ["#1+#2", 2, "x"],
        "LegacyDefault": ["#1+#2", 2, "x"],
    }


@pytest.mark.parametrize(
    "value",
    (
        7,
        [r"#1"],
        [r"#1", 10],
        [True, r"#1"],
        [r"#1", True],
        [1.5, r"#1"],
        [r"#1", 1.5],
        [float("inf"), r"#1"],
        [r"#1", float("-inf")],
        [float("nan"), r"#1"],
        [r"#1", 1, 2],
    ),
)
def test_mathjax_macro_adapter_rejects_values_outside_the_schema(value: object) -> None:
    with pytest.raises(ValueError, match=r"macro 'Broken'.*schema-supported"):
        renderer_dependencies.normalize_mathjax_macros({"Broken": value})


def test_core_renderer_does_not_synthesize_math_dependency_categories() -> None:
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

    assert "categories" not in prepared
    assert all("category" not in entity for entity in prepared["entities"])
    assert "edge_categories" not in prepared


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

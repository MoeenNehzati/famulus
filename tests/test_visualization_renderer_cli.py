from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

import officina.visualization.base_renderer_cli as base_renderer_cli
import officina.visualization.html_renderer.dependencies as renderer_dependencies


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


@pytest.mark.parametrize(
    "value",
    (
        7,
        [r"#1"],
        [r"#1", 10],
        [True, r"#1"],
        [r"#1", 1, 2],
    ),
)
def test_mathjax_macro_adapter_rejects_values_outside_the_schema(value: object) -> None:
    with pytest.raises(ValueError, match=r"macro 'Broken'.*schema-supported"):
        renderer_dependencies.normalize_mathjax_macros({"Broken": value})


def test_deprecated_macro_file_is_validated_before_renderer_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "graph.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "entities": [
                    {
                        "id": "node",
                        "type": "node",
                        "short_title": "Node",
                        "position": 0,
                        "connects_to": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    macro_file = tmp_path / "macros.json"
    macro_file.write_text(json.dumps({"Broken": [1, 2]}), encoding="utf-8")
    html_out = tmp_path / "graph.html"

    def reject_renderer_entry(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("renderer received an unvalidated preprocessed payload")

    monkeypatch.setattr(base_renderer_cli, "build_html_with_elk", reject_renderer_entry)

    with pytest.raises(jsonschema.ValidationError):
        base_renderer_cli.main(
            [
                str(source),
                "--macro-file",
                str(macro_file),
                "--html-out",
                str(html_out),
            ]
        )
    assert not html_out.exists()


def test_deprecated_macro_file_preserves_base_payload_schema_errors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "graph.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "renderer_dependencies": ["not-an-object"],
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    macro_file = tmp_path / "macros.json"
    macro_file.write_text("{}", encoding="utf-8")

    with pytest.raises(jsonschema.ValidationError):
        base_renderer_cli.main(
            [
                str(source),
                "--macro-file",
                str(macro_file),
                "--html-out",
                str(tmp_path / "x.html"),
            ]
        )


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

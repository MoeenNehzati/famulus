from __future__ import annotations

import json

import officina.visualization.base_renderer_cli as base_renderer_cli
from officina.visualization.html_renderer.quick_guide import (
    QuickGuide,
    QuickGuideStep,
)
from officina.visualization.html_renderer.quick_guides.default import DEFAULT_QUICK_GUIDE
from officina.visualization.elk_html_renderer import ElkHtmlRenderer, build_html_with_elk


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


def _minimal_graph_payload() -> dict:
    return {
        "schema_version": 2,
        "graph_kind": "math-dependency",
        "entities": [
            {
                "id": "root",
                "type": "theorem",
                "short_title": "Root",
                "position": 0,
                "connects_to": [{"to": "leaf", "type": "proves"}],
            },
            {
                "id": "leaf",
                "type": "result",
                "short_title": "Leaf",
                "position": 1,
                "connects_to": [],
            },
        ],
    }


def test_renderer_html_includes_disabled_quick_guide_config() -> None:
    payload = base_renderer_cli.prepare_render_payload(
        _minimal_graph_payload(), profile="math-dependency"
    )
    html = ElkHtmlRenderer().render_graph_html(payload)

    assert 'const QUICK_GUIDE_CONFIG = null;' in html
    assert html.count('id="quick-guide-toolbar-item"') == 1
    assert html.count('id="quick-guide-dialog"') == 1
    assert html.count('id="quick-guide-highlight"') == 1
    assert html.count('id="quick-guide-close"') == 1
    assert html.count('id="quick-guide-btn"') == 1


def test_renderer_html_includes_default_quick_guide_ids_text() -> None:
    payload = base_renderer_cli.prepare_render_payload(
        _minimal_graph_payload(), profile="math-dependency"
    )
    html = ElkHtmlRenderer(quick_guide=DEFAULT_QUICK_GUIDE).render_graph_html(payload)

    assert '"read-graph"' in html
    assert "Nodes are items" in html
    assert '\"inspect-selection\"' in html
    assert "Toolbar actions include hide/dim selection, redraw/reset, and zoom/Fit." in html


def test_renderer_html_uses_custom_quick_guide() -> None:
    payload = base_renderer_cli.prepare_render_payload(
        _minimal_graph_payload(), profile="math-dependency"
    )
    guide = QuickGuide(
        title="Custom guide",
        steps=(
            QuickGuideStep(
                id="custom",
                target="#does-not-exist",
                title="Custom",
                body="Custom body",
            ),
        ),
    )
    html = ElkHtmlRenderer(quick_guide=guide).render_graph_html(payload)

    assert '"custom"' in html
    assert "Custom body" in html


def test_build_html_with_elk_keeps_quick_guide_config_null() -> None:
    payload = base_renderer_cli.prepare_render_payload(
        _minimal_graph_payload(), profile="math-dependency"
    )
    html = build_html_with_elk(payload)
    assert 'const QUICK_GUIDE_CONFIG = null;' in html


def test_main_supports_quick_guide_renderer_injection(tmp_path, capsys) -> None:
    payload = base_renderer_cli.prepare_render_payload(
        _minimal_graph_payload(), profile="math-dependency"
    )
    source = tmp_path / "graph.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    macro_file = tmp_path / "mathjax-macros.json"
    macro_file.write_text(json.dumps({"R": "\\\\mathbb{R}"}), encoding="utf-8")
    html_out = tmp_path / "graph.html"
    guide = QuickGuide(
        title="Injected quick guide",
        steps=(
            QuickGuideStep(
                id="custom",
                target="#canvas-wrap",
                title="Injected",
                body="Injected custom guide body",
            ),
        ),
    )
    result = base_renderer_cli.main(
        [
            str(source),
            "--macro-file",
            str(macro_file),
            "--html-out",
            str(html_out),
        ],
        renderer=ElkHtmlRenderer(quick_guide=guide),
    )
    assert result == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["html"] == str(html_out)
    html = html_out.read_text(encoding="utf-8")
    assert '"custom"' in html
    assert "Injected custom guide body" in html
    assert '"R": "\\\\\\\\mathbb{R}"' in html

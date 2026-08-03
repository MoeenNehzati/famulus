"""Contract tests for generic visualization filtering metadata and controls."""

from __future__ import annotations

import pytest

from officina.common.visualization.base_renderer import BaseRenderer
from officina.common.visualization.elk_html_renderer import build_html_with_elk
from officina.common.visualization.graph import Graph


def _payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "categories": [
            {"id": "interface", "label": "Interface"},
            {"id": "exported", "label": "Exported Interface", "parent": "interface"},
        ],
        "entities": [
            {
                "id": "api",
                "type": "interface",
                "kind": "python",
                "category": "exported",
                "short_title": "API",
                "position": 0,
                "connects_to": [],
            }
        ],
    }


def test_renderer_exposes_generic_filtering_controls() -> None:
    html = build_html_with_elk(_payload())

    assert "Find nodes or relations" in html
    assert 'id="graph-detail-level"' in html
    assert "Hide selected" in html
    assert "Dim selected" in html
    assert "excludedKinds" in html
    assert "filter-retained-owner" in html


def test_graph_accepts_true_category_hierarchy() -> None:
    Graph().validate_graph(_payload())


def test_graph_rejects_category_hierarchy_cycle() -> None:
    payload = _payload()
    payload["categories"] = [
        {"id": "a", "label": "A", "parent": "b"},
        {"id": "b", "label": "B", "parent": "a"},
    ]

    with pytest.raises(ValueError, match="hierarchy contains a cycle"):
        Graph().validate_graph(payload)


def test_inline_graph_json_cannot_terminate_its_script() -> None:
    payload = _payload()
    payload["document"] = {
        "title": "</script><script>document.body.dataset.injected='yes'</script>"
    }

    rendered = build_html_with_elk(payload)

    assert "<\\/script><script>" in rendered
    assert "</script><script>document.body.dataset.injected" not in rendered


def test_normalization_preserves_parallel_edge_annotations() -> None:
    payload = _payload()
    payload["entities"].append(
        {
            "id": "worker",
            "type": "source",
            "short_title": "Worker",
            "position": 1,
            "connects_to": [],
        }
    )
    payload["entities"][0]["connects_to"] = [
        {"to": "worker", "type": "calls", "description": "first phase"},
        {"to": "worker", "type": "calls", "description": "second phase"},
    ]

    normalized = BaseRenderer().normalize(payload)

    assert [edge["description"] for edge in normalized["entities"][0]["connects_to"]] == [
        "first phase",
        "second phase",
    ]


def test_graph_rejects_unknown_edge_category() -> None:
    payload = _payload()
    payload["edge_categories"] = [{"id": "wraps", "label": "Wraps"}]
    payload["entities"][0]["connects_to"] = [{"to": "api", "type": "calls"}]

    with pytest.raises(ValueError, match="absent from 'edge_categories'"):
        Graph().validate_graph(payload)


def test_graph_rejects_invalid_initial_ui_reference() -> None:
    payload = _payload()
    payload["ui"] = {"focus": {"selected_node_id": "missing"}}

    with pytest.raises(ValueError, match="selected_node_id references unknown"):
        Graph().validate_graph(payload)


def test_public_html_builder_validates_payload() -> None:
    payload = _payload()
    payload["entities"][0]["connects_to"] = [{"to": "missing", "type": "calls"}]

    with pytest.raises(ValueError, match="is not defined"):
        build_html_with_elk(payload)


def test_mathjax_dependency_is_bundled_offline() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [{"id": "mathjax", "version": "3"}]

    rendered = build_html_with_elk(payload)

    assert "cdn.jsdelivr.net/npm/mathjax" not in rendered
    assert "MathJax" in rendered


def test_runtime_exposes_safe_filter_projection_contract() -> None:
    rendered = build_html_with_elk(_payload())

    assert "const edgeById = new Map" in rendered
    assert "searchEditStartSnapshot = null" in rendered
    assert "const relationTransitions" in rendered
    assert "const subsumedTypesByType = new Map" in rendered
    assert 'role="status" aria-live="polite"' in rendered
    assert "cdn.jsdelivr.net/npm/elkjs" not in rendered
    assert "graph-detail-level" in rendered


def test_graph_rejects_unknown_entity_detail_level() -> None:
    payload = _payload()
    payload["detail_levels"] = [{"id": "overview", "label": "Overview"}]
    payload["entities"][0]["detail_level"] = "undeclared"

    with pytest.raises(ValueError, match="unknown detail level"):
        Graph().validate_graph(payload)

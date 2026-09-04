from __future__ import annotations

from officina.visualization.html_renderer.quick_guides.default import DEFAULT_QUICK_GUIDE


def test_default_quick_guide_title_and_order() -> None:
    assert DEFAULT_QUICK_GUIDE.title == "Quick guide"
    assert [step.id for step in DEFAULT_QUICK_GUIDE.steps] == [
        "read-graph",
        "zoom-and-pan",
        "inspect-selection",
        "search-and-legend",
        "search",
        "trace-ancestors",
        "trace-successors",
        "hide-dim-selection",
        "hide-dim-complement",
        "restore-hidden",
        "more-controls",
    ]


def test_default_quick_guide_targets() -> None:
    assert [step.target for step in DEFAULT_QUICK_GUIDE.steps] == [
        "#canvas-wrap",
        "#zoom-in-btn",
        "#details",
        "#legend",
        "#graph-filter-search",
        '.relation-traverse-button[data-relation-scope="all"][data-direction="ancestors"]',
        '.relation-traverse-button[data-relation-scope="all"][data-direction="successors"]',
        "#hide-selected-btn",
        "#hide-unselected-btn",
        "#hidden-nodes",
        "#panel-toggle",
    ]


def test_default_quick_guide_content_is_nonempty() -> None:
    for step in DEFAULT_QUICK_GUIDE.steps:
        assert step.title
        assert step.body

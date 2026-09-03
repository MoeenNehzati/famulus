"""Default quick-guide content for passive viewer guidance."""

from __future__ import annotations

from ..quick_guide import QuickGuide, QuickGuideStep

DEFAULT_QUICK_GUIDE = QuickGuide(
    title="Quick guide",
    steps=(
        QuickGuideStep(
            id="read-graph",
            target="#canvas-wrap",
            title="Read the graph",
            body="Nodes are items, and arrows and relations show how they connect.",
        ),
        QuickGuideStep(
            id="inspect-selection",
            target="#details",
            title="Inspect selection",
            body="Selecting a node or edge shows its metadata in the Inspector.",
        ),
        QuickGuideStep(
            id="trace-ancestors",
            target='.relation-traverse-button[data-relation-scope="all"][data-direction="ancestors"]',
            title="Trace ancestors",
            body="Select a result, then use the all-relations Ancestors control to add prerequisites through currently visible relation types.",
        ),
        QuickGuideStep(
            id="trace-successors",
            target='.relation-traverse-button[data-relation-scope="all"][data-direction="successors"]',
            title="Trace successors",
            body="Select a result, then use the all-relations Successors control to add downstream results through currently visible relation types.",
        ),
        QuickGuideStep(
            id="act-on-selection",
            target="#canvas-toolbar",
            title="Act on selection",
            body="Toolbar actions include hide/dim selection, redraw/reset, and zoom/Fit.",
        ),
        QuickGuideStep(
            id="more-controls",
            target="#panel-toggle",
            title="More controls",
            body="Controls contains filtering, relation controls, and the full How to use reference.",
        ),
    ),
)

__all__ = ["DEFAULT_QUICK_GUIDE"]

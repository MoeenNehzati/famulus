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
            body="Nodes are items, and arrows and relations show how they connect. Hover a node or "
            "edge for a quick info popup, or drag a node to reposition it.",
        ),
        QuickGuideStep(
            id="zoom-and-pan",
            target="#zoom-in-btn",
            title="Zoom and pan",
            body="Zoom with the +/− toolbar buttons, the + and − keys (Ctrl+= and Ctrl+− work too), "
            "or by scrolling over the canvas. Drag empty canvas space to pan around; dragging a node "
            "instead moves that node.",
        ),
        QuickGuideStep(
            id="inspect-selection",
            target="#details",
            title="Inspect selection",
            body="Click a node or edge to select it and show its full metadata in the Inspector. "
            "Ctrl/Cmd+click additional nodes to add them to the selection.",
        ),
        QuickGuideStep(
            id="search-and-legend",
            target="#legend",
            title="Select by legend",
            body="Clicking a legend entry selects every node of that type or color; Ctrl/Cmd+click "
            "adds it to the current selection instead of replacing it. Each relation type in the "
            "legend has its own ancestors/successors arrows to traverse only that relation.",
        ),
        QuickGuideStep(
            id="search",
            target="#graph-filter-search",
            title="Search",
            body="Search highlights nodes and relations matching your text and dims everything "
            "else, without changing what is selected or hidden.",
        ),
        QuickGuideStep(
            id="trace-ancestors",
            target='.relation-traverse-button[data-relation-scope="all"][data-direction="ancestors"]',
            title="Trace ancestors",
            body="Select a node, then use the all-relations Ancestors control to walk edges backward "
            "and add everything upstream that impacts it — its full transitive prerequisites.",
        ),
        QuickGuideStep(
            id="trace-successors",
            target='.relation-traverse-button[data-relation-scope="all"][data-direction="successors"]',
            title="Trace successors",
            body="Select a node, then use the all-relations Successors control to walk edges forward "
            "and add its full downstream dependency closure — everything that depends on it.",
        ),
        QuickGuideStep(
            id="hide-dim-selection",
            target="#hide-selected-btn",
            title="Hide or dim selection",
            body="Hide or dim every selected node at once to declutter the view. Hiding a node "
            "draws a derived edge directly between its neighbors, so you can still see they were "
            "connected even though the node between them is gone. Double-click a single node to "
            "hide it directly, without selecting it first.",
        ),
        QuickGuideStep(
            id="hide-dim-complement",
            target="#hide-unselected-btn",
            title="Hide or dim everything else",
            body="The same hide and dim actions also work in bulk on the complement of the "
            "selection, keeping only what you selected in view.",
        ),
        QuickGuideStep(
            id="restore-hidden",
            target="#hidden-nodes",
            title="Restore hidden nodes",
            body="Nodes you hide are listed here individually and can be restored one at a time, or "
            "all at once with Reset.",
        ),
        QuickGuideStep(
            id="more-controls",
            target="#panel-toggle",
            title="More controls",
            body="Controls also holds edge-type filters, layout and geometry options, and the full "
            "How to use reference.",
        ),
    ),
)

__all__ = ["DEFAULT_QUICK_GUIDE"]

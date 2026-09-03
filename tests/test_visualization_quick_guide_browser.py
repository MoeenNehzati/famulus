from __future__ import annotations

import json

from officina.visualization.elk_html_renderer import ElkHtmlRenderer
from officina.visualization.html_renderer.quick_guide import QuickGuide, QuickGuideStep
from test_support.browser import require_chrome, run_html


def _run_quick_guide_case(
    script: str,
    payload: dict,
    guide: QuickGuide | None = None,
    *,
    virtual_time_budget: int = 7000,
    window_size: str | None = None,
) -> None:
    chrome = require_chrome()
    html = ElkHtmlRenderer(quick_guide=guide).render_graph_html(payload) if guide else ElkHtmlRenderer().render_graph_html(payload)
    html = html.replace(
        "</body>",
        f'''<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        const waitForLayout = async () => {{
          for (let attempt = 0; attempt < 200; attempt += 1) {{
            if (!document.getElementById("elk-status").textContent.includes("Rendering graph layout")) return;
            await delay(20);
          }}
          throw new Error("layout did not settle before interaction assertion");
        }};
        const run = async () => {{
          try {{
            document.body.dataset.testStatus = "RUNNING";
            await waitForLayout();
            {script}
            document.body.dataset.testStatus = "PASS";
          }} catch (error) {{
            document.body.dataset.testStatus = "FAIL:" + (error.message || String(error));
          }}
        }};
        window.addEventListener("load", () => setTimeout(() => {{ run(); }}, 150));
        </script></body>''',
    )
    result = run_html(chrome, html, virtual_time_budget=virtual_time_budget, window_size=window_size)
    marker = 'data-test-status="'
    marker_start = result.stdout.find(marker)
    status = (
        result.stdout[marker_start + len(marker) :].split('"', 1)[0]
        if marker_start >= 0
        else "MISSING"
    )
    assert status == "PASS", status


def _payload() -> dict:
    return {
        "schema_version": 2,
        "graph_id": "quick-guide-browser",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [{"id": "dependency", "label": "Dependency"}],
        "relation_semantics": {
            "transformations": {"node_omission": {"rules": []}},
            "subsumptions": [],
        },
        "detail_levels": [
            {"id": "overview", "label": "Overview"},
            {"id": "detail", "label": "Detail"},
        ],
        "entities": [
            {
                "id": "a",
                "type": "source",
                "category": "node",
                "short_title": "A",
                "position": 0,
                "detail_level": "overview",
                "connects_to": [{"to": "b", "type": "dependency"}],
            },
            {
                "id": "b",
                "type": "source",
                "category": "node",
                "short_title": "B",
                "position": 1,
                "detail_level": "overview",
                "connects_to": [],
            },
        ],
    }


def test_quick_guide_toolbar_hidden_when_disabled() -> None:
    _run_quick_guide_case(
        """
        if (!document.getElementById("quick-guide-toolbar-item").hidden) {
          throw new Error("toolbar item is shown when quick guide is disabled");
        }
        """,
        _payload(),
        guide=None,
    )


def test_quick_guide_toolbar_visibility_navigation_and_target_skips() -> None:
    guide = QuickGuide(
        title="Browser guide",
        steps=(
            QuickGuideStep(
                id="canvas", target="#canvas-wrap", title="Canvas", body="Canvas target"
            ),
            QuickGuideStep(
                id="bad", target="[invalid", title="Bad", body="Bad selector"
            ),
            QuickGuideStep(
                id="missing", target="#does-not-exist", title="Missing", body="Missing target"
            ),
            QuickGuideStep(
                id="hidden", target="#hidden-step-target", title="Hidden", body="Hidden target"
            ),
            QuickGuideStep(
                id="details", target="#details", title="Details", body="Details target"
            ),
            QuickGuideStep(
                id="fit", target="#fit-btn", title="Fit", body="Fit target"
            ),
        ),
    )

    _run_quick_guide_case(
        """
        const toolbarItem = document.getElementById("quick-guide-toolbar-item");
        if (toolbarItem.hidden) {
          throw new Error("enabled quick guide toolbar item is hidden");
        }

        const hiddenTarget = document.createElement("div");
        hiddenTarget.id = "hidden-step-target";
        hiddenTarget.textContent = "hidden";
        hiddenTarget.style.display = "none";
        document.body.appendChild(hiddenTarget);

        document.getElementById("quick-guide-btn").click();
        await delay(40);
        if (document.getElementById("quick-guide-dialog").hidden) {
          throw new Error("quick guide did not open");
        }
        const stepText = document.getElementById("quick-guide-step").textContent;
        if (stepText !== "1 of 3") {
          throw new Error(`expected first usable step text to be 1 of 3, got ${stepText}`);
        }
        document.getElementById("quick-guide-next").click();
        await delay(40);
        if (document.getElementById("quick-guide-step").textContent !== "2 of 3") {
          throw new Error(`expected second usable step text to be 2 of 3, got ${document.getElementById("quick-guide-step").textContent}`);
        }
        if (document.getElementById("quick-guide-title").textContent !== "Details") {
          throw new Error("first usable after skips was not Details");
        }
        if (document.getElementById("quick-guide-body").textContent !== "Details target") {
          throw new Error("details body missing");
        }
        document.getElementById("quick-guide-next").click();
        await delay(40);
        if (document.getElementById("quick-guide-title").textContent !== "Fit") {
          throw new Error("second usable after skips was not Fit");
        }
        if (document.getElementById("quick-guide-next").offsetParent !== null) {
          throw new Error("next should hide on final usable step");
        }
        if (document.getElementById("quick-guide-finish").offsetParent === null) {
          throw new Error("finish should show on final usable step");
        }
        document.getElementById("quick-guide-back").click();
        await delay(40);
        if (document.getElementById("quick-guide-title").textContent !== "Details") {
          throw new Error("back did not return to previous usable step");
        }
        document.getElementById("quick-guide-finish").click();
        await delay(20);
        if (!document.getElementById("quick-guide-dialog").hidden) {
          throw new Error("close did not hide dialog");
        }
        """,
        _payload(),
        guide=guide,
    )


def test_quick_guide_recovery_forward_then_backward_for_disappearing_target() -> None:
    guide = QuickGuide(
        title="Recovery guide",
        steps=(
            QuickGuideStep(id="canvas", target="#canvas-wrap", title="Canvas", body="Canvas target"),
            QuickGuideStep(
                id="disappearing", target="#disappearing-step-target", title="Disappear", body="Disappearing target"
            ),
            QuickGuideStep(id="fallback", target="#fallback-step-target", title="Fallback", body="Fallback target"),
        ),
    )

    _run_quick_guide_case(
        """
        const disappearing = document.createElement("div");
        disappearing.id = "disappearing-step-target";
        disappearing.textContent = "Disappear";
        document.body.appendChild(disappearing);
        const fallback = document.createElement("div");
        fallback.id = "fallback-step-target";
        fallback.textContent = "Fallback";
        fallback.style.padding = "12px";
        document.body.appendChild(fallback);

        document.getElementById("quick-guide-btn").click();
        await delay(40);
        document.getElementById("quick-guide-next").click();
        await delay(40);
        if (document.getElementById("quick-guide-title").textContent !== "Disappear") {
          throw new Error("guide did not start on disappearing target");
        }
        disappearing.style.display = "none";
        await delay(60);
        if (document.getElementById("quick-guide-title").textContent !== "Fallback") {
          throw new Error("guide did not recover forward after target disappeared");
        }
        fallback.style.display = "none";
        await delay(60);
        if (document.getElementById("quick-guide-title").textContent !== "Canvas") {
          throw new Error("guide did not recover backward when no usable forward target existed");
        }
        document.getElementById("quick-guide-close").click();
        await delay(20);
        """,
        _payload(),
        guide=guide,
    )


def test_quick_guide_geometry_and_resize_scroll_and_escape() -> None:
    guide = QuickGuide(
        title="Geometry guide",
        steps=(
            QuickGuideStep(
                id="details", target="#details", title="Details", body="Details target"
            ),
            QuickGuideStep(
                id="fit", target="#fit-btn", title="Fit", body="Fit target"
            ),
        ),
    )

    _run_quick_guide_case(
        """
        document.body.style.height = "2000px";
        document.getElementById("quick-guide-btn").click();
        await delay(40);
        document.getElementById("quick-guide-next").click();
        await delay(40);
        const dialog = document.getElementById("quick-guide-dialog");
        const hint = document.getElementById("quick-guide-highlight");
        if (dialog.hidden || hint.hidden) {
          throw new Error("guide dialog/hightlight not visible after opening");
        }
        const targetRect = document.getElementById("fit-btn").getBoundingClientRect();
        const highlightRect = hint.getBoundingClientRect();
        const dialogRect = dialog.getBoundingClientRect();
        if (dialogRect.left < 8 || dialogRect.top < 8 || dialogRect.right > window.innerWidth - 8 || dialogRect.bottom > window.innerHeight - 8) {
          throw new Error(`dialog left outside viewport margin: ${JSON.stringify(dialogRect)}`);
        }
        const overlaps = !(highlightRect.right < targetRect.left || highlightRect.left > targetRect.right || highlightRect.bottom < targetRect.top || highlightRect.top > targetRect.bottom);
        if (!overlaps) {
          throw new Error("highlight does not overlap target");
        }
        const beforeStep = document.getElementById("quick-guide-step").textContent;
        window.dispatchEvent(new Event("resize"));
        await delay(60);
        if (document.getElementById("quick-guide-step").textContent !== beforeStep) {
          throw new Error("resize changed logical step");
        }
        window.scrollTo(0, 120);
        window.dispatchEvent(new Event("scroll"));
        await delay(60);
        if (document.getElementById("quick-guide-step").textContent !== beforeStep) {
          throw new Error("scroll changed logical step");
        }
        document.body.dataset.testBeforeEsc = JSON.stringify(dialog.getBoundingClientRect());
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
        await delay(20);
        if (!dialog.hidden) throw new Error("escape did not close guide");
        """,
        _payload(),
        guide=guide,
        window_size="700,700",
    )


def test_quick_guide_shortcuts_do_not_escape_focus_while_open() -> None:
    _run_quick_guide_case(
        """
        setNodeSelection(["a"], "a", "explicit");
        const baselineUndo = JSON.stringify(graphUndoStack);
        document.body.style.height = "2500px";
        document.getElementById("quick-guide-btn").click();
        await delay(40);

        const beforeRenderVersion = renderVersion;
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "r", bubbles: true}));
        if (renderVersion !== beforeRenderVersion) {
          throw new Error("redraw shortcut changed render state while guide was focused");
        }

        document.getElementById("graph-filter-search").blur();
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "/", bubbles: true}));
        await delay(20);
        if (document.activeElement === document.getElementById("graph-filter-search")) {
          throw new Error("search shortcut moved focus while guide was focused");
        }

        document.dispatchEvent(new KeyboardEvent("keydown", {key: "z", ctrlKey: true, bubbles: true}));
        if (JSON.stringify(graphUndoStack) !== baselineUndo) {
          throw new Error("undo shortcut changed graph history while guide was focused");
        }

        document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
        await delay(20);
        if (!document.getElementById("quick-guide-dialog").hidden) {
          throw new Error("escape did not close guide");
        }

        document.getElementById("quick-guide-btn").click();
        await delay(40);
        const fit = document.getElementById("fit-btn");
        fit.focus();
        zoomAt(0.7, 120, 120);
        const before = {panX, panY, zoomLevel};
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "f", bubbles: true}));
        await delay(20);
        if (panX === before.panX && panY === before.panY && zoomLevel === before.zoomLevel) {
          throw new Error("fit shortcut was blocked while focus was outside guide");
        }
        """,
        _payload(),
        guide=QuickGuide(
            title="Shortcut guide",
            steps=(
                QuickGuideStep(
                    id="fit", target="#fit-btn", title="Fit", body="Fit target"
                ),
                QuickGuideStep(
                    id="details", target="#details", title="Details", body="Details target"
                ),
            ),
        ),
    )


def test_quick_guide_isolation_of_renderer_state() -> None:
    _run_quick_guide_case(
        """
        setNodeSelection(["a"], "a", "explicit");
        if (graphUndoStack.length === 0) {
          throw new Error("quick-start baseline does not have non-empty graph history");
        }

        const guideIsolationSnapshot = () => JSON.stringify({
          graph: graphStateSnapshot(),
          filter: serializeFilterState(),
          filterUndo: [...filterUndoStack],
          filterRedo: [...filterRedoStack],
          undo: JSON.parse(JSON.stringify(graphUndoStack)),
          redo: JSON.parse(JSON.stringify(graphRedoStack)),
          viewport: {panX, panY, zoomLevel},
          sidebars: {
            leftPanelCollapsed,
            rightPanelCollapsed,
            leftPanelWidth,
            rightPanelWidth,
          },
          renderVersion,
          mainStorage: localStorage.getItem(viewerStateKey),
          sidebarStorage: localStorage.getItem(viewerStateKey + "::sidebar"),
        });

        const baseline = guideIsolationSnapshot();
        document.getElementById("quick-guide-btn").click();
        await delay(40);
        if (document.getElementById("quick-guide-dialog").hidden) {
          throw new Error("guide did not open");
        }
        if (guideIsolationSnapshot() !== baseline) throw new Error("state changed immediately after open");

        document.getElementById("quick-guide-next").click();
        await delay(60);
        if (guideIsolationSnapshot() !== baseline) throw new Error("state changed after next");

        document.getElementById("quick-guide-back").click();
        await delay(60);
        if (guideIsolationSnapshot() !== baseline) throw new Error("state changed after back");

        window.dispatchEvent(new Event("resize"));
        await delay(60);
        if (guideIsolationSnapshot() !== baseline) throw new Error("state changed after resize");

        window.scrollTo(0, 80);
        window.dispatchEvent(new Event("scroll"));
        await delay(60);
        if (guideIsolationSnapshot() !== baseline) throw new Error("state changed after scroll");

        document.getElementById("quick-guide-finish").click();
        await delay(20);
        if (guideIsolationSnapshot() !== baseline) throw new Error("state changed after finish");
        """,
        _payload(),
        guide=QuickGuide(
            title="Isolation guide",
            steps=(
                QuickGuideStep(id="details", target="#details", title="Details", body="Details target"),
                QuickGuideStep(id="fit", target="#fit-btn", title="Fit", body="Fit target"),
                QuickGuideStep(id="canvas", target="#canvas-wrap", title="Canvas", body="Canvas target"),
            ),
        ),
    )

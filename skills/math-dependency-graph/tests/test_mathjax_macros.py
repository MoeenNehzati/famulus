#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = SKILL_DIR / "scripts"
FIXTURE_DIR = SKILL_DIR / "tests" / "fixtures" / "macro-paper"
sys.path.insert(0, str(SCRIPT_DIR))

from build_math_dependency_graph import TYPE_STYLES, build_html_with_elk  # noqa: E402
from extract_mathjax_macros import default_output_path, extract_macros  # noqa: E402


class MathJaxMacroExtractionTest(unittest.TestCase):
    def test_extracts_recursive_and_mid_document_macros(self) -> None:
        macros = extract_macros(FIXTURE_DIR / "main.tex")

        self.assertEqual(macros["R"], "\\mathbb{R}")
        self.assertEqual(macros["BFn"], "\\mathbf{n}")
        self.assertEqual(macros["ev"], "\\operatorname{ev}")
        self.assertEqual(macros["vQ"], "\\mathbf{Q}")
        self.assertEqual(macros["TC"], "\\operatorname{TC}")
        self.assertEqual(macros["QTC"], "\\vQ^{\\Pi_{\\TC_X}}")
        self.assertEqual(macros["MidMacro"], "\\QTC\\circ\\ev")
        self.assertEqual(macros["InnerMacro"], "\\operatorname{inner}")
        self.assertEqual(macros["OuterMacro"], [2, "\\InnerMacro(#1,#2)+\\QTC"])

    def test_cli_writes_default_build_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "macro-paper"
            shutil.copytree(FIXTURE_DIR, work)
            entrypoint = work / "main.tex"
            expected = default_output_path(entrypoint)

            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "extract_mathjax_macros.py"), str(entrypoint)],
                check=True,
                text=True,
                capture_output=True,
            )
            payload = json.loads(result.stdout)

            self.assertEqual(Path(payload["out"]), expected)
            self.assertTrue(expected.exists())
            written = json.loads(expected.read_text(encoding="utf-8"))
            self.assertIn("QTC", written)
            self.assertEqual(written["BFn"], "\\mathbf{n}")
            self.assertIn("OuterMacro", written)

    def test_renderer_generates_and_merges_default_macro_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "macro-paper"
            shutil.copytree(FIXTURE_DIR, work)
            graph = work / "graph.json"
            html_out = work / "_build" / "graph.html"
            macro_file = work / "_build" / "main-mathjax-macros.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "build_math_dependency_graph.py"),
                    str(graph),
                    "--tex-entry",
                    str(work / "main.tex"),
                    "--html-out",
                    str(html_out),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            payload = json.loads(result.stdout)

            self.assertEqual(Path(payload["macro_file"]), macro_file)
            self.assertTrue(macro_file.exists())
            self.assertTrue(html_out.exists())
            html = html_out.read_text(encoding="utf-8")
            self.assertIn('"BFn": "\\\\mathbf{n}"', html)
            self.assertIn('"QTC": "\\\\vQ^{\\\\Pi_{\\\\TC_X}}"', html)
            self.assertIn('"OuterMacro": [', html)
            self.assertIn('"ev": "\\\\operatorname{eval}"', html)

    def test_selected_node_uses_stable_outline_not_blue_filter(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertEqual(TYPE_STYLES["theorem"]["shape"], "rect")
        self.assertIn(".selection-ring", html)
        self.assertIn("stroke: #111111;", html)
        self.assertIn("stroke-width: 3;", html)
        self.assertIn("function expandSelectionRing(", html)
        self.assertIn("const SELECTION_RING_GAP = 6;", html)
        self.assertIn("const SELECTION_RING_STROKE_WIDTH = 3;", html)
        self.assertIn("extraClearance: 3", html)
        self.assertIn("const gap = SELECTION_RING_GAP;", html)
        self.assertIn("const offsetLines = rawPoints.map", html)
        self.assertIn("function lineIntersection(", html)
        self.assertLess(
            html.index('group.appendChild(selectionRing);'),
            html.index('group.appendChild(shapeEl);'),
        )
        self.assertIn(".graph-node.selected .node-shape", html)
        self.assertIn("filter: brightness(1.18) saturate(1.06);", html)
        self.assertIn('shapeEl.setAttribute("class", "node-shape")', html)
        self.assertIn('selectionRing.setAttribute("class", "selection-ring")', html)
        self.assertNotIn("rgba(41,128,185", html)

    def test_drag_release_reroutes_edges_without_relayout(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn("function manualDoglegPath(", html)
        self.assertIn("function edgeNodeGap(", html)
        self.assertIn("return SELECTION_RING_GAP + SELECTION_RING_STROKE_WIDTH + routingConfig.extraClearance;", html)
        self.assertIn("function offsetEdgeEndpoints(", html)
        self.assertIn("function offsetEndpointAwayFromNode(", html)
        self.assertIn("ts + edgeNodeGap()", html)
        self.assertIn("tt + edgeNodeGap()", html)
        self.assertIn("srcPos.x + srcPos.width + edgeNodeGap()", html)
        self.assertIn("dstPos.x - edgeNodeGap()", html)
        self.assertIn("const MIN_ARROW_LANDING_RUN = 18;", html)
        self.assertIn("const mergeLane = edgeNodeGap() + Math.max(MIN_ARROW_LANDING_RUN, routingConfig.mergeLaneDistance);", html)
        self.assertIn("const laneX = sourceOnRight ? dstPos.x - mergeLane : dstPos.x + dstPos.width + mergeLane;", html)
        self.assertIn("const key = pathEl.dataset.targetNodeId;", html)
        self.assertIn("function incidentEdgePaths(nodeId)", html)
        self.assertIn("function rerouteIncidentEdgesFromCurrentPositions(nodeId)", html)
        self.assertIn("function rerouteAllVisibleEdgesFromCurrentPositions()", html)
        mouseup_handler = html.split('document.addEventListener("mouseup", event => {', 1)[1].split("});", 1)[0]
        self.assertIn("rerouteIncidentEdgesFromCurrentPositions(droppedNodeId);", mouseup_handler)
        self.assertNotIn("updateVisibilityFull();", mouseup_handler)

    def test_arrowheads_are_synced_svg_polygons_not_markers(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn(".edge-arrow", html)
        self.assertIn("function pathPointsForArrow(pathEl)", html)
        self.assertIn("function syncArrowheadForPath(pathEl)", html)
        self.assertIn("function attachArrowhead(pathEl)", html)
        self.assertIn("path.dataset.edgeId = meta.edge_id || elkEdge.id;", html)
        self.assertIn("attachArrowhead(path);", html)
        self.assertIn("syncArrowheadForPath(pathEl);", html)
        self.assertIn('edgeLayer.querySelectorAll(".edge-arrow[data-bridge=\'true\']")', html)
        self.assertIn("arrowEl.setAttribute(\"fill\", pathEl.style.stroke || pathEl.getAttribute(\"stroke\") || \"#111111\");", html)
        self.assertNotIn('marker-end="url(#arrow)"', html)
        self.assertNotIn('setAttribute("marker-end"', html)

    def test_routing_controls_update_edges_and_layout(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn('<div class="routing-controls" id="routing-controls">', html)
        self.assertIn('<summary>Presets</summary>', html)
        self.assertIn('for="routing-compactness"', html)
        self.assertIn('>Graph spread</label>', html)
        self.assertIn('<summary>Advanced</summary>', html)
        self.assertIn('const routingPresets = {', html)
        self.assertIn('const shapePresets = {', html)
        self.assertIn('compact: { extraClearance: 0, parallelSpacing: 8, mergeLaneDistance: 18', html)
        self.assertIn('spacious: { extraClearance: 16, parallelSpacing: 36, mergeLaneDistance: 80', html)
        self.assertIn('curvy: { cornerRadius: 200 }', html)
        self.assertIn('max="260"', html)
        self.assertIn('function applyEdgeRoutingChange(patch)', html)
        self.assertIn('function applyLayoutRoutingChange(patch)', html)
        self.assertIn('rerouteAllVisibleEdgesFromCurrentPositions();', html)
        self.assertIn('updateVisibilityFull();', html)
        self.assertIn('routingCompactnessSelect.addEventListener("change"', html)
        self.assertIn('routingShapeSelect.addEventListener("change"', html)
        self.assertIn('routingConfig', html)
        self.assertIn('String(routingConfig.nodeSpacing)', html)
        self.assertIn('routingConfig.cornerRadius', html)
        self.assertIn('routingConfig.mergeLaneDistance', html)
        self.assertIn("function enforceVerticalNodeSpacing(children)", html)
        self.assertIn("routingConfig.nodeSpacing", html)
        self.assertIn("rerouteAllVisibleEdgesFromCurrentPositions();", html)
        self.assertIn('aria-label="Vertical cell spacing"', html)

    def test_http_page_watches_for_regenerated_html(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn('const GRAPH_BUILD_ID = "', html)
        self.assertIn("function startBuildRefreshWatcher()", html)
        self.assertIn('fetch(url.toString(), { cache: "no-store" })', html)
        self.assertIn('url.searchParams.set("graph_probe"', html)
        self.assertIn('reloadUrl.searchParams.set("graph_v", nextBuildId);', html)
        self.assertIn("window.location.replace(reloadUrl.toString());", html)

    def test_delete_paths_do_not_call_deselect_and_reset_view(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        toolbar_handler = html.split('deleteNodeBtn.addEventListener("click", () => {', 1)[1].split("});", 1)[0]
        double_click_handler = html.split('nodeEl.addEventListener("dblclick", event => {', 1)[1].split("});", 1)[0]
        self.assertIn("clearSelectionDetails();", toolbar_handler)
        self.assertNotIn("deselect();", toolbar_handler)
        self.assertIn("clearSelectionDetails();", double_click_handler)
        self.assertNotIn("deselect();", double_click_handler)

    def test_restore_hidden_node_reroutes_incident_edges(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        restore_handler = html.split('item.addEventListener("dblclick", () => {', 1)[1].split("});", 1)[0]
        self.assertIn("hiddenNodes.delete(entity.id);", restore_handler)
        self.assertIn("updateVisibilityFast();", restore_handler)
        self.assertIn("rerouteIncidentEdgesFromCurrentPositions(entity.id);", restore_handler)
        self.assertLess(
            restore_handler.index("updateVisibilityFast();"),
            restore_handler.index("rerouteIncidentEdgesFromCurrentPositions(entity.id);"),
        )

    def test_toolbar_help_uses_wrapper_tooltips_for_disabled_buttons(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn(".toolbar-tip::after", html)
        self.assertIn('data-tooltip="Hide the selected node from the graph.', html)
        self.assertIn('<button id="delete-node-btn" class="toolbar-btn" type="button" disabled aria-label="Hide selected node">', html)

    def test_reset_click_preserves_categories_double_click_clears_legend(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn('<button id="reset-btn" class="toolbar-btn" type="button" aria-label="Reset graph state">Reset</button>', html)
        self.assertIn('resetViewState({includeCategories: false});', html)
        self.assertIn('resetViewState({includeCategories: true});', html)
        self.assertIn('row.classList.toggle("inactive", hiddenTypes.has(row.dataset.type));', html)
        self.assertNotIn('document.querySelectorAll(".legend-btn")', html)

    def test_browser_smoke_for_core_interactions(self) -> None:
        chrome = shutil.which("google-chrome")
        if chrome is None:
            self.skipTest("google-chrome is not available")

        doc = {
            "document": {"title": "Browser Smoke"},
            "entities": [
                {
                    "id": "root",
                    "type": "definition",
                    "ref": "",
                    "short_title": "Root",
                    "title": "Root",
                    "description": "Root definition.",
                    "defined": "Fixture",
                    "active_in": "Fixture",
                    "source": "explicit",
                    "depends_on": [],
                    "position": 1,
                },
                {
                    "id": "other",
                    "type": "definition",
                    "ref": "",
                    "short_title": "Other",
                    "title": "Other",
                    "description": "Other definition.",
                    "defined": "Fixture",
                    "active_in": "Fixture",
                    "source": "explicit",
                    "depends_on": [],
                    "position": 2,
                },
                {
                    "id": "child",
                    "type": "theorem",
                    "ref": "",
                    "short_title": "Child",
                    "title": "Child",
                    "description": "Depends on two definitions.",
                    "defined": "Fixture",
                    "active_in": "Fixture",
                    "source": "explicit",
                    "depends_on": [
                        {"id": "root", "use_type": "uses", "description": "Uses root.", "confidence": "Verified", "evidence": "fixture"},
                        {"id": "other", "use_type": "uses", "description": "Uses other.", "confidence": "Verified", "evidence": "fixture"},
                    ],
                    "position": 3,
                },
                {
                    "id": "stray",
                    "type": "remark",
                    "ref": "",
                    "short_title": "Stray",
                    "title": "Stray",
                    "description": "Non-ancestor.",
                    "defined": "Fixture",
                    "active_in": "Fixture",
                    "source": "explicit",
                    "depends_on": [],
                    "position": 4,
                },
            ],
        }
        html = build_html_with_elk(doc)
        html = html.replace(
            '<script src="https://cdn.jsdelivr.net/npm/elkjs/lib/elk.bundled.js"></script>',
            """
            <script>
              class ELK {
                async layout(graph) {
                  const positions = {root: [40, 80], other: [40, 210], child: [340, 145], stray: [340, 300]};
                  graph.children.forEach((node, idx) => {
                    const pos = positions[node.id] || [40 + idx * 260, 80];
                    node.x = pos[0]; node.y = pos[1];
                  });
                  graph.edges.forEach((edge, idx) => {
                    const source = graph.children.find(node => node.id === edge.sources[0]);
                    const target = graph.children.find(node => node.id === edge.targets[0]);
                    edge.sections = [{
                      startPoint: {x: source.x + source.width, y: source.y + source.height / 2},
                      bendPoints: [{x: 260, y: source.y + source.height / 2}, {x: 260, y: target.y + target.height / 2}],
                      endPoint: {x: target.x, y: target.y + target.height / 2}
                    }];
                  });
                  graph.width = 620; graph.height = 430;
                  return graph;
                }
              }
            </script>
            """,
        )
        html = html.replace(
            '<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>',
            """
            <script>
              window.MathJax.typesetPromise = () => Promise.resolve();
              window.MathJax.typesetClear = () => {};
            </script>
            """,
        )
        html = html.replace(
            "</body>",
            """
            <script>
              function fail(message) {
                document.body.setAttribute("data-test-status", "FAIL:" + message);
                document.title = "FAIL:" + message;
              }
              function pass() {
                document.body.setAttribute("data-test-status", "PASS");
                document.title = "PASS";
              }
              function waitFor(selector, timeout = 1000) {
                const started = Date.now();
                return new Promise((resolve, reject) => {
                  const tick = () => {
                    const el = document.querySelector(selector);
                    if (el) return resolve(el);
                    if (Date.now() - started > timeout) return reject(new Error("missing " + selector));
                    setTimeout(tick, 20);
                  };
                  tick();
                });
              }
              window.addEventListener("load", () => {
                setTimeout(async () => {
                  try {
                    const root = await waitFor('[data-node-id="root"]');
                    const child = await waitFor('[data-node-id="child"]');
                    const theoremLegend = Array.from(document.querySelectorAll(".legend-row"))
                      .find(row => row.dataset.type === "theorem");
                    theoremLegend.click();
                    if (!theoremLegend.classList.contains("inactive")) throw new Error("legend did not hide theorem");
                    document.getElementById("reset-btn").click();
                    await new Promise(resolve => setTimeout(resolve, 240));
                    if (!theoremLegend.classList.contains("inactive")) throw new Error("single reset cleared category");
                    document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
                    await new Promise(resolve => setTimeout(resolve, 60));
                    if (theoremLegend.classList.contains("inactive")) throw new Error("double reset did not clear category");

                    child.dispatchEvent(new MouseEvent("click", {bubbles: true, clientX: 360, clientY: 160}));
                    await new Promise(resolve => setTimeout(resolve, 220));
                    document.getElementById("focus-toggle").click();
                    document.getElementById("focus-toggle").click();
                    await new Promise(resolve => setTimeout(resolve, 60));
                    const stray = document.querySelector('[data-node-id="stray"]');
                    if (stray.style.display !== "none") throw new Error("focus mode did not hide non-ancestor");
                    root.dispatchEvent(new MouseEvent("click", {bubbles: true, clientX: 60, clientY: 100}));
                    await new Promise(resolve => setTimeout(resolve, 220));
                    document.getElementById("delete-node-btn").click();
                    await new Promise(resolve => setTimeout(resolve, 60));
                    if (stray.style.display !== "none") throw new Error("delete reset ancestor focus");

                    document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
                    await new Promise(resolve => setTimeout(resolve, 80));
                    const unaffected = document.querySelector('[data-source-node-id="other"][data-target-node-id="child"]');
                    const affected = document.querySelector('[data-source-node-id="root"][data-target-node-id="child"]');
                    const unaffectedBefore = unaffected.getAttribute("d");
                    const affectedBefore = affected.getAttribute("d");
                    root.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 60, clientY: 100}));
                    document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 120, clientY: 130}));
                    document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 120, clientY: 130}));
                    await new Promise(resolve => setTimeout(resolve, 60));
                    if (affected.getAttribute("d") === affectedBefore) throw new Error("incident edge did not reroute");
                    if (unaffected.getAttribute("d") !== unaffectedBefore) throw new Error("unaffected edge rerouted");

                    document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
                    await new Promise(resolve => setTimeout(resolve, 80));
                    const childAfterReset = document.querySelector('[data-node-id="child"]');
                    const rootChildAfterReset = document.querySelector('[data-source-node-id="root"][data-target-node-id="child"]');
                    const staleRootChildPath = rootChildAfterReset.getAttribute("d");
                    childAfterReset.dispatchEvent(new MouseEvent("dblclick", {bubbles: true, clientX: 360, clientY: 160}));
                    await new Promise(resolve => setTimeout(resolve, 80));
                    root.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 60, clientY: 100}));
                    document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 140, clientY: 150}));
                    document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 140, clientY: 150}));
                    await new Promise(resolve => setTimeout(resolve, 80));
                    const removedChild = Array.from(document.querySelectorAll(".removed-item"))
                      .find(item => item.textContent.includes("Child"));
                    if (!removedChild) throw new Error("removed child not listed");
                    removedChild.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
                    await new Promise(resolve => setTimeout(resolve, 80));
                    if (rootChildAfterReset.style.display === "none") throw new Error("restored edge stayed hidden");
                    if (rootChildAfterReset.getAttribute("d") === staleRootChildPath) throw new Error("restored edge used stale path");
                    pass();
                  } catch (error) {
                    fail(error.message || String(error));
                  }
                }, 80);
              });
            </script>
            </body>
            """,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "browser-smoke.html"
            path.write_text(html, encoding="utf-8")
            try:
                result = subprocess.run(
                    [
                        chrome,
                        "--headless",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--virtual-time-budget=3000",
                        "--dump-dom",
                        path.as_uri(),
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:
                self.skipTest(f"headless Chrome failed in this environment: {exc}")
        self.assertIn('data-test-status="PASS"', result.stdout)


# ---------------------------------------------------------------------------
# TODO: Playwright browser tests
#
# The existing test_browser_smoke_for_core_interactions uses raw Chrome + an
# injected JS assertion script, which can only check a pass/fail flag. A proper
# Playwright suite would cover the interactive behaviors that the current HTML-
# structure tests cannot reach. Recommended scope:
#
#   Infrastructure
#   - Session-scoped fixture that starts serve_graph.py as a subprocess and
#     tears it down after the suite. Build the HTML from a small fixture JSON
#     into a temp directory that the server serves.
#   - Wait helper: poll for `document.querySelector(".graph-node")` to appear
#     (ELK is async; layout completes in a Web Worker).
#
#   Test cases
#   - Nodes render: at least N .graph-node elements appear after ELK finishes.
#   - Side panel: click a node → panel shows the correct short_title and ref.
#   - Hover tooltip: hover a node → tooltip div appears with the right type text.
#   - Node drag: mouse-drag a node → its SVG transform attribute changes.
#   - Edge reroute after drag: after dragging a node, at least one .edge-path
#     `d` attribute changes (edges follow the moved node).
#   - Legend filter: click a type chip → nodes of that type get display:none;
#     a bridge edge (.edge-path[data-bridge="true"]) appears if applicable.
#   - Ancestor focus: click a node, press "h" twice → non-ancestor nodes
#     get opacity 0.18 (dim mode) then display:none (hide mode).
#   - Double-click reset: double-click the Reset button → routingConfig returns
#     to balanced/soft defaults (read from localStorage or a JS eval).
#   - localStorage persistence: reload the page → selected node and routing
#     config are restored from localStorage.
#   - MathJax: after page load, at least one <svg> element exists inside the
#     node layer (MathJax rendered something). Don't try to assert correctness
#     of the SVG output — just that it ran.
#
#   Install: pip install playwright && playwright install chromium
#   Run:     pytest tests/test_browser_playwright.py
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()

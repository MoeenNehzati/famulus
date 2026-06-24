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

from build_math_dependency_graph import build_html_with_elk  # noqa: E402
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

        self.assertIn(".selection-ring", html)
        self.assertIn("stroke: #f8fbff;", html)
        self.assertIn("stroke-width: 6;", html)
        self.assertIn(".graph-node.selected .node-shape", html)
        self.assertIn("filter: brightness(1.18) saturate(1.06);", html)
        self.assertIn('shapeEl.setAttribute("class", "node-shape")', html)
        self.assertIn('selectionRing.setAttribute("class", "selection-ring")', html)
        self.assertNotIn("rgba(41,128,185", html)

    def test_drag_release_reroutes_edges_without_relayout(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn("function manualDoglegPath(", html)
        self.assertIn("function incidentEdgePaths(nodeId)", html)
        self.assertIn("function rerouteIncidentEdgesFromCurrentPositions(nodeId)", html)
        mouseup_handler = html.split('document.addEventListener("mouseup", event => {', 1)[1].split("});", 1)[0]
        self.assertIn("rerouteIncidentEdgesFromCurrentPositions(droppedNodeId);", mouseup_handler)
        self.assertNotIn("updateVisibilityFull();", mouseup_handler)

    def test_delete_paths_do_not_call_deselect_and_reset_view(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        toolbar_handler = html.split('deleteNodeBtn.addEventListener("click", () => {', 1)[1].split("});", 1)[0]
        double_click_handler = html.split('nodeEl.addEventListener("dblclick", event => {', 1)[1].split("});", 1)[0]
        self.assertIn("clearSelectionDetails();", toolbar_handler)
        self.assertNotIn("deselect();", toolbar_handler)
        self.assertIn("clearSelectionDetails();", double_click_handler)
        self.assertNotIn("deselect();", double_click_handler)

    def test_toolbar_help_uses_wrapper_tooltips_for_disabled_buttons(self) -> None:
        doc = json.loads((FIXTURE_DIR / "graph.json").read_text(encoding="utf-8"))

        html = build_html_with_elk(doc)

        self.assertIn(".toolbar-tip::after", html)
        self.assertIn('data-tooltip="Hide the selected node from the visible graph.', html)
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


if __name__ == "__main__":
    unittest.main()

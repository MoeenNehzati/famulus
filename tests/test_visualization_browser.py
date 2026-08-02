from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import pytest

from officina.common.visualization.elk_html_renderer import build_html_with_elk


def test_filter_interactions_keep_layout_and_explain_projection():
    chrome = shutil.which("google-chrome")
    if chrome is None:
        # famulus-skip: category=capability-unavailable; reason=Google Chrome is not installed; alternate=renderer contract tests cover payload and HTML generation
        pytest.skip("google-chrome unavailable")
    doc = {
        "schema_version": 1,
        "graph_id": "temporary-filter-smoke",
        "categories": [
            {"id": "element", "label": "Element"},
            {"id": "endpoint", "label": "Endpoint", "parent": "element"},
            {"id": "artifact", "label": "Artifact", "parent": "element"},
        ],
        "edge_categories": [
            {"id": "dependency", "label": "Dependency"},
            {"id": "uses", "label": "Uses", "parent": "dependency"},
        ],
        "detail_levels": [
            {"id": "overview", "label": "Overview"},
            {"id": "component", "label": "Components"},
            {"id": "item", "label": "Items"},
        ],
        "entities": [
            {"id": "root", "type": "group", "detail_level": "overview", "kind": "composite", "category": "element", "short_title": "Root", "position": 0, "connects_to": [{"to": "alpha", "type": "dependency", "description": "owns execution"}]},
            {"id": "nested", "type": "group", "detail_level": "overview", "kind": "composite", "category": "element", "short_title": "Nested", "container": "root", "position": 1, "connects_to": []},
            {"id": "alpha", "type": "port", "detail_level": "item", "kind": "service", "category": "endpoint", "short_title": "Alpha API", "container": "nested", "position": 2, "connects_to": [{"to": "beta", "type": "uses", "description": "reads records"}, {"to": "beta", "type": "uses", "description": "updates records"}, {"to": "beta", "type": "dependency", "description": "requires storage"}]},
            {"id": "beta", "type": "record", "detail_level": "component", "kind": "data", "category": "artifact", "short_title": "Beta Source", "container": "nested", "position": 3, "connects_to": []},
            {"id": "odd\"node", "type": "record", "detail_level": "component", "kind": "service", "category": "artifact", "short_title": "Odd Node", "container": "nested", "position": 4, "connects_to": []},
        ],
    }
    html = build_html_with_elk(doc).replace(
        '<script src="https://cdn.jsdelivr.net/npm/elkjs/lib/elk.bundled.js"></script>',
        """<script>
        class ELK {
          async layout(graph) {
            const flatten = nodes => nodes.flatMap(node => [node, ...flatten(node.children || [])]);
            const nodes = flatten(graph.children || []);
            nodes.forEach((node, index) => { node.x = 50 + index * 230; node.y = 80 + index * 30; });
            const byId = new Map(nodes.map(node => [node.id, node]));
            (graph.edges || []).forEach(edge => {
              const source = byId.get(edge.sources[0]);
              const target = byId.get(edge.targets[0]);
              edge.sections = [{startPoint: {x: source.x + source.width, y: source.y + 30}, endPoint: {x: target.x, y: target.y + 30}}];
            });
            graph.width = 900; graph.height = 500;
            return graph;
          }
        }
        </script>""",
    )
    html = html.replace(
        "</body>",
        """<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        const fail = message => { document.body.dataset.testStatus = "FAIL:" + message; document.title = "FAIL:" + message; };
        const pass = () => { document.body.dataset.testStatus = "PASS"; document.title = "PASS"; };
        window.addEventListener("load", () => setTimeout(async () => {
          try {
            const alpha = document.querySelector('[data-node-id="alpha"]');
            const beta = document.querySelector('[data-node-id="beta"]');
            const root = document.querySelector('[data-node-id="root"]');
            if (!alpha || !beta || !root) throw new Error("initial nodes missing");
            if (!alpha.hasAttribute("tabindex")) throw new Error("nodes are not keyboard focusable");
            if (!document.querySelector(".legend-row[tabindex]")) throw new Error("legend is not keyboard focusable");
            if (!document.querySelector('meta[name="viewport"]')) throw new Error("mobile viewport metadata missing");
            if (!document.getElementById("details").closest("#left-panel")) throw new Error("selection details are not in left inspector");
            if (document.getElementById("left-panel-toggle").getAttribute("aria-expanded") !== "true") throw new Error("left inspector did not start open");
            if (document.getElementById("panel-toggle").getAttribute("aria-expanded") !== "true") throw new Error("right controls did not start open");
            const bundledEdges = document.querySelectorAll('.edge-path[data-source-node-id="alpha"][data-target-node-id="beta"]');
            if (bundledEdges.length !== 1 || bundledEdges[0].dataset.bundle !== "true") throw new Error("parallel relationships were not bundled into one path");
            bundledEdges[0].dispatchEvent(new MouseEvent("click", {bubbles: true}));
            if (!document.getElementById("details").textContent.includes("3 visible relationships") || !document.getElementById("details").textContent.includes("updates records")) throw new Error("bundle inspector lost constituent annotations");
            const containmentEdge = document.querySelector('.edge-path[data-source-node-id="root"][data-target-node-id="alpha"]');
            const rootPos = lastNodePositions.get("root");
            const coordinates = (containmentEdge?.getAttribute("d") || "").match(/-?\\d+(?:\\.\\d+)?/g)?.map(Number) || [];
            for (let index = 0; index + 1 < coordinates.length; index += 2) {
              const x = coordinates[index]; const y = coordinates[index + 1];
              if (x < rootPos.x - 1 || x > rootPos.x + rootPos.width + 1 || y < rootPos.y - 1 || y > rootPos.y + rootPos.height + 1) throw new Error("containment edge routed outside its container");
            }
            const lifecycleRenderVersion = renderVersion;
            const initialBundlePath = bundledEdges[0].getAttribute("d");
            const lifecycleParentLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="dependency"]');
            const lifecycleChildLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="uses"]');
            lifecycleParentLegend.click();
            if (bundledEdges[0].style.display !== "none" || arrowForPath(bundledEdges[0])?.style.display !== "none") throw new Error("edge legend did not hide path and arrowhead");
            if (renderVersion !== lifecycleRenderVersion) throw new Error("edge legend hide triggered relayout");
            lifecycleParentLegend.click();
            if (bundledEdges[0].style.display === "none" || arrowForPath(bundledEdges[0])?.style.display === "none") throw new Error("edge legend did not restore path and arrowhead");
            if (bundledEdges[0].getAttribute("d") !== initialBundlePath || renderVersion !== lifecycleRenderVersion) throw new Error("edge legend restore changed layout");
            lifecycleChildLegend.click();
            if (bundledEdges[0].style.display === "none") throw new Error("hiding one bundle constituent hid surviving relationship types");
            bundledEdges[0].dispatchEvent(new MouseEvent("click", {bubbles: true}));
            if (!document.getElementById("details").textContent.includes("1 visible relationship") || document.getElementById("details").textContent.includes("updates records")) throw new Error("bundle inspector ignored constituent visibility");
            lifecycleChildLegend.click();
            bundledEdges[0].dispatchEvent(new MouseEvent("click", {bubbles: true}));
            if (!document.getElementById("details").textContent.includes("3 visible relationships")) throw new Error("bundle constituent restore did not restore inspector data");
            const alphaBeforeDrag = {...getEffectivePos("alpha")};
            alpha.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 200, clientY: 200}));
            document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 224, clientY: 212}));
            document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 224, clientY: 212}));
            await delay(20);
            const alphaAfterDrag = getEffectivePos("alpha");
            const movedBundlePath = bundledEdges[0].getAttribute("d");
            if (!alphaAfterDrag || (alphaAfterDrag.x === alphaBeforeDrag.x && alphaAfterDrag.y === alphaBeforeDrag.y)) throw new Error("node drag did not update effective position");
            if (movedBundlePath === initialBundlePath || renderVersion !== lifecycleRenderVersion) throw new Error("node drag did not reroute edge in place");
            alpha.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(20);
            if (bundledEdges[0].style.display !== "none") throw new Error("hiding moved node retained incident edge");
            const lifecycleRestore = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes("Alpha API"));
            if (!lifecycleRestore) throw new Error("hidden moved node was not restorable");
            lifecycleRestore.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
            await delay(20);
            if (bundledEdges[0].style.display === "none" || bundledEdges[0].getAttribute("d") !== movedBundlePath) throw new Error("restoring moved node did not restore edge geometry");
            const movedContainmentEdge = document.querySelector('.edge-path[data-source-node-id="root"][data-target-node-id="alpha"]');
            const movedCoordinates = (movedContainmentEdge?.getAttribute("d") || "").match(/-?\\d+(?:\\.\\d+)?/g)?.map(Number) || [];
            for (let index = 0; index + 1 < movedCoordinates.length; index += 2) {
              const x = movedCoordinates[index]; const y = movedCoordinates[index + 1];
              if (x < rootPos.x - 1 || x > rootPos.x + rootPos.width + 1 || y < rootPos.y - 1 || y > rootPos.y + rootPos.height + 1) throw new Error("moved containment edge escaped its container");
            }
            alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            await delay(220);
            beta.dispatchEvent(new MouseEvent("click", {bubbles: true, ctrlKey: true}));
            await delay(220);
            if (selectedNodeIds.size !== 2 || selectedNodeId !== "beta" || !alpha.classList.contains("selected") || !beta.classList.contains("selected")) throw new Error("additive node selection failed");
            if (!document.getElementById("details").textContent.includes("2 nodes selected")) throw new Error("multi-selection summary missing");
            const selectionRenderVersion = renderVersion;
            document.getElementById("dim-selected-btn").click();
            if (!alpha.classList.contains("user-dimmed") || !beta.classList.contains("user-dimmed")) throw new Error("bulk dim did not affect selection");
            if (renderVersion !== selectionRenderVersion) throw new Error("bulk dim triggered graph relayout");
            document.getElementById("dim-selected-btn").click();
            document.getElementById("hide-selected-btn").click();
            await delay(20);
            if (!hiddenNodes.has("alpha") || !hiddenNodes.has("beta") || selectedNodeIds.size !== 0) throw new Error("bulk hide did not consume selection");
            for (const label of ["Alpha API", "Beta Source"]) {
              const restoreItem = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes(label));
              if (!restoreItem) throw new Error("bulk-hidden node missing from restoration list");
              restoreItem.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
              await delay(20);
            }
            const renderVersionBeforeResize = renderVersion;
            const leftWidthBefore = leftPanelWidth;
            document.getElementById("left-panel-resize").dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
            if (leftPanelWidth <= leftWidthBefore) throw new Error("keyboard resize did not widen left inspector");
            if (renderVersion !== renderVersionBeforeResize) throw new Error("sidebar resize triggered graph relayout");
            document.getElementById("left-panel-toggle").click();
            if (!layoutEl.classList.contains("left-panel-collapsed") || layoutEl.classList.contains("right-panel-collapsed")) throw new Error("left collapse affected wrong panel");
            document.getElementById("left-panel-toggle").click();
            document.getElementById("panel-toggle").click();
            if (!layoutEl.classList.contains("right-panel-collapsed") || layoutEl.classList.contains("left-panel-collapsed")) throw new Error("right collapse affected wrong panel");
            document.getElementById("panel-toggle").click();
            const odd = nodeElement('odd"node');
            odd.dispatchEvent(new MouseEvent("mouseenter", {bubbles: true, clientX: window.innerWidth - 1, clientY: window.innerHeight - 1}));
            const tooltipRect = document.getElementById("tooltip").getBoundingClientRect();
            if (tooltipRect.right > window.innerWidth || tooltipRect.bottom > window.innerHeight) throw new Error("tooltip escaped viewport");
            odd.dispatchEvent(new MouseEvent("mouseleave", {bubbles: true}));
            if (window.innerWidth <= 720 && getComputedStyle(document.querySelector(".layout")).gridTemplateColumns.split(" ").length !== 1) throw new Error("mobile layout kept fixed sidebar column");
            const alphaTransform = alpha.getAttribute("transform");
            const search = document.getElementById("graph-filter-search");
            search.focus(); search.value = "alpha";
            search.dispatchEvent(new Event("input", {bubbles: true}));
            if (!selectedNodeIds.has("alpha") || selectionSource !== "search") throw new Error("find did not create node selection");
            if (beta.classList.contains("selected") || beta.style.display === "none") throw new Error("find changed nonmatching node visibility");
            if (document.querySelector('[data-node-id="alpha"]') !== alpha) throw new Error("filter replaced surviving node");
            if (alpha.getAttribute("transform") !== alphaTransform) throw new Error("filter moved surviving node");
            search.value = "beta"; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            if (selectedNodeIds.has("alpha") || !selectedNodeIds.has("beta")) throw new Error("find selection stayed stale");
            search.value = "uses"; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            if (alpha.style.display === "none" || beta.style.display === "none") throw new Error("matching relation endpoints were not retained");
            if (!selectedNodeIds.has("alpha") || !selectedNodeIds.has("beta")) throw new Error("relation find did not select endpoints");
            document.getElementById("dim-selected-btn").click();
            if (!dimmedNodes.has("alpha") || !dimmedNodes.has("beta")) throw new Error("search selection did not share bulk dim action");
            document.getElementById("dim-selected-btn").click();
            search.value = ""; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            if (selectedNodeIds.size !== 0 || alpha.style.display === "none" || beta.style.display === "none") throw new Error("clearing find left stale selection or visibility");
            search.value = "alpha"; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            const relation = document.querySelector('input[data-filter-key="excludedEdgeTypes"][data-filter-value="dependency"]');
            relation.checked = false; relation.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(20);
            const relationChild = document.querySelector('input[data-filter-key="excludedEdgeTypes"][data-filter-value="uses"]');
            if (!relationChild.disabled || relationChild.checked) throw new Error("edge-category child contradicted excluded parent");
            const edge = document.querySelector('.edge-path[data-source-node-id="alpha"][data-target-node-id="beta"]');
            if (edge && edge.style.display !== "none") throw new Error("relation facet did not hide edge");
            if (edge && !edge.hasAttribute("tabindex")) throw new Error("edges are not keyboard focusable");
            document.getElementById("filter-undo").click();
            await delay(20);
            search.focus(); search.value = "beta";
            search.dispatchEvent(new Event("input", {bubbles: true}));
            search.dispatchEvent(new Event("change", {bubbles: true}));
            const historyQueries = filterUndoStack.map(item => JSON.parse(item).query).slice(-6).join("|");
            document.getElementById("filter-undo").click();
            if (search.value !== "alpha") throw new Error("undo=" + JSON.stringify(search.value) + ";history=" + historyQueries);
            const summary = document.getElementById("filter-summary").textContent;
            if (!summary.includes("nodes") || !summary.includes("relations")) throw new Error("visibility summary missing");
            document.getElementById("filter-clear").click();
            const dataKind = document.querySelector('input[data-filter-key="excludedKinds"][data-filter-value="data"]');
            dataKind.checked = false; dataKind.dispatchEvent(new Event("change", {bubbles: true}));
            document.getElementById("filter-undo").click();
            if (!document.querySelector('input[data-filter-key="excludedKinds"][data-filter-value="data"]').checked) throw new Error("undo did not restore facet");
            document.getElementById("filter-redo").click();
            if (document.querySelector('input[data-filter-key="excludedKinds"][data-filter-value="data"]').checked) throw new Error("redo did not restore facet");
            document.getElementById("filter-clear").click();
            const parentCategory = document.querySelector('input[data-filter-key="excludedCategories"][data-filter-value="element"]');
            parentCategory.checked = false; parentCategory.dispatchEvent(new Event("change", {bubbles: true}));
            const childCategory = document.querySelector('input[data-filter-key="excludedCategories"][data-filter-value="endpoint"]');
            if (childCategory.checked || !childCategory.disabled) throw new Error("child category contradicted excluded parent");
            document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(30);
            if (document.getElementById("graph-filter-search").value !== "") throw new Error("full reset kept search");
            if (!document.querySelector('input[data-filter-key="excludedCategories"][data-filter-value="element"]').checked) throw new Error("full reset kept category filter");
            const focusAlpha = document.querySelector('[data-node-id="alpha"]');
            const focusBeta = document.querySelector('[data-node-id="beta"]');
            const legendParent = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="dependency"]');
            const legendChild = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="uses"]');
            legendParent.click();
            if (legendChild.getAttribute("aria-disabled") !== "true") throw new Error("edge legend hierarchy did not disable child");
            legendParent.click();
            focusBeta.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            await delay(250);
            const focusRelation = document.querySelector('input[data-filter-key="excludedEdgeTypes"][data-filter-value="dependency"]');
            focusRelation.checked = false; focusRelation.dispatchEvent(new Event("change", {bubbles: true}));
            document.getElementById("focus-toggle").click();
            await delay(20);
            if (focusAlpha.style.opacity !== "0.18") throw new Error("ancestor focus traversed an excluded relation");
            document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(20);
            const restoredOdd = nodeElement('odd"node');
            restoredOdd.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(20);
            const hiddenItem = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes("Odd Node"));
            if (!hiddenItem || !hiddenItem.closest("#left-panel") || hiddenItem.getAttribute("role") !== "button" || hiddenItem.tabIndex !== 0) throw new Error("hidden node is not keyboard accessible in left inspector");
            hiddenItem.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
            await delay(20);
            if (restoredOdd.style.display === "none") throw new Error("keyboard restore did not reveal hidden node");
            const detailLevel = document.getElementById("graph-detail-level");
            detailLevel.value = "overview";
            detailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(250);
            const overviewRoot = document.querySelector('[data-node-id="root"]');
            const overviewNested = document.querySelector('[data-node-id="nested"]');
            const overviewAlpha = document.querySelector('[data-node-id="alpha"]');
            if (!overviewRoot?.querySelector(".detail-promoted")) throw new Error("overview did not promote container label");
            if (!overviewRoot.querySelector(".detail-promoted-branch") || !overviewNested?.querySelector(".detail-promoted-leaf")) throw new Error("detail promotion did not distinguish branch and leaf containers");
            const rootLabelSize = parseFloat(getComputedStyle(overviewRoot.querySelector(".node-label")).fontSize);
            const nestedLabelSize = parseFloat(getComputedStyle(overviewNested.querySelector(".node-label")).fontSize);
            if (!(rootLabelSize > nestedLabelSize)) throw new Error("nested container label outranked its supernode");
            if (overviewAlpha && overviewAlpha.style.display !== "none") throw new Error("overview retained fine-detail node");
            Object.defineProperty(window, "innerWidth", {value: 650, configurable: true});
            window.dispatchEvent(new Event("resize"));
            if (!layoutEl.classList.contains("narrow-layout")) throw new Error("narrow viewport did not enter responsive layout");
            if (document.getElementById("left-panel-toggle").getAttribute("aria-expanded") !== "false" || document.getElementById("panel-toggle").getAttribute("aria-expanded") !== "false") throw new Error("narrow viewport did not collapse both panels");
            Object.defineProperty(window, "innerWidth", {value: 800, configurable: true});
            window.dispatchEvent(new Event("resize"));
            if (document.getElementById("left-panel-toggle").getAttribute("aria-expanded") !== "true" || document.getElementById("panel-toggle").getAttribute("aria-expanded") !== "true") throw new Error("desktop panel preferences were not restored");
            pass();
          } catch (error) { fail(error.message || String(error)); }
        }, 100));
        </script></body>""",
    )
    path = Path("/tmp/officina-filter-smoke.html")
    path.write_text(html, encoding="utf-8")
    if os.environ.get("BUILD_ONLY") == "1":
        return
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run(
            [
                chrome,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=5000",
                "--dump-dom",
                path.as_uri(),
            ],
            check=True, capture_output=True, text=True,
        )
    assert 'data-test-status="PASS"' in result.stdout

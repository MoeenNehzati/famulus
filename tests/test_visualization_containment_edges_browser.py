"""Browser regression coverage for edges whose endpoints contain one another."""

from officina.visualization.elk_html_renderer import build_html_with_elk
from test_support.browser import require_chrome, run_html


def test_containment_edges_reach_both_endpoint_boundaries() -> None:
    """Keep parent-child edges visible while unrelated nodes and text occlude them."""
    chrome = require_chrome()
    payload = {
        "schema_version": 2,
        "graph_id": "containment-edge-layering",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [{"id": "link", "label": "Link"}],
        "entities": [
            {
                "id": "parent-out",
                "type": "module",
                "category": "node",
                "short_title": "Parent source",
                "position": 0,
                "connects_to": [{"to": "parent-out.child", "type": "link"}],
            },
            {
                "id": "parent-out.child",
                "type": "source",
                "category": "node",
                "short_title": "Child target",
                "container": "parent-out",
                "position": 1,
                "connects_to": [],
            },
            {
                "id": "parent-in",
                "type": "module",
                "category": "node",
                "short_title": "Parent target",
                "position": 2,
                "connects_to": [],
            },
            {
                "id": "parent-in.child",
                "type": "source",
                "category": "node",
                "short_title": "Child source",
                "container": "parent-in",
                "position": 3,
                "connects_to": [{"to": "parent-in", "type": "link"}],
            },
            {
                "id": "unrelated",
                "type": "source",
                "category": "node",
                "short_title": "Unrelated node",
                "position": 4,
                "connects_to": [],
            },
        ],
    }
    html = build_html_with_elk(payload).replace(
        "</body>",
        """<script>
        window.addEventListener("load", async () => {
          try {
            await updateVisibilityFull();
            let visibilityApplicationCount = 0;
            const originalApplyVisibilityPresentation = applyVisibilityPresentation;
            applyVisibilityPresentation = (...args) => {
              visibilityApplicationCount += 1;
              return originalApplyVisibilityPresentation(...args);
            };
            let mathClearCount = 0;
            window.MathJax = window.MathJax || {};
            window.MathJax.typesetClear = () => { mathClearCount += 1; };
            let tooltipFrameCount = 0;
            const originalRequestAnimationFrame = window.requestAnimationFrame;
            window.requestAnimationFrame = callback => {
              tooltipFrameCount += 1;
              return originalRequestAnimationFrame(callback);
            };
            const nearBoundary = (point, rect) => {
              const distances = [
                Math.abs(point.x - rect.x),
                Math.abs(point.x - (rect.x + rect.width)),
                Math.abs(point.y - rect.y),
                Math.abs(point.y - (rect.y + rect.height)),
              ];
              return Math.min(...distances) <= 8;
            };
            const check = (source, target, parent, parentAtStart) => {
              const path = document.querySelector(
                `.edge-path[data-source-node-id="${source}"][data-target-node-id="${target}"]`
              );
              if (!path || path.style.display === "none" || path.getTotalLength() < 10) {
                throw new Error(`${source} -> ${target} is not visibly routed`);
              }
              const parentRect = getEffectivePos(parent);
              const point = path.getPointAtLength(parentAtStart ? 0 : path.getTotalLength());
              if (!nearBoundary(point, parentRect)) {
                throw new Error(`${source} -> ${target} terminates inside the parent header`);
              }
              if (path.hasAttribute("mask")) throw new Error("edge retained an occlusion mask");
            };
            check("parent-out", "parent-out.child", "parent-out", true);
            check("parent-in.child", "parent-in", "parent-in", false);
            const layerIds = Array.from(document.getElementById("graph-svg").children)
              .filter(element => element.tagName.toLowerCase() === "g")
              .map(element => element.id);
            const expectedLayers = ["edge-layer", "presentation-node-layer", "container-layer", "edge-interaction-layer", "node-layer"];
            if (JSON.stringify(layerIds) !== JSON.stringify(expectedLayers)) {
              throw new Error(`unexpected graph layer order: ${layerIds.join(",")}`);
            }
            const containerCover = document.querySelector(
              '[data-node-id="parent-out"] .node-edge-cover'
            );
            if (containerCover?.getAttribute("fill-opacity") !== "0.78") {
              throw new Error("container does not attenuate crossing edges");
            }
            if (document.querySelector("[data-edge-occlusion-mask], .edge-path[mask], .edge-arrow[mask]")) {
              throw new Error("edge occlusion resources remain");
            }
            const child = document.querySelector('[data-node-id="parent-out.child"]');
            const edge = document.querySelector(
              '.edge-path[data-source-node-id="parent-out"][data-target-node-id="parent-out.child"]'
            );
            document.querySelectorAll(".toolbar-btn").forEach(button => { button.style.display = "none"; });
            const screenMatrix = edge.getScreenCTM();
            const pointerTargets = [0.1, 0.25, 0.4, 0.6, 0.75, 0.9].map(fraction => {
              const point = edge.getPointAtLength(edge.getTotalLength() * fraction);
              const screenPoint = new DOMPoint(point.x, point.y).matrixTransform(screenMatrix);
              return document.elementFromPoint(screenPoint.x, screenPoint.y);
            });
            if (!pointerTargets.some(target => target?.dataset.edgeId === edge.dataset.edgeId)) {
              const proxy = document.querySelector(`.edge-pointer-proxy[data-edge-id="${edge.dataset.edgeId}"]`);
              const hits = pointerTargets.map(target => `${target?.tagName}.${target?.className?.baseVal || target?.className || ""}`).join(",");
              throw new Error(`contained edge has no pointer-accessible segment; hits=${hits}; proxy=${proxy ? `${getComputedStyle(proxy).stroke}/${getComputedStyle(proxy).pointerEvents}` : "missing"}`);
            }
            const pointerTarget = pointerTargets.find(target => target?.dataset.edgeId === edge.dataset.edgeId);
            child.dispatchEvent(new MouseEvent("mouseenter", {bubbles: true}));
            for (let index = 0; index < 20; index += 1) {
              child.dispatchEvent(new MouseEvent("mousemove", {
                bubbles: true,
                clientX: 100 + index,
                clientY: 100 + index,
              }));
            }
            if (edge.style.filter) throw new Error("hover emphasis uses an SVG filter");
            child.classList.add("selected", "filter-match");
            if (getComputedStyle(child.querySelector(".node-shape")).filter !== "none") {
              throw new Error("selection or search emphasis uses an SVG filter");
            }
            child.classList.remove("selected", "filter-match");
            child.dispatchEvent(new MouseEvent("mouseleave", {bubbles: true}));
            pointerTarget.dispatchEvent(new MouseEvent("mouseenter", {bubbles: true}));
            if (hoveredEdgePath !== edge || tooltip.style.display === "none") {
              throw new Error("pointer proxy did not activate the semantic edge");
            }
            pointerTarget.dispatchEvent(new MouseEvent("mouseleave", {bubbles: true}));
            if (visibilityApplicationCount !== 0) {
              throw new Error(`ordinary hover triggered ${visibilityApplicationCount} global visibility passes`);
            }
            if (mathClearCount !== 0) {
              throw new Error(`plain-text hover triggered ${mathClearCount} MathJax clears`);
            }
            if (tooltipFrameCount < 1 || tooltipFrameCount > 4) {
              throw new Error(`tooltip movement scheduled ${tooltipFrameCount} frames`);
            }
            document.body.dataset.testStatus = "PASS";
          } catch (error) {
            document.body.dataset.testStatus = "FAIL:" + (error.message || String(error));
          }
        });
        </script></body>""",
    )
    result = run_html(
        chrome,
        html,
        virtual_time_budget=12000,
    )
    marker = 'data-test-status="'
    start = result.stdout.find(marker)
    status = result.stdout[start + len(marker) :].split('"', 1)[0] if start >= 0 else "missing"
    assert status == "PASS", status


def test_full_graph_has_no_edge_occlusion_resources() -> None:
    """Removing masks prevents edge resources from scaling with graph size."""
    chrome = require_chrome()
    node_count = 72
    entities = []
    for index in range(node_count):
        entities.append(
            {
                "id": f"node-{index}",
                "type": "source",
                "category": "node",
                "short_title": f"Node {index}",
                "position": index,
                "connects_to": (
                    [{"to": f"node-{index + 1}", "type": "link"}]
                    if index + 1 < node_count
                    else []
                ),
            }
        )
    payload = {
        "schema_version": 2,
        "graph_id": "edge-occlusion-scale",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [{"id": "link", "label": "Link"}],
        "entities": entities,
    }
    html = build_html_with_elk(payload).replace(
        "</body>",
        """<script>
        window.addEventListener("load", () => setTimeout(async () => {
          await window.officinaRendererDiagnostics.whenIdle();
          const masks = document.querySelectorAll("[data-edge-occlusion-mask]");
          const maskedEdges = document.querySelectorAll(".edge-path[mask], .edge-arrow[mask]");
          const routes = document.querySelectorAll(".edge-path[d]");
          document.body.dataset.testStatus = masks.length === 0
            && maskedEdges.length === 0
            && routes.length === 71
            ? "PASS"
            : `FAIL:${masks.length} masks, ${maskedEdges.length} masked edges, ${routes.length} routes`;
        }, 150));
        </script></body>""",
    )
    result = run_html(
        chrome,
        html,
        virtual_time_budget=4000,
    )
    assert 'data-test-status="PASS"' in result.stdout, result.stdout[-1000:]

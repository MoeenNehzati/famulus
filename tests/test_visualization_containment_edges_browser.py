"""Browser regression coverage for edges whose endpoints contain one another."""

import pytest
pytestmark = pytest.mark.xdist_group("browser")

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
        window.addEventListener("load", () => setTimeout(() => {
          try {
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
              if (!path.getAttribute("mask")) throw new Error("edge occlusion mask missing");
            };
            check("parent-out", "parent-out.child", "parent-out", true);
            check("parent-in.child", "parent-in", "parent-in", false);
            const child = document.querySelector('[data-node-id="parent-out.child"]');
            const edge = document.querySelector(
              '.edge-path[data-source-node-id="parent-out"][data-target-node-id="parent-out.child"]'
            );
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
            edge.dispatchEvent(new MouseEvent("mouseenter", {bubbles: true}));
            edge.dispatchEvent(new MouseEvent("mouseleave", {bubbles: true}));
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
        }, 150));
        </script></body>""",
    )
    result = run_html(
        chrome,
        html,
        virtual_time_budget=12000,
    )
    assert 'data-test-status="PASS"' in result.stdout, result.stdout[-1000:]


def test_edge_occlusion_geometry_scales_with_local_intersections() -> None:
    """Avoid multiplying every edge mask by every node in the graph."""
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
        window.addEventListener("load", () => setTimeout(() => {
          const blockerCount = document.querySelectorAll(
            "[data-edge-occlusion-mask] rect"
          ).length;
          const viewBox = document.getElementById("graph-svg").viewBox.baseVal;
          const graphArea = Math.max(1, viewBox.width * viewBox.height);
          const maskArea = Array.from(document.querySelectorAll("[data-edge-occlusion-mask]"))
            .reduce((total, mask) => total
              + Number(mask.getAttribute("width")) * Number(mask.getAttribute("height")), 0);
          document.body.dataset.blockerCount = String(blockerCount);
          document.body.dataset.maskAreaRatio = String(maskArea / graphArea);
          document.body.dataset.testStatus = blockerCount < 1000 && maskArea < graphArea * 10
            ? "PASS"
            : `FAIL:${blockerCount} mask rectangles; mask ratio ${maskArea / graphArea}`;
        }, 150));
        </script></body>""",
    )
    result = run_html(
        chrome,
        html,
        virtual_time_budget=4000,
    )
    assert 'data-test-status="PASS"' in result.stdout, result.stdout[-1000:]

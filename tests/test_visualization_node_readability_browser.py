"""Browser regression coverage for overview-scale node readability."""

import json

import pytest

from officina.visualization.elk_html_renderer import build_html_with_elk
from test_support.browser import require_chrome, run_html


@pytest.mark.parametrize(
    ("subtitle", "expanded_subtitle", "collapsed_subtitle"),
    [("Domain", "Domain", "Domain · collapsed"), ("", None, "collapsed"), (None, None, "collapsed")],
)
def test_presentation_nodes_preserve_producer_subtitle_through_collapse(subtitle, expanded_subtitle, collapsed_subtitle):
    """Facet labels cannot replace producer text in expanded or collapsed shells."""
    group = {
        "id": "group", "type": "node", "short_title": "Group title", "position": 0,
        "member_ids": ["member"],
        "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "visible"},
        "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"},
    }
    if subtitle is not None:
        group["subtitle"] = subtitle
    payload = {
        "schema_version": 2, "graph_id": "presentation-subtitle",
        "categories": [{"id": "node", "label": "Node"}], "edge_categories": [],
        "entities": [{"id": "member", "type": "node", "short_title": "Member", "position": 0}],
        "presentation_nodes": [group],
        "ui": {"presentation_node_controls": [{
            "id": "grouping", "label": "Grouping", "selector_label": "Group by", "default_facet": "facet",
            "facets": [{"id": "facet", "label": "Deliberately different facet label", "activation": "all", "node_ids": ["group"]}],
        }]},
    }
    script = """<script>
    window.addEventListener("load", () => setTimeout(async () => {
      try {
        const idle = window.officinaRendererDiagnostics.whenIdle;
        const failures = [];
        const check = (stage, expected) => {
          const shell = document.querySelector('[data-presentation-node-id="group"]');
          if (!shell || shell.querySelector(".node-label")?.textContent !== "Group title") throw Error("group title missing");
          const subtitle = shell.querySelector(".node-subtitle");
          if ((subtitle?.textContent ?? null) !== expected) failures.push(`${stage} subtitle ${JSON.stringify(subtitle?.textContent ?? null)} differs from ${JSON.stringify(expected)}`);
        };
        await idle(); check("expanded", __EXPANDED_SUBTITLE__);
        togglePresentationNodeCollapsed("group"); await idle(); check("collapsed", __COLLAPSED_SUBTITLE__);
        togglePresentationNodeCollapsed("group"); await idle(); check("restored", __EXPANDED_SUBTITLE__);
        if (failures.length) throw Error(failures.join("; "));
        document.body.dataset.testStatus = "PASS";
      } catch (error) { document.body.dataset.testStatus = "FAIL:" + error.message; }
    }, 100));
    </script>""".replace("__EXPANDED_SUBTITLE__", json.dumps(expanded_subtitle)).replace(
        "__COLLAPSED_SUBTITLE__", json.dumps(collapsed_subtitle)
    )
    page = build_html_with_elk(payload).replace("</body>", script + "</body>")
    result = run_html(require_chrome(), page, virtual_time_budget=6000)
    marker = 'data-test-status="'
    status = result.stdout.split(marker, 1)[1].split('"', 1)[0] if marker in result.stdout else "MISSING"
    assert status == "PASS", status


def test_default_nodes_use_readable_literal_producer_text() -> None:
    """Core cells use readable producer text rather than descriptive metadata."""
    chrome = require_chrome()

    single_line_title = (
        "A producer-owned title deliberately long enough to wrap across several "
        "lines while remaining the only visible text in this graph cell for "
        "measurement"
    )
    doc = {
        "schema_version": 2,
        "graph_id": "node-readability-smoke",
        "categories": [
            {"id": "lemma", "label": "Lemma", "shape": "ellipse", "color": "#1e8449"}
        ],
        "entities": [
            {
                "id": "alpha",
                "type": "misleading-type",
                "ref": "misleading-ref",
                "category": "lemma",
                "title": "An intentionally ignored legacy title that is much longer than the cell",
                "short_title": "Ignored short title",
                "label": "Visible producer label",
                "subtitle": "Visible producer subtitle",
                "position": 0,
                "connects_to": [],
            },
            {
                "id": "empty-subtitle",
                "type": "misleading-type", "ref": "misleading-ref", "category": "lemma",
                "title": "Ignored legacy title one", "short_title": single_line_title,
                "subtitle": "", "position": 1, "connects_to": [],
            },
            {
                "id": "missing-subtitle",
                "type": "different-misleading-type", "ref": "different-misleading-ref", "category": "lemma",
                "title": "Ignored legacy title two, deliberately different", "short_title": single_line_title,
                "position": 2, "connects_to": [],
            },
        ],
    }
    html = build_html_with_elk(doc).replace(
        "</body>",
        """<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        window.addEventListener("load", () => setTimeout(async () => {
          try {
            for (let attempt = 0; attempt < 200; attempt += 1) {
              if (document.querySelectorAll("[data-node-id]").length === 3) break;
              await delay(20);
            }
            const node = document.querySelector('[data-node-id="alpha"]');
            const position = lastNodePositions.get("alpha");
            const label = node?.querySelector(".node-label");
            if (!node || !position || !label) throw new Error("rendered node is missing");
            if (position.width < 291 || position.height < 99) {
              throw new Error(`node dimensions are ${position.width}x${position.height}`);
            }
            const style = getComputedStyle(label);
            if (!style.fontFamily.includes("DejaVu Sans Condensed")) {
              throw new Error(`unexpected label font ${style.fontFamily}`);
            }
            if (parseFloat(style.fontSize) < 21 || parseFloat(style.fontWeight) < 900) {
              throw new Error(`label typography is ${style.fontSize}/${style.fontWeight}`);
            }
            const emptyBody = document.querySelector('[data-node-id="empty-subtitle"] .node-fo-body');
            const missingBody = document.querySelector('[data-node-id="missing-subtitle"] .node-fo-body');
            if (!emptyBody || !missingBody) throw new Error("subtitle-free body is missing");
            if (label.textContent !== "Visible producer label" || node.querySelector(".node-subtitle")?.textContent !== "Visible producer subtitle") {
              throw new Error(`unexpected visible text ${node.textContent}`);
            }
            if (node.textContent.includes("misleading-type misleading-ref")) {
              throw new Error(`renderer-derived subtitle leaked: ${node.textContent}`);
            }
            if (emptyBody.querySelector(".node-subtitle") || missingBody.querySelector(".node-subtitle")) {
              throw new Error("empty subtitle reserved a subtitle row");
            }
            if (emptyBody.textContent !== "A producer-owned title deliberately long enough to wrap across several lines while remaining the only visible text in this graph cell for measurement" || missingBody.textContent !== "A producer-owned title deliberately long enough to wrap across several lines while remaining the only visible text in this graph cell for measurement") {
              throw new Error("empty subtitle changed visible title text");
            }
            const expectedBody = document.createElement("div");
            expectedBody.className = "node-fo-body";
            Object.assign(expectedBody.style, {width: "max-content", height: "auto", minWidth: "291px", maxWidth: "416px", position: "absolute", visibility: "hidden"});
            const expectedLabel = document.createElement("div");
            expectedLabel.className = "node-label";
            expectedLabel.textContent = "A producer-owned title deliberately long enough to wrap across several lines while remaining the only visible text in this graph cell for measurement";
            expectedBody.appendChild(expectedLabel);
            document.body.appendChild(expectedBody);
            const singleLineWidth = Math.max(291, Math.ceil(expectedBody.getBoundingClientRect().width));
            const singleLineHeight = Math.max(99, Math.ceil(expectedBody.scrollHeight));
            expectedBody.remove();
            const emptyWidth = lastNodePositions.get("empty-subtitle")?.width;
            const missingWidth = lastNodePositions.get("missing-subtitle")?.width;
            if (emptyWidth !== singleLineWidth || missingWidth !== singleLineWidth) {
              throw new Error(`subtitle-free widths are ${emptyWidth}x${missingWidth}, expected ${singleLineWidth}`);
            }
            const emptyHeight = lastNodePositions.get("empty-subtitle")?.height;
            const missingHeight = lastNodePositions.get("missing-subtitle")?.height;
            if (emptyHeight !== singleLineHeight || missingHeight !== singleLineHeight) {
              throw new Error(`subtitle-free dimensions are ${emptyHeight}x${missingHeight}, expected ${singleLineHeight}`);
            }
            document.body.dataset.testStatus = "PASS";
            document.title = "PASS";
          } catch (error) {
            document.body.dataset.testStatus = "FAIL:" + (error.message || String(error));
            document.title = document.body.dataset.testStatus;
          }
        }, 100));
        </script></body>""",
    )
    result = run_html(
        chrome,
        html,
        virtual_time_budget=3000,
        window_size="1200,800",
    )

    marker = 'data-test-status="'
    start = result.stdout.find(marker)
    status = result.stdout[start + len(marker) :].split('"', 1)[0] if start >= 0 else "missing"
    assert status == "PASS", status

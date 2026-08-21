"""Browser regression coverage for overview-scale node readability."""

import pytest

from officina.visualization.elk_html_renderer import build_html_with_elk
from test_support.browser import require_chrome, run_html


pytestmark = pytest.mark.xdist_group("browser")


def test_default_nodes_use_larger_cells_and_heavy_condensed_labels() -> None:
    """Ordinary nodes expose the selected 130%-cell readability treatment."""
    chrome = require_chrome()

    doc = {
        "schema_version": 2,
        "graph_id": "node-readability-smoke",
        "categories": [
            {"id": "lemma", "label": "Lemma", "shape": "ellipse", "color": "#1e8449"}
        ],
        "entities": [
            {
                "id": "alpha",
                "type": "lemma",
                "category": "lemma",
                "short_title": "Alpha",
                "position": 0,
                "connects_to": [],
            }
        ],
    }
    html = build_html_with_elk(doc).replace(
        "</body>",
        """<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        window.addEventListener("load", () => setTimeout(async () => {
          try {
            for (let attempt = 0; attempt < 200; attempt += 1) {
              if (document.querySelector('[data-node-id="alpha"]')) break;
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

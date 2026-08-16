"""Browser regression coverage for overview-scale node readability."""

from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from officina.common.visualization.elk_html_renderer import build_html_with_elk


pytestmark = pytest.mark.xdist_group("browser")


def test_default_nodes_use_larger_cells_and_heavy_condensed_labels(tmp_path: Path) -> None:
    """Ordinary nodes expose the selected 130%-cell readability treatment."""
    chrome = shutil.which("google-chrome")
    if chrome is None:
        # famulus-skip: category=capability-unavailable; reason=Google Chrome is not installed; alternate=renderer contract tests cover generated node dimensions and typography assets
        pytest.skip("google-chrome unavailable")

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
    page = tmp_path / "node-readability.html"
    page.write_text(html, encoding="utf-8")

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
                "--virtual-time-budget=3000",
                "--window-size=1200,800",
                "--dump-dom",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    marker = 'data-test-status="'
    start = result.stdout.find(marker)
    status = result.stdout[start + len(marker) :].split('"', 1)[0] if start >= 0 else "missing"
    assert status == "PASS", status

"""Browser regression coverage for adapter-declared omission projection."""

from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest
pytestmark = pytest.mark.xdist_group("browser")

from officina.common.visualization.elk_html_renderer import build_html_with_elk


def test_hidden_module_projects_only_its_used_interface_implementation(
    tmp_path: Path,
) -> None:
    """Preserve real implementation dependencies without crossing sibling gateways."""
    chrome = shutil.which("google-chrome")
    if chrome is None:
        # famulus-skip: category=capability-unavailable; reason=Google Chrome is not installed; alternate=projection policy tests cover transformation semantics without a browser
        pytest.skip("google-chrome unavailable")
    payload = {
        "schema_version": 2,
        "graph_id": "interface-projection",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [
            {"id": "uses-interface", "label": "Uses interface"},
            {"id": "indirectly-uses-interface", "label": "Indirectly uses interface"},
            {"id": "binds-interface", "label": "Binds interface"},
            {"id": "indirectly-binds-interface", "label": "Indirectly binds interface"},
        ],
        "relation_semantics": {
            "transformations": {"node_omission": {"rules": [{
                "id": "hidden-interface-implementation",
                "causes": ["user-hidden"],
                "left_types": ["uses-interface", "indirectly-uses-interface"],
                "right_types": ["uses-interface"],
                "outcomes": [{"type": "indirectly-uses-interface", "fidelity": "exact"}],
            }, {
                "id": "hidden-use-binding",
                "causes": ["user-hidden"],
                "left_types": ["uses-interface", "indirectly-uses-interface"],
                "right_types": ["binds-interface"],
                "outcomes": [{"type": "indirectly-uses-interface", "fidelity": "exact"}],
            }, {
                "id": "hidden-binding",
                "causes": ["user-hidden"],
                "left_types": ["binds-interface", "indirectly-binds-interface"],
                "right_types": ["binds-interface"],
                "outcomes": [{"type": "indirectly-binds-interface", "fidelity": "exact"}],
            }]}},
            "subsumptions": [{
                "stronger_type": "uses-interface",
                "weaker_types": ["indirectly-uses-interface"],
            }, {
                "stronger_type": "binds-interface",
                "weaker_types": ["indirectly-binds-interface"],
            }],
        },
        "entities": [
            {"id": "consumer", "type": "source", "category": "node", "short_title": "Consumer", "position": 0,
             "connects_to": [{"to": "service.interface", "type": "uses-interface", "projection_target": "service.impl"}]},
            {"id": "service", "type": "module", "category": "node", "short_title": "Service", "position": 1, "connects_to": []},
            {"id": "service.interface", "type": "interface", "category": "node", "short_title": "Service interface", "container": "service", "position": 2, "connects_to": []},
            {"id": "service.impl", "type": "source", "category": "node", "short_title": "Service implementation", "container": "service", "position": 3,
             "connects_to": [{"to": "storage.interface", "type": "uses-interface"}]},
            {"id": "storage.interface", "type": "interface", "category": "node", "short_title": "Storage", "position": 4, "connects_to": []},
            {"id": "separate-consumer", "type": "source", "category": "node", "short_title": "Separate consumer", "position": 5,
             "connects_to": [{"to": "split.interface", "type": "uses-interface", "projection_target": "split.impl"}]},
            {"id": "split", "type": "module", "category": "node", "short_title": "Split service", "position": 6, "connects_to": []},
            {"id": "split.interface", "type": "interface", "category": "node", "short_title": "Split interface", "container": "split", "position": 7, "connects_to": []},
            {"id": "split.impl", "type": "source", "category": "node", "short_title": "Used implementation", "container": "split", "position": 8, "connects_to": []},
            {"id": "split.gateway", "type": "source", "category": "node", "short_title": "Sibling gateway", "container": "split", "position": 9,
             "connects_to": [{"to": "setup.interface", "type": "uses-interface"}]},
            {"id": "setup.interface", "type": "interface", "category": "node", "short_title": "Setup", "position": 10, "connects_to": []},
            {"id": "bind.root", "type": "interface", "category": "node", "short_title": "Binding root", "position": 11,
             "connects_to": [{"to": "bind.middle", "type": "binds-interface"}]},
            {"id": "bind.middle", "type": "interface", "category": "node", "short_title": "Binding middle", "position": 12,
             "connects_to": [{"to": "bind.leaf", "type": "binds-interface"}]},
            {"id": "bind.leaf", "type": "interface", "category": "node", "short_title": "Binding leaf", "position": 13, "connects_to": []},
            {"id": "use.consumer", "type": "source", "category": "node", "short_title": "Binding consumer", "position": 14,
             "connects_to": [{"to": "use.middle", "type": "uses-interface"}]},
            {"id": "use.middle", "type": "interface", "category": "node", "short_title": "Used middle", "position": 15,
             "connects_to": [{"to": "use.leaf", "type": "binds-interface"}]},
            {"id": "use.leaf", "type": "interface", "category": "node", "short_title": "Used leaf", "position": 16, "connects_to": []},
        ],
    }
    html = build_html_with_elk(payload).replace(
        "</body>",
        """<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        window.addEventListener("load", () => setTimeout(async () => {
          try {
            document.querySelector('[data-node-id="service"]').dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(80);
            const desired = document.querySelector('.edge-path[data-source-node-id="consumer"][data-target-node-id="storage.interface"][data-edge-type="indirectly-uses-interface"]');
            if (!desired || desired.style.display === "none") throw new Error("desired implementation dependency missing");
            document.querySelector('[data-node-id="split"]').dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(80);
            const falseEdge = document.querySelector('.edge-path[data-source-node-id="separate-consumer"][data-target-node-id="setup.interface"]');
            if (falseEdge && falseEdge.style.display !== "none") throw new Error("projection crossed into unrelated sibling gateway");
            document.querySelector('[data-node-id="bind.middle"]').dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(80);
            const indirectBinding = document.querySelector('.edge-path[data-source-node-id="bind.root"][data-target-node-id="bind.leaf"][data-edge-type="indirectly-binds-interface"]');
            if (!indirectBinding || indirectBinding.style.display === "none") throw new Error("binding-layer projection missing");
            document.querySelector('[data-node-id="use.middle"]').dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(80);
            const indirectUse = document.querySelector('.edge-path[data-source-node-id="use.consumer"][data-target-node-id="use.leaf"][data-edge-type="indirectly-uses-interface"]');
            if (!indirectUse || indirectUse.style.display === "none") throw new Error("interface-use binding projection missing");
            document.body.dataset.testStatus = "PASS";
          } catch (error) { document.body.dataset.testStatus = "FAIL:" + (error.message || String(error)); }
        }, 100));
        </script></body>""",
    )
    path = tmp_path / "interface-projection-browser.html"
    path.write_text(html, encoding="utf-8")
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run([
            chrome, "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--disable-crash-reporter",
            f"--user-data-dir={profile}", "--virtual-time-budget=12000",
            "--dump-dom", path.as_uri(),
        ], check=True, capture_output=True, text=True)
    marker = 'data-test-status="'
    start = result.stdout.find(marker)
    status = (
        result.stdout[
            start + len(marker):result.stdout.find('"', start + len(marker))
        ]
        if start >= 0
        else "FAIL:status missing"
    )
    assert status == "PASS", status

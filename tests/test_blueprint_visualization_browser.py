"""Browser acceptance coverage for repository-blueprint visualization."""

from pathlib import Path

from officina.visualization.elk_html_renderer import build_html_with_elk
from officina.visualization.from_blueprint.extractor import build_blueprint_payload
from test_support.browser import require_chrome, run_html


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repository_projection_keeps_semantics_and_presents_derivation() -> None:
    payload = build_blueprint_payload(REPO_ROOT)
    assert {category["id"] for category in payload["edge_categories"]} == {
        "binds-interface",
        "dependency",
        "depends-on-source",
        "exposes-child-interface",
        "routes-module",
        "uses-interface",
    }

    script = r'''
    <script>
    window.addEventListener("load", () => setTimeout(async () => {
      const check = (condition, message) => { if (!condition) throw new Error(message); };
      try {
        await window.officinaRendererDiagnostics.whenIdle();
        const detail = document.getElementById("graph-detail-level");
        detail.value = "interface";
        detail.dispatchEvent(new Event("change", {bubbles: true}));
        await window.officinaRendererDiagnostics.whenIdle();

        const omitted = document.querySelector(
          '[data-node-id="ci-debug._rtx.interface.run-ci"]'
        );
        check(omitted, "test interface is not visible");
        omitted.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
        await window.officinaRendererDiagnostics.whenIdle();

        const projected = document.querySelector(
          '.edge-path[data-source-node-id="ci-debug.source.gateway"]' +
          '[data-target-node-id="ci-debug._rtx.source.rtx-run-ci.interface.run-ci"]' +
          '[data-edge-type="uses-interface"]'
        );
        check(projected?.dataset.derived === "true", "derived base-type edge missing");
        check(projected.dataset.edgePresentationSignature === "derivation:derived",
          "derived presentation missing");
        check(projected.__edgeMeta.metadata.projection.fidelity === "exact",
          "exact projection fidelity missing");
        check(getComputedStyle(projected).strokeDasharray !== "none",
          "derived edge is not dashed");
        document.body.dataset.testStatus = "PASS";
      } catch (error) {
        document.body.dataset.testStatus = `FAIL:${error.message || String(error)}`;
      }
    }, 150));
    </script>
    </body>
    '''
    html = build_html_with_elk(payload).replace("</body>", script)
    result = run_html(
        require_chrome(),
        html,
        virtual_time_budget=20_000,
        window_size="1920,1080",
    )
    marker = 'data-test-status="'
    start = result.stdout.find(marker)
    status = (
        result.stdout[
            start + len(marker) : result.stdout.find('"', start + len(marker))
        ]
        if start >= 0
        else "FAIL:status missing"
    )
    assert status == "PASS", status

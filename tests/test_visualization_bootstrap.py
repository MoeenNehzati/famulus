"""Bootstrap contracts for standalone renderer data and ELK isolation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

from officina.visualization.elk_html_renderer import build_html_with_elk
from test_support.browser import require_chrome


def _benchmark_module():
    path = Path(__file__).parents[1] / "scripts" / "benchmark-html-renderer.py"
    spec = importlib.util.spec_from_file_location("bootstrap_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "graph_id": "bootstrap-contract",
        "document": {"title": "Graph </script> title"},
        "categories": [{"id": "node", "label": "Node"}],
        "entities": [
            {
                "id": "a",
                "type": "node",
                "short_title": "A",
                "position": 0,
                "connects_to": [{"to": "b", "type": "uses"}],
            },
            {
                "id": "b",
                "type": "node",
                "short_title": "B",
                "position": 1,
                "connects_to": [],
            },
        ],
    }


def _inert_json(html: str, element_id: str) -> str:
    match = re.search(
        rf'<script id="{element_id}" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_graph_and_edges_are_inert_json_with_safe_script_termination() -> None:
    rendered = build_html_with_elk(_payload())
    graph_json = _inert_json(rendered, "officina-graph-data")
    edge_json = _inert_json(rendered, "officina-edge-data")

    assert json.loads(graph_json)["graph_id"] == "bootstrap-contract"
    assert json.loads(edge_json) == [
        {
            "edge_id": "edge_1",
            "source": "a",
            "target": "b",
            "annotation_source": "explicit",
            "to": "b",
            "type": "uses",
        }
    ]
    assert "<\\/script> title" in graph_json
    assert "const docData = {" not in rendered


def test_bootstrap_retains_source_json_and_uses_a_native_elk_worker() -> None:
    rendered = build_html_with_elk(_payload())
    vendor = Path(__file__).parents[1] / "src/officina/visualization/html_renderer/vendor"
    elk_api = (vendor / "elk-api.js").read_text(encoding="utf-8")
    elk_bundled = (vendor / "elk.bundled.js").read_text(encoding="utf-8")

    assert 'document.getElementById("officina-graph-data").textContent' in rendered
    assert 'document.getElementById("officina-edge-data").textContent' in rendered
    assert "const graphDocumentJson" in rendered
    assert "JSON.parse(graphDocumentJson)" in rendered
    assert '<script id="officina-viewer-runtime">' in rendered
    assert '<script id="officina-elk-client">' in rendered
    assert "workerFactory: () => new Worker(ELK_WORKER_URL)" in rendered
    assert elk_api in rendered
    assert elk_bundled not in rendered
    assert "Falling back to non-web worker version." not in rendered


def test_initial_layout_runs_in_one_native_worker_without_fallback_warning() -> None:
    rendered = build_html_with_elk(_payload())
    probe = """<script>
    const BrowserWorker = window.Worker;
    window.bootstrapWorkerCount = 0;
    window.bootstrapWarnings = [];
    window.Worker = function(...args) {
      window.bootstrapWorkerCount++;
      return new BrowserWorker(...args);
    };
    const browserWarn = console.warn;
    console.warn = (...args) => {
      window.bootstrapWarnings.push(args.join(" "));
      browserWarn(...args);
    };
    window.addEventListener("load", () => setTimeout(async () => {
      try {
        await window.officinaRendererDiagnostics.whenIdle();
        const mounted = document.querySelectorAll(".graph-node").length;
        const fallback = window.bootstrapWarnings.some(message =>
          message.includes("Falling back to non-web worker")
        );
        if (window.bootstrapWorkerCount !== 1) {
          throw new Error(`native worker count ${window.bootstrapWorkerCount}`);
        }
        if (fallback) throw new Error("ELK used its fake worker fallback");
        if (mounted !== 2) throw new Error(
          `mounted ${mounted}/2 nodes: ${document.getElementById("elk-status").textContent}`
        );
        const result = document.createElement("pre");
        result.id = "benchmark-result";
        result.textContent = JSON.stringify({status: "PASS"});
        document.body.appendChild(result);
      } catch (error) {
        document.body.dataset.benchmarkError = error.message;
      }
    }, 100));
    </script>"""
    rendered = rendered.replace("</head>", probe + "</head>")

    result = _benchmark_module().run_benchmark_html(
        require_chrome(), rendered, timeout_seconds=20
    )

    assert result == {"status": "PASS"}

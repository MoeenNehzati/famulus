import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_support.browser import require_chrome


def _benchmark_module():
    path = Path(__file__).parents[1] / "scripts" / "benchmark-html-renderer.py"
    spec = importlib.util.spec_from_file_location("benchmark_html_renderer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_trial_records_action_wide_frame_and_long_task_maxima():
    graph = {
        "schema_version": 2,
        "graph_id": "benchmark-probe",
        "entities": [],
    }
    page = f"""<!doctype html><html><head></head><body>
      <select id="routing-geometry"><option>curved</option><option>straight</option></select>
      <svg id="graph-svg"><g id="edge-layer"></g>
        </svg>
      </div>
      <script>
      const docData = {json.dumps(graph)};
      let idle = Promise.resolve();
      let frameTime = performance.now();
      let frameIndex = 0;
      window.requestAnimationFrame = callback => setTimeout(() => {{
        frameTime += frameIndex++ % 3 === 1 ? 80 : 10;
        callback(frameTime);
      }}, 5);
      window.__benchmarkLongTaskObserver.takeRecords = () => [{{duration: 80}}];
      window.officinaRendererDiagnostics = {{whenIdle: () => idle}};
      document.getElementById("routing-geometry").addEventListener("change", () => {{
        idle = new Promise(resolve => setTimeout(() => {{
          document.getElementById("graph-svg").dataset.done = "true";
          resolve();
        }}, 40));
      }});
      </script></body></html>"""

    result = _benchmark_module().trial(require_chrome(), page, "routing_change", [])

    assert (
        result["heartbeat_gap_ms"] >= 60,
        result["longest_task_ms"] >= 50,
    ) == (True, True), result


def test_full_graph_starts_completion_sampling_at_load_without_fixed_delay():
    """Initial-render timing begins at navigation, not 100 ms after load."""
    script = _benchmark_module().probe_script("full_graph", [])

    assert "window.addEventListener('load',async()=>" in script
    assert "}},100));</script>" not in script
    assert "edge.__edgeMeta" in script
    assert "Object.keys(value).sort()" in script


def test_summary_records_p95_metrics_and_reports_gate_and_scene_parity_failures():
    module = _benchmark_module()

    def sample(duration, *, node_ids=("a",), long_task=0, input_latency=0, heartbeat=0, metadata="baseline"):
        return {
            "duration_ms": duration,
            "longest_task_ms": long_task,
            "input_latency_ms": input_latency,
            "heartbeat_gap_ms": heartbeat,
            "mounted_nodes": len(node_ids),
            "mounted_edges": 0,
            "visible_node_ids": list(node_ids),
            "visible_edge_records": [{"id": "edge", "metadata": metadata, "aggregate": {"constituents": ["a"]}}],
            "svg_descendants": 1,
        }

    baseline = [sample(index) for index in range(1, 21)]
    candidate = [sample(index + 60, long_task=51) for index in range(1, 21)]
    results = {
        "baseline": {"reduce_to_40": {"samples": baseline}},
        "candidate": {"reduce_to_40": {"samples": candidate}},
    }
    candidate[0] = sample(
        61,
        node_ids=("a",),
        long_task=51,
        input_latency=51,
        heartbeat=101,
        metadata="candidate-only-provenance",
    )
    results["candidate"]["drag_completion"] = {"samples": [sample(33) for _ in range(20)]}
    results["baseline"]["drag_completion"] = {"samples": [sample(10) for _ in range(20)]}
    results["candidate"]["full_graph"] = {"samples": [sample(10, input_latency=51) for _ in range(20)]}
    results["baseline"]["full_graph"] = {"samples": [sample(10) for _ in range(20)]}
    summary = module.summarize_samples(candidate)
    verdict = module.acceptance_verdict(results)

    assert summary["p95_duration_ms"] == 79
    assert summary["p95_longest_task_ms"] == 51
    assert verdict["status"] == "fail"
    assert "candidate.reduce_to_40 p95 duration 79.0 ms exceeds 75 ms" in verdict["violations"]
    assert "candidate.reduce_to_40 has a 51.0 ms long task (limit 50 ms)" in verdict["violations"]
    assert "candidate.reduce_to_40 has a 101.0 ms heartbeat gap (limit 100 ms)" in verdict["violations"]
    assert "candidate.drag_completion p95 duration 33.0 ms exceeds 32 ms" in verdict["violations"]
    assert "candidate.full_graph has a 51.0 ms input latency (limit 50 ms)" in verdict["violations"]
    assert "reduce_to_40 sampled scene differs between baseline and candidate" in verdict["violations"]


def test_main_rejects_mismatched_payloads_before_launching_chrome(tmp_path, monkeypatch):
    module = _benchmark_module()
    baseline = tmp_path / "baseline.html"
    candidate = tmp_path / "candidate.html"
    output = tmp_path / "result.json"
    baseline.write_text("<script>const docData = {\"entities\": []};\n</script>")
    candidate.write_text("<script>const docData = {\"entities\": [{\"id\": \"x\"}]};\n</script>")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark-html-renderer.py",
            "--baseline-html",
            str(baseline),
            "--candidate-html",
            str(candidate),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit, match="baseline and candidate payloads differ"):
        module.main()


@pytest.mark.parametrize("diagnostic", ["whenIdle", "math"])
def test_trial_times_out_candidate_completion_and_math_diagnostics(diagnostic):
    graph = {"schema_version": 2, "graph_id": "timeout", "entities": []}
    stalled = (
        "window.officinaRendererDiagnostics={whenIdle:()=>new Promise(()=>{})};"
        if diagnostic == "whenIdle"
        else "window.officinaRendererDiagnostics={whenIdle:()=>Promise.resolve()};window.officinaMathDiagnostics=()=>new Promise(()=>{});"
    )
    page = f"""<!doctype html><html><head></head><body>
      <select id="routing-geometry"><option>curved</option><option>straight</option></select>
      <svg id="graph-svg"><g id="edge-layer"></g>
        </svg>
      </div><script>const docData = {json.dumps(graph)};{stalled}</script></body></html>"""

    with pytest.raises(SystemExit, match="routing_change: completion timed out"):
        _benchmark_module().trial(require_chrome(), page, "routing_change", [])


def test_drag_setup_is_conditional_and_precedes_the_measured_boundary():
    script = _benchmark_module().probe_script("drag_completion", [])

    assert "'drag_completion'&&!ordinary().length" in script
    assert "detail.selectedIndex++;detail.dispatchEvent" in script
    assert "await settle(before)" in script
    assert script.index("await settle(before)") < script.index("start=full?window.__benchmarkStart:performance.now()")


def test_main_writes_failed_manifest_after_every_fresh_trial(tmp_path, monkeypatch):
    module = _benchmark_module()
    page = '<html><head></head><body><script>const docData = {"entities": []};\n</script></body></html>'
    baseline = tmp_path / "baseline.html"
    candidate = tmp_path / "candidate.html"
    output = tmp_path / "result.json"
    baseline.write_text(page)
    candidate.write_text(page)
    calls = []

    def trial(chrome, observed_page, action, keep):
        calls.append((observed_page, action))
        return {
            "duration_ms": 1,
            "longest_task_ms": 0,
            "input_latency_ms": 51 if observed_page == page and len(calls) > 161 else 0,
            "heartbeat_gap_ms": 0,
            "mounted_nodes": 0,
            "mounted_edges": 0,
            "visible_node_ids": [],
            "visible_edge_records": [],
            "svg_descendants": 1,
        }

    monkeypatch.setattr(module, "trial", trial)
    monkeypatch.setattr(module, "chrome_executable", lambda: "chrome")
    monkeypatch.setattr(module.subprocess, "check_output", lambda *args, **kwargs: "Chrome test")
    monkeypatch.setattr(sys, "argv", ["benchmark", "--baseline-html", str(baseline), "--candidate-html", str(candidate), "--output", str(output)])

    assert module.main() == 1
    manifest = json.loads(output.read_text())
    assert manifest["acceptance"]["status"] == "fail"
    assert len(calls) == len(module.ACTIONS) * 2 * 23
    assert [action for _, action in calls[:23]] == ["full_graph"] * 23
    assert [action for _, action in calls[23:46]] == ["reduce_to_40"] * 23

import errno
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from test_support.browser import require_chrome


def _benchmark_module():
    path = Path(__file__).parents[1] / "scripts" / "benchmark-html-renderer.py"
    spec = importlib.util.spec_from_file_location("benchmark_html_renderer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "page",
    [
        '<script>const docData = {"entities": [{"id": "legacy"}]};\n</script>',
        '<script id="officina-graph-data" type="application/json">'
        '{"entities": [{"id": "inert"}]}</script>',
    ],
)
def test_payload_extracts_legacy_and_inert_renderer_documents(page):
    value, canonical = _benchmark_module().payload(page)

    expected_id = "legacy" if "legacy" in page else "inert"
    assert value["entities"][0]["id"] == expected_id
    assert canonical == json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode()


def test_payload_prefers_candidate_inert_data_over_unrelated_script_text():
    page = """<script>const docData = {not valid graph syntax};</script>
    <script id="officina-graph-data" type="application/json">
    {"entities": [{"id": "candidate"}]}
    </script>"""

    value, _ = _benchmark_module().payload(page)

    assert value["entities"] == [{"id": "candidate"}]


def test_trial_measures_a_real_synchronous_stall():
    """The actual launcher must not freeze performance.now during JS work."""
    page = """<!doctype html><html><head></head><body>
      <select id="routing-geometry"><option>curved</option><option>straight</option></select>
      <svg id="graph-svg"></svg>
      <script>
      const docData = {"entities": []};
      window.officinaRendererDiagnostics = {whenIdle: () => new Promise(resolve => setTimeout(resolve, 25))};
      document.getElementById("routing-geometry").addEventListener("change", () => {
        const stallUntil = performance.now() + 150;
        while (performance.now() < stallUntil) {}
      });
      </script></body></html>"""

    result = _benchmark_module().trial(require_chrome(), page, "routing_change", [])

    assert result["duration_ms"] >= 100, result
    assert max(result["longest_task_ms"], result["heartbeat_gap_ms"]) >= 100, result


@pytest.mark.parametrize("idle_connection", [False, True])
def test_real_time_launcher_bounds_a_page_without_a_result(monkeypatch, idle_connection):
    module = _benchmark_module()
    if idle_connection:
        launch = module.subprocess.Popen

        def launch_after_browser_preconnect(command, **kwargs):
            # Chromium can open an HTTP connection before sending any request.
            address = urlsplit(command[-1])
            connection = socket.create_connection((address.hostname, address.port))
            release = threading.Timer(3, connection.close)
            release.daemon = True
            release.start()
            return launch(command, **kwargs)

        monkeypatch.setattr(module.subprocess, "Popen", launch_after_browser_preconnect)
    start = time.monotonic()
    with pytest.raises(SystemExit, match="benchmark result timed out"):
        module.run_benchmark_html(
            require_chrome(), "<html><body></body></html>", timeout_seconds=0.5
        )
    assert time.monotonic() - start < 2.5


def test_real_time_launcher_does_not_wait_for_reverse_dns(monkeypatch):
    module = _benchmark_module()

    def slow_reverse_dns(host):
        time.sleep(3)
        return host

    monkeypatch.setattr(socket, "getfqdn", slow_reverse_dns)
    start = time.monotonic()
    with pytest.raises(SystemExit, match="benchmark result timed out"):
        module.run_benchmark_html(
            require_chrome(), "<html><body></body></html>", timeout_seconds=0.5
        )
    assert time.monotonic() - start < 2.5


def test_real_time_launcher_retries_inflight_profile_cleanup(monkeypatch):
    module = _benchmark_module()
    cleanup = module.tempfile.TemporaryDirectory.cleanup
    raced = False

    def cleanup_with_last_child_write(directory):
        nonlocal raced
        if not raced and "famulus-benchmark-" in directory.name:
            raced = True
            raise OSError(errno.ENOTEMPTY, "Chrome child finished a profile write")
        return cleanup(directory)

    monkeypatch.setattr(module.tempfile.TemporaryDirectory, "cleanup", cleanup_with_last_child_write)
    page = '<html><body><pre id="benchmark-result">{"completed": true}</pre></body></html>'

    assert module.run_benchmark_html(require_chrome(), page) == {"completed": True}


def test_windows_launcher_terminates_chrome_tree_before_profile_cleanup(monkeypatch):
    module = _benchmark_module()
    cleanup = module.tempfile.TemporaryDirectory.cleanup
    state = {"parent_alive": True, "child_alive": True}
    commands = []

    class FakeProcess:
        pid = 4312
        returncode = None

        def poll(self):
            return None if state["parent_alive"] else self.returncode

        def terminate(self):
            state["parent_alive"] = False
            self.returncode = 0

        def kill(self):
            self.terminate()

        def wait(self, timeout=None):
            state["parent_alive"] = False
            self.returncode = 0
            return self.returncode

    class FakeServer:
        def __init__(self, _address, _handler):
            self.server_address = ("127.0.0.1", 4313)
            self.server_port = 4313
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def handle_request(self):
            return None

    process = FakeProcess()

    def terminate_tree(command, **_kwargs):
        commands.append(command)
        if command == ["taskkill", "/PID", "4312", "/T"]:
            state["parent_alive"] = False
            state["child_alive"] = False
            process.returncode = 0
        return SimpleNamespace(returncode=0)

    def cleanup_after_child_exit(directory):
        if state["child_alive"] and "famulus-benchmark-" in directory.name:
            raise PermissionError(errno.EACCES, "Chrome child holds Account Web Data")
        return cleanup(directory)

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(module.subprocess, "run", terminate_tree)
    monkeypatch.setattr(
        module.tempfile.TemporaryDirectory, "cleanup", cleanup_after_child_exit
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    with pytest.raises(SystemExit, match="benchmark result timed out"):
        module.run_benchmark_html(
            "chrome.exe", "<html><body></body></html>", timeout_seconds=0
        )

    assert commands == [["taskkill", "/PID", "4312", "/T"]]


def test_real_time_launcher_serves_large_pages_without_transfer_timeouts(monkeypatch):
    module = _benchmark_module()
    request_log = []
    handle_request = module.BaseHTTPRequestHandler.handle_one_request

    def record_http_diagnostics(handler):
        handler.log_message = lambda pattern, *args: request_log.append(pattern % args)
        return handle_request(handler)

    monkeypatch.setattr(module.BaseHTTPRequestHandler, "handle_one_request", record_http_diagnostics)
    # Pause HTML parsing while a repository-sized payload is still being sent.
    page = """<html><head><script>
      const end = Date.now() + 400; while (Date.now() < end) {}
      </script></head><body><script type="application/json">"""
    page += json.dumps({"payload": "x" * (12 * 1024 * 1024)})
    page += '</script><pre id="benchmark-result">{"completed": true}</pre></body></html>'

    try:
        # This is a transfer-correctness test, not a contended-host performance gate.
        result = module.run_benchmark_html(require_chrome(), page, timeout_seconds=10)
    except SystemExit as error:
        pytest.fail(f"{error}; HTTP diagnostics: {request_log}")
    assert result == {"completed": True}


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
    assert "if(full)input=window.__benchmarkInput" in script
    assert "if(!Number.isFinite(input)||input<0)" in script


def test_fast_full_graph_page_collects_the_completed_head_input_timer():
    module = _benchmark_module()
    page = """<!doctype html><html><head></head><body>
      <svg id="graph-svg"></svg>
      <script>
      const docData = {entities: []};
      window.officinaRendererDiagnostics = {whenIdle: () => Promise.resolve()};
      </script></body></html>"""

    samples = [module.trial(require_chrome(), page, "full_graph", []) for _ in range(5)]

    assert all(sample["input_latency_ms"] >= 0 for sample in samples), samples


def test_summary_records_p95_metrics_and_reports_gate_and_scene_parity_failures():
    module = _benchmark_module()

    def sample(duration, *, node_ids=("a",), long_task=0, input_latency=0, heartbeat=0, metadata="baseline"):
        return {
            "duration_ms": duration,
            "longest_task_ms": long_task,
            "input_latency_ms": input_latency,
            "heartbeat_gap_ms": heartbeat,
            "mounted_nodes": len(node_ids),
            "mounted_edges": 1,
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


@pytest.mark.parametrize("mounted_nodes,mounted_edges", [(1, 1), (0, 1), (2, 1), (1, 0), (1, 2)])
def test_visible_parity_allows_dom_reduction_but_checks_candidate_mounts(mounted_nodes, mounted_edges):
    module = _benchmark_module()
    baseline = {
        "duration_ms": 1, "longest_task_ms": 0, "input_latency_ms": 0, "heartbeat_gap_ms": 0,
        "mounted_nodes": 5, "mounted_edges": 4, "visible_node_ids": ["a"],
        "visible_edge_records": [{"edge_id": "a-loop", "source": "a", "target": "a",
                                  "metadata": {"provenance": "canonical"}}],
    }
    candidate = baseline | {"mounted_nodes": mounted_nodes, "mounted_edges": mounted_edges}

    verdict = module.acceptance_verdict({
        "baseline": {"reduce_to_40": {"samples": [baseline]}},
        "candidate": {"reduce_to_40": {"samples": [candidate]}},
    })

    assert not any("differs between baseline and candidate" in message for message in verdict["violations"])
    expected_violations = []
    for field, actual in (("mounted_nodes", mounted_nodes), ("mounted_edges", mounted_edges)):
        if actual != 1:
            expected_violations.append(f"candidate.reduce_to_40 {field} {actual} differs from visible scene count 1")
    assert verdict["violations"] == expected_violations
    assert verdict["status"] == ("fail" if expected_violations else "pass")


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

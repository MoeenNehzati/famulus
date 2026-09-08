#!/usr/bin/env python3
"""Compare interaction responsiveness for two standalone renderer pages."""
from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_support.browser import chrome_executable, run_html


ACTIONS = (
    "full_graph", "reduce_to_40", "show_all", "detail_change", "collapse_expand",
    "routing_change", "drag_completion",
)
METRICS = ("duration_ms", "longest_task_ms", "input_latency_ms", "heartbeat_gap_ms")
SCENE_FIELDS = ("mounted_nodes", "mounted_edges", "visible_node_ids", "visible_edge_records")
P95_DURATION_LIMITS = {"reduce_to_40": 75, "show_all": 350, "drag_completion": 32}
INPUT_LATENCY_LIMITS = {"full_graph": 50, "show_all": 50, "detail_change": 50, "collapse_expand": 50}


def payload(page: str) -> tuple[dict, bytes]:
    match = re.search(r"const docData = (\{.*?\});\n", page, re.DOTALL)
    if not match:
        raise SystemExit("page has no embedded renderer payload")
    value = json.loads(match.group(1))
    return value, json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def probe_script(action: str, keep: list[str]) -> str:
    return f"""<script>window.addEventListener('load',async()=>{{try{{
const sleep=ms=>new Promise(r=>setTimeout(r,ms)),timeout=promise=>Promise.race([promise,new Promise((_,reject)=>setTimeout(()=>reject(Error('{action}: completion timed out')),10000))]),nextFrame=()=>new Promise(resolve=>{{let done=false,finish=()=>{{if(!done){{done=true;resolve()}}}};requestAnimationFrame(finish);setTimeout(finish,16)}}),baselineIdle=async before=>{{let seen=before<0,last=window.__benchmarkGraphMutations,quiet=0,deadline=performance.now()+10000;while(performance.now()<deadline){{await nextFrame();const now=window.__benchmarkGraphMutations;seen=seen||now>before;quiet=seen&&now===last?quiet+1:0;last=now;if(quiet>=2&&document.getElementById('elk-status')?.textContent!=='Rendering graph layout...')return}}throw Error('{action}: completion timed out')}},settle=async before=>{{if(window.officinaRendererDiagnostics)await timeout(officinaRendererDiagnostics.whenIdle());else await baselineIdle(before);if(window.officinaMathDiagnostics)await timeout(window.officinaMathDiagnostics())}},full='{action}'==='full_graph';if(full)await nextFrame();
if(!full)await settle(-1);const keep=new Set({json.dumps(keep)}),hide=()=>hideNodes(docData.entities.map(e=>e.id).filter(id=>!keep.has(id))),ordinary=()=>docData.entities.map(e=>e.id).filter(id=>nodeElement(id)&&!isContainerNode(id)).sort();
if('{action}'==='show_all'){{const before=window.__benchmarkGraphMutations;hide();await settle(before)}}if('{action}'==='drag_completion'&&!ordinary().length){{const detail=document.getElementById('graph-detail-level'),before=window.__benchmarkGraphMutations;if(!detail||detail.selectedIndex+1>=detail.options.length)throw Error('no detail level with ordinary nodes');detail.selectedIndex++;detail.dispatchEvent(new Event('change',{{bubbles:true}}));await settle(before)}}
const long=window.__benchmarkLongTasks;if(!full){{window.__benchmarkLongTaskBoundary=performance.now();window.__benchmarkDrainLongTasks();long.length=0}}
let input=full?window.__benchmarkInput:-1,start=full?window.__benchmarkStart:performance.now(),heartbeat=window.__benchmarkHeartbeat;if(!full){{heartbeat.active=false;heartbeat=window.__benchmarkHeartbeat=window.__benchmarkStartHeartbeat();setTimeout(()=>input=performance.now()-start,0)}}const before=window.__benchmarkGraphMutations;
if('{action}'==='reduce_to_40')hide();if('{action}'==='show_all')showNodes(docData.entities.map(e=>e.id));
if('{action}'==='detail_change'){{const e=document.getElementById('graph-detail-level');if(!e||e.selectedIndex+1>=e.options.length)throw Error('no next detail option');e.selectedIndex++;e.dispatchEvent(new Event('change',{{bubbles:true}}));}}
if('{action}'==='collapse_expand'){{const id=docData.entities.map(e=>e.id).filter(id=>nodeElement(id)&&isContainerNode(id)).sort()[0];if(!id)throw Error('no collapsible container');toggleContainerCollapsed(id);await settle(before);toggleContainerCollapsed(id);}}
if('{action}'==='routing_change'){{const e=document.getElementById('routing-geometry');if(!e||e.selectedIndex+1>=e.options.length)throw Error('no next routing option');e.selectedIndex++;e.dispatchEvent(new Event('change',{{bubbles:true}}));}}
if('{action}'==='drag_completion'){{const id=ordinary()[0],e=nodeElement(id);if(!e)throw Error('no ordinary node to drag');const r=e.getBoundingClientRect();e.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true,button:0,clientX:r.x,clientY:r.y}}));document.dispatchEvent(new MouseEvent('mousemove',{{bubbles:true,clientX:r.x+40,clientY:r.y+20}}));document.dispatchEvent(new MouseEvent('mouseup',{{bubbles:true,clientX:r.x+40,clientY:r.y+20}}));}}
await settle(full?0:before);const duration=performance.now()-start;await new Promise(resolve=>requestAnimationFrame(resolve));heartbeat.active=false;window.__benchmarkDrainLongTasks();for(let i=0;i<10&&input<0;i++)await sleep(10);const nodes=[...document.querySelectorAll('.graph-node')],edges=[...document.querySelectorAll('.edge-path')],visible=e=>getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden',stable=value=>Array.isArray(value)?value.map(stable):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])])):value,semanticEdge=edge=>stable(edge.__edgeMeta||Object.fromEntries([...edge.attributes].map(attribute=>[attribute.name,attribute.value])));
const out={{duration_ms:duration,longest_task_ms:Math.max(0,...long),input_latency_ms:input,heartbeat_gap_ms:heartbeat.longest,mounted_nodes:nodes.length,mounted_edges:edges.length,visible_node_ids:nodes.filter(visible).map(e=>e.dataset.nodeId).sort(),visible_edge_records:edges.filter(visible).map(semanticEdge).sort((left,right)=>JSON.stringify(left).localeCompare(JSON.stringify(right))),svg_descendants:document.querySelectorAll('#graph-svg *').length}};
const e=document.createElement('pre');e.id='benchmark-result';e.textContent=JSON.stringify(out);document.body.appendChild(e);
}}catch(e){{document.body.dataset.benchmarkError=e.message}}}});</script>"""


def trial(chrome: str, page: str, action: str, keep: list[str]) -> dict:
    instrumentation = """<script>localStorage.clear();window.__benchmarkStart=performance.now();window.__benchmarkInput=-1;setTimeout(()=>window.__benchmarkInput=performance.now()-window.__benchmarkStart,0);window.__benchmarkStartHeartbeat=()=>{const state={last:performance.now(),longest:0,active:true},tick=now=>{state.longest=Math.max(state.longest,now-state.last);state.last=now;if(state.active)requestAnimationFrame(tick)};requestAnimationFrame(tick);return state};window.__benchmarkHeartbeat=window.__benchmarkStartHeartbeat();window.__benchmarkGraphMutations=0;window.__benchmarkLongTasks=[];window.__benchmarkLongTaskObserver=null;window.__benchmarkLongTaskBoundary=window.__benchmarkStart;window.__benchmarkKeepLongTask=entry=>entry.startTime===undefined||entry.startTime>=window.__benchmarkLongTaskBoundary;window.__benchmarkDrainLongTasks=()=>{const observer=window.__benchmarkLongTaskObserver;if(observer)window.__benchmarkLongTasks.push(...observer.takeRecords().filter(window.__benchmarkKeepLongTask).map(entry=>entry.duration))};try{window.__benchmarkLongTaskObserver=new PerformanceObserver(entries=>window.__benchmarkLongTasks.push(...entries.getEntries().filter(window.__benchmarkKeepLongTask).map(entry=>entry.duration)));window.__benchmarkLongTaskObserver.observe({entryTypes:['longtask']})}catch(e){}</script>"""
    instrumented = (
        page.replace("<head>", "<head>" + instrumentation)
        .replace(
            "        </svg>\n      </div>",
            "        </svg><script>new MutationObserver(()=>window.__benchmarkGraphMutations++).observe(document.getElementById('graph-svg'),{attributes:true,childList:true,subtree:true})</script>\n      </div>",
            1,
        )
        .replace("</body>", probe_script(action, keep) + "</body>")
    )
    result = run_html(chrome, instrumented, virtual_time_budget=12000, window_size="1440,1000")
    match = re.search(r'<pre id="benchmark-result">(.*?)</pre>', result.stdout)
    if not match:
        error = re.search(r'data-benchmark-error="([^"]+)', result.stdout)
        raise SystemExit(html_module.unescape(error.group(1)) if error else f"{action} did not complete")
    return json.loads(html_module.unescape(match.group(1)))


def p95(samples: list[dict], metric: str) -> float:
    values = sorted(float(sample[metric]) for sample in samples)
    if not values:
        raise ValueError("cannot aggregate an empty sample set")
    return values[math.ceil(0.95 * len(values)) - 1]


def summarize_samples(samples: list[dict]) -> dict:
    return {f"p95_{metric}": p95(samples, metric) for metric in METRICS} | {"samples": samples}


def acceptance_verdict(results: dict) -> dict:
    """Evaluate only the gates observable in this benchmark manifest."""
    violations = []
    candidate = results.get("candidate", {})
    baseline = results.get("baseline", {})
    for action, record in candidate.items():
        samples = record["samples"]
        duration_limit = P95_DURATION_LIMITS.get(action)
        duration = record.get("p95_duration_ms", p95(samples, "duration_ms"))
        if duration_limit is not None and duration > duration_limit:
            violations.append(f"candidate.{action} p95 duration {duration:.1f} ms exceeds {duration_limit} ms")
        longest_task = max(float(sample["longest_task_ms"]) for sample in samples)
        if longest_task > 50:
            violations.append(f"candidate.{action} has a {longest_task:.1f} ms long task (limit 50 ms)")
        longest_heartbeat = max(float(sample["heartbeat_gap_ms"]) for sample in samples)
        if longest_heartbeat > 100:
            violations.append(f"candidate.{action} has a {longest_heartbeat:.1f} ms heartbeat gap (limit 100 ms)")
        input_limit = INPUT_LATENCY_LIMITS.get(action)
        if input_limit is not None:
            longest_input = max(float(sample["input_latency_ms"]) for sample in samples)
            if longest_input > input_limit:
                violations.append(f"candidate.{action} has a {longest_input:.1f} ms input latency (limit {input_limit} ms)")
        other = baseline.get(action)
        if other and any(
            any(candidate_sample[field] != baseline_sample[field] for field in SCENE_FIELDS)
            for baseline_sample, candidate_sample in zip(other["samples"], samples, strict=True)
        ):
            violations.append(f"{action} sampled scene differs between baseline and candidate")
    return {
        "status": "pass" if not violations else "fail",
        "scope": "benchmark-observable gates and sampled visible-scene parity only",
        "violations": violations,
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-html", required=True, type=Path)
    parser.add_argument("--candidate-html", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    pages = {name: path.read_text(encoding="utf-8") for name, path in (("baseline", args.baseline_html), ("candidate", args.candidate_html))}
    parsed = {name: payload(page) for name, page in pages.items()}
    if parsed["baseline"][1] != parsed["candidate"][1]:
        raise SystemExit("baseline and candidate payloads differ")
    chrome = chrome_executable()
    if not chrome:
        raise SystemExit("Chrome unavailable")
    graph, canonical = parsed["candidate"]
    keep = sorted(str(entity["id"]) for entity in graph["entities"])[:40]
    manifest = {
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "entity_count": len(graph["entities"]),
        "relationship_count": sum(len(entity.get("connects_to", [])) for entity in graph["entities"]),
        "viewport": [1440, 1000],
        "chromium_version": subprocess.check_output([chrome, "--version"], text=True).strip(),
        "action_parameters": {"reduce_to": 40, "drag_delta": [40, 20], "drag_setup": "advance detail level only when the initial view has no ordinary node; setup is outside the measured drag action", "timeout_ms": 10000},
        "warmups": 3,
        "recorded_trials": 20,
        "reduce_to_40_ids": keep,
        "results": {},
    }
    for version, page in pages.items():
        manifest["results"][version] = {}
        for action in ACTIONS:
            samples = [trial(chrome, page, action, keep) for _ in range(23)][3:]
            manifest["results"][version][action] = summarize_samples(samples)
            write_manifest(args.output, manifest)
            print(f"completed {version} {action}", flush=True)
    manifest["acceptance"] = acceptance_verdict(manifest["results"])
    write_manifest(args.output, manifest)
    if manifest["acceptance"]["status"] == "fail":
        print("benchmark acceptance failed; see result manifest", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

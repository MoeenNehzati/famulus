# Responsive, simplicity-first HTML renderer refactor

## Implementation contract

Refactor the existing SVG runtime in place so every renderer interaction stays
responsive. Preserve current public behavior and data contracts while removing
the two measured sources of avoidable work: computed edge masks and graph
objects mounted while hidden.

This is a bounded refactor, not a replacement renderer:

- keep the current model, state, projection, layout, interaction, persistence,
  and caller boundaries;
- use one keyed, visible-scene reconciler for full layout and position-reusing
  updates;
- yield during large paints so input and browser frames continue;
- keep shipped first-party renderer code net-zero or smaller;
- expect **755 changed lines** and never exceed **900 changed lines** without
  revising this plan.

“Changed lines” means additions plus deletions. The ceiling is contingency, not
a target.

## Why this work is necessary

The repository trial inspected on 2026-09-06 contained 758 entities and 842
rendered edges:

| Case | Current observation |
|---|---:|
| Full graph DOM | 34,787 elements |
| Occlusion-mask children | 25,157 |
| Full mask refresh | 152-154 ms |
| Show all with mask refresh | 486-575 ms |
| Show all with mask refresh disabled | 273-342 ms |
| Mostly hidden state | 40 visible nodes, but 758 nodes and 842 edges mounted |
| Mostly hidden presentation pass | 47-50 ms |

Mask removal eliminates a measured 152-154 ms synchronous pass, but the
273-342 ms mask-disabled show-all result proves that removal alone cannot keep
the UI responsive. Large scene updates must also unmount hidden objects and
yield between bounded paint chunks.

## Compatibility boundary

Preserve:

- `ElkHtmlRenderer`, `build_html_with_elk`, schema version 2, canonical ids,
  standalone HTML output, dependency embedding, and inline-script escaping;
- graph projection, omission, redirects, aggregation, bundling, dominance,
  provenance, containment, semantic edge styling, node styling, and
  presentation nodes;
- pan, wheel and touch zoom, fit, search, filters, legends, detail level,
  selection, traversal, hide/restore, dim/undim, collapse, undo/redo, dragging,
  inspector behavior, shortcuts, responsive sidebars, Quick Guide, MathJax,
  viewer-state persistence and migrations, and live build refresh;
- node dimensions, label readability, keyboard activation, focus behavior,
  and untrusted-text safety.

The only approved visible change is edge overlap treatment. Remove partial
luminance masks beneath nodes, containers, and labels. Instead, preserve
readability through paint order, opaque ordinary-node fills, and containment
layering. Routes, endpoints, direction, semantic styling, and practical
readability remain required; exact overlap pixels do not.

Remove in this refactor:

- mask elements, mask refreshes, and shape-cloned mask blockers;
- mounted graph objects absent from the visible scene.

The following are not compatibility requirements, but deleting them is not
part of the eleven-file base scope: dead filter-history stacks, dead
retained-endpoint state, defensive behavior rejected by public validation, and
private function names unrelated to this refactor. Do not add work merely to
preserve them.

The sibling
[`2026-09-06-functionality-inventory.md`](2026-09-06-functionality-inventory.md)
is the detailed behavior ledger.

## Target mechanism

### 1. Mask-free layer order

Delete computed edge occlusion. Order SVG layers as the
`presentation-node-layer`, `container-layer`, `edge-layer`, then `node-layer`.
Edges therefore remain visible above containers while ordinary-node fills
cover crossings.

### 2. Keyed visible-scene reconciliation

Factor the existing full-render paint body into one reconciler shared by ELK
layout and position-reusing updates. Reconcile stable node and edge ids:

- remove objects absent from the visible scene;
- retain unchanged objects;
- create or update changed objects;
- reuse `lastNodePositions` when restoring known nodes;
- invoke ELK only when a newly visible node lacks a valid position or the
  action already requires layout.

### 3. Bounded, cancellable paint

Build a deterministic sequence of stable-id remove, create, geometry, and
presentation operations. Process it until a 6 ms frame deadline expires, yield
with `requestAnimationFrame`, and resume only if the captured `renderVersion`
is still current. Route whole-graph routing-control updates through the same
bounded edge loop.

Maintain one renderer-owned latest-paint promise. Expose it as
`window.officinaRendererDiagnostics.whenIdle(): Promise<void>`. It resolves
after the newest paint and its queued graph-scene MathJax work have settled.
Existing callers remain unchanged and may ignore return values.

Cancellation applies only to paint generations: superseded DOM work stops and
the newest generation settles to current state. It does not roll back graph
state, history, or persistence.

### 4. Incremental MathJax and bounded interactions

Clear removed math before detaching elements. Typeset only created graph
elements and graph labels whose text changed; include that queue tail in
`whenIdle()`.

Keep cheap interactions cheap:

- pan and zoom change only the transform;
- hover changes only CSS;
- selection and dimming change only attributes when membership is unchanged;
- drag preview updates only moved nodes and incident edges;
- inspector, tooltip, and other local UI typesetting may retain focused calls,
  but no action may typeset the entire graph scene.

## Exact files and three-dimensional budget

“3D” records added, deleted, and net lines. Hard churn is additions plus
deletions. Moving slack between files requires an explicit plan edit.

### Production

| File | Add | Delete | Net | Hard churn | Change |
|---|---:|---:|---:|---:|---|
| `src/officina/visualization/html_renderer/runtime/geometry.js` | 0 | 158 | -158 | 165 | Delete bounds scanning, mask blockers, occlusion constants, intersections, and `refreshEdgeOcclusionMasks`; support bounded all-edge routing. |
| `src/officina/visualization/html_renderer/page.html` | 2 | 2 | 0 | 10 | Put `edge-layer` after `container-layer` and before `node-layer`; keep `presentation-node-layer` behind them. |
| `src/officina/visualization/html_renderer/runtime/render_pipeline.js` | 90 | 70 | +20 | 180 | Add keyed visible-scene reconciliation, 6 ms yielding, position reuse, latest-paint completion, and paint-generation cancellation. |
| `src/officina/visualization/html_renderer/runtime/layout.js` | 25 | 65 | -40 | 100 | Replace `updateVisibilityFast` with position-reusing reconciliation; retain ELK fallback for missing positions. |
| `src/officina/visualization/html_renderer/runtime/controls.js` | 15 | 5 | +10 | 35 | Remove mask refreshes and bound whole-graph routing changes. |
| `src/officina/visualization/html_renderer/runtime/math_typesetter.js` | 35 | 15 | +20 | 75 | Return the queue tail, clear removed math, and typeset only created or text-changed graph elements. |

Production subtotal: **+167 / -315 / net -148**, **482 expected churn**, and
**565 hard churn**. Shipped first-party renderer code must remain net-zero or
smaller.

### Tests, benchmark, and documentation

| File | Add | Delete | Net | Hard churn | Change |
|---|---:|---:|---:|---:|---|
| `tests/test_visualization_browser.py` | 45 | 10 | +35 | 70 | Test unmount/restore, position reuse, cancellation, completion, input latency, rAF heartbeat, and incremental MathJax. |
| `tests/test_visualization_containment_edges_browser.py` | 20 | 45 | -25 | 70 | Replace mask tests with layer-order, containment-route, and overlap-readability tests. |
| `tests/test_visualization_inspector_and_bezier_browser.py` | 20 | 45 | -25 | 70 | Replace shape-mask cloning tests with outcome-based nonrectangular-node readability tests. |
| `scripts/benchmark-html-renderer.py` | 75 | 0 | +75 | 105 | Add the reproducible responsiveness benchmark and deterministic result manifest. |
| `src/officina/visualization/html_renderer/README.md` | 8 | 5 | +3 | 20 | Document visible-only reconciliation, cancellation, overlap behavior, completion diagnostics, and benchmarking. |

Supporting subtotal: **+168 / -105 / net +63**, **273 expected churn**, and
**335 hard churn**.

| Budget | Add | Delete | Net | Churn |
|---|---:|---:|---:|---:|
| Lean | 250 | 420 | -170 | 670 |
| Expected | 335 | 420 | -85 | 755 |
| Hard ceiling | — | — | — | **900** |

Tests and documentation do not count as shipped renderer complexity. The
expected diff leaves 145 lines of contingency below the hard ceiling.

## Implementation sequence

### Task 1: Remove masks and correct layer order

Files: `geometry.js`, `page.html`, `render_pipeline.js`, `layout.js`,
`controls.js`, and the two mask-specific browser-test files.

Delete mask construction and every production call. Reorder the layers and
replace private mask assertions with observable checks:

- every visible relationship retains a route and arrow direction;
- ordinary nodes cover crossing edges;
- edges attached to contained nodes remain above container backgrounds;
- nonrectangular nodes remain legible at unrelated crossings;
- layer order is `presentation-node-layer`, `container-layer`, `edge-layer`,
  `node-layer`;
- no `[data-edge-occlusion-mask]` element or edge `mask` reference remains.

Checkpoint: focused containment and geometry browser tests pass, and the full
graph contains zero mask resources.

### Task 2: Reconcile the visible scene responsively

Files: `render_pipeline.js`, `layout.js`, `controls.js`, and
`test_visualization_browser.py`.

Implement the keyed reconciler, known-position restoration, bounded operation
loop, routing-control path, paint-generation cancellation, and
`window.officinaRendererDiagnostics.whenIdle()` completion described above.

Checkpoint: mounted node and edge ids exactly equal the visible scene after
hide, restore, filtering, detail changes, collapse, undo, and redo. In a
deliberately slowed reconciliation, a scheduled pointer probe and browser frame
run between chunks. If a newer generation supersedes the work, the old paint
stops and `window.officinaRendererDiagnostics.whenIdle()` resolves only after
mounted ids match the newest state.

### Task 3: Make graph-scene MathJax incremental

Files: `math_typesetter.js`, `render_pipeline.js`, and
`test_visualization_browser.py`.

Clear removed math before detachment. Queue typesetting only for created or
text-changed graph elements. Return the queue tail and include it in
`window.officinaRendererDiagnostics.whenIdle()`.

Checkpoint: hide/restore and label-change fixtures render the same math,
removal leaves no stale MathJax state, unchanged labels are not retypeset, and
completion waits for the relevant queue tail.

### Task 4: Benchmark and document

Files: `scripts/benchmark-html-renderer.py` and the renderer `README.md`.

Implement the protocol below and document how to run it. If an acceptance gate
fails, profile that action and amend this plan before changing another runtime
file.

## Benchmark protocol

The benchmark CLI is:

```text
scripts/benchmark-html-renderer.py \
  --baseline-html BASELINE.html \
  --candidate-html CANDIDATE.html \
  --output RESULT.json
```

`BASELINE.html` and `CANDIDATE.html` are standalone pages produced by the
pre-refactor and candidate renderers from the same canonical JSON bytes. Build
that JSON once with `build_blueprint_payload(repo_root)`. The benchmark loads
each supplied page directly, extracts its embedded payload, and aborts unless
the canonical payload bytes and trial parameters match. The result manifest
must contain:

- payload SHA-256, `len(payload["entities"])`, and canonical relationship
  count `sum(len(entity.get("connects_to", [])) for entity in
  payload["entities"])`;
- viewport, Chromium version, and action parameters;
- the reduce-to-40 ids, selected as the first 40 entity ids in lexicographic
  order.

Measure full graph, reduce-to-40, show-all, detail change, collapse/expand,
routing change, and drag completion. Each trial starts from a fresh page load
after clearing viewer storage; action cases are isolated and run in the order
listed. Define their inputs as follows:

- full graph measures initial navigation from immediately before page load to
  the applicable completion route below;
- reduce-to-40 uses the existing hide action to hide every entity except the
  recorded 40 ids; show-all restores that state;
- detail change selects the next option after the initial value in the detail
  control; abort if no such option exists;
- collapse/expand targets the lexicographically first visible collapsible
  container id and performs both transitions;
- routing change selects the next geometry option after the initial value;
- drag targets the lexicographically first visible, non-container ordinary
  node and moves it by +40 px horizontally and +20 px vertically.

Use the repository browser-test launcher at a 1440x1000 viewport. Run three
unrecorded warmups and 20 recorded trials per action. Compute p95 as
`sorted_samples[ceil(0.95 * n) - 1]`.

For action cases, schedule an input probe and an independent
`requestAnimationFrame` heartbeat before dispatch. For full graph, install both
probes before navigation. Reset the Long Tasks observer before the measured
boundary and drain it after completion. Candidate completion is
`window.officinaRendererDiagnostics.whenIdle()`. Because the baseline predates
that seam, observe `#graph-svg` from its creation for full graph and before
dispatch for other cases, require the first relevant subtree mutation, then
require two consecutive mutation-free animation frames and await
`window.officinaMathDiagnostics()` when present. Apply a fixed 10-second
timeout to either completion route. Record:

- end-to-end duration and longest observed main-thread task;
- input-probe latency and longest heartbeat gap;
- mounted node/edge counts and total SVG descendants.

## Acceptance gates

Run these gates in host-capable Chromium against the fixed repository payload.

### Responsiveness

- Pan, zoom, hover, inspector, and sidebar interactions perform no graph
  projection, ELK layout, graph-scene MathJax pass, or whole-scene repaint.
  Focused inspector or tooltip typesetting remains allowed.
- Drag-move frames reroute only incident edges; p95 duration is at most 32 ms.
- Hide, filter, selection, dim, routing, and drag completion produce no
  main-thread task above 50 ms.
- Show-all, detail changes, collapse/expand, and cold layout may exceed 100 ms
  end to end, but produce no main-thread task above 50 ms and have input-probe
  latency at most 50 ms.
- No isolated benchmark action observes a long task or rAF heartbeat gap above
  100 ms.
- A newer structural action stops superseded paint. After
  `window.officinaRendererDiagnostics.whenIdle()`, mounted ids and presentation
  match the newest state; state and history are not rolled back.

### Performance and size

- Mask-refresh cost is eliminated rather than relocated.
- Reduce-to-40 p95 end-to-end duration is at most 75 ms. Actual category and
  relation filtering remains covered by the 50 ms responsiveness gate above.
- Known-position show-all p95 end-to-end duration is at most 350 ms while also
  meeting the responsiveness gates.
- Mounted `.graph-node` and `.edge-path` ids equal independently computed
  expected ids in small fixtures. For the repository benchmark, compare
  captured baseline and candidate id sets instead of duplicating projection
  semantics in a test projector.
- The mostly hidden state mounts only nodes in the independently computed
  visible scene, including retained ownership containers, plus its rendered
  edges.
- Shipped production JavaScript is net-zero or smaller.

### Compatibility

- Existing public Python, schema, projection-policy, CLI, persistence,
  interaction, accessibility, Quick Guide, and MathJax tests pass.
- Replace private mask tests; do not weaken unrelated behavior assertions.
- Inline graph JSON cannot terminate its script, label readability remains,
  and live build polling works.
- For fixed action sequences, baseline and candidate visible node ids, semantic
  edge records, inspector contents, control availability, selection state,
  manual positions, and saved viewer state match.

## Measurement-triggered contingency

The eleven files budgeted above are the complete base scope. Add a second-pass
file only when a failed gate identifies its measured cause, and assign that file
a new three-dimensional budget before implementation.

| Measured cause | Candidate files |
|---|---|
| Scheduling is needed outside the reconciler | new `runtime/render_scheduler.js`, `html_renderer/assets.py` |
| Caller completion ordering fails | `runtime/graph_actions.js`, `runtime/filtering.js`, `runtime/presentation_nodes.js` |
| Hidden presentation work remains mounted | `runtime/visibility.js` |
| Overlap readability fails | `viewer.css` |
| Pure projection exceeds 50 ms | `runtime/projection.js`; using a worker requires a separate reviewed design |

## Out of scope

- A second renderer or permanent dual-runtime cutover.
- A new normalized payload, state architecture, global transaction framework,
  duplicate semantic projector, or atomic state/history rollback.
- Schema or producer migration.
- New undo behavior, unrelated sidebar fixes, or unrelated Quick Guide changes.
- A percentage LOC target, exhaustive UI manifest, screenshot archive,
  confidence-interval framework, or mandatory repeated audit ceremony.
- Viewport culling, Canvas, WebGL, spatial indexes, or semantic zoom unless
  separately proposed after this plan passes its responsiveness gates.

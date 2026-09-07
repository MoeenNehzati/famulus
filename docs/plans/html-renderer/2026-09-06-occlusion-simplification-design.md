# Simplicity-first HTML renderer replacement

This supersedes the earlier occlusion-only proposal. The primary goal is an
HTML renderer that a maintainer can understand and change safely. Performance
improvements qualify only when they remove work or reduce machinery. An
optimization that introduces a culling lifecycle, spatial index, dirty-set
protocol, or comparable subsystem is outside this replacement unless later
measurement proves it necessary.

## Decision

Replace the browser runtime behind the existing `ElkHtmlRenderer` boundary as
an internal refactor. Preserve the current user interface and observable
behavior with minimal change. Simplicity comes from replacing internal
machinery, not from deleting established workflows.

The first replacement uses ordinary SVG, mounts only the state-visible scene,
and follows one structural render path:

`action -> prepare state -> derive scene -> layout if needed -> paint -> commit`

Pan and zoom change only the view transform. Hover changes only CSS state.
Selection is paint-only when scene membership is unchanged; because selection
can retain otherwise-filtered nodes and their owners, other selection changes
use the structural path above.

## Verified problem

The current repository payload contains 758 entities. A host-capable Chrome
audit measured 842 rendered edges at full detail.

| Case | Current observation |
|---|---:|
| Full graph DOM | 34,787 elements in one run |
| Occlusion-mask children | 25,157 |
| Full mask refresh | 152-154 ms |
| Show all with mask refresh | 486-575 ms |
| Show all with mask refresh disabled | 273-342 ms; existing mask DOM remained mounted |
| Mostly hidden state | 40 visible nodes, but 758 nodes and 842 edges remain mounted |
| Mostly hidden presentation pass | 47-50 ms |
| Hidden-list rebuild | about 3 ms |

`refreshEdgeOcclusionMasks` performs an edge-by-node geometric pass and builds
one SVG mask per visible edge. Fast visibility changes use `display:none`, so
objects hidden by state remain in the SVG and whole-graph presentation passes
continue to visit them.

The disabled-refresh measurement isolates refresh CPU; it is not a measurement
of the proposed mask-free renderer because previously built mask DOM remained
mounted. The evidence supports two removals: computed masking and retained
hidden scene objects. It does not support optimizing the hidden-node list or
adding viewport culling.

## Governing constraints

1. Current user-facing behavior has priority over internal simplification. A
   feature may be reimplemented, but not removed or materially changed without
   a separate explicit product decision.
2. The replacement must delete substantially more executable browser logic
   than it adds.
3. There is one authoritative state API, one visibility derivation, and one
   structural render transaction. This does not collapse committed state,
   ephemeral interaction state, derived data, and position sources into one
   undifferentiated object.
4. SVG graph-layer DOM size is proportional to the visible scene, not the
   payload. This does not authorize hidden-list virtualization.
5. No operation outside ELK layout may have an edge-by-node cross-product.
6. Existing documented behavior, active producer output, or a focused browser
   test establishes a compatibility requirement unless explicitly retired.
7. A performance technique that adds a subsystem needs fresh evidence from the
   simplified renderer; evidence from the current renderer is insufficient.

## Preserved product boundary

- `ElkHtmlRenderer` and `build_html_with_elk` call signatures.
- Canonical graph-payload validation and identifiers.
- Standalone, self-contained HTML output.
- Containment, typed nodes and edges, node/category styling, edge styling, and
  current metadata-driven presentation.
- ELK layout with a timeout, latest-result protection, and a clear failure
  state.
- Pan, wheel zoom, fit, text search, current filters and legends, detail level,
  multi-selection, relation traversal, keyboard activation, single and bulk
  hide/restore, dim/undim, container collapse, undo/redo, node dragging,
  inspector behavior, and persisted viewer state.
- Current document-configurable layout and routing controls.
- Edge regeneration after hide, restore, filtering, detail changes, collapse,
  drag, and undo/redo, including current projection, aggregation, bundling,
  dominance, provenance, and presentation styling semantics.
- Blueprint presentation nodes and their grouping controls.
- The `quick_guide` keyword; when supplied it activates an isolated UI
  extension that cannot own graph state or introduce another render path.

The refactor keeps the current version-2 payload contract. It may normalize
that payload once into a smaller immutable browser model, but it does not
require producers, stored artifacts, or callers to migrate as part of this
work. A future schema revision is a separate product change.

| Version-2 payload field | Disposition |
|---|---|
| `schema_version`, `entities` | Required and validated. |
| `graph_id`, `graph_kind`, `document`, `metadata` | Preserve as passive identity and inspector metadata. |
| `categories`, `edge_categories` | Preserve basic labels, colors, shapes, and filter catalogs. |
| `detail_levels`, `ui.visibility.detail_level` | Preserve in core. |
| `ui.visibility.hidden_types`, `hidden_nodes`, `hidden_edge_types`, `collapsed_containers` | Preserve as initial values consumed by the one visibility predicate. |
| `ui.layout` | Preserve current controls and defaults; normalize once for the layout module. |
| `ui.type_styles`, `ui.edge_styles` | Preserve current node and semantic edge styling. |
| `ui.filtering.search_placeholder` | Preserve as passive text. |
| `ui.focus` | Preserve current initial focus and selection behavior. |
| `renderer_dependencies` | Preserve for registered optional dependencies such as MathJax. |
| `relation_semantics` | Preserve omission rules, redirects, subsumptions, fidelity, and inspectable provenance. |
| `presentation_nodes`, `ui.presentation_node_controls` | Preserve grouping, selection, inspection, drag, collapse, and persistence behavior. |
| `render_modes`, `default_mode` | Preserve accepted input and current behavior. |
| `ui.edge_presentation`, `edge_metadata_styles`, `interaction`, `panel` | Preserve current observable behavior and styling. |

Domain-specific scene construction remains upstream where it already lives.
The browser normalizer must not silently discard a currently supported field.

## Minimal architecture

The exact file split is secondary to the boundaries below. Do not split small
functions merely to meet a module count.

### Model

Index the immutable payload once: entities, declared edges, categories, and one
canonical containment parent map. It has no DOM access.

### State

One state owner exposes explicit partitions behind one API:

- immutable normalized document data;
- committed viewer state, including visibility, dimming, selection, filters,
  collapse, presentation-node state, routing choices, and persisted view data;
- ephemeral interaction state for hover, pointer gestures, drag previews, and
  in-flight transactions;
- derived visibility, semantic edges, layout input, and painted scene; and
- position state composed by the layout owner from ELK results, ordinary manual
  positions, presentation-member offsets, presentation-shell offsets, and
  transient drag offsets.

Actions prepare the next committed state without mutating the current snapshot.
The current operable global graph undo/redo remains. Filter mutations continue
to participate in that history where they do today. The currently populated but
unconsumed filter undo/redo stacks are dead machinery and are not reproduced.
Ordinary drag continues to persist manual positions but is not made undoable by
this refactor. Adding filter-specific undo or drag undo requires a separate
product decision. No-op actions create neither history nor render work.

### Scene projection

Visibility derivation is pure and returns more than one Boolean:

- the painted universe contains nodes and edges that should exist in the SVG;
- retained selection and ownership context can enter that universe even when a
  filter would otherwise exclude it;
- the layout universe contains the nodes and projected layout edges supplied to
  ELK for the current layout transaction; and
- omission causes distinguish user hide, filters, detail, and collapse because
  they drive different edge semantics.

`projectScene(document, state)` consumes that derivation and returns the painted
scene. Hidden, filtered, detail-omitted, and collapsed objects that are not
retained context do not enter it.

The existing single-node, selection-wide, and complement-wide hide and dim
actions remain. Restore-one, reset, inherited container hiding, and their
selection cleanup semantics remain. Hidden nodes are absent from the SVG scene;
dimmed nodes remain mounted and use lightweight presentation state.

One edge-projection module owns a readable sequence of pure stages. Given the
normalized document and viewer state, it preserves the current behavior for:

1. schema-declared omission traversal and projection-target redirects;
2. exact/degraded fidelity, cycle termination, and deterministic witnesses;
3. dominance/subsumption and direct-edge precedence;
4. aggregation through detail-hidden and collapsed containment;
5. lossless parallel-relation bundling and represented-edge provenance; and
6. category, relation, and presentation-facet filtering.

The stages are:

`canonical relations -> omission and containment aggregation -> dominance,
deduplication, bundling, and provenance -> layout-edge projection -> style
resolution -> paint records`

The module has no DOM access. The layout-edge stage returns only the edges that
constrain the current ELK universe. Edge style resolution remains distinct from
node/category style normalization and from paint-owned SVG resource creation;
there is no global style god object. Paint code does not infer graph semantics.

### Layout

One layout owner composes ELK positions, ordinary manual positions,
presentation-member offsets, presentation-shell offsets, and transient drag
offsets behind one coordinate API. Keep the current layout and routing controls,
but normalize them once and use this explicit action matrix:

| Action | History | Persist | Projection | ELK | Paint / preview |
|---|---|---|---|---|---|
| Initial load or persisted-state restore | None | No new entry | Full | Run on the currently visible/retained universe, matching baseline membership | Full paint |
| Detail change, container collapse/expand, spacing/algorithm change, layout-affecting presentation grouping | Preserve current graph or presentation history semantics | On successful commit | Full semantic and layout projection | Run | Full paint |
| Hide/restore, bulk hide, reset, category/relation facet | Global history where currently recorded | On successful commit | Recompute painted edges | Reuse positions when all newly visible nodes have valid positions; otherwise relayout or use the current non-influencing fallback behavior | Replace visible scene |
| Dim/undim | Global history | Yes | No topology change | No | Presentation-only update |
| Selection or search | Preserve current behavior | Preserve current behavior | Reproject only if retained scene membership changes | No unless a newly retained node has no usable position | Presentation-only or structural transaction as required |
| Ordinary node drag | No history | Once at drag end | Incident edges only | No | Transient coordinate/incident-edge preview; commit manual positions at drag end |
| Presentation-member or shell drag | Preserve its current committed-state rollback behavior | At successful end | Affected grouping/edges | Only when current behavior requires it | Transient preview followed by one commit |
| Routing/geometry control | Preserve current history behavior | Yes | Regenerate routes and presentation | Only when the selected ELK option requires layout | Edge repaint |
| Undo/redo | Consume current global history | After successful restore | According to restored-state delta | Only for layout-affecting delta | Cheapest correct structural or presentation update |
| Hover, pan, zoom, inspector | None | Gesture endpoints persist as today | None | No | CSS or view-transform update |

At initial load, persisted filters and initial visibility affect ELK membership
as they do in the baseline renderer; the refactor must not silently change the
default geometry by laying out hidden objects. Data may retain positions for
unmounted objects. When a restored or retained node lacks a valid position, the
transaction succeeds through a bounded relayout or the existing non-influencing
fallback-placement behavior rather than reporting an invariant error.

The implementation may replace the current geometry algorithms, but every
existing control must remain operable and produce the same class of visible
result. Dragged nodes retain current persistence behavior, and incident edges
remain attached throughout the interaction. A drag preview is explicitly
ephemeral and is the sole permitted rendering path outside committed structural
transactions.

### Paint and interaction

Repaint the visible SVG scene straightforwardly before introducing keyed
reconciliation. Preserve the containment layering invariant with presentation
and container shells behind relationship edges and ordinary nodes/labels above
edges, or an equivalent endpoint-aware layer split. Edges attached to contained
nodes must remain visible above their container background and below ordinary
nodes. Ordinary fills and label styling provide occlusion; there are no
computed masks, including for containers.

Use shared SVG arrow markers where they preserve current appearance and apply
the resolved node and edge styles through ordinary CSS/SVG attributes. Prefer
delegated node/edge events only when that also makes interaction code smaller;
event delegation is not itself a performance requirement.

### Transaction and failure semantics

A structural action prepares a candidate state without exposing it as current,
then derives visibility and edges, runs ELK when required, paints the candidate,
and only then commits state, history, and persistence. A failed transaction
restores the previous painted scene and reports the current clear error state.
A superseded transaction exits without committing state, history, positions,
presentation offsets, or persistence. MathJax completion and the documented
post-paint frames are part of the action's completion promise.

Synchronous presentation-only actions use the same commit rules without ELK.
Transient drag previews may update coordinates and incident routes before
commit, but cancellation restores their pre-drag coordinates and creates no
history or persistence entry.

### Shipped extensions

| Extension | Cutover status | Consumer | Supported behavior | In complexity budget |
|---|---|---|---|---|
| Edge projection and presentation | Required | Blueprint, docstring, Rutter, and math graphs | Current omission, aggregation, bundling, provenance, filtering, and styling behavior | Yes |
| Quick guide | Required when `quick_guide` is supplied | Blueprint and math visualizers | Current usable-step, focus, and target behavior | Yes |
| Math typesetting | Required when declared in `renderer_dependencies` | Math graph | Serialize typesetting and expose completion | Yes |
| Legend and traversal | Required | All graph viewers | Current filtering, selection, ancestor/successor traversal, and presentation explanations | Yes |
| Persistence | Required | All graph viewers | Current supported state and migrations, implemented through the single state owner | Yes |
| Presentation nodes | Required when supplied | Blueprint presentation views | Current grouping and bounded interactions | Yes |

An extension cannot create a competing viewer-state owner or structural render
entry point. It requests ordinary actions and consumes normalized scene data.

The default Quick Guide retains its current topics. Existing inaccurate prose
is corrected to match baseline behavior—for example, search creates and
replaces a search-sourced selection rather than leaving selection unchanged.
Browser tests must prove that every guide target exists and that dragging,
multi-selection, legend traversal, bulk hide/dim, restore, search, pan/zoom,
and the controls panel still work as described.

## Internal machinery deliberately removed or consolidated

- Computed edge occlusion and all per-edge masks/resources.
- Viewport culling, spatial indexes, dirty sets, invalidation taxonomies,
  semantic zoom, Canvas, and WebGL.
- Competing full, fast, presentation-only, and edge-only render pipelines;
  replace them with one action-to-scene transaction and bounded paint-only
  updates.
- Duplicate snapshot and rollback helpers; consolidate them behind the state
  API while preserving operable global history and presentation mutations'
  transactional rollback. Do not reproduce the dead filter-history stacks.
- DOM-based semantic inference and style resolution; projection and styling
  become pure data stages before paint.
- Redundant geometry caches and fallback cascades when the same visible result
  can be produced by one route representation.

No user-visible control or documented workflow is removed by this plan. Build
polling and any other non-viewer behavior may be removed only after confirming
that it is not a supported user workflow.

Canvas or viewport culling may be reconsidered only if a mask-free,
visible-state-only SVG renderer still fails a benchmark with many objects
simultaneously visible. They are not fallback work already authorized by this
plan.

## Complexity accounting

The budget covers the union of all unique first-party browser JavaScript
shipped for every supported cutover profile, including core, omission
projection, Quick Guide, MathJax coordination, inline template scripts, and
generated first-party runtime source. Optional naming does not exempt shipped
code.

The checked-in complexity check must:

- count the unminified source of every shipped first-party browser asset once;
- reject minification, compressed formatting, generated duplication, or moving
  JavaScript into HTML/configuration to reduce the count;
- report modules, state owners, structural render entry points, and each
  extension's contribution;
- derive the baseline from the exact shipped-asset manifest using one declared
  physical or executable-line metric, then report the result by subsystem;
- treat a 25% reduction as an evidence-dependent target until a parity-complete
  candidate demonstrates it; structural simplification and a net reduction are
  required, but a percentage never authorizes a compatibility regression;
- include all renderer-motivated Python/schema/adapter additions in a separate
  whole-change before/after report.

Its planned invocation is:

```bash
scripts/check-html-renderer-complexity.py --root . --baseline-ref BASELINE_SHA --target-reduction-percent 25
```

The whole change must be net-negative in first-party executable and schema
logic. Moving browser behavior into Python, schemas, templates, generated
payloads, or adapters solely to improve the count does not qualify as
simplification. Directly changed upstream code is part of both the audit and
deletion accounting.

The architecture check also asserts one state API, one coordinate-composition
owner, one edge-projection module with explicit stages, and one committed
structural render entry point. Ephemeral drag preview is allowed only through
the bounded path defined above. These checks support, but do not replace, the
reviewer trace from action through candidate state, scene, optional layout,
paint, and commit.

## Implementation sequence

1. Turn the functionality inventory into a compatibility ledger in the sibling
   plan. Every current user-facing behavior is preserved unless a separate,
   explicit product decision retires it.
2. Keep the version-2 schema and maintained adapters stable. Add one internal
   normalization boundary and parity fixtures covering blueprint, docstring,
   Rutter, and math payloads.
3. Add the checked-in complexity checker, benchmark harness/state fixture, and
   contract behavior matrix before building the replacement.
4. Build the simple runtime alongside the current runtime without changing the
   default. Keep the default Quick Guide accurate against the preserved UI.
5. Verify visible-only mounting, mask-free painting, the single state path,
   version-2 normalization compatibility, guide accuracy, and the benchmarks
   below.
6. Require independent subagent audits for simplicity, UI/behavior parity, and
   measured performance. Audit findings are rulings to resolve, not prose to
   append and ignore.
7. Switch the default only after those audits pass, then delete the replaced
   runtime. Do not retain two permanent implementations.

## Acceptance gates

Add an executable checked-in harness at
`scripts/benchmark-html-renderer.py` and a deterministic state fixture at
`tests/fixtures/visualization/html-renderer-benchmark-state.json`. Generate one
repository artifact, record its SHA-256 and the exact filter state producing a
40-node visible-id set in the trial manifest, and use the identical artifact and manifest for baseline and
candidate. Check in raw JSON samples under
`docs/plans/html-renderer/evidence/` with renderer commit, payload SHA, node and
edge counts, Chrome version and flags, viewport, host/OS, headless status, and
every sample.

Its planned invocation is:

```bash
scripts/benchmark-html-renderer.py --repo-root . --runs 40 --baseline-runtime BASELINE_RUNTIME --candidate-runtime CANDIDATE_RUNTIME --output docs/plans/html-renderer/evidence/RESULT.json
```

Run at least 40 samples per case, alternating baseline and candidate trials.
Each sample uses a fresh browser profile or clears all viewer storage before
navigation. Define p95 as nearest rank
`sorted_samples[ceil(0.95 * count) - 1]`; report paired candidate/baseline
deltas and their bootstrap 95% confidence interval.

Every structural action returns a completion promise. Measure from before
dispatch until projection, automatic ELK, scene paint, the MathJax queue when
active, two `requestAnimationFrame` callbacks, and
`PerformanceObserver.takeRecords()` have completed. Reset the `longtask`
observer before each sample. Report projection, layout, paint/typesetting, and
end-to-end components.

- Zero `[data-edge-occlusion-mask]` elements and zero mask references.
- Mounted `.graph-node` and `.edge-path` counts equal expectations computed by
  a test-side reference projector, not counts reported by the runtime itself.
- Report total `#graph-svg` descendants and per-layer descendants so deleted
  mask/resource complexity cannot move elsewhere in the SVG.
- Filtering to the 40-node audit case: end-to-end median at most 50 ms, p95 at
  most 100 ms, and no long task above 100 ms during the full transaction.
- Showing the full repository graph with already-known positions: end-to-end
  median at most 350 ms and p95 at most 500 ms.
- For both filter-to-40 and known-position show-all, candidate median and p95 must
  be at most 75% of their paired current-runtime baseline.
- Cold detail expansion and container collapse/expand include automatic ELK
  end-to-end. Their paired median and p95 ratios must have an upper 95%
  confidence bound no greater than 1.05; report layout and paint separately.
- Measure omission projection on the repository trial plus a bounded
  branching/cycle fixture. Assert deterministic derived-edge contents and
  report projection time; do not add memoization or indexing without a new
  measured bottleneck and plan review.
- Pan and zoom do not project, lay out, or repaint the scene.
- Single-finger pan, pinch zoom, two-finger-tap zoom-out, double-tap zoom-in,
  keyboard and toolbar zoom, fit, zoom-to-selection, and gesture-end
  persistence retain baseline behavior.
- The checked-in complexity checker passes the asset-manifest, metric, and
  net-reduction contract and reports the 25% target separately. Missing that
  target does not authorize functionality removal. The change adds no new
  culling, index, worker-coordination, or invalidation subsystem.
- A reviewer can trace a structural action through state, scene projection,
  optional layout, and paint without crossing competing state owners or render
  paths.
- A checked-in UI parity manifest records every toolbar control, sidebar
  section, legend action, shortcut, and guide target. Baseline and candidate
  must expose the same labels, order, enabled states, and actions unless an
  individually documented change has explicit approval.
- Baseline/candidate screenshots at the same desktop and narrow-screen
  viewports cover the default graph, an active selection, grouping, dimming,
  hidden-node restoration, and expanded controls. Review tolerates geometry
  and minor paint differences caused by mask removal, but not missing or
  substantially rearranged UI.

Every `Contractual` and `Simplify internally` row, plus the retained user-facing
purpose of each `Remove mechanism` row, must have a baseline/candidate
assertion. The behavior matrix includes initial visibility; single-node,
selection, and complement hide/dim actions; individual restore and reset;
multi-selection; current global undo/redo; dragging and manual-position
persistence; category/relation filters and legends; relation traversal; detail
change; presentation-node grouping; collapse/expand; edge regeneration,
projection, aggregation, bundling, provenance, dominance, and styling; layout
and routing controls; persistence/migration; inspector and keyboard behavior;
touch gestures; node shapes/decorations and detail-promotion visuals; tooltip
and responsive-sidebar behavior; and every current Quick Guide behavior.
Representative blueprint, docstring, Rutter, and math payloads must pass the
same observable assertions in baseline
and candidate runtimes. Performance and LOC results cannot substitute for
these contracts.

Timing thresholds are initial targets derived from the current audit, not
guarantees. If the simple implementation misses one, profile it before adding
machinery; a miss does not authorize a new subsystem.

## Audit record

Three independent read-only subagent audits were required before this revision:

- Architecture audit: replace the runtime, use one render path, and do not add
  culling or invalidation machinery before measuring the simple version.
- Performance audit: mask deletion and visible-state mounting address the
  measured costs; hidden-list, listener, and viewport optimizations lack
  evidence as first-step work.
- Maintainability audit: separate user-facing compatibility from internal
  implementation parity and make the inventory an explicit contract ledger.

A later fresh three-agent audit agreed with the core performance direction but
did not establish permission to retire current functionality. The subsequent
product ruling is that this is an internal refactor with minimal UI change:
all shipped extensions count toward complexity, current graph and interaction
semantics remain compatibility requirements, layout decisions are finite, and
performance uses a checked-in matched-trial harness rather than plan-only
timings.

A final fresh simplicity, functionality, and adversarial-architecture audit
endorsed the same core direction but required the precise contracts now above:
state partitions behind one API; distinct painted and layout universes; staged
edge projection; coordinate composition; atomic async commit/rollback;
containment-safe layering; bounded drag preview; fallback placement; touch
parity; correction of nonexistent drag/filter undo claims; and an asset-derived
rather than assumed complexity baseline.

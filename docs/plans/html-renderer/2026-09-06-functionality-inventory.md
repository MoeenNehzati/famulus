# html_renderer behavior inventory and disposition ledger

This inventory is the compatibility checklist for an internal refactor. The
replacement in `2026-09-06-occlusion-simplification-design.md` preserves
current user-facing behavior while replacing the machinery that implements it.
Exact DOM structure and internal algorithms are not compatibility requirements.

The detailed sections are evidence about the implementation as inspected on
2026-09-06. Their `file:line` anchors are historical navigation aids and may
move as the runtime changes. For example, `updateVisibilityFast` now lives in
`layout.js`, not `render_pipeline.js`.

## Disposition policy

| Status | Meaning |
|---|---|
| Contractual | Observable behavior that the replacement must preserve. |
| Simplify internally | Preserve observable behavior through a smaller mechanism. |
| Remove mechanism | Remove this implementation technique while retaining its user-facing purpose where applicable. |

Each item is also classified by evidence: **verified current behavior**,
**dead mechanism**, or **approved product change**. Unless explicitly labelled
otherwise, detailed items are verified current behavior. Dead mechanisms are
not parity requirements. This refactor contains no implicit product additions;
fixing an existing defect or adding behavior requires separate approval.

## Summary rulings

| Section | Status | Replacement ruling |
|---:|---|---|
| 1. Layout | Simplify internally | Preserve current controls, containment-aware ELK results, timeout, latest-result protection, and manual positions through one layout owner. |
| 2. Render paths | Simplify internally | Replace full/fast/presentation paths with one structural transaction plus bounded presentation-only updates. |
| 3. Edge geometry | Simplify internally | Preserve routing controls, attached edges during drag, containment-safe routes, and parallel-edge readability; algorithms and caches may change. |
| 4. Occlusion | Remove mechanism | Remove per-edge masks and preserve basic readability through paint order, fills, and simple styling. Exact attenuation pixels are not a contract. |
| 5. Arrowheads | Simplify internally | Preserve visible arrow direction and style with shared markers where possible. |
| 6. Containment | Contractual | Keep one canonical parent index and derive containment once during scene projection. |
| 7. Node shapes/style | Contractual | Preserve current category, kind, shape, selection, container, and declared decoration behavior; consolidate style resolution. |
| 8. Detail-promotion visuals | Contractual | Preserve the current visible distinction for containers representing hidden detail. |
| 9. Decorations | Contractual | Preserve payload-declared visible decorations without requiring the current DOM construction. |
| 10. Edge presentation | Contractual | Preserve semantic color/dash/width, declared facets, aggregate/bundle presentation, gradients, filters, halos, and legend explanations through one resolver. |
| 11. Derived/projected edges | Contractual | Preserve omission rules, redirects, aggregation, bundling, dominance/subsumption, witnesses, represented edges, filtering, and deterministic fidelity behavior in one pure projector. |
| 12. Legend | Contractual | Preserve category/relation filtering, selection, traversal, hierarchy, tooltips, and edge-presentation explanations. |
| 13. Navigation | Contractual | Preserve mouse and touch pan, wheel/pinch/button/keyboard/tap zoom, fit, zoom-to-selection, and gesture persistence. |
| 14. Node dragging | Contractual | Preserve node/descendant dragging, manual positions, live incident-edge regeneration, and persistence. Drag undo is not current behavior and is not added here. |
| 15. Hover/tooltips | Simplify internally | Preserve current node/edge hover emphasis, viewport clamping, drag suppression, and math-aware tooltip behavior. |
| 16. Selection/shortcuts | Contractual | Preserve multi-selection, primary selection, keyboard activation, traversal selection, and current shortcuts. |
| 17. Routing controls | Contractual | Preserve the controls and visible route choices; consolidate their implementation. |
| 18. Sidebar layout | Contractual | Preserve responsive panels, resizing, collapse, reordering, and persisted state. |
| 19. Inspector | Contractual | Preserve the current structured details and multi-selection views. Normalize legacy detail forms before paint. |
| 20. Search/filter | Contractual | Keep text search and basic category/relation filters under one visibility predicate and one state owner. No separate filter history. |
| 21. Detail level | Contractual | Keep one detail-level state field because large hierarchical payloads depend on it for their initial scene. |
| 22. Presentation nodes | Contractual | Preserve grouping facets, compartments, selection, inspection, drag, collapse, reset, persistence, and scope behavior through the common state/action path. |
| 23. Hide/show/dim | Contractual | Preserve single and bulk hide/dim, complement actions, restore, reset, inherited container behavior, and unmounted hidden scene objects. |
| 24. Bulk actions/history | Contractual | Preserve bulk actions and operable global undo/redo. Populated but unconsumed filter history is dead machinery, not a second required timeline. |
| 25. Quick guide | Contractual | Keep the public option and current guide topics as an isolated UI extension that does not own graph state. |
| 26. Persisted state | Contractual | Preserve current state coverage and supported migrations through the single state owner. |
| 27. Math typesetting | Contractual when declared | Keep the narrow dependency interface and current completion behavior. |
| 28. Cross-cutting | Simplify internally | Normalize styles and actions before paint; verify build polling separately before changing it. |

## Hard compatibility boundary

The replacement preserves `ElkHtmlRenderer`, `build_html_with_elk`, the current
version-2 payload contract, entity and edge identifiers, containment,
standalone HTML output, current controls, and the observable behaviors in this
ledger. Exact DOM structure, private function boundaries, cache shapes, mask
construction, and pixel-identical geometry are not contracts. Existing
browser tests are compatibility evidence; changing their asserted behavior
requires an explicit product decision, not a refactor convenience. When guide
prose conflicts with verified runtime behavior, preserve the runtime behavior
and correct the guide as documentation maintenance.

## 1. Layout (ELK integration)

- Hierarchical, recursive containment-aware layout: each container's children
  are laid out bottom-up before the container itself is sized. `layout.js:184-252`
- Layout algorithm auto-selection: `layered` when a subgraph has edges, else
  `rectpacking`; overridable via `docData.ui.layout.elk_algorithm`. `layout.js:187-188`
- Document-configurable layout knobs: direction (`docData.ui.layout.rankdir`,
  `LR/RL/TB/BT` → ELK `RIGHT/LEFT/DOWN/UP`, default `RIGHT`), target aspect
  ratio (`aspect_ratio`, default **1.7** here — also read by presentation-node
  compartment layout with a *different* default, see §22), and edge routing
  style (`edge_routing`, default `"ORTHOGONAL"`). `layout.js:61-65,202`
- Per-container spacing clamping — nested containers get tighter node/layer
  spacing than the root, via depth-based metrics (3 tiers, capped at depth 2).
  `layout.js:190-196`, `bootstrap.js:220-230`
- Edge projection into containers: cross-descendant edges are projected onto
  a container's direct children so they influence that container's internal
  layout. `layout.js:158-172`
- Routing presets (`compact`/`balanced`/`spacious`) bundling clearance/spacing/
  merge-lane defaults, `balanced` is default; overridable per-document via
  `docData.ui.layout.node_spacing`/`layer_spacing`. `layout.js:55-80`
- 15s ELK layout timeout with explicit error message; "ELK failed to load"
  error if the `ELK` global isn't present. `layout.js:109-118,174-182`
- Fallback node placement for entities ELK didn't return: relative-to-container
  offset cascade for siblings, or a position-based grid fallback.
  `render_pipeline.js:58-99`
- Render-generation staleness guard: a superseded in-flight full-layout call
  aborts without applying stale results. `render_pipeline.js:8,31`
- Layout error rollback: restores previous positions/presentation-node state,
  persists, shows an error message — never leaves a half-applied layout.
  `render_pipeline.js:220-227`
- Manual drag-position pruning on relayout: non-grouped child/container nodes
  lose manual overrides on full relayout unless explicitly preserved or a
  presentation-node restore is in progress. `render_pipeline.js:101-111`
- Empty-graph and zero-rendered-node status messages. `render_pipeline.js:13-26,211-217`
- Auto-fit viewBox/size computed from the union of all node + presentation-
  shell bounds, minimum 900×500, run once per full layout. `render_pipeline.js:114-137`
- `docData.ui.focus.selected_node_id` seeds the initial selection at load, so
  an adapter can pre-select a node on first render. `bootstrap.js:410-413`

Dead code, not live behavior — no need to preserve: `enforceVerticalNodeSpacing`
(`layout.js:320-337`) has zero call sites anywhere in the codebase.

## 2. Fast vs. full render paths

- `updateVisibilityFast`: toggles node/non-derived-edge `display` without
  recomputing geometry (positions stay frozen); only derived/projected edges
  are rebuilt, since they depend on the current omission set. Falls back to a
  full relayout if any expected-visible node has no DOM element yet.
  `layout.js:448-511`
- `updateVisibilityFull`: full ELK rerun + full DOM rebuild — used only for
  spacing/algorithm changes and structural changes (container collapse,
  detail-level change). `render_pipeline.js:1-228`
- Edge-only routing changes (geometry/curvature/etc.) reroute + remask
  without a full rebuild or ELK rerun. `controls.js:213-218`
- MathJax clear/retypeset wrapped around every layer replacement, since
  labels/subtitles/tooltips can contain math markup. `render_pipeline.js:14-16,124-125,198-199`

## 3. Edge routing / geometry

- Five geometry styles: bezier (default), polyline, spline, straight,
  orthogonal — each with its own curvature/bend/tension config, auto-choosing
  horizontal vs. vertical dominant axis. `layout.js:66-71`, `geometry.js:150-233`
- Rounded-corner path builder for point-list routes (orthogonal doglegs,
  containment paths). `layout.js:339-361`
- Edge-node gap (selection-ring gap + stroke + configurable extra clearance)
  keeps edges clear of nodes' selection rings. `geometry.js:70-75`
- Endpoint offset outward from node center so arrowheads land outside the
  shape. `layout.js:296-318`
- Containment-aware routing: an edge between a container and its descendant
  routes via a dedicated internal path, not ordinary point-to-point.
  `geometry.js:235-244,304-310`
- Containment internal path picks the cheapest of 4 candidate anchor pairs
  (clearance-based), with a penalty for cutting through the container header.
  `geometry.js:264-302`
- Merged-edge lanes: edges sharing a target converge into one shared lane
  before the target's left-middle point, avoiding visual clutter.
  `layout.js:432-444`
- Parallel-edge lateral offset for multiple edges between the same node pair,
  order-independent grouping key so both directions get consistent lane
  counts. `geometry.js:118,193-225,326-332`
- Manual straight-line clipping for live drag rendering. `geometry.js:82-109`
- Post-drag rerouting: single-node (`rerouteIncidentEdgesFromCurrentPositions`)
  and whole-graph (`rerouteAllVisibleEdgesFromCurrentPositions`) variants,
  both honoring manual position overrides over ELK positions.
  `geometry.js:334-380`
- Manual position overrides survive relayout (except where explicitly pruned,
  see §1) via `getEffectivePos` preferring manual over ELK position.
  `geometry.js:7-9`

## 4. Edge occlusion masking (mechanism removed; readability intent retained)

- Per-edge SVG luminance mask dimming (not fully hiding) the portion of an
  edge passing under a non-endpoint node/container, at fixed strengths:
  node 0.78, container 0.74, label text 0.86. `geometry.js:419-431,465-538`
- Deliberately partial (not full) occlusion so a route stays traceable under
  a shape — an explicit design intent, not an accident. `geometry.js:419-421`
- Node-shape-clone blockers follow the node's actual shape (not just its
  bbox), so non-rectangular nodes occlude correctly. `geometry.js:426-454`
- A path's own endpoint nodes are exempted from occluding themselves via the
  *shape* blocker only — the label/subtitle text-bounds blocker runs
  unconditionally, so an endpoint's own label can still dim the edge that
  terminates at it. `geometry.js:521-522,527-531`
- Bounding-box pre-filter (8px padding) avoids unnecessary mask elements.
  `geometry.js:457-463,522-531`
- Recomputed after every full render, fast toggle, and drag-driven reroute.
  `geometry.js:503`, `render_pipeline.js:195,510`

## 5. Arrowheads

- Arrowhead geometry sampled from the path's actual rendered end
  (`getTotalLength`/`getPointAtLength`, 14px back from tip) so arrows follow
  curved/rounded paths correctly, not just raw coordinates. `layout.js:363-373`
- Triangle size scales with the edge's resolved stroke width (min 12px,
  ratio increased this session). `layout.js:384-419`
- Arrowhead color/opacity/display tracks its path's stroke/opacity/display;
  hidden when the path is hidden. `layout.js:384-419`
- One `.edge-arrow` polygon per edge path, keyed by edge id, replaced (not
  duplicated) on rebuild. `layout.js:421-430`
- Graceful fallback (arrow left unchanged) if path geometry sampling throws
  (e.g. zero-length/disconnected path). `layout.js:363-372,391`

## 6. Containment / nesting

- Container index built from both `entity.children` and reverse
  `entity.container` pointers, filtered to currently visible entities.
  `geometry.js:11-40`
- "Is a container" is derived dynamically from having ≥1 visible child in the
  current index, not a static entity flag — container-ness can change with
  visibility. `geometry.js:65-68`
- Iterative descendant gathering (for group-drag/hide-with-children).
  `geometry.js:46-63`
- Container auto-fit around descendants' bounding boxes plus per-container
  padding, bottom-up, cycle-defended. `projection.js:359-395`
- Containment graph construction defends against cycles (falls back to a
  childless leaf on cycle detection) and sorts children by declared
  `position` then id. `projection.js:397-502`

## 7. Node shapes & styling

- 11 distinct shape renderers: ellipse, circle, diamond, hexagon,
  parallelogram, stadium, cylinder, note, roundrect, double-rect, and default
  rect (used for unknown shapes and always for containers regardless of the
  entity's declared shape). `node_renderer.js:178-347`
- Selection ring: a geometric outward offset of the node's own shape (not
  just a bbox), including a real winding-aware polygon-offset algorithm for
  diamond/hexagon/parallelogram/note. Two gaps in this: `cylinder`'s shape is
  an SVG `path`, which `expandSelectionRing` doesn't handle, so it gets an
  unexpanded, shape-identical ring; `stadium`'s cloned rect ring doesn't keep
  its rounded-cap proportions after resizing (only the `roundrect` branch
  adjusts `rx`/`ry`). `node_renderer.js:64-133`
- Multi-color "kind" fill: ≥2 colors on a node produce a cached diagonal
  linearGradient instead of a flat fill. `node_renderer.js:5-27`
- Inferred-node dashing (`entity.source === "inferred"` → dashed stroke).
  `node_renderer.js:194,262,293`
- Container shell styling: translucent fill (0.055 subtle / 0.16 strong) +
  category-colored stroke, selected via `presentation.tone`. `node_renderer.js:29-35`
- Standalone container-shell renderer (non-interactive frame + header,
  capped at 58px tall) used separately from the main per-node path.
  `node_renderer.js:37-62`
- Containment depth tracked and exposed as `data-containment-depth` for CSS.
  `node_renderer.js:157-164,198-200`
- Corollary render-type override: a `type:"corollary"` entity can visually
  borrow another type's category style via `renderTypeOverrides`.
  `projection.js:326-337`

## 8. Detail-level promotion visuals

- Containers with a detail-hidden descendant get `detail-promoted` styling,
  a depth-based class (capped at depth 2), and a branch/leaf distinction
  (still has other visible children vs. none). `node_renderer.js:138-176`

## 9. Node decorations

- Offset-outline "stacked card" decoration: a single `entity.decorations`
  entry (`type:"outline", style:"offset"`) drives both an extra background
  plate offset (+5,-5) behind the node, non-interactive, drawn first, *and*
  a badge pill (uppercased label + optional count, auto-width 58-116px,
  category-colored) top-right — these are one decoration, not two; the badge
  never appears without the plate. `node_renderer.js:186-188,297-345`

## 10. Edge type styling & metadata presentation

- Per-edge-type style catalog (stroke/color/dash), explicit or palette+dash-
  rotation fallback. When a type has no resolvable style, the edge falls
  back to `edgeColorForTarget` — colored by the **target node's** palette
  index, i.e. by which node it points to, not by edge type.
  `projection.js:311-320`, `bootstrap.js:190-200`
- Declarative facet-variant matching (`docData.ui.edge_presentation.facets`)
  against edge field values (supports dotted `metadata.*` paths, including
  `constituent.metadata?.outside_id` for bundle display-target overrides),
  composed into a merged style. `edge_presentation.js:23-50`, `filtering.js:53`, `inspector.js:219`
- `line_pattern` → dasharray mapping (dashed/dotted/solid/unset), with
  explicit vs. deferred-to-semantic-style distinction. `edge_presentation.js:52-57`
- Aggregate/bundle metadata presentation: `aggregate`, `same_type_bundle`,
  `mixed_type_bundle` states, cumulative (max stroke width across active
  states), each contributing halo/outline width/color/opacity. Labels,
  descriptions, and style values are overridable per-document via
  `docData.ui.edge_metadata_styles`, merged over the built-in catalog.
  `edge_presentation.js:59-95`, `bootstrap.js:30-67`
- Deterministic per-edge resource ids (FNV-1a hash of edge id) so gradients/
  filters get replaced, not duplicated, across re-renders. `edge_presentation.js:97-105`
- Presentation resource cleanup prevents `<defs>` accumulation across
  re-renders/derived-edge churn. `edge_presentation.js:107-114`
- Mixed-type bundle gradient: one color per distinct constituent type,
  evenly-banded (not smoothly blended) with a narrow neutral seam between
  bands, keeping each type visually distinct. Band order — and therefore
  which color the arrowhead inherits — is determined by **alphabetically
  sorting** constituent type strings, not by witness or edge order.
  `edge_presentation.js:116-158`
- Halo/outline compositing via SVG filter (feMorphology dilate + feFlood +
  feComposite + feMerge), only created if at least one effect is configured.
  `edge_presentation.js:160-208`
- Gradient geometry re-anchored to actual path start/end after reroute, with
  silent no-op on a momentarily-detached/empty path. `edge_presentation.js:210-223`
- Single presentation-application entry point orchestrating stroke/dash/
  width/opacity/filter resolution with clear precedence (metadata style →
  declared facet style → semantic default). `edge_presentation.js:225-263`

## 11. Derived / projected edges

- Multi-hop transitive projection across chains of hidden/omitted nodes,
  driven by declarative `node_omission.rules`, with confidence ranking
  (Verified > Likely > Speculative), cycle defense via visited-state set, and
  fidelity degradation tracking (exact vs. degraded). `projection.js:15-52,199-244`
- Declared projection-target override lets data authors redirect where a
  hidden-node edge "should" project to. `projection.js:253-269`
- Aggregate-edge synthesis across collapsed containers: hidden/collapsed
  endpoints are substituted with a visible ancestor, re-emitted as
  `aggregate: true` edges tracking original endpoints. For nested collapsed
  containers, the substituted representative is the **outermost** collapsed
  ancestor, not the nearest one — a second walk keeps overwriting the result
  all the way to the root. `projection.js:148-197`
- Edge bundling by shared endpoints + presentation signature: label becomes
  "N relationships", underlying canonical edges tracked in
  `metadata.represented_edges` (recursively unwrapping prior bundles).
  `projection.js:67-107`
- Merge logic for competing edges at the same slot: non-derived (canonical)
  always outranks derived regardless of confidence; among same derived-ness,
  higher confidence wins; aggregate/derived unions accumulate witnesses
  rather than replacing. `projection.js:109-147`
- Dominance/suppression among competing projected edges sharing an endpoint
  pair: a **strictly-stronger-type** peer needs only equal-or-better
  fidelity to dominate, but a **same-type** peer needs **strictly better**
  fidelity — two same-type edges of equal fidelity never suppress each
  other. Suppressed relationships are recorded (not discarded) on the
  dominator. `projection.js:283-307`
- Hidden-edge-type filtering applied consistently across aggregate,
  traversal, and final-assembly passes. `projection.js:172-173,197,247-248,276`
- `docData.relation_semantics.subsumptions` (`{stronger_type, weaker_types}`)
  is compiled into a transitively-closed dominance map at bootstrap and is
  the data source for the "stronger-type" half of the dominance rule above.
  `bootstrap.js:163-182`

## 12. Legend

- Category (shape) legend: only categories actually present in the document
  get a row; parent/child grouping with a clickable parent heading (selects
  nodes across **all descendant categories**, not just direct children);
  category icons mirror the real shape set (plus a distinct frame icon for
  containers). `legend.js:3-81,136,139-145,209-265`
- Category row click → node selection (replace on plain click, additive on
  Ctrl/Cmd-click, toggles fully-selected state); disabled when the category
  currently has zero visible members. `legend.js:139-207,244-265`
- "Colors" (kind) legend section only renders when kind is *informative* —
  i.e. partitions entities differently than category/type — computed via a
  bijection check. `legend.js:272-293`
- Color row click → kind-based selection, same replace/additive semantics as
  category rows. `legend.js:147-152,190-207`
- Edge-type legend: tree built only from categories actually present, depth-
  capped at 2 for indentation; row click toggles exclusion (excluding a
  parent also clears now-redundant child exclusions; child rows locked while
  parent excluded). `legend.js:325-357,452-466`
- Relation traversal buttons (ancestors/successors) per edge-type row and a
  global "Relations" header: BFS-expand current selection along rendered,
  visible edges of that type, bundle/aggregate-aware (unwraps to real
  constituents before type-matching). `legend.js:359-418`
- Edge-presentation ("aggregate/bundle/gradient explanation") legend section:
  rebuilt from scratch on every relevant state change; declared-facet rows
  shown only if a currently-visible edge matches; aggregate/bundle-state rows
  are always shown, marked "unavailable" (not omitted) when not currently
  present — the one legend area that shows absent items rather than hiding
  them. `legend.js:476-535`
- Legend row selection-state sync and hover tooltips (bold label +
  description, mouse-follow positioning). `legend.js:120-166`
- Collapsible legend sections (`<details>`, default open). `legend.js:90-100`

## 13. Panning / zooming / gestures

- Mouse-drag pan (left-button on empty canvas only — excludes nodes/edges/
  arrowheads) and single-finger touch-pan equivalent. `viewer_state.js:199-225,280-294`
- Mouse wheel zoom (1.12×/step), toward cursor point. `viewer_state.js:180-198`
- Two-finger pinch zoom with a move-threshold to distinguish pinch-tap
  (zooms out 1.3×) from a real pinch. `viewer_state.js:180-277`
- Double-tap-to-zoom (≤320ms, ≤30px apart) zooms in 1.3×. `viewer_state.js:180-277`
- Keyboard zoom in/out (`+`/`=`, `-`) at 1.2×, centered on canvas; toolbar
  zoom buttons at 1.3× centered on content. `controls.js:161-169,300-313`
- Fit-to-content (`F` key, toolbar button): bounds from all visible entities
  + presentation-node shells, 40px padding, never zooms in past 100% via
  `MIN_ZOOM..1` clamp. `controls.js:171-174,314-319`, `viewer_state.js:122-178`
- Zoom-toward-selection: if a node is selected and visible, zoom targets its
  center instead of overall content center. `viewer_state.js:122-178`
- All gesture end-points persist viewer state. `viewer_state.js:180-277`

## 14. Node dragging

- Left-button-only drag start; suppressed for nodes in an active presentation
  group. `interactions.js:153-176`
- Dragging a node drags all its descendants together (`gatherDescendantIds`).
  `interactions.js:159-160`
- Drag-threshold (5px) preserves click semantics for a near-zero-movement
  mousedown+mouseup. `controls.js:10-14`
- Live rerouting of only the affected incident edges during drag (not a full
  reroute pass). `controls.js:44-48`
- On drag end: full incident-edge reroute, occlusion-mask refresh, and state
  persistence — but only if an actual drag occurred. `controls.js:58-76`
- Ordinary manual positions are absent from `graphStateSnapshot`; drag end does
  not record a graph-history entry. Drag undo is therefore not current behavior.
  `graph_actions.js:12-27`, `controls.js:58-76`
- Post-drag click suppression via a one-tick-delayed flag reset, specifically
  to swallow the synthetic click mouseup can trigger. `controls.js:74-76`, `interactions.js:130-131`

## 15. Hover / tooltips

- Node hover: tooltip + `.hovered` class + highlight of all *incoming* edges;
  suppressed while dragging. `interactions.js:112-129`
- Edge hover: tooltip + emphasis (forced dark stroke, widened, opacity 0.98,
  arrowhead resynced); only the currently-hovered edge's own leave event
  clears it (stale mouseleave from a since-replaced hover is a no-op).
  `interactions.js:79-96`
- Tooltip positioning is rAF-throttled and viewport-clamped (10px margin,
  16px cursor offset). `interactions.js:31-47`
- Tooltip content re-typesets math on show, clears math tracking state on
  hide. `interactions.js:49-67`

## 16. Click / selection / keyboard shortcuts

- Single-click (180ms debounce to allow a following dblclick to cancel it):
  plain click replaces selection with just this node; Ctrl/Cmd-click toggles
  additive multi-select. `interactions.js:130-139`
- Double-click: hides the node, *unless* it's a collapsible container and
  Alt is held, in which case it toggles collapsed state instead.
  `interactions.js:140-151`
- Edge click: opens edge details in the inspector, no debounce, always clears
  node selection **and the presentation-node selection**. `interactions.js:97-100`, `inspector.js:147-153`
- Escape/`deselect()` also clears the presentation-node selection, not just
  node selection. `inspector.js:163-169`
- Full keyboard-activation parity (Enter/Space trigger the same handlers as
  click) for both nodes and edges, via `tabindex="0"`/`role="button"`.
  `interactions.js:73-77,107-111`
- Global keyboard shortcuts (all suppressed while typing in a field or while
  the quick guide owns focus): `Escape` deselect; `R` clear all manual
  positions + full relayout; `C`/`Shift+C` reset view (with/without
  categories, the latter also wiping persisted state); `H`/`Shift+H` hide
  selected/unselected (preserving ancestors of the selection);
  `D`/`Shift+D` toggle-dim selected/unselected; `+`/`=`/`-` zoom; `F` fit.
  `controls.js:260-319`
- Toolbar Reset **button** has its own independent click/double-click
  mechanism, distinct from the `C`/`Shift+C` keyboard shortcut: single click
  (180ms debounce) resets without categories; double-click cancels the
  pending single click and resets *with* categories (including the
  localStorage wipe). `controls.js:143-159`
- Global undo/redo (`Ctrl/Cmd+Z`, `Ctrl+Y`, `Ctrl+Shift+Z`), 100-entry
  history, captured in the capture phase so it wins over other handlers.
  `graph_actions.js:3-5,90-95,218-249`
- Multi-select summary strip with per-node badges: click makes that node
  primary, double-click removes it from selection. `selection.js:44-84`
- Search-driven auto-selection: while a query is active, matching
  nodes/edge-endpoints auto-select (tagged `"search"` source vs. `"explicit"`
  for manual selection); clearing the query clears a search-sourced
  selection. `selection.js:128-151`

## 17. Routing controls (sidebar UI)

- Compactness preset select — a *layout*-level change (full relayout).
  `controls.js:226-232`
- Geometry select and per-geometry numeric sliders (clearance, corner
  radius, parallel spacing, merge-lane distance, polyline bend, spline
  tension, bezier curvature) — *edge*-level changes (reroute + remask only,
  no relayout). `controls.js:234-250`
- Node/layer/edge-node spacing sliders — *layout*-level changes (full
  relayout). `controls.js:252-256`
- Parallel-spacing row and per-geometry parameter rows shown/hidden based on
  whether the graph has any parallel edges and which geometry is active.
  `layout.js:89-100`

## 18. Sidebar layout management

- Two independently resizable/collapsible sidebars, width-clamped
  (220-520px, also capped at 42% viewport width) for both pointer-drag and
  keyboard (arrow/Home/End) resize — both paths route through the same
  `clampSidebarWidth()`. `sidebar_layout.js:3-109`
- Narrow-viewport (≤720px) mode switches to mutually-exclusive full-screen
  drawer semantics instead of collapse/expand. `sidebar_layout.js:13-14,50-69`
- Drag-to-reorder sidebar sections (drag handle required), persisted under a
  separate `::sidebar` localStorage key; fixed-top sections excluded from
  reordering/persistence. `controls.js:179-209`, `viewer_state.js:91-113`
- Advanced-controls (`routing-controls`, `raw-json`) relocated into a
  dedicated slot once at startup. `sidebar_layout.js:111-117`

Known pre-existing bugs recorded during the inventory. They are historical
evidence, not preservation requirements for the simplified replacement:
1. Viewport narrow-transition drawer resets don't call `saveViewerState()`.
   `sidebar_layout.js:119-128`
2. Sidebar-collapse state doesn't survive a reload: `saveViewerState` writes
   `leftPanelCollapsed`/`rightPanelCollapsed`, but `restoreViewerState` only
   reads `leftPanelCollapsed` under the long-obsolete `version === 4`, and
   reads `rightPanelCollapsed` from a legacy field (`panelCollapsed`) never
   written by current (`version: 7`) saves. `viewer_state.js:15-16,72-75`

## 19. Inspector panel

- Legacy vs. structured entity detail rendering, dispatched by presence of
  `entity.details`. Legacy format reads `entity.ref`, `active_in`, `source`,
  `defined`, and `connects_to[].{to,label,edge_label,type,confidence,
  evidence,metadata}`; structured format supports arbitrary sections/fields
  with `reference`/`reference-list`/`list`/`code`/`path`/default formats,
  optional copy-to-clipboard per field (with an `execCommand` fallback for
  contexts where the async clipboard API isn't available). `inspector.js:3-145`
- Outgoing/incoming relationship sections filtered by currently-hidden
  edge-type categories. `inspector.js:76-97`
- Bundled-edge detail view: lists visible constituents (filtered by hidden
  category + active search), with a visible-count header. `inspector.js:212-233`
- "Raw JSON" panel shows the *currently selected* entity's or edge's own
  JSON; falls back to full `docData` only when nothing is selected.
  `inspector.js:144,160`, `selection.js:36-42`
- Cross-reference buttons navigate to and select the referenced entity.
  `inspector.js:121-128`

## 20. Search / filter

- Free-text search across a normalized multi-field haystack for both nodes
  and edges (matches highlight, don't hide). `filtering.js:33-66,440-455`
- `/` and Ctrl/Cmd+K focus search (suppressed while typing or while the
  quick guide owns focus). `filtering.js:477-484`
- Placeholder text overridable via `docData.ui.filtering.search_placeholder`.
  `filtering.js:273`
- Hierarchical category and edge-category facet filtering with ancestor-
  exclusion semantics and indeterminate checkbox state. `filtering.js:298-382`
- "Retained ownership containers": an ancestor container of a filtered-out
  node stays visible for structural context, distinctly styled (not treated
  as a real match). `filtering.js:97-121,204-217`
- The "Clear" button next to search intentionally calls `deselect()` (Esc's
  behavior) rather than clearing the search query or filter exclusions — a
  deliberate design decision, not a bug despite the `id="filter-clear"`
  naming; its tooltip reads "Deselect (Esc)". `filtering.js:253,461`
- Selection always overrides filters (a selected node is never treated as
  filtered out). `filtering.js:114-121,214-216`
- Removable filter chips per active exclusion; live filter summary line
  ("Showing N of M nodes...", derived-path/match/retained-container counts).
  `filtering.js:384-438`
- Dead mechanism, not live undo behavior: `filterUndoStack` and
  `filterRedoStack` receive snapshots, including one coalesced snapshot per
  search focus/blur session, but the runtime contains no operation that pops
  and restores either stack. `filtering.js:27-28,166-202`
- Filter-facet changes participate in operable global graph undo through
  `recordGraphHistory`; free-text search edits do not. The replacement removes
  the unused filter stacks without claiming filter-scoped undo parity.
  `filtering.js:175-202`, `graph_actions.js:218-249`

Dead code, not live behavior: "retained endpoints" (`retainedEndpointIds`,
`filtering.js:29-30,210-217`) is declared and cleared but never populated
anywhere in the codebase.

## 21. Detail-level control

- "Visible detail" dropdown, populated from `detailLevels`, hidden entirely
  if none configured. `filtering.js:85-91,255-281,456-460`
- Changing detail level drops now-hidden selected nodes and forces a full
  relayout (not the fast path). `filtering.js:191-197`
- Unranked/unknown detail levels never hide a node (rank-map guard).
  `filtering.js:85-91`

## 22. Presentation-node grouping / compartments

- "View by X" facet controls, two activation modes: `single` (exclusive,
  but still exposes a per-node-id checkbox list to hide/show individual
  values within the active facet) and `multiple` (checkbox subset via
  `<details>`/summary with a selected count). `presentation_nodes.js:523-614`
- Ungoverned presentation nodes (not referenced by any control) auto-show if
  `presentation.default_visibility === "visible"`. `presentation_nodes.js:56-67`
- Signature-based compartment computation: overlapping membership across
  multiple active presentation nodes produces nested/inset shells, not
  exclusive groups — a node can visibly belong to more than one facet value
  at once. Compartment grid layout reads `docData.ui.layout.aspect_ratio`
  with a fallback default of **1.6** — distinct from ELK's own 1.7 default
  for the same key (see §1). `presentation_nodes.js:144-241,160`
- Hard-fail invariant: presentation roots aren't supposed to overlap;
  violation throws rather than silently misrendering. `presentation_nodes.js:132-140`
- Two independent drag modes: `"members"` (persistent offset applied to all
  member subtrees on every layout, composable across overlapping
  presentation nodes) vs. `"self"` (offsets only the visual shell, cheap
  re-render, no relayout). `presentation_nodes.js:243-251,357-403`
- Selection (mutually exclusive with regular node selection), hide toggle
  (double-click or Delete/Backspace), collapse toggle (Alt+double-click, only
  for `collapse_effect: "self"`), full keyboard access. Per-node
  `interaction.selectable`/`interaction.inspectable` flags (default true)
  gate selection/inspection entirely. `presentation_nodes.js:267-356,269,275`
- Independent "committed state" snapshot/rollback, separate from the general
  undo/redo stack. `presentation_nodes.js:445-452`
- One-way migration path from a legacy v6 "dimension/value" schema to the
  current facet/node-id scheme on restore, with defensive filtering against
  stale/unknown ids. `presentation_nodes.js:465-506`
- Layout-affecting mutations trigger async full relayout (history/save only
  on success); non-layout mutations render synchronously. `presentation_nodes.js:562-581`

## 23. Hide / show / dim semantics

- Composite hidden rule: user-hidden (self or ancestor), filter-hidden (and
  not retained), legacy-hidden-category (and not retained), detail-hidden, or
  inside a collapsed container. `visibility.js:3-29`
- Omission-cause diagnostic (`user-hidden`/`filter-hidden`/`detail-hidden`/
  `null`) checked in that priority order; collapsed-container hiding is
  deliberately excluded from this cause. `visibility.js:3-29`
- Hidden-nodes list panel: double-click restores, single click shows
  details, sorted by position then title. `visibility.js:33-68`
- Toolbar button state sync: disabled when nothing applicable selected;
  dim button label toggles Undim when *all* targets already dimmed.
  `visibility.js:70-91`
- Presentation pass: dim = opacity 0.2 (independent of hide); hide = display
  none (always wins over dim); edges hidden if type-suppressed or either
  endpoint hidden, else opacity 0.96. `visibility.js:93-123`

## 24. Bulk actions & undo/redo

- Unified graph-state snapshot (hidden/dimmed/selection/collapsed/detail
  level/filter exclusions/presentation-node state) as the undo/redo unit.
  `graph_actions.js:12-27`
- No-op detection: if state is unchanged after an action, skip render/save/
  history entirely. `graph_actions.js:105-118`
- Hide cascades dimmed-state cleanup to descendants (without adding them to
  the explicit hidden set — they're hidden implicitly via ancestor check).
  `graph_actions.js:120-128`
- Dim/undim only ever target currently-visible nodes; toggle-dim only undims
  if *every* target is already dimmed. `graph_actions.js:137-157`
- Container collapse/expand always forces a full relayout. `graph_actions.js:159-166`
- Post-mutation invariant: any newly-hidden node loses dimmed state and drops
  out of selection (re-picking a remaining selected id if the primary was
  removed). `graph_actions.js:58-73`
- Undo/redo target picks render cost dynamically: full relayout only if
  detail level, presentation-node layout key, or collapsed-container set
  actually changed; otherwise a cheap visibility/presentation-only pass.
  `graph_actions.js:168-216`

## 25. Quick guide / onboarding

- Known documentation correction: the default Search step says search leaves
  selection unchanged, but current browser behavior creates and replaces a
  search-sourced selection and clears it with the query. Preserve the runtime
  behavior and correct the guide text during the refactor.
  `quick_guides/default.py:40-45`, `selection.js:128-151`

- Step-anchored tour, each step gated on its CSS target actually being
  visible; auto-skips unusable steps in either direction, auto-closes if none
  remain usable. `quick_guide.js:36-94`
- Live target tracking via `MutationObserver` — repositions or advances if
  the page changes under the guide mid-tour. `quick_guide.js:19-26,170-176`
- Viewport-clamped dialog placement (prefers below target, falls back
  above). `quick_guide.js:113-150`
- Step counter counts only usable steps; Back/Next/Finish visibility and
  focus management. `quick_guide.js:84-109`
- Toolbar entry point's visibility is computed **once at page load** (not
  live) from whether any step's target currently resolves — later actions
  that remove every guide target won't retroactively hide it; only the open
  guide's own steps react live. `quick_guide.js:207-219`
- Focus-return to the pre-guide focused element on close; Escape closes
  (capture-phase priority over other Escape handlers). `quick_guide.js:156,179-190,221-226`
- "Owns focus" gate consumed by filtering.js and graph_actions.js to suppress
  `/`, Ctrl+K, and undo/redo shortcuts while the guide is open. `quick_guide.js:32-34`
- Reposition on resize/scroll (capture phase, any scrolling container).
  `quick_guide.js:228-234`

## 26. Persisted viewer state

- Versioned localStorage payload (`version: 7`), keyed per-document; covers
  hidden/dimmed/collapsed sets, selection, sidebar state, manual positions,
  routing config, full filter state, full presentation-node state, pan/zoom.
  `viewer_state.js:3-27`
- Backward-compatible restore across versions 3-7 with per-field fallbacks;
  any other version or malformed payload triggers a full wipe+discard rather
  than a partial/corrupt apply. `viewer_state.js:34-89`
- Every restored id collection is filtered against current live catalogs
  (types, edge types, entities, containers) before acceptance — schema/
  document drift silently drops stale ids instead of erroring.
  `viewer_state.js:39-57,78-80`
- Restored selection drops hidden members; falls back to the last valid
  selected id if the saved primary is gone. `viewer_state.js:57-60`
- Pan/zoom restore is guarded by a type check and marks `hasFittedOnce` so
  the initial auto-fit is skipped when state was restored. `viewer_state.js:81-85`
- Sidebar section order persisted under a separate `::sidebar` key with its
  own independent corrupt-data wipe path. `viewer_state.js:91-113`
- See §18 for a sidebar-collapse persistence bug in this same mechanism.

## 27. Math typesetting

- Delimiter-based math detection (`$$`, `\[\]`, `\(\)`, `$`) as a cheap
  early-out before ever invoking MathJax. `math_typesetter.js:11-27`
- Per-element generation counter guards against a stale async typeset
  applying after the element's content changed again or it was disconnected.
  `math_typesetter.js:5-9,37-47`
- All typeset calls serialize through one shared queue promise (not
  concurrent), each link independently fault-tolerant. `math_typesetter.js:2,37-47`
- Graceful no-op (renders literal delimiter text) when MathJax isn't loaded.
  `math_typesetter.js:32,38`
- Diagnostics hook (`window.officinaMathDiagnostics`) drains the queue and
  reports unresolved commands / error node counts — likely test/tooling
  support, not end-user-visible. `math_typesetter.js:49-62`

## 28. Misc / cross-cutting

- Build-refresh polling: every 1.5s (http/https only) checks for a changed
  `GRAPH_BUILD_ID` and does a cache-busted client-side reload; network
  failures during the poll are silently swallowed by design. `layout.js:32-53`
- Category/type style resolution with palette-index fallback for categories
  without an explicit style; kind-component color blending for composite
  `"a+b"` kind strings. `bootstrap.js:16-124`
- Node dimension measurement (label/subtitle text) via a hidden DOM host,
  memoized by content+class key, with distinct min/max sizing for containers
  vs. leaves (including a "compact container" case). `bootstrap.js:232-291`

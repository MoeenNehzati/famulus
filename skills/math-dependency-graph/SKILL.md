---
name: math-dependency-graph
description: |
  Extract a direct mathematical dependency graph from a LaTeX math document, including standing assumptions, definitions, lemmas, propositions, theorems, corollaries, notation blocks, and dependency edges with evidence.

  Use when the user wants document-internal assumptions-to-results structure, a canonical JSON artifact, or an interactive HTML graph of direct dependencies.

  Do not use when the main goal is proof validation, notation cleanup, prose review, or a literature map.

  Success criteria:
  - produce JSON as the source of truth for entities and direct dependencies
  - represent only direct dependencies, not inherited transitive closure
  - identify ambient assumptions from notation or scope phrases
  - attach short descriptions and evidence to dependency links
  - render a standalone HTML graph from the JSON
---

When this skill is used, begin with:

Skill: math-dependency-graph

Category: document-oriented

Dependencies: none
Category: mathematical-analysis

## 1. Goal

Build a direct-dependency graph for a mathematical document.

The canonical artifact is JSON.
The HTML graph is a visualization of that JSON and must not invent structure.
The model is responsible for building the JSON.
Python is responsible for rendering and view-layer interaction only.

The JSON must be presentation-ready.
Do not rely on the renderer to repair malformed math, infer math mode, or normalize prose.

## 2. Dependency semantics

Record only direct dependencies.

If entity `X` depends on entity `Y`, and `Y` depends on standing assumption `A`,
do not also connect `A` directly to `X` unless `X` independently uses `A`
in its own statement, proof, or ambient notation.

Each dependency edge must include:
- the prerequisite id
- a short description of how it is used
- a use type
- a confidence level
- a short evidence string

All string fields intended for display must already be valid MathJax-compatible text.
If a symbol such as `\in`, `\Rightarrow`, `\Omega`, or `\Pi_i` should render as math, put it in math mode in the JSON.

## 3. What counts as an entity

Prefer these entity types:
- `standing-assumption`
- `local-assumption`
- `definition`
- `notation`
- `lemma`
- `proposition`
- `theorem`
- `corollary`
- `remark`

Standing assumptions may come from:
- explicit assumption environments
- ambient scope phrases such as `Throughout`, `Fix ... throughout`, `In this section we assume`
- notation that imposes mathematical conditions in a reusable way

## 4. Output

Start with:

- `Mode: Explore`
- `Skill: math-dependency-graph`

Then report briefly:
- where the JSON was written
- where the HTML was written
- main extraction gaps or ambiguous edges

## 5. Workflow

1. Read the document and identify theorem-like environments and ambient assumption paragraphs.
2. Construct the canonical JSON directly by understanding the mathematical structure.
3. Add only direct dependencies with descriptions and evidence.
4. Write or propose that JSON first.
5. If the document uses local TeX macros, give the active TeX entrypoint to the
   macro extractor:
   ```
   python scripts/extract_mathjax_macros.py <entrypoint.tex>
   ```
   This writes `_build/<entrypoint>-mathjax-macros.json` by default. The
   extractor recursively follows `\input`/`\include`, scans macro definitions
   throughout the reachable TeX source, and writes the transitive closure needed
   by MathJax.
6. Once the JSON is written, invoke the renderer:
   ```
   python scripts/build_math_dependency_graph.py <source.json> --tex-entry <entrypoint.tex>
   ```
   Output defaults to `_build/<name>.html` next to the JSON. Use `--html-out <path>` to override.
7. For repeated browser inspection after rerenders, run the no-cache local
   server from the document workspace:
   ```
   python scripts/serve_graph.py --directory <document-workspace> --port 8765
   ```
   The server binds to `127.0.0.1` by default and keeps running until stopped.
   Open `http://127.0.0.1:8765/<graph>.html`.
8. Flag uncertain or heuristic edges explicitly.

## 6. Canonical fields

Use `type`, not `kind`.

For entities, use these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier for the entity. |
| `type` | string | Entity type: `definition`, `lemma`, `theorem`, etc. |
| `ref` | string | Document-assigned number as it appears in the paper, e.g. `4.2` or `A.3`. Empty if unnumbered. |
| `short_title` | string | Short identifier for display in the cell, e.g. `Barbalat`. No type prefix. |
| `title` | string | Full descriptive name shown in the side panel. |
| `description` | string | One-sentence mathematical summary. MathJax-compatible. |
| `defined` | string | Where the entity is introduced, e.g. `Section 4, Assumption 4.1`. |
| `active_in` | string | Region of the document where the entity is in force, e.g. `Section 4–5`. |
| `source` | string | `explicit` if stated in the paper, `inferred` if introduced by the model. |
| `depends_on` | array | Direct dependency edges. |
| `position` | int | Ordering position within the document. Layout-only, not displayed. |

For dependencies, use these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Id of the prerequisite entity. |
| `use_type` | string | How the prerequisite is used: `ambient`, `applies`, `uses`, `invokes`, etc. |
| `description` | string | Short description of how the prerequisite is used in this context. |
| `confidence` | string | `Verified`, `Likely`, or `Speculative`. |
| `evidence` | string | Short quote or pointer from the proof or statement. |

Prefer the model to justify difficult edges from mathematical content, not from string matching.
If the document is ambiguous, keep the JSON conservative and mark uncertainty explicitly.

If the document uses local TeX macros, set the active entrypoint under:

- `document.source_entrypoint`

For example:

```json
{
  "document": {
    "source_entrypoint": "or.tex"
  }
}
```

The renderer will use `_build/<entrypoint>-mathjax-macros.json` by default when
`document.source_entrypoint` is present. If the file is missing and the
entrypoint can be resolved, it regenerates the macro file from the TeX source.

If any extracted macro is not MathJax-compatible, override it explicitly under:

- `document.mathjax_macros`

For example:

```json
{
  "document": {
    "mathjax_macros": {
      "R": "\\mathbb{R}",
      "G": "\\mathcal{G}"
    }
  }
}
```

Do not emit bare local macros unless they are declared there.
Do not emit malformed TeX such as ambiguous subscripts or superscripts.
Graph-local `document.mathjax_macros` overrides extracted macros, so use it for
manual corrections.

## 7. Rendering rules

The HTML graph should:
- use different node shapes for different entity types
- color-code entity types
- use a single-line boundary for every unselected node; reserve the double-line
  boundary for the selected node, drawn as two somewhat thick black outlines
  separated by a small gap; the second outline must sit outside the cell and
  must not overlap the node label
- encode dependency confidence through edge style
- use a layered left-to-right layout for distinct implications
- place same-result vertical variants such as attached corollaries directly below their parent when appropriate
- show extra details on hover
- show fuller metadata in a side panel on click
- support hiding entity categories from the legend without breaking visible causality
- support temporarily removing individual nodes from the graph without breaking visible causality
- support ancestor-focused viewing for a selected node
- persist viewer state locally when feasible

The current renderer uses `elkjs` in the browser for layout and routing.
The renderer may optionally apply graph-theoretic transitive reduction to the rendered view for readability, but this does not change the JSON source of truth.

Do not treat inferred notation-imposed assumptions as fully verified facts.
Mark them as inferred or unclear when appropriate.

## 8. Tool split

The model should do:
- entity identification
- standing-assumption identification
- direct dependency judgment
- link descriptions
- confidence assignment
- production of valid display-ready strings
- declaration of any required MathJax macros

The Python renderer should do only:
- load canonical JSON
- validate basic structure
- merge extracted MathJax macros from `_build/<entrypoint>-mathjax-macros.json`
- optionally apply graph-theoretic transitive reduction for readability
- render the interactive HTML graph
- manage visualization-only state such as filters, local hiding, focus modes, and persistence

Do not delegate semantic graph extraction to the renderer script.

## 9. Field-to-HTML rendering map

Use this table to verify that the JSON is correct and to debug rendering issues.
Each row shows a JSON field and exactly where its value appears in the HTML.

### Node cell (the colored shape in the graph)

| JSON field | Rendered as |
|---|---|
| `short_title` | Large bold white text, top line of the cell |
| `type` + `ref` | Small subtitle, bottom line of the cell (e.g. `theorem 5.1`) |
| `type` | Determines node shape and fill color (see shape table below) |

### Hover tooltip (shown while the mouse is over a node)

| JSON field | Rendered as |
|---|---|
| `short_title` | Bold first line |
| `ref` | Second line (empty string if `""`) |
| `type` | Third line |
| `description` | Fourth line |

### Side panel on click (right-hand panel when a node is selected)

| JSON field | Rendered as |
|---|---|
| `short_title` | `<h2>` heading |
| `ref` | **Ref:** row (shows `—` if empty) |
| `type` | **Type:** row in `<code>` |
| `title` | **Title:** row (shows `—` if empty) |
| `active_in` | **Active in:** row (shows `—` if empty) |
| `source` | **Source:** row (shows `—` if empty) |
| `defined` | **Defined:** row (shows `—` if empty) |
| `description` | Paragraph body below the metadata rows |

### Dependency list (in the side panel, under "Direct dependencies")

Each entry in `depends_on` renders as one `<li>`:

| `depends_on` field | Rendered as |
|---|---|
| `id` | Looked up → target's `short_title` shown in bold |
| `use_type` | `<code>` label after the name |
| `description` | Text on the second line |
| `evidence` | Small grey text, with confidence in parentheses |
| `confidence` | Appended as `(confidence: …)` in small grey text |

### Edge visual encoding

| `depends_on.confidence` | Edge style |
|---|---|
| `"Verified"` | Solid line |
| `"Likely"` | Long-dashed (`8 5`) |
| `"Speculative"` | Short-dashed (`3 5`) |
| *(bridge, auto-generated)* | Medium-dashed (`6 4`) |

Edge color is assigned by `position` order, cycling through a fixed 6-color palette.
Lower `position` values get earlier colors.

### Node shapes by `type`

| `type` | Shape |
|---|---|
| `standing-assumption` | Hexagon |
| `local-assumption` | Diamond |
| `definition` | Rounded rectangle |
| `notation` | Parallelogram |
| `lemma` | Ellipse |
| `proposition` | Rectangle |
| `theorem` | Rectangle |
| `corollary` | Circle |
| `remark` | Rectangle |

---

## 10. Developer architecture

This section is for contributors extending or debugging the renderer. It is not part of the model's extraction workflow.

### 10.1 Three-component pipeline

```
LaTeX source
    │
    ▼
[extract_mathjax_macros.py]  ──▶  _build/<entry>-mathjax-macros.json
                                           │
[model: you]  ──▶  canonical JSON          │
                        │                  │
                        ▼                  ▼
               [build_math_dependency_graph.py]
                        │
                        ▼
               _build/<name>.html  (self-contained, no server needed)
```

**Component 1 — the model.** Reads the math document and writes the canonical JSON. All semantic decisions (what is an entity, what depends on what, confidence levels) live here. The renderer does no semantic work.

**Component 2 — macro extractor (`extract_mathjax_macros.py`).** Recursively follows `\input`/`\include`, collects `\newcommand`, `\renewcommand`, `\DeclareMathOperator`, and writes a flat `{ "macroName": "expansion" }` dict to `_build/<entrypoint>-mathjax-macros.json`. The renderer merges this into MathJax's `tex.macros` config so that local TeX macros (e.g. `\vx`, `\PP`) render correctly in the browser. Override any MathJax-incompatible macros via `document.mathjax_macros` in the JSON (see §6 above).

**Component 3 — renderer (`build_math_dependency_graph.py`).** A ~2700-line Python script that generates one self-contained HTML file. All interactivity is inline JS inside that HTML. External dependencies are ELK.js and MathJax, both loaded from CDN.

### 10.2 JSON → HTML interface contract

The renderer consumes the JSON verbatim — no semantic normalization, no field inference. Key invariants a new developer must respect:

- **Every display string must be MathJax-ready.** `description`, `title`, `short_title`, `evidence`, and `dep.description` all pass through `MathJax.typesetPromise()`. Invalid LaTeX will produce visible error markup.
- **No raw LaTeX cross-references in `evidence`.** `\ref{label}` does not resolve in MathJax and renders as literal text. Write resolved references: `"Lemma C.1"`, `"Assumption 5.1"`.
- **No LaTeX tilde `~` outside math mode.** It appears as a literal `~` in the browser. Use a regular space.
- **`position` is layout-only.** It controls ELK node ordering and edge color assignment (cycled across a 6-color palette). It is not displayed.
- **Missing optional fields** (`ref`, `title`, `active_in`, `source`, `defined`) render silently as `—` in the side panel.
- **Hover tooltips are not MathJax-rendered** (they use `escapeHtml` only). The side panel is MathJax-rendered. This means math in `description` renders in the panel but shows raw LaTeX on hover — author accordingly.

### 10.3 Python renderer structure (lines 1–290)

| Function | Lines | Purpose |
|---|---|---|
| `validate_and_normalize` | ~60–80 | Adds missing `position` fields, checks required keys |
| `reduce_transitive_edges` | ~140–165 | Removes edges where an alternate path exists (optional, default off) |
| `resolve_render_type` | ~230–260 | Maps corollaries to their parent's visual type for shape inheritance |
| `prepare_macro_file` | ~165–185 | Runs extractor if needed; returns resolved macro file path |
| `merge_mathjax_macros` | ~112–130 | Merges extracted macros into `doc["document"]["mathjax_macros"]` |

The HTML template begins at line ~290 as a Python f-string. Convention inside: `{{`/`}}` = literal JS brace; `{expr}` = Python expression evaluated at render time. Entity data is embedded via `json.dumps(doc)` into the JS variable `const docData`.

### 10.4 JavaScript subsystems (inside the generated HTML)

The embedded JS has no module system. Subsystems share module-level variables and communicate via direct function calls. Reading the file top-to-bottom follows the initialization order.

**Core data (~lines 940–990)**

```js
docData      // parsed JSON: { document, entities, ... }
entityMap    // Map<id, entity> — primary entity lookup
edgeData     // flat list of { source, target, confidence, use_type, ... }
outgoing     // Map<id, Set<id>> — adjacency for ancestor traversal
incoming     // Map<id, Set<id>> — adjacency for ancestor traversal (reversed)
```

**Routing config (~lines 1000–1050)**

```js
routingConfig = {
  compactnessPreset,   // "compact" | "balanced" | "spacious"
  shapePreset,         // "sharp" | "soft" | "curvy"
  cornerRadius,        // 0–200; fraction t = cornerRadius/400 for soft; pull frac = cornerRadius/200 for curvy
  parallelSpacing,     // lateral offset between parallel edges to same target
  mergeLaneDistance,   // how far out from target incoming edges converge before splitting
  sourceLaneDistance,  // how long outgoing edges from same source travel bundled before fanning out
  nodeSpacing,         // vertical gap between nodes in same ELK layer
  layerSpacing,        // horizontal gap between ELK layers
  edgeNodeSpacing,     // ELK edge-to-node minimum clearance
  extraClearance       // extra padding between edge paths and node borders
}
```

All slider changes update `routingConfig` immediately. Layout-affecting keys (`nodeSpacing`, `layerSpacing`, `edgeNodeSpacing`) call `applyLayoutRoutingChange()` → `updateVisibilityFull()`. Edge-only keys call `applyEdgeRoutingChange()` → `rerouteAllVisibleEdgesFromCurrentPositions()`.

**Layout engine (~lines 2200–2300)**

`updateVisibilityFull()` — async. Runs ELK layout, creates fresh DOM nodes and edge paths, then calls `rerouteAllVisibleEdgesFromCurrentPositions()` to apply manual routing on top of ELK's computed waypoints.

`updateVisibilityFast()` — synchronous. Toggles `display` on existing DOM elements (no ELK), then calls `rerouteAllVisibleEdgesFromCurrentPositions()` to keep routing consistent after visibility changes.

**Edge path generators (~lines 1158–1290)**

Three path functions, each returning an SVG path string:

| Function | When used |
|---|---|
| `simpleEdgePath(src, dst)` | During live node drag (straight line clipped to node boundaries) |
| `softDoglegPath(points, t)` | Fraction-based rounded polyline; `t=0` → sharp right angles, `t=0.5` → maximum rounding. Used by `manualDoglegPath` for sharp and soft modes, and for ELK-computed waypoints. |
| `manualDoglegPath(srcPos, dstPos, routeIndex, routeCount)` | Main dogleg router. Chooses H-primary or V-primary based on `|dx|` vs `|dy|`. Applies parallel offset, source-lane bundling, and curve style (curvy = cubic Bézier with `frac = cornerRadius/200`; soft/sharp = `softDoglegPath` with `t = cornerRadius/400`). |

`rerouteAllVisibleEdgesFromCurrentPositions()` calls `manualDoglegPath` for every visible `.edge-path` DOM element, using positions from `manualPositions` (drag overrides) or `lastNodePositions` (ELK output).

**Bridge edges (~lines 1680–1760)**

When nodes are hidden (via legend filter or explicit removal), `computeVisibleEdges()` generates synthetic bridge edges connecting visible endpoints across hidden intermediaries via `traverse()` (BFS over `outgoing`/`incoming`). Bridge edges render as medium-dashed lines (`stroke-dasharray: 6 4`).

**Ancestor focus (~lines 1490–1590)**

Three modes via `ancestorFocusMode` (0=off, 1=dim, 2=hide). `collectAncestors(nodeId)` walks `incoming` upward, returning a `Set` of ancestor IDs including the focus node itself. `applyAncestorFocus()` sets visibility via inline `style.opacity`/`style.display` — not CSS classes — to avoid specificity conflicts with the normal visibility system which also uses inline styles.

**State persistence (~lines 1054–1130)**

`saveViewerState()` / `restoreViewerState()` serialize `routingConfig`, `hiddenNodes`, `hiddenTypes`, `manualPositions`, `selectedNodeId`, `focusNodeId`, `ancestorFocusMode` to `localStorage`. Key is `math-dependency-graph::<source_file or title>`.

**Node drag (~lines 2380–2430)**

Drag stores positions in `manualPositions: Map<id, {x,y,width,height}>`. On mousemove: calls `rerouteIncidentEdgesFromCurrentPositions(nodeId)`. On mouseup: calls `rerouteAllVisibleEdgesFromCurrentPositions()` and saves state. The Redraw button clears `manualPositions` and re-runs ELK.

### 10.5 Adding a new entity type

1. Add entries to `TYPE_STYLES` in Python (~line 55): `{ "fill": "#hex", "shape": "shape-name" }`.
2. Add ELK sizing and CSS polygon/ellipse definition to `TYPE_SHAPE_ATTRS` (~line 85).
3. Add a rendering branch in the JS `renderNodeShape()` function (~line 2050).
4. Update the legend rendering (~line 1419) if the legend is generated from `TYPE_STYLES`.

### 10.6 Routing system quick reference

The routing slider panel (collapsed by default) exposes all `routingConfig` parameters. Presets are layered: a compactness preset sets layout parameters; a shape preset sets `cornerRadius`. Individual sliders override preset values. Double-clicking a slider resets it to the current preset's value. Double-clicking Reset resets all routing to defaults (`balanced` compactness, `soft` shape).

| Preset | Nodes compact | Edge bundled | Use when |
|---|---|---|---|
| compact | yes | tight | dense graphs, many nodes |
| balanced | moderate | moderate | default; most documents |
| spacious | open | loose | presentations, few nodes |

| Shape | Curve style |
|---|---|
| sharp | Right-angle bends via `softDoglegPath(t=0)` |
| soft | Rounded corners via `softDoglegPath(t = cornerRadius/400)` |
| curvy | Smooth S-curves via cubic Bézier (`frac = cornerRadius/200`) |

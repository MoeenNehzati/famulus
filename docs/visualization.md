# Officina visualization module

`officina.common.visualization` is the shared graph payload and rendering layer
for repository visualizations. Domain-specific extractors should produce the
canonical JSON shape in
`src/officina/common/visualization/graph_specification.schema.json`; the common
renderer then handles validation, layout, HTML rendering, serving, and generic
graph operations.

## Architecture

The module is split by responsibility:

| Layer | Files | Responsibility |
|---|---|---|
| Graph payload model | `graph.py`, `graph_specification.schema.json` | Validate graph shape and references, build adjacency indexes, and run graph-level transforms such as same-type transitive reduction. |
| Extractor contract | `base_extractor.py` | Define `BaseJsonExtractor.extract(GraphSource) -> dict` for any domain that can produce graph JSON. |
| Renderer contract | `base_renderer.py` | Normalize payloads, validate against the schema, write HTML artifacts, and call graph transforms. |
| HTML renderer | `elk_html_renderer.py` | Render canonical graph JSON as the interactive ELK-backed browser UI. |
| Orchestration | `base_visualizer.py`, `server.py` | Resolve file/module sources, run extractor -> validator -> renderer, and optionally serve output artifacts. |
| Docstring adapter | `from_docstring/` | Convert validated docstring metadata into graph entities and typed edges. |
| Blueprint adapter | `from_blueprint/` | Project the canonical repository blueprint graph into scoped hierarchical entities and typed edges. |

## Payload contract

Payloads use `entities` as nodes. Each entity may carry outgoing `connects_to`
edges. Every edge must have:

- `to`: target entity id
- `type`: semantic edge type, such as `call`, `instantiation`, `wraps`, or
  `dispatch`

Renderers must treat `type` as the generic edge classifier. Domain adapters may
add metadata such as `description`, `label`, `source`, `implicit`, and
`metadata`, but they should not require renderer-specific fields.

## Generic renderer rules

`BaseRenderer` owns these behaviors:

- Normalize edge payloads without discarding parallel relationships and reject deprecated `depends_on`.
- Validate payloads, category catalogs, containment, edge types, inspector references, and initial UI references.
- Apply same-type transitive reduction through `Graph`.
- Write rendered artifacts.

All public render paths validate by default, including
`build_html_with_elk(...)`. The unvalidated document assembler is private to the
validated renderer pipeline.

`ElkHtmlRenderer` owns only the presentation-specific HTML/JavaScript viewer:

- ELK layout and edge routing.
- Node and edge legends.
- Type-specific edge colors and dash styles.
- Viewer interactions such as hiding edge types, moving nodes, rerouting edges,
  and serving a standalone HTML document.
- Script-safe serialization at every inline-data boundary.
- Offline core layout through bundled, worker-backed ELK assets.
- Offline MathJax rendering when a payload requests the pinned MathJax capability.
- Stable, versioned viewer state keyed by graph identity rather than build time.
- A generic resizable two-sidebar shell: selection details on the left and
  graph-wide controls on the right, with independent collapse state and
  narrow-screen drawers.
- Lossless presentation-time bundling of parallel directed relationships. One
  physical link lists all constituent annotations, while type filters remove
  only matching constituents and preserve layout.
- Internal routing for relationships between a container and its descendants,
  avoiding paths that leave a supernode and turn back into it.

Node and edge category parents are operational hierarchies. Excluding a parent
through either facets or the legend excludes its descendants, and retained
selection or ownership context follows one precedence rule across both controls.

Payloads may also declare ordered `detail_levels`, from coarsest to finest, and
assign each entity a `detail_level`. Selecting a level cumulatively includes that
level and every coarser level. Unlike ordinary filters, a detail-level change
performs a full layout so visible containers can resize, hidden descendants can
roll their relationships up to visible owners, and containers whose children are
hidden can use larger centered labels. The selected level is part of persisted
viewer state and filter history.

Node interaction and Find use one generic selection set. Ctrl/Cmd-click builds
an explicit multi-selection, while Find selects matching nodes and the endpoints
of matching relations. The renderer applies Hide or persistent, layout-preserving
Dim actions to the entire set and retains one primary node for inspection and
ancestor focus.

Domain code should import `build_html_with_elk` from
`officina.common.visualization.elk_html_renderer` or use `ElkHtmlRenderer`.
`base_renderer.py` intentionally does not export ELK-specific functions.

## Docstring graph adapter

`officina.common.visualization.from_docstring` is intentionally shallow:

- It runs docstring validation before extraction.
- It converts `CallsFromRepo`, `InstantiationsFromRepo`, `Wraps`, and
  `Dispatches` into typed graph edges.
- It creates placeholder nodes for repo dependencies outside the rendered module
  as `repo-call-target` or `repo-product-target`, not as external-library
  dependencies.
- It keeps docstring-specific category styling in the adapter payload, not in
  the generic renderer.

Use `DocstringVisualizer` or `build_docstring_graph(...)` for normal module or
directory rendering.

## Extension rule

For a new visualization domain, add a small extractor package that implements
`BaseJsonExtractor`, then reuse `BaseVisualizer` plus `ElkHtmlRenderer`.
Do not copy the HTML renderer, graph server, source resolver, or transitive
reduction logic into the domain package.

## Blueprint graph adapter

`officina.common.visualization.from_blueprint` calls
`load_repository_blueprint_graph(...)` and visualizes its canonical logical
nodes. It never traverses or parses blueprint files independently.

Use whole-repository scope for an architectural overview, or pass logical
skill/module ids to select a smaller scope:

```bash
python -m officina.common.visualization.from_blueprint . \
  --skills skill-certifier \
  --output-dir graphs/blueprint \
  --name skill-certifier
```

The default `graphs/` output tree is local generated data and is ignored by
Git. Graphs intentionally embedded in documentation belong under
`docs/graphs/`; their structured sources and rendering tools belong under
`docs_tooling/graphs/`.

Containment is represented by each child's `container` field. Modules,
behavioral sources, and interfaces therefore render inside their logical owner
rather than through containment arrows. Double-click a container to collapse or
expand it. Relationships hidden by collapse roll up to visibly distinct
aggregate edges whose hover details retain the represented relationships.

Blueprint nodes also expose an open-ended `kind` derived from their canonical
`gateway.language`. The shared renderer assigns shape by structural `type` and
color by semantic `kind`, so Python and Markdown modules, sources, and
interfaces remain visually distinct without hardcoded language enums. Exported
and private interfaces inherit the language of their implementing source.

Selected scopes retain immediate incoming and outgoing relationships as
explicit boundary summaries grouped by the outside top-level module. Scope
never expands implicitly. Modules initially render expanded with all contained
nodes visible; users may collapse containers to declutter the graph.
Certification relationships are available but hidden by default. When an
explicit interface use is also a direct certification dependency, the adapter
annotates that interface edge instead of emitting a redundant indirect edge.

The blueprint adapter declares three cumulative detail levels: `module` shows
modules and out-of-scope boundaries, `source` adds behavioral sources, and
`interface` adds exported and private interfaces. This assignment is a blueprint
projection decision; the renderer only implements the generic ordered-level
contract.

Hiding an ordinary node does not imply that arbitrary relationship types compose.
An edge category must set `bridge_hidden_nodes: true` before the renderer derives
a same-type path across hidden intermediate nodes.


## HTML renderer architecture

`ElkHtmlRenderer` is the stable public entry point for interactive HTML output.
It accepts the canonical graph payload and emits one standalone document. Its
private `html_renderer` package separates the page shell, stylesheet, browser
runtime, and asset assembly so each can be understood without reading a mixed
Python/HTML/CSS/JavaScript file.

The renderer remains domain-neutral. Adapters choose node `type` and `kind`,
edge `type`, containment, decorations, and metadata; the renderer maps those generic fields
to filtering, palettes, shapes, layout, and details. The complete maintenance
contract and extension guide is in
`src/officina/common/visualization/html_renderer/README.md`.

Node `presentation` is also part of the generic contract. Its `form` distinguishes
ordinary nodes from containment frames, while `tone` controls relative visual
intensity. The legend renders the same form, tone, kind color, and category label
as the canvas, so container semantics remain visible outside the graph itself.

Blueprint entities provide structured `details` sections selected by the
blueprint adapter. These explain authoritative behavior, logical and physical
repository position, gateway binding, and interface exposure. The HTML renderer
only renders generic fields, copy controls, references, and graph-derived incoming
and outgoing relationships; adapters without structured details remain unchanged.

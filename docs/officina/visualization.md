# Visualizing Officina graphs

A visualization is a view of Officina's declared structure, not a second
authority for that structure. Blueprint views come from the canonical blueprint
graph; docstring views come from validated docstring metadata. If a view is
wrong, correct the owning blueprint, docstring, or adapter rather than editing a
rendered artifact.

`officina.visualization` separates domain meaning from presentation.
Domain-specific adapters select and translate authoritative data into the
canonical graph payload. The generic renderer validates that payload and owns
layout, HTML rendering, serving, and domain-neutral graph interactions. This
keeps one renderer reusable without asking it to infer blueprint or docstring
semantics.

Choose the route that matches the work:

1. **Use an existing visualization.** Use the [blueprint
   adapter](#blueprint-graph-adapter) for repository architecture or the
   [docstring adapter](#docstring-graph-adapter) for Python dependency graphs.
2. **Add a visualization domain.** Implement the small adapter described by the
   [extension rule](#extension-rule), then reuse the shared payload and renderer.
3. **Maintain renderer internals.** Start with the [architecture](#architecture),
   then use the [renderer maintenance
   guide](https://github.com/MoeenNehzati/famulus/blob/master/src/officina/visualization/html_renderer/README.md).

The canonical payload is defined by [`graph_specification.schema.json`](../../src/officina/visualization/graph_specification.schema.json).
For context, see the [Overview](README.md), [Getting
Started](getting-started.md), [Blueprints](blueprints.md), [Schemas](schema.md),
[Docstring Contract](docstring.md), and [Utility Map](utility-map.md).

## Architecture

The module is split by responsibility:

| Layer | Files | Responsibility |
|---|---|---|
| Graph payload model | `graph.py`, `graph_specification.schema.json` | Validate graph shape and references, build adjacency indexes, and run graph-level transforms such as same-type transitive reduction. |
| Extractor contract | `base_extractor.py` | Define `BaseJsonExtractor.extract(GraphSource) -> dict` for any domain that can produce graph JSON. |
| Payload contract | `payload.py`, `graph.py` | Normalize and validate canonical payloads, then provide domain-neutral graph algorithms. |
| Renderer contract | `base_renderer.py` | Render prepared canonical payloads through a presentation-specific implementation. |
| Artifact output | `artifacts.py` | Write canonical JSON and renderer output without knowing adapter semantics. |
| HTML renderer | `elk_html_renderer.py` | Render canonical graph JSON as the interactive ELK-backed browser UI. |
| Orchestration | `base_visualizer.py`, `server.py` | Resolve file/module sources, run extractor -> validator -> renderer, and optionally serve output artifacts. |
| Docstring adapter | `from_docstring/` | Convert validated docstring metadata into graph entities and typed edges; delegate canonical preparation and artifacts to the parent module. |
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
- Metadata-driven edge presentation that remains separate from relation meaning:
  hidden-detail summaries retain semantic paint inside a subtle halo, multiple
  same-type edges retain semantic paint with extra width, and mixed types use a
  constituent-color gradient with a neutral outline.

Edge `type` is semantic: it says what the relationship means and selects the
relation legend color/dash. Renderer metadata such as `aggregate` and `bundle`
describes why one visible path represents hidden or multiple edges. The optional
`ui.edge_metadata_styles` object can tune only the bounded visual properties for
those generic states; it cannot add predicates or redefine relationship meaning.
Payloads may also declare `ui.edge_presentation.facets` to match a canonical
scalar edge field or one-level `metadata.<key>` by equality. A matched variant
may set only `line_pattern` (`solid`, `dashed`, or `dotted`), positive
`stroke_width`, and `opacity` from zero to one. Facet and variant ids must be
unique, and two facets cannot write the same style property.

The runtime applies semantic type style first, payload-declared facet style
second, and renderer-computed aggregate/bundle presentation last. A declared
presentation signature participates in projection and bundling identity, so
otherwise parallel edges with different matches remain distinct. Semantic
types stay in the relationship legend; present declared variants and computed
states are explained separately in the Edge presentation legend.

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

Payloads may declare first-class `presentation_nodes` plus generic
`ui.presentation_node_controls`. Each presentation node references canonical
root entities through many-to-many `member_ids` and declares its visual form,
tone, default visibility, and bounded interaction capabilities in JSON. A
control supplies ordered facets; an `all` facet enables every referenced node,
while a `multiple` facet lets the user select several nodes.

Presentation nodes use the existing supernode shell style but remain outside
canonical graph semantics. Activating a facet moves each member root and its
containment subtree as one rigid block. Overlapping memberships use signature
compartments, so a logical presentation node may render as several components
without cloning a canonical entity or falsely enclosing a nonmember.

Presentation nodes can be selected, inspected, dragged, hidden, restored, and
self-collapsed according to their JSON capabilities. Hiding or collapsing one
never hides its members, descendants, or edges. Their state is persisted under
viewer-state version 7; version-6 metadata-grouping state is migrated when its
stable facet and value IDs still resolve. Reset restores JSON defaults without
changing canonical nodes, edges, or containment.

Node interaction and Find use one generic selection set. Ctrl/Cmd-click builds
an explicit multi-selection, while Find selects matching nodes and the endpoints
of matching relations. The renderer applies Hide or persistent, layout-preserving
Dim actions to the entire set and retains one primary node for inspection and
ancestor focus.

Domain code should import `build_html_with_elk` from
`officina.visualization.elk_html_renderer` or use `ElkHtmlRenderer`.
`base_renderer.py` intentionally does not export ELK-specific functions.

## Docstring graph adapter

`officina.visualization.from_docstring` is intentionally shallow:

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

`officina.visualization.from_blueprint` calls
`load_repository_blueprint_graph(...)` and visualizes its canonical logical
nodes. It never traverses or parses blueprint files independently.

The adapter is decomposed by policy: `scope.py` selects repository entities,
`catalog.py` declares blueprint categories, detail levels, and omission
composition semantics, `presentation_nodes.py` projects configured discovery
metadata into generic in-scope presentation-node instances, and the payload
builder maps canonical blueprint records.
The extraction facade and visualizer only coordinate these components and the
parent module's generic payload, renderer, and artifact services.

Use whole-repository scope for an architectural overview, or pass logical
skill/module ids to select a smaller scope:

```bash
python -m officina.visualization.from_blueprint . \
  --skills node-certify \
  --output-dir graphs/blueprint \
  --name node-certify
```

The default `graphs/` output tree is local generated data and is ignored by
Git. The documentation site generates its interactive repository blueprint
under `_build/docs-site/source/graphs/blueprint/`; rendered graph artifacts are
not committed under `docs/`.

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
never expands implicitly. Blueprint graphs initially select module-level detail;
users may reveal sources and interfaces through the detail control. Visible
modules render expanded, and users may collapse containers to declutter the graph.
Certification relationships are available but hidden by default. When an
explicit interface use is also a direct certification dependency, the adapter
annotates that interface edge instead of emitting a redundant indirect edge.

The blueprint adapter declares three cumulative detail levels: `module` shows
modules and out-of-scope boundaries, `source` adds behavioral sources, and
`interface` adds exported and private interfaces. This assignment is a blueprint
projection decision; the renderer only implements the generic ordered-level
contract.

Hiding an ordinary node does not imply that arbitrary relationships compose.
Adapters opt in through
`relation_semantics.transformations.node_omission.rules`. Each finite rule names
an incoming edge type, an outgoing edge type, the derived result type, and the
omission causes for which the rule applies. Blueprint rules currently compose
only nodes explicitly hidden by the user; filtering, detail-level omission, and
container collapse retain their separate presentation semantics.

An edge may set `projection_target` when its visible logical target is implemented
through another canonical node. This lets a declared interface dependency continue
through the exact implementing source if the interface is hidden, without guessing
from siblings or containment. The renderer applies only declared finite transitions,
never feeds derived edges back into projection, suppresses an indirect result when a
corresponding direct edge exists, and records the canonical witness edges and omitted
nodes as provenance on every derived edge.


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
`src/officina/visualization/html_renderer/README.md`.

Node `presentation` is also part of the generic contract. Its `form` distinguishes
ordinary nodes from containment frames, while `tone` controls relative visual
intensity. The legend renders the same form, tone, kind color, and category label
as the canvas, so container semantics remain visible outside the graph itself.

Blueprint entities provide structured `details` sections selected by the
blueprint adapter. These explain authoritative behavior, logical and physical
repository position, gateway binding, and interface exposure. The HTML renderer
only renders generic fields, copy controls, references, and graph-derived incoming
and outgoing relationships; adapters without structured details remain unchanged.

# Generic HTML renderer

This package is the browser implementation behind `ElkHtmlRenderer`. It renders
any normalized visualization graph; it does not know what a skill, docstring,
module, call, or blueprint is.

## Use

Most callers should use the public renderer rather than importing this package:

```python
from officina.common.visualization import ElkHtmlRenderer

ElkHtmlRenderer().render(graph_payload, "graph.html")
```

The result is one standalone HTML file. CSS and JavaScript are maintained as
separate source assets here, then inlined when the document is generated. Core
ELK layout is bundled and runs in a worker, so layout remains offline-capable and
does not block browser interaction while a dense graph is being computed.

## Input contract

The public renderer accepts the canonical graph payload documented by
`graph_specification.schema.json`. Browser behavior relies on these generic
fields:

- `entity.id` is the stable identity used by edges and persisted viewer state.
- `entity.type` selects a generic shape and provides a node-filter category.
- `entity.kind` selects a generic color and may express an open-ended subtype.
- `entity.container` places the entity inside another entity.
- `entity.decorations` adds generic node-local annotations without inventing edges.
- `entity.presentation` exposes whether the entity is a node or container and
  whether its visual tone is subtle or strong; canvas and legend use the same data.
- `entity.detail_level` assigns an entity to one entry in the payload's ordered
  `detail_levels` catalog. Selecting a level includes it and all coarser levels.
  Level ids and meanings are adapter-defined; the renderer treats them as opaque.
- `entity.details` carries extractor-selected summaries and labeled sections for
  the generic inspector. Payloads without it retain the legacy inspector view.
- `entity.connects_to` declares outgoing edges.
- Edge `details` uses the same generic inspector sections as entities, allowing
  extractors to explain relationship meaning and provenance without renderer logic.
- `categories` and `edge_categories` may provide labels and descriptions; the
  generic legend displays those descriptions on hover.
- Category parents define operational filter and legend hierarchies. Parent
  exclusions apply transitively and temporarily disable descendant controls.
- An edge category may set `bridge_hidden_nodes: true` only when same-type paths
  have valid composition semantics. Derived paths are disabled by default.
- `edge.type` selects edge color/dash styling and provides an edge filter.
- Additional entity and edge fields are retained for the details panel.

Adapters are responsible for translating domain concepts into these fields.
The renderer must not branch on adapter-specific names.

## Architecture

- `../elk_html_renderer.py` validates/prepares Python data and exposes the public API.
- `assets.py` loads assets and assembles one self-contained document.
- `page.html` defines document structure and serialized-data insertion points.
- `viewer.css` owns presentation and panel/canvas layout.
- `dependencies.py` resolves graph-declared optional browser dependencies from a
  trusted, pinned, locally bundled registry; payloads cannot inject arbitrary
  script URLs or introduce a network requirement.
- `runtime/` contains ordered JavaScript fragments assembled into one private
  browser closure. The fragments share runtime state but each owns one stage or
  interaction concern.

## Browser runtime map

- `runtime/bootstrap.js` indexes payload data, binds DOM elements, and initializes
  shared viewer state and style catalogs.
- `runtime/sidebar_layout.js` owns the generic two-sidebar layout, responsive
  drawers, independent collapse controls, and pointer/keyboard width adjustment.
- `runtime/viewer_state.js` owns persistence, panel state, pan, and zoom.
- `runtime/core.js` contains renderer-wide value and escaping helpers.
- `runtime/selection.js` owns explicit, additive, and search-derived node
  selection plus the persistent user-dimmed set and multi-selection inspector.
- `runtime/filtering.js` owns search, facets, history, retained context, and
  source-relation visibility summaries.
- `runtime/math_typesetter.js` owns optional MathJax detection, invalidation, and
  serialized dynamic typesetting.
- `runtime/geometry.js` owns SVG geometry and edge rerouting from current positions.
- `runtime/legend.js` builds category controls and legend tooltips.
- `runtime/visibility.js` owns node visibility, hidden-node restoration, and ancestor focus.
- `runtime/inspector.js` formats and binds generic node and edge details.
- `runtime/projection.js` projects collapsed or hidden structure into visible edges.
- `runtime/layout.js` builds hierarchical ELK input and converts layout geometry.
- `runtime/node_renderer.js` paints generic nodes, containers, and decorations.
- `runtime/interactions.js` owns node/edge hover, selection, and edge emphasis.
- `runtime/render_pipeline.js` coordinates full layout renders and fast visibility updates.
- `runtime/controls.js` owns dragging, toolbar controls, routing controls, keyboard
  shortcuts, sidebar ordering, and startup.

`assets.py` defines the runtime order. Fragments are not independent browser
modules and must not load one another; generated HTML still contains one runtime
closure and has no dependency on local source assets.

The browser runtime follows a fixed pipeline:

1. Index entities, containment, and edges.
2. Apply node and edge filters plus collapsed-container state.
3. Roll collapsed descendants up to visible representatives, derive hidden-node
   paths only for edge categories that explicitly permit same-type composition,
   and bundle parallel visible relationships by directed endpoint pair.
4. Recursively size contained graphs and obtain geometry from ELK.
5. Paint container backgrounds, then edges, then ordinary nodes.
6. Apply interaction-only updates without relaying out the graph when possible.

Find and direct interaction share one node-selection model. A normal click
replaces the selection, Ctrl/Cmd-click toggles membership, and a search selects
matching nodes plus both endpoints of matching relations. Hide and Dim are bulk
actions over that selection; user dimming preserves layout and is distinct from
category exclusion. The most recently selected member remains the primary node
for inspection and ancestor focus.

Ordinary filters use the fast interaction path and preserve the current layout.
Changing `detail_level` is intentionally structural: it runs the full layout,
projects relationships from hidden descendants onto their nearest visible owners,
and promotes labels on containers with hidden descendants. Promotion follows
containment depth, so enclosing supernodes remain more prominent than nested
containers; only promoted leaf containers center their labels. Detail selection
participates in filter undo/redo and persisted viewer state.

This order is part of the renderer contract. In particular, edges attached to a
contained node remain visible above its container background but below ordinary
nodes.

## Extension rules

- Add a new domain by producing the canonical payload in a `from_*` adapter.
- Add node or edge categories through payload values; the fallback palettes are
  intentionally open-ended.
- Use an `outline` decoration with `style: offset` when a domain property should
  remain visible at low zoom but is not itself a traversable graph relation.
- Add generic visual behavior here only when every adapter can use it.
- Keep semantic inference, repository traversal, and validation outside this package.
- Preserve standalone output. Do not introduce runtime references to these local assets.
- Preserve edge identity. Parallel source relationships may share endpoints and type
  while carrying distinct annotations. The browser bundles them only as a lossless
  presentation projection; filtering and inspection still operate on constituents.
- Route relationships between containers and their descendants inside the container;
  unrelated endpoints continue to use ordinary graph routing.
- Treat payload text as untrusted at HTML and script boundaries. Use the centralized
  serializers and DOM-safe rendering helpers rather than interpolating raw values.

## Debugging map

Start in the runtime fragment matching the symptom: `projection.js` for incorrect
visibility or rolled-up edges, `layout.js` for geometry, `node_renderer.js` for SVG
appearance, `interactions.js` for hover/selection, and `controls.js` for dragging or
toolbar behavior. Start in `page.html` or `viewer.css` only for document structure
and presentation problems.

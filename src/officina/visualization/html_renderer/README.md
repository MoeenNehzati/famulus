# Generic HTML renderer

This package is the browser implementation behind `ElkHtmlRenderer`. It renders
any normalized visualization graph; it does not know what a skill, docstring,
module, call, or blueprint is.

## Use

Most callers should use the public renderer rather than importing this package:

```python
from officina.visualization import ElkHtmlRenderer

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
- `relation_semantics.transformations.node_omission.rules` declares a finite
  relation transducer. Every outcome is independently asserted by the adapter,
  and fidelity records whether the transformation retained exact information.
- `relation_semantics.subsumptions` is an acyclic endpoint information order.
  At a visible endpoint, stronger eligible results suppress weaker equivalents;
  incomparable results remain bundled. Legend/category parents remain purely
  operational filter hierarchy and never imply semantic substitutability.
- `edge.type` selects edge color/dash styling and provides an edge filter.
- `ui.edge_styles` optionally overrides semantic relation colors and dashes.
- `ui.edge_presentation.facets` optionally maps scalar edge-field values to
  bounded line pattern, width, and opacity presentation without creating new
  semantic relationship types.
- `ui.edge_metadata_styles` optionally labels and tunes the bounded generic
  presentations for hidden-detail summaries, same-type multiplicity, and
  mixed-type edges. It cannot define arbitrary metadata predicates.
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
- `runtime/presentation_nodes.js` parses generic presentation-node instances and
  controls, lays out overlapping memberships, and owns their independent
  selection, inspection, drag, hide, collapse, persistence, and migration state.
- `runtime/math_typesetter.js` owns optional MathJax detection, invalidation, and
  serialized dynamic typesetting.
- `runtime/geometry.js` owns SVG geometry and edge rerouting from current positions.
- `runtime/legend.js` builds category controls and legend tooltips.
- `runtime/visibility.js` owns node visibility, hidden-node restoration, and ancestor focus.
- `runtime/inspector.js` formats and binds generic node and edge details.
- `runtime/projection.js` projects collapsed or hidden structure into visible edges.
- `runtime/edge_presentation.js` resolves metadata presentation, owns edge-local
  gradients and filters, synchronizes them after rerouting, restores base styles
  after interaction, and constructs matching explanatory legend samples.
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
   paths only through adapter-declared typed composition rules, and bundle parallel
   visible relationships by directed endpoint pair.
4. Recursively size contained graphs and obtain geometry from ELK.
5. Paint container and ordinary node shapes, then masked edges. Each edge remains
   above its source and target shapes, including containment endpoints, but its
   mask occludes it beneath every unrelated ordinary node, attenuates it behind
   unrelated translucent containers, and fully occludes it beneath every measured
   label and subtitle. Text therefore remains visually above graph lines without
   sacrificing endpoint-over-edge semantics or erasing contained relationships.
6. Apply interaction-only updates without relaying out the graph when possible.

### Edge meaning and presentation

An edge has three deliberately separate layers:

1. Its semantic relation type states what the dependency means. The relation
   catalog and `ui.edge_styles` determine the ordinary color and dash.
2. Payload-declared presentation facets explain bounded distinctions such as
   explicit versus inferred provenance.
3. Renderer-computed metadata states explain why one visible path represents
   additional edges or hidden structure.
4. A resolved style turns those inputs into SVG paint without changing the
   underlying relation records.

The generic metadata states are mutually interpretable rather than mutually
exclusive. `aggregate` adds a halo around the semantic foreground,
`same_type_bundle` increases foreground width, and `mixed_type_bundle` replaces
the foreground with a solid constituent-color gradient plus a neutral outline.
When states coincide, width takes the maximum and every applicable outer effect
is retained. Mixed-type presentation alone replaces semantic dash because
different dash patterns cannot truthfully occupy one path.

`edge_presentation.js` creates deterministic graph-local SVG resources. A full
render replaces resources with the same edge identity; transient derived-edge
removal explicitly deletes its resources. User-space gradients are synchronized
after every route change, and hover emphasis stores/restores the resolved base
width and filter. The Edge presentation legend calls the same resolver and icon
builder, so it documents actual paint behavior rather than a parallel convention.

Declared presentation facets use scalar equality only. `field` names a
canonical scalar edge property such as `implicit` or a one-level metadata key
such as `metadata.provenance`. Each matching variant may set
`line_pattern`, `stroke_width`, or `opacity`; arbitrary CSS and expressions are
not accepted. Semantic style is applied first, declared variants second, and
computed aggregate/bundle overlays last. Matched facet identities are included
in projection and bundling keys so visually distinct edges cannot be merged.
Only variants present on visible edges appear under their facet in the Edge
presentation legend.

Example declared facet:

```json
{
  "ui": {
    "edge_presentation": {
      "facets": [{
        "id": "provenance",
        "label": "Provenance",
        "field": "implicit",
        "variants": [
          {
            "id": "explicit",
            "equals": false,
            "label": "Explicit",
            "description": "Asserted by the source.",
            "style": {"line_pattern": "solid"}
          },
          {
            "id": "inferred",
            "equals": true,
            "label": "Inferred",
            "description": "Inferred from the source.",
            "style": {"line_pattern": "dashed"}
          }
        ]
      }]
    }
  }
}
```

Example override:

```json
{
  "ui": {
    "edge_metadata_styles": {
      "aggregate": {
        "label": "Hidden detail summary",
        "style": {"halo_width": 10, "halo_opacity": 0.22}
      },
      "same_type_bundle": {
        "label": "Multiple of same type",
        "style": {"stroke_width": 5}
      },
      "mixed_type_bundle": {
        "label": "Mixed types",
        "style": {"outline_width": 8, "transition_color": "#f8fafc"}
      }
    }
  }
}
```

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

### Node-omission projection contract

The renderer owns the projection algorithm; adapters own its domain semantics.
An adapter may declare finite
`relation_semantics.transformations.node_omission.rules`. Each rule identifies
allowed causes, left and right relation types, and one or more truthful outcomes
with fidelity. No matching rule means no derived dependency. Container collapse
instead rolls endpoints up to visible owners and does not invoke node-omission
composition.

Canonical edges may provide `projection_target` to identify the exact node through
which a hidden logical endpoint continues. The renderer must not infer that target
from a sibling, container, label, or naming convention. Projection traverses only
canonical edges, applies declared transitions finitely, rejects self-links, deduplicates
equivalent witnesses, and never composes a derived edge again. If a matching direct
edge exists, it takes precedence over the indirect result. Derived edges retain their
omitted-node and canonical-edge provenance for inspection and restoration.

## Extension rules

- Add a new domain by producing the canonical payload in a `from_*` adapter.
- Add node or edge categories through payload values; the fallback palettes are
  intentionally open-ended.
- Use an `outline` decoration with `style: offset` when a domain property should
  remain visible at low zoom but is not itself a traversable graph relation.
- Add generic visual behavior here only when every adapter can use it.
- Keep semantic inference, repository traversal, and validation outside this package.
- Declare domain-specific omission compositions and `projection_target` values in the
  adapter payload; do not add domain edge names or inference rules to the renderer.
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
visible-edge meaning, `edge_presentation.js` for metadata states, gradients,
halos, or presentation legend samples, `layout.js` and `geometry.js` for routes,
`node_renderer.js` for node SVG appearance, `interactions.js` for hover/selection,
and `controls.js` for dragging or toolbar behavior. Start in `page.html` or
`viewer.css` only for document structure and CSS layout problems.

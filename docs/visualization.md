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
| Graph payload model | `graph.py`, `base_graph.py`, `graph_specification.schema.json` | Validate graph shape, build adjacency indexes, and run graph-level transforms such as same-type transitive reduction. |
| Extractor contract | `base_extractor.py` | Define `BaseJsonExtractor.extract(GraphSource) -> dict` for any domain that can produce graph JSON. |
| Renderer contract | `base_renderer.py` | Normalize payloads, validate against the schema, write HTML artifacts, and call graph transforms. |
| HTML renderer | `elk_html_renderer.py` | Render canonical graph JSON as the interactive ELK-backed browser UI. |
| Orchestration | `base_visualizer.py`, `server.py` | Resolve file/module sources, run extractor -> validator -> renderer, and optionally serve output artifacts. |
| Docstring adapter | `from_docstring/` | Convert validated docstring metadata into graph entities and typed edges. |

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

- Normalize edge payloads and reject deprecated `depends_on`.
- Validate payloads against the shared JSON schema.
- Apply same-type transitive reduction through `Graph`.
- Write rendered artifacts.

`ElkHtmlRenderer` owns only the presentation-specific HTML/JavaScript viewer:

- ELK layout and edge routing.
- Node and edge legends.
- Type-specific edge colors and dash styles.
- Viewer interactions such as hiding edge types, moving nodes, rerouting edges,
  and serving a standalone HTML document.

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

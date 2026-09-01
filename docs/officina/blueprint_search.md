# Blueprint Search

Blueprint search answers architectural questions without requiring a reader to
open every blueprint: Which behavioral sources support Windows? Which nodes
declare Python gateways? Which interfaces report direct I/O? It searches the
repository's canonical module and behavioral-source blueprints and returns a
structured projection of the matching facts.

Use the [Implementation Map](utility-map.md) when you already know the concern
and need its current code owner. Use blueprint search when the answer depends
on declarations across the repository graph.

## The search model

Every query has three conceptual steps:

1. **Filter** the inventory to records whose declared facts match a predicate.
2. **Select** only the fields needed for the decision.
3. **Read the result** as blueprint data, optionally with an explanation of
   which concrete values satisfied wildcard predicates.

The inventory contains only canonical blueprint locations:

```text
<module-root>/blueprint.yaml
<module-root>/blueprints/*.yaml
```

It does not infer nodes from hidden sidecars or arbitrary YAML files.

## Two cumulative examples

First, find behavioral sources and return only their IDs and gateway
languages:

```yaml
filter:
  path: node_type
  op: eq
  value: behavioral_source
select:
  - id
  - gateway.language
```

Then narrow the same search to sources that declare Windows support, add their
interface descriptions, and ask the result to explain successful predicates:

```yaml
filter:
  all:
    - path: node_type
      op: eq
      value: behavioral_source
    - path: platform_support.windows
      op: eq
      value: true
select:
  - id
  - gateway.language
  - platform_support.windows
  - as: interfaces
    path: interfaces.*.description
explain: true
```

The second query changes one decision at a time: a narrower filter, a richer
projection, and evidence for how the match was obtained.

## Python API

The reusable API is `officina.blueprints.search.search_blueprints`:

```python
from officina.blueprints.search import search_blueprints

rows = search_blueprints(
    "/path/to/repository",
    {
        "filter": {
            "all": [
                {"path": "node_type", "op": "eq", "value": "behavioral_source"},
                {
                    "path": "platform_support.windows",
                    "op": "eq",
                    "value": True,
                },
            ]
        },
        "select": [
            "id",
            "gateway.language",
            "platform_support.windows",
            {"as": "interfaces", "path": "interfaces.*.description"},
        ],
        "explain": True,
    },
)
```

For one exact file, use `load_blueprint_record(path, repo_root=...)` rather
than parsing YAML directly.

## CLI

`scripts/search_blueprints.py` is the JSON-emitting CLI wrapper:

```bash
python3 scripts/search_blueprints.py --query-file /tmp/query.yaml --pretty
```

Without a query file, each row contains `module`, `id`, `node_type`, and the
repository-relative `path`.

## Complete query reference

Top-level keys are:

- `filter`: an optional predicate tree;
- `select`: a projection list, or `"all"` for the parsed blueprint;
- `comments`: `drop` by default, or `raw` to include source text;
- `explain`: include the concrete matches for successful predicates;
- `include_hidden`: include hidden module directories.

Boolean filters use `all`, `any`, and `not`:

```yaml
all:
  - path: node_type
    op: eq
    value: behavioral_source
  - any:
      - path: gateway.language
        op: regex
        pattern: ^Python
      - path: gateway.language
        op: eq
        value: Markdown
```

Predicate operations are `exists`, `missing`, `eq`, `neq`, `contains`,
`regex`, and `not_regex`.

Selectors use dotted paths:

```text
exports.*.source_interface
interfaces.*.process_binding.kind
runtime_dependencies.*.name
**.direct_io
```

`.` descends through mapping keys. `*` expands mapping values or list items.
`**` matches descendants recursively. Numeric segments select list indexes.
Wildcard projections always return a list.

`strip_selected_paths(data, selectors)` returns a deep copy with every selected
path removed. It is for stable structured projections; certification hashes do
not use a second search-specific hashing mechanism.

## Comments

Structured results are parsed with PyYAML and therefore omit comments.
`comments: raw` adds the complete source text under `raw`; it does not attach
comments to individual structured values.

## Related documentation

- [Officina Overview](README.md)
- [Getting Started](getting-started.md)
- [Blueprints](blueprints.md)
- [Implementation Map](utility-map.md)

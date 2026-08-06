# Blueprint Search

`scripts/search_blueprints.py` searches the repository's canonical module and
behavioral-source blueprints. The reusable API is
`officina.blueprint_search.search_blueprints`; the script is a JSON-emitting
CLI wrapper.

Inventory discovers:

```text
<module-root>/blueprint.yaml
<module-root>/blueprints/*.yaml
```

It does not infer nodes from hidden sidecars or arbitrary YAML files.

## Python API

```python
from officina.blueprint_search import search_blueprints

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

For one exact file, use
`load_blueprint_record(path, repo_root=...)` rather than parsing YAML directly.

## CLI

```bash
python3 scripts/search_blueprints.py --query-file /tmp/query.yaml --pretty
```

Without a query file, each row contains `module`, `id`, `node_type`, and
repository-relative `path`.

## Query format

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

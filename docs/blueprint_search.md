# Blueprint Search

`scripts/search_blueprints.py` searches all local skill blueprints:

```text
skills/<skill>/blueprint.yaml
```

The reusable API lives in `src/officina/blueprint_search.py`; the script is a
thin CLI wrapper around that API. Output is always JSON.

## Python API

```python
from officina.blueprint_search import search_blueprints

rows = search_blueprints(
    "/path/to/AI",
    {
        "filter": {
            "all": [
                {"path": "interfaces.machine.*.platform_support.windows", "op": "eq", "value": True},
                {
                    "any": [
                        {"path": "category", "op": "regex", "pattern": "development"},
                        {
                            "path": "interfaces.machine.*.invocation.kind",
                            "op": "regex",
                            "pattern": "python",
                            "flags": "i",
                        },
                    ]
                },
            ]
        },
        "select": [
            "skill",
            "path",
            "category",
            {"as": "windows_support", "path": "interfaces.machine.*.platform_support.windows"},
            {"as": "invocation_kinds", "path": "interfaces.machine.*.invocation.kind"},
        ],
        "explain": True,
    },
)
```

For exact-path callers, use `load_blueprint_record(path, repo_root=...)` rather
than reading YAML directly. It returns the same parsed `BlueprintRecord` shape
used by repository-wide search.

## CLI

```bash
python3 scripts/search_blueprints.py --query-file /tmp/query.yaml --pretty
```

Without a query file, every blueprint is returned with `skill` and `path`.

## Query Format

Top-level keys:

- `filter`: optional filter tree. Missing or empty means every blueprint
  matches.
- `select`: optional projection. Missing means `["skill", "path"]`; `"all"`
  returns the parsed full blueprint.
- `comments`: `drop` by default. `raw` includes the original source text under
  `raw`.
- `explain`: when true, include concrete match evidence for every selected
  value that satisfied each predicate.
- `include_hidden`: when true, include hidden skill directories.

Boolean filter nodes:

```yaml
all:
  - path: interfaces.machine.*.platform_support.windows
    op: eq
    value: true
  - path: category
    op: regex
    pattern: assistant
```

```yaml
any:
  - path: category
    op: regex
    pattern: development
  - path: interfaces.machine.*.description
    op: regex
    pattern: yaml|blueprint
    flags: i
```

```yaml
not:
  path: interfaces.machine.*.platform_support.windows
  op: eq
  value: false
```

Predicate operations:

- `exists`
- `missing`
- `eq`
- `neq`
- `contains`
- `regex`
- `not_regex`

Selector syntax:

- `category`
- `interfaces.machine.*.platform_support.windows`
- `interfaces.machine.*.uses_interfaces.*.version`
- `interfaces.machine.*.invocation.kind`
- `**.direct_io`
- `suggested_permissions.bash.*.command.0`

`.` descends through mapping keys. `*` expands mapping values or list items.
`**` matches descendants recursively at any depth. Numeric path segments select
list indexes. Wildcard projections always return a list, even when exactly one
value matches.

The Python API also exposes `strip_selected_paths(data, selectors)`, which
returns a deep copy with every selected path removed. Callers use this to build
stable YAML projections before comparing or hashing structured data. For
example, `skill-drift` hashes blueprint metadata after stripping
`**.direct_io`.

## Comments

PyYAML parses values and drops comments. `comments: raw` includes the complete
source text, including comments, as `raw`. It does not preserve comments inside
structured fragments.

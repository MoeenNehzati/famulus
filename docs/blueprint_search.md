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
                {"path": "cross_platform", "op": "eq", "value": True},
                {
                    "any": [
                        {"path": "category", "op": "regex", "pattern": "development"},
                        {
                            "path": "interfaces.machine.*.runtime.kind",
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
            "cross_platform",
            {"as": "runtime_kinds", "path": "interfaces.machine.*.runtime.kind"},
        ],
        "explain": True,
    },
)
```

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
  - path: cross_platform
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
  path: cross_platform
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
- `cross_platform`
- `interfaces.machine.*.uses_interfaces.*.version`
- `interfaces.machine.*.runtime.kind`
- `suggested_permissions.bash.*.command.0`

`.` descends through mapping keys. `*` expands mapping values or list items.
Numeric path segments select list indexes. Wildcard projections always return a
list, even when exactly one value matches.

## Comments

PyYAML parses values and drops comments. `comments: raw` includes the complete
source text, including comments, as `raw`. It does not preserve comments inside
structured fragments.

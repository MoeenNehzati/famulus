# Blueprint References

The concrete schemas in this directory are the canonical source for blueprint
shape and field-level authoring rules. See
[`docs/skill-blueprints.md`](../../docs/skill-blueprints.md) for the contributor
architecture overview.

Version-3 authoring schema entry points:

- `schema.json`: compatibility dispatcher for legacy and typed blueprints
- `skill.schema.json`: canonical skill graph root
- `default-llm-interface.schema.json`: inline default-interface contract
- `llm-interface.schema.json`: one file-backed LLM interface
- `machine-interface.schema.json`: one file-backed machine interface
- `behavior-source.schema.json`: one file-backed behavior source
- `health.schema.json`: authenticated generated node and pool health records
- `pooled-review.schema.json`: generated non-authoritative review document
- `schema-meta.json`: field metadata protocol and validator-rule catalog

`template.yaml` is the committed schema-family artifact manifest. It names the
canonical root with its inline default interface, behavior-source sidecar,
machine-interface gateways, and generated-output examples. Authoring templates
are generated from each concrete type schema, whose `x-famulus` metadata
contains all examples and guidance.

## Version-3 Authoring Contract

Every authored node uses `schema_version: 3`, a `node_type`, a `gateway`, and a
non-empty `content` list. Content entries are case-sensitive Python regular
expressions evaluated with `re.fullmatch` against normalized POSIX paths
relative to the node's ownership root. Every pattern must match at least one
regular non-symlink file, and the resolved set must include the gateway file.

Resolved content has exactly one authored owner. Blueprint files, health and
audit records, and pooled-review artifacts cannot be node content. The skill
root owns `SKILL.md` and embeds `default_interface`; version 3 has no
default-interface sidecar.

Behavior-source nodes classify the complete logical artifact with
`semantic_type`. The exact values are `policy`, `instructions`, `reference`,
`configuration`, `preference`, `schema`, `template`, `example`, `checklist`,
and `dataset`. Machine gateways use `python-entrypoint` or `command-file`.

`gateway` replaces the proposed authored term `entry_point`; do not author
`entry_point` in a version-3 node. The normalized derived-record term is
`gateway_path`. Runtime compatibility records may still use `entrypoint`
internally when a version-3 gateway is translated at the legacy invocation
boundary.

For version-3 Python gateways, `uses_interfaces` also defines the same-skill
provider closure admitted to the descriptor-backed runtime snapshot. The
dispatcher opens the selected node's Python content and those provider content
files only; undeclared `_rtx` files are excluded. Version 2 retains its
whole-package snapshot behavior for compatibility.

Version-2 schemas are retained under [`v2/`](v2/) for read-only compatibility.
Their `binding` and `local_hash_inputs` fields describe existing version-2
artifacts; do not use them for new authoring.

`legacy-skill.schema.json` is an exact migration snapshot of the former
monolithic schema. Do not add new features to it.

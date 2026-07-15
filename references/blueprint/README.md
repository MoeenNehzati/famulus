# Blueprint References

The concrete schemas in this directory are the canonical source for blueprint
shape and field-level authoring rules. See
[`docs/skill-blueprints.md`](../../docs/skill-blueprints.md) for the contributor
architecture overview.

Schema entry points:

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
canonical root with its inline default interface, shared-file sidecar, command-interface, and
generated-output examples. Authoring templates are generated from each concrete
type schema, whose `x-famulus` metadata contains all examples and guidance.

`legacy-skill.schema.json` is an exact migration snapshot of the former
monolithic schema. Do not add new features to it.

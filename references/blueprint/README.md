# Blueprint References

The canonical architecture and authoring rules are in [guide.md](guide.md).

Schema entry points:

- `schema.json`: compatibility dispatcher for legacy and typed blueprints
- `skill.schema.json`: canonical skill graph root
- `llm-interface.schema.json`: one file-backed LLM interface
- `machine-interface.schema.json`: one file-backed machine interface
- `behavior-source.schema.json`: one file-backed behavior source
- `health.schema.json`: authenticated generated node and pool health records
- `pooled-review.schema.json`: generated non-authoritative review document
- `schema-meta.json`: field metadata protocol and validator-rule catalog

`template.yaml` is the committed schema-family artifact manifest. It names the
canonical root, default interface, shared-file sidecar, command-interface, and
generated-output examples. Authoring templates are generated from each concrete
type schema, whose `x-famulus` metadata contains all examples and guidance.

`legacy-skill.schema.json` is an exact migration snapshot of the former
monolithic schema. Do not add new features to it.

# Configured JSON Schemas

`officina.common.configured_schema` is the repository boundary for loading
configuration documents and JSON Schemas. Repository configurations stay as
small, value-only YAML or JSON documents; they do not contain JSON Schema
keywords or identify their own type.

## Configuration contract

- `src/officina/common/configuration.schema.json` validates every supported
  repository configuration through a strict natural-key `oneOf`.
- The document's own keys identify its configuration family. There is no
  synthetic `kind` discriminator.
- Unknown fields and documents that combine multiple configuration families
  are rejected.
- `load_configuration(path)` uses the central schema by default. An explicit
  `config_schema_path` is reserved for externally owned configuration formats.
- `validate_configuration(mapping)` applies the same contract before a caller
  writes an in-memory configuration.
- Credentials, generated state, blueprints, standards, and provider-owned
  metadata are not repository configuration and do not belong in the central
  schema.

The central schema currently recognizes blueprint catalog vocabulary,
docstring policy, certification node-hash policy, recurring jobs, and
cloud-files settings. Add a new strict branch when another stable repository
configuration family is introduced.

```python
from officina.common.configured_schema import load_configuration

config = load_configuration("config.yaml")
```

## Schema configuration contract

The same module can tighten a domain JSON Schema from a validated
configuration. A domain schema opts in with `x-officina-config` at the exact
schema location to constrain. Passing `config_path=None` leaves an unannotated
schema unchanged; an annotated schema still requires its declared values.

Composition is monotonic: configuration may intersect an enum or add required
object fields, but cannot widen an enum or remove requirements. Version 1 has
three operations:

| Operation | Configuration source | Effect |
| --- | --- | --- |
| `keys-to-enum` | Nonempty object | Intersect the target enum with its keys. |
| `values-to-enum` | Nonempty array of unique strings | Intersect the target enum with its values. |
| `extend-required` | Nonempty array of unique strings | Add names to an object schema's `required` array. |

```json
{
  "type": "string",
  "x-officina-config": {
    "operation": "values-to-enum",
    "source": "/taxonomy/categories"
  }
}
```

```yaml
taxonomy:
  categories: [research, infrastructure]
```

```python
from officina.common.configured_schema import configured_validator

validator = configured_validator(
    "references/blueprint/module.schema.json",
    config_path="references/blueprint/config.yaml",
)
validator.validate(document)
```

The blueprint configuration supplies catalog and activation enums for the
discoverable-module format. Current skill blueprints use the configured
`discovery.catalog` vocabulary. The configured schema provides no
backward-compatible branch for the retired `category`, `role`, and `kind`
fields.

## Configured schema loading and references

- Consumers whose schema is actually tightened by configuration construct
  validators through `configured_validator` or `ConfiguredSchemaBundle`.
- Ordinary domain schemas use `jsonschema` directly. They should not pass
  `config_path=None` merely to route unrelated validation through this module.
- Every `$ref` resolves inside a filesystem-confined bundle; network retrieval
  is forbidden.
- Relative references are discovered recursively. Absolute `$id` documents
  must be supplied through `referenced_schema_paths`.
- `ConfiguredSchemaBundle` retains composed documents and resolver aliases.
  Exposed mappings are defensive snapshots.
- `load_configured_schema` is appropriate only for standalone schemas that do
  not require the bundle for reference resolution.

The configured-schema implementation currently uses jsonschema's compatibility
`RefResolver` internally. Package metadata must include
`configuration.schema.json` so `importlib.resources` can load the central
configuration contract.

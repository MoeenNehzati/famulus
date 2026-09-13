# Schemas

Officina moves structured data across boundaries that ordinary programming
language types do not cover: between model-interpreted instructions and code,
between repository documents and validators, and between one process and
another. Without a shared contract, each producer and consumer can develop a
different idea of the same payload. A field can disappear, change type, or
acquire a second meaning without any boundary rejecting the change.

Schemas supply that missing shared type system. They make the permitted
structure of a document or payload explicit so tools can reject incompatible
data before later machinery relies on it. They do not replace the architectural
or semantic authorities that explain what the data is supposed to mean.

## The most formal adequate contract

Use the most formal contract that adequately represents a boundary. When the
shape of structured data can be specified mechanically, its owning contract
declares that shape. JSON Schema is appropriate for structured documents and
payloads when producers and consumers need a shared, machine-checkable
representation. Free-form language remains appropriate for the semantic
remainder that cannot be expressed faithfully as structure.

Not every boundary needs a separate JSON Schema. Officina caller contracts
provide inline type specifications for bounded values such as strings, numbers,
enums, paths, files, directories, and lists. Their invocation bindings and
authored patterns also form a machine-checkable command grammar. Use those
narrower contracts when they fully describe the boundary; reference a domain
JSON Schema when a structured document or payload needs a richer shared shape.
The [caller-contract schema](../../references/blueprint-schema/caller-contract.schema.json)
defines both forms.

The [Dispatcher](dispatcher.md) enforces the declared invocation grammar and
supported input types while resolving a call. It does not generally validate a
gateway's output against a declared output schema. A producer, consumer, or
owning adapter must therefore perform output validation where that boundary
requires it.

## Structural validity is not semantic truth

Consider a small weather payload:

```json
{"location": "Boston", "temperature_c": 18.2}
```

Its domain schema might require exactly those two fields:

```json
{
  "type": "object",
  "required": ["location", "temperature_c"],
  "additionalProperties": false,
  "properties": {
    "location": {"type": "string", "minLength": 1},
    "temperature_c": {"type": "number"}
  }
}
```

Validating the payload against that schema establishes that the required fields
exist and have permitted types. It cannot establish that the temperature is
current, that Boston was the requested location, or that the provider reported
the value accurately. Those are semantic claims and need evidence beyond the
schema.

The authority boundaries are therefore deliberate:

- [Architectural Principles](architectural-principles.md) governs the
  architecture and the rule for choosing representations.
- [Blueprints](blueprints.md) own authored architectural facts about nodes,
  dependencies, interfaces, authority, and effects.
- [Standards](standards.md) own semantic policy and its applicability.
- Schemas own the permitted structure of the artifacts they validate.
- [Certification](certification_and_drift.md) supplies retained mechanical and
  semantic evidence that schemas cannot establish.

## Schema roles in Officina

The roles below are a taxonomy, not an inventory of every schema in the
repository. Each role names the boundary made mechanically checkable and routes
to an owning guide or representative family.

### Communication payloads

Domain payload schemas make message fields, nesting, cardinality, and value
types checkable between producers and consumers. Interface declarations can
reference those schemas while retaining inline Officina types for simpler
values. See the [Dispatcher guide](dispatcher.md) for the invocation boundary
and the representative
[caller-contract family](../../references/blueprint-schema/caller-contract.schema.json)
for declared inputs and outputs.

### Blueprint documents

Blueprint schemas make node identity, containment fields, dependency records,
interface declarations, and other authored architectural fields structurally
checkable. Repository validators then add cross-document and graph checks that
JSON Schema alone cannot express. The [Blueprints guide](blueprints.md) owns
their meaning and authoring model.

### Retained state and review artifacts

Schemas for certificates and review records make evidence fields, assessment
records, hashes, versions, and status vocabulary structurally checkable before
assurance is retained. They cannot prove that an assessment was sound or a
blueprint was faithful. The [Certification and Drift guide](certification_and_drift.md)
owns that lifecycle.

### Graph payloads

Graph schemas make canonical entity, relationship, category, and presentation
fields checkable across extractors and renderers. Graph validators add reference
and domain invariants after the payload passes structural validation. The
[Visualization guide](visualization.md) owns the canonical graph payload and
adapter boundary.

### Structured standards

Standards schemas make requirement identifiers, imports, applicability
conditions, evidence links, limitations, and remedies structurally checkable.
The schema permits a policy representation; the standard remains the authority
for the policy's meaning. See [Standards](standards.md) for the authoring and
query model.

### Repository configuration documents

Configuration schemas make accepted configuration families, required keys,
value types, and unknown-field rejection checkable before runtime code consumes
repository settings. The representative
[central schema](../../src/officina/configuration/schema.json) and its APIs are
described in
[Repository configuration documents](#repository-configuration-documents).

### Configuration-specialized schemas

Some domain schemas acquire narrower constraints from a validated repository
configuration. The
[specialization implementation](../../src/officina/configuration/configured_schema.py)
makes the annotation protocol, configuration source, and monotonic composition
mechanically checkable while preventing configuration from weakening the
domain contract. See
[Configuration-derived schemas](#configuration-derived-schemas).

## Repository configuration documents

`officina.configuration` is the repository boundary for loading configuration
documents. Repository configurations are small, value-only YAML or JSON
documents; they do not contain JSON Schema keywords or identify their own type.

The contract is:

- [`src/officina/configuration/schema.json`](../../src/officina/configuration/schema.json)
  validates every supported repository configuration through a strict
  natural-key `oneOf`.
- A document's own keys identify its configuration family. There is no
  synthetic `kind` discriminator.
- Unknown fields and documents that combine multiple configuration families
  are rejected.
- `load_configuration(path)` uses the central schema by default. An explicit
  `config_schema_path` is reserved for externally owned configuration formats.
- `validate_configuration(mapping)` applies the same contract to an in-memory
  configuration.
- Credentials, generated state, blueprints, standards, and provider-owned
  metadata are not repository configuration and do not belong in the central
  schema.

The central schema currently recognizes blueprint catalog vocabulary,
docstring policy, certification node-hash policy, recurring jobs, cloud-files
settings, and the core Officina repository configuration. Add a new strict
branch only when another stable repository configuration family is introduced.

```python
from officina.configuration.configured_schema import load_configuration

config = load_configuration("config.yaml")
```

## Configuration-derived schemas

The same module can tighten a domain JSON Schema from a validated
configuration. A domain schema opts in with `x-officina-config` at the exact
schema location to constrain. Passing `config_path=None` leaves an unannotated
schema unchanged; an annotated schema still requires its declared
configuration values.

Composition is monotonic: configuration may intersect an enum or add required
object fields, but it cannot widen an enum or remove requirements. Version 1
has three operations:

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
from officina.configuration.configured_schema import configured_validator

validator = configured_validator(
    "references/blueprint-schema/module.schema.json",
    config_path="references/blueprint-schema/config.yaml",
)
validator.validate(document)
```

The blueprint configuration supplies catalog and activation enums for the
discoverable-module format. Current skill blueprints use the configured
`discovery.catalog` vocabulary. The configured schema provides no
backward-compatible branch for the retired `category`, `role`, and `kind`
fields.

### Loading and references

- Consumers whose schema is tightened by configuration construct validators
  through `configured_validator` or `ConfiguredSchemaBundle`.
- Ordinary domain schemas use `jsonschema` directly. They should not pass
  `config_path=None` merely to route unrelated validation through this module.
- Every `$ref` resolves inside a filesystem-confined bundle; network retrieval
  is forbidden.
- Relative references are discovered recursively. Documents addressed through
  absolute `$id` values must be supplied through `referenced_schema_paths`.
- `ConfiguredSchemaBundle` retains composed documents and resolver aliases.
  Exposed mappings are defensive snapshots.
- `load_configured_schema` is appropriate only for a standalone schema that
  does not require the bundle for reference resolution.

The implementation currently uses `jsonschema.RefResolver` compatibility
internally. Package metadata must include
`src/officina/configuration/schema.json`; the current setuptools package-data
rule includes the Officina package tree so `importlib.resources` can load that
central contract.

## Related guides

- [Overview](README.md)
- [Getting Started](getting-started.md)
- [Architectural Principles](architectural-principles.md)
- [Utility Map](utility-map.md)

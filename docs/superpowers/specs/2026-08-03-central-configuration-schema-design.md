# Central Configuration Schema Design

## Goal

Use one repository-owned JSON Schema and one Python loading boundary for every
repository configuration while keeping configuration files compact,
value-oriented, and easy to edit.

## Scope

The central schema covers four configuration families identified by their
existing root fields:

- Docstring policy, identified by fields such as `allowed_abs` and `profiles`.
- Node-hash policy, identified by `policy_version` and `rules`.
- Recurring jobs, identified by `jobs`.
- Cloud-files settings, identified by fields such as `remote_llm_root`.

Blueprints, standards, credentials, generated state, certification records,
provider metadata, and tool configuration are not repository configuration
documents and remain outside this schema.

## User-facing syntax

Configuration documents contain only ordinary YAML or JSON values: mappings,
arrays, strings, numbers, booleans, and null where a domain explicitly permits
it. They never contain JSON Schema vocabulary or schema fragments. Adding or
removing allowed taxonomy values remains a YAML-list edit.

## Schema architecture

`src/officina/common/configuration.schema.json` uses a strict top-level `oneOf`.
Each branch is selected through existing natural fields and rejects unrelated
fields with `additionalProperties: false`. This prevents mixed configuration
families without adding a synthetic `kind` field.

The existing `x-officina-config` annotation contract moves into the shared
schema's `definitions.configAnnotation`. `configured_schema.py` validates
annotations against that definition rather than against the document root.

## Loading architecture

`load_configuration()` defaults to the central configuration schema. Callers
may supply another companion schema only for a genuinely external format.
`validate_configuration()` validates an in-memory mapping before a writer
persists it. Both enforce duplicate-key rejection, string mapping keys, finite
numbers, local-only references, and JSON compatibility.

Every production JSON Schema loader uses `load_configured_schema_bundle()`,
`load_configured_schema()`, or `configured_validator()`. `config_path=None` is
valid for unannotated schemas and fails closed when an annotation requires
configuration.

## Migration behavior

Docstring configuration becomes strict instead of silently coercing malformed
values. Missing optional docstring configuration still selects typed defaults.
Node-hash policy preserves its domain error type. Recurring-tasks receives one
shared jobs loader and validates both reads and writes. Cloud-files validates
settings but continues to treat OAuth credentials as secrets, not config.

Blueprint, visualization, list-manager, and frozen migration schema loading
delegate reference resolution and validation to the common boundary. Domain
modules retain domain-specific error translation and semantic checks.

## Enforcement and tests

Focused tests prove all four lightweight config syntaxes, reject mixed and
schema-shaped user input, preserve optional configuration, reject non-finite
numbers, and exercise each migrated loader. An architecture test prevents new
production JSON Schema loading outside `configured_schema.py` while allowing
modules to import exception and protocol types needed for domain error handling.

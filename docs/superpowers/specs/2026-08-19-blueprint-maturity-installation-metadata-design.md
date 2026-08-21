# Blueprint Maturity and Installation Metadata

## Goal

Make node readiness, default-install membership, and personal-preference status explicit in blueprints, then let installation derive optional dependency selection from those declarations rather than package-name exceptions.

## Approved model

Every module and behavioral-source blueprint declares:

```yaml
maturity: stable # or experimental
```

Discoverable module blueprints additionally declare:

```yaml
installation_tier: core # or optional
```

Module blueprints also declare the nested preference record:

```yaml
personal_preference:
  applies: false
```

When `applies` is true, `description` is required and explains what makes the node a personal preference:

```yaml
personal_preference:
  applies: true
  description: Explains the user-specific workflow preference.
```

The initial maturity vocabulary is exactly `stable` and `experimental`. Maturity and installation tier are independent: an experimental node may be core, and a stable node may be optional. The deterministic migration defaults existing nodes to `stable`, `core`, and `personal_preference.applies: false`; it overrides `pdf-to-markdown` to `optional` and the `using-compass` and `rutter` node IDs to `experimental` when those nodes are present.

## Scope and ownership

`maturity` is node metadata and applies to both `module` and `behavioral_source` schemas. `installation_tier` and `personal_preference` describe user-selectable skills, so they apply only to discoverable module blueprints. A selected optional module includes its contained behavioral sources and their runtime-dependency closure.

Existing authored blueprints are migrated by a deterministic Python script using the repository's existing YAML loading conventions. Hand-editing the repository-wide metadata rollout is not part of the implementation. The script is idempotent, has explicit override maps for the named exceptions, and fails when a targeted v6 blueprint cannot be parsed or updated safely.

The canonical blueprint remains the source of truth. The blueprint syncer continues to generate `references/blueprint/runtime_dependencies.json`; the installer does not scan raw YAML independently.

## Generated dependency selection

The generated runtime manifest will preserve module installation metadata and attribute executable dependency records to their owning discoverable modules. Installation will:

1. select all `core` modules;
2. present `optional` modules with their affected interfaces/skills;
3. calculate the package delta for each optional selection, including transitive runtime dependencies and platform filtering;
4. report package names and resolver-available download/install-size estimates; and
5. pool the selected package set once, deduplicating shared packages.

No package name, including `marker-pdf`, is special-cased in installer policy. The installer obtains wheel/sdist sizes from package-index metadata, using its existing cache boundary where available; if metadata cannot provide a reliable size, it reports the package and marks the estimate unavailable instead of inventing a value.

The core lock remains the checked-in universal lock. When optional modules are selected, installation generates a separate selection lock with the pinned `uv` and `--generate-hashes`, validates and installs it with hash enforcement, and records the selected module IDs plus the generated input and lock hashes in the candidate artifact. Optional selection therefore never mutates or silently broadens the core lock.

## Validation and documentation

The schemas require valid maturity and installation-tier values. `personal_preference.applies: true` requires a nonempty description. Discoverable module examples and documentation explain the two maturity values, core/optional selection, and the preference description. Generated manifests and the universal lock remain synchronized artifacts and must pass their existing validation flow.

## Non-goals

- No new maturity values beyond `stable` and `experimental`.
- No authored cost field; installation estimates cost dynamically.
- No partial selection of behavioral sources within an optional module.
- No automatic inference that experimental means optional or that optional means experimental.

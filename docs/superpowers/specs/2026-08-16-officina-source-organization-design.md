# Officina source organization design

**Status:** Approved architecture; detailed design awaiting user review
**Date:** 2026-08-16

## Objective

Reorganize `src/officina/` around coherent domains while preserving runtime
behavior. Replace ambiguous and duplicated import locations with one canonical
address for every public capability. Update every repository invocation to the
new address; do not retain compatibility facades at old module paths.

This work changes package and Officina module identities deliberately. It does
not delete capabilities, redesign their behavior, or broaden their authority.

## Decisions

1. Keep `officina.common`, but restrict it to small cross-cutting primitives
   that do not constitute an independent subsystem.
2. Give each substantial shared subsystem a top-level package under
   `officina/`.
3. Align each listed substantial package with one registered Officina module.
4. Use concise domain nouns consistently: singular `blueprint`,
   `certification`, `configuration`, `controller`, `credentials`, `docstring`,
   and `visualization`, plus plural `standards` for the repository standards
   collection.
5. Use each domain package's `__init__.py` as its canonical public Python
   gateway. Cross-domain callers import from that gateway rather than from
   implementation modules.
6. Do not re-export moved names from `officina.common` or from deleted legacy
   modules. Old imports must fail after all repository and user-owned callers
   have migrated.
7. Keep existing cohesive top-level domains—`dispatcher`, `install`, `runtime`,
   `validators`, and `wakeup`—as top-level packages.

## Why not put every shared system under `common`

`common` describes reuse, not responsibility. A tree such as
`common.docstring`, `common.blueprint`, and `common.certification` would improve
indentation without fixing the ownership problem: `common` would remain a
large umbrella containing unrelated models, policy, configuration, and
interfaces. Registered nested modules would also require an extra parent-child
registration and namespace-export layer.

Conversely, eliminating `common` entirely would place one-file primitives such
as date formatting and atomic file operations directly beside full systems.
The selected hybrid keeps those primitives together while giving real domains
names and boundaries of their own.

## Target package structure

```text
src/officina/
├── common/
│   ├── __init__.py
│   ├── atomic_files.py
│   ├── codex_toml.py
│   ├── dates.py
│   ├── famulus_paths/
│   ├── python_source_cache.py
│   ├── repository_paths.py
│   └── toml_io.py
├── blueprint/
│   ├── __init__.py
│   ├── authorization.py
│   ├── graph.py
│   ├── inventory.py
│   ├── pooled.py
│   ├── process_binding.py
│   ├── projection.py
│   ├── search.py
│   └── template.py
├── certification/
│   ├── __init__.py
│   ├── hashing.py
│   ├── provenance.py
│   ├── records.py
│   └── view.py
├── configuration/
│   ├── __init__.py
│   ├── configured_schema.py
│   ├── repository.py
│   └── schema.json
├── controller/
│   ├── __init__.py
│   ├── model.py
│   └── protocol.py
├── credentials/
│   ├── __init__.py
│   ├── google.py
│   ├── oauth.py
│   └── secret_store.py
├── docstring/
│   ├── __init__.py
│   ├── config.yaml
│   ├── parser.py
│   ├── policy.py
│   └── validation.py
├── standards/
│   ├── __init__.py
│   ├── extractor.py
│   └── query.py
├── visualization/
│   ├── __init__.py
│   ├── artifacts.py
│   ├── base_extractor.py
│   ├── base_renderer.py
│   ├── base_visualizer.py
│   ├── graph.py
│   ├── payload.py
│   ├── server.py
│   ├── blueprint/
│   ├── docstring/
│   └── html/
├── dispatcher/
├── install/
├── repository/
│   └── checks/
├── runtime/
├── validators/
└── wakeup/
```

Every registered module keeps its own `blueprint.yaml`. Its `blueprints/`
directory remains reserved for behavioral-source YAML declarations. Python
blueprint implementation therefore belongs in the singular
`officina/blueprint/` package, not in a `blueprints/` metadata directory.

The tree describes package ownership, not a requirement to split every file in
the first commit. Large implementation files may be renamed and moved without
internal decomposition. Behavioral decomposition is separate work and is not
authorized by this reorganization.

## Current-to-target mapping

| Current source | Target domain |
|---|---|
| `common/docstring/` and `common/docstring_*.py` | `docstring/`; delete the three outer facades |
| `common/blueprint_*.py`, `pooled_blueprint.py`, `process_binding_compiler.py`, `interface_projection.py`, root `blueprint_search.py` | `blueprint/` |
| `common/certification_*.py`, `certificate_records.py`, `git_provenance.py` | `certification/` |
| `common/configured_schema.py`, `configuration.schema.json`, `repository_configuration.py` | `configuration/` |
| `common/controller.py`, `controller_protocol.py` | `controller/` |
| `common/google_credentials.py`, `oauth_json.py`, `secret_store.py` | `credentials/` |
| `common/standard_extractor.py`, `standard_query.py` | `standards/` |
| `common/visualization/` | top-level `visualization/` |
| `repository_checks.py`, `repo_checks/` | `repository/checks/` |
| root `_validator_snapshot.py` | `validators/snapshot.py` |
| small path, file, date, TOML, and source-cache helpers | remain in `common/` |

`dispatcher`, `install`, `runtime`, `validators`, and `wakeup` remain cohesive
top-level domains. Their internal imports and declarative paths change only as
needed to consume the moved shared domains. A later behavior-focused refactor
may reorganize their internals separately.

## Canonical addressing

Public Python callers use domain gateways:

```python
from officina.docstring import parse_graph_block
from officina.blueprint import load_repository_blueprint_graph
from officina.certification import compute_node_hash_states
from officina.configuration import load_configuration
from officina.common import atomic_files
```

Within one domain, implementation modules use relative imports. Across domains,
production code imports only names deliberately published by the target
domain's `__init__.py`. It must not reach into another domain's implementation
file merely because the file is importable.

The following old addresses are removed rather than forwarded:

```text
officina.common.docstring_parser
officina.common.docstring_schema
officina.common.docstring_validation
officina.common.blueprint_graph
officina.common.certification_hashing
officina.common.visualization
```

Canonical addressing applies to more than Python imports. Each domain move also
updates:

- `python -m` module commands and installed launcher arguments;
- dispatcher process targets and gateway paths;
- blueprint module IDs, source IDs, exports, uses-interface edges, caller
  allowlists, content patterns, and dependencies;
- certification-basis and node-hash-policy paths;
- test patch targets and dynamically loaded module strings;
- schema/configuration lookup paths;
- documentation, examples, and validator allowlists.

## Blueprint and ownership migration

Physical package moves and Officina graph changes form one migration. For each
new substantive domain:

1. Create the module marker and behavioral-source declarations from the live
   schema and canonical templates.
2. Move directly owned implementation and configuration into that module.
3. Publish every existing public capability under its one canonical new domain
   address, while keeping private implementation details private.
4. Replace `common.*` module/source/interface IDs with the new domain IDs in
   all exact callers and dependencies.
5. Remove the moved content, source registrations, and exports from the
   `common` module.
6. Rebuild and validate the graph before moving the next domain.

The current standards query contains a material inconsistency: the adopted
architecture and live blueprints use schema version 6, while the queried
refactor requirement still says edited blueprints use schema version 5. The
implementation must resolve that canonical-standard defect before authoring
new module blueprints. It must not downgrade live v6 blueprints or silently
ignore the returned requirement.

## Migration strategy

No compatibility facades are permitted. Therefore each domain is migrated as
one internally complete slice:

1. Move the implementation and owned data.
2. Define the new package gateway and Officina module surface.
3. Update every repository consumer and invocation.
4. Remove old files and old graph identities.
5. Run the domain's focused verification and graph checks.
6. Inspect the exact diff and commit that slice before beginning another
   structural move.

Recommended slice order:

1. `controller` as a low-coupling pilot for the module/package procedure;
2. `configuration`;
3. `docstring`;
4. `blueprint`;
5. `certification`;
6. `credentials` and `standards`;
7. `visualization`;
8. repository checks and the final `common` contraction;
9. downstream dispatcher, runtime, installer, validator, wakeup, skill, and
   documentation closure not already updated within earlier slices.

Dependencies may require combining two adjacent slices, but an old address may
never be retained solely to make an incomplete slice pass.

## Behavior-preservation contract

The reorganization preserves:

- every existing public capability, callable name, and declared interface,
  relocated to its canonical new domain;
- arguments, return values, exceptions, and side effects;
- CLI arguments, output, exit codes, and working-directory behavior;
- dispatcher authorization outcomes and generated commands;
- configuration and schema semantics;
- certification hashes except where path or node identity is intentionally
  part of the hash;
- platform behavior and installed-runtime behavior;
- test intent and patch lookup semantics.

Expected intentional changes are limited to:

- Python import paths;
- `python -m` targets;
- Officina module, source, and interface identities;
- ownership and certification manifests derived from those identities;
- documentation and examples that name those addresses.

Certificate staleness caused by these source, blueprint, or basis changes is an
expected outcome. Fresh certification occurs only after the final reviewed
state; certificates are not rewritten or treated as current during migration.

## Failure handling

- A domain slice with unresolved callers, invalid blueprints, or failing
  focused tests is non-final and must be repaired or reverted before another
  structural move begins.
- Dynamic imports, process targets, configuration strings, and documentation
  commands count as callers even when an AST import scan cannot see them.
- An unknown external caller does not justify a facade. Instead, record its
  required canonical replacement and migrate it explicitly outside this
  repository.
- Unrelated dirty worktree changes remain untouched and excluded from every
  slice.
- Generated bytecode and package metadata under `src/` are cleanup artifacts,
  not migration inputs.

## Verification

Before the first move, record a baseline for the focused tests of the selected
domain. For each slice:

1. Search tracked source, tests, blueprints, docs, command strings, and
   configuration for every old path and identity.
2. Import the new domain gateway and exercise its published names.
3. Run the domain's focused unit, contract, and integration tests.
4. Validate the exact blueprint graph and interface routes affected by the
   move.
5. Run route-smoke checks for changed process bindings.
6. Run repository validators that enforce imports, blueprint relationships,
   platform neutrality, documentation references, and certification basis.
7. Confirm `git diff --check` and inspect the slice diff against the
   behavior-preservation contract.

After all slices, run the repository's supported pre-commit and broader test
suites. Report unrelated failures separately; do not weaken checks or preserve
legacy paths to make them disappear.

## Non-goals

- No behavior redesign, feature addition, or API-semantic cleanup.
- No large-file decomposition merely because a file is being moved.
- No compatibility modules, alias packages, deprecation period, or dual
  Officina identities.
- No unrelated reorganization of skills or non-`src/officina` source.
- No certification issuance before the final reviewed state.

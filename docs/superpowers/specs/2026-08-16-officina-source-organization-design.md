# Officina source organization design

**Status:** Implemented mechanically through the blueprint-aware relocation manifest
**Date:** 2026-08-16

The final implementation uses README-only package initializers and direct
implementation-module imports; it does not use Python import facades.

## Implementation progress

The implementation is relocation-only: preserve implementation bodies and
behavior, move files to their owning packages, update imports and path-bearing
references, and make each affected `__init__.py` the package README. Do not
decompose large files, redesign interfaces, change standards, or add unrelated
features.

- [x] `controller`: moved the model and protocol, added the package README,
  updated active imports, and passed focused tests.
- [x] `configuration` and `docstring`: moved implementation and resource files,
  removed legacy facades, updated active references, added package READMEs,
  and passed 140 focused tests plus direct module/docstring checks.
- [x] `blueprints`, `certification`, `credentials`, and `git`.
- [x] `standards` and `visualization`.
- [x] repository checks, validator snapshot, and final `common` contraction.
- [x] repository-wide old-address closure and focused/full verification.

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
3. Align each newly extracted substantial package with one registered Officina
   module. Registration changes for the already top-level `dispatcher`,
   `runtime`, and `validators` packages are outside this reorganization.
4. Use concise domain nouns consistently: plural `blueprints`,
   `certification`, `configuration`, `controller`, `credentials`, `docstring`,
   `git`, and `visualization`, plus plural `standards` for the repository
   standards collection.
5. Use each domain package's `__init__.py` as its README. Callers import from
   concrete owning modules; package initializers do not form import facades.
6. Do not re-export moved names from `officina.common` or from deleted legacy
   modules. Old imports must fail after all repository and user-owned callers
   have migrated.
7. Keep existing cohesive top-level domains—`dispatcher`, `install`, `runtime`,
   `validators`, and `wakeup`—as top-level packages.
8. Make every package `__init__.py` a self-contained README for that package:
   its module docstring explains the package's responsibility and accounts for
   every owned file without duplicating implementation details.

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
├── blueprints/
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
├── git/
│   ├── __init__.py
│   └── provenance.py
├── standards/
│   ├── __init__.py
│   ├── extractor.py
│   └── query.py
├── visualization/
│   ├── __init__.py
│   ├── artifacts.py
│   ├── base_extractor.py
│   ├── base_renderer.py
│   ├── base_renderer_cli.py
│   ├── base_visualizer.py
│   ├── elk_html_renderer.py
│   ├── graph.py
│   ├── graph_specification.schema.json
│   ├── payload.py
│   ├── server.py
│   ├── from_blueprint/
│   ├── from_docstring/
│   └── html_renderer/
├── dispatcher/
├── install/
├── repository/
│   ├── __init__.py
│   └── checks/
│       ├── __init__.py
│       ├── discovery.py
│       ├── remote.py
│       ├── remote_macos_windows.py
│       └── runner.py
├── runtime/
├── validators/
└── wakeup/
```

Every registered module keeps its own `blueprint.yaml`. Its `blueprints/`
directory remains reserved for behavioral-source YAML declarations. Python
blueprint implementation therefore belongs in the top-level
`officina/blueprints/` package; behavioral-source declarations remain under
its nested `blueprints/` metadata directory.
The code-focused tree omits these repeated metadata entries: each newly
registered `blueprints`, `certification`, `configuration`, `controller`,
`credentials`, `docstring`, `git`, `standards`, `visualization`, and
`repository` module also contains `blueprint.yaml` and `blueprints/*.yaml`.

The tree describes package ownership, not a requirement to split every file in
the first commit. Large implementation files may be renamed and moved without
internal decomposition. Behavioral decomposition is separate work and is not
authorized by this reorganization.

## Current-to-target mapping

Unless marked `repository-root`, paths in this table are relative to
`src/officina/`.

| Current source | Target source |
|---|---|
| `common/docstring/docstring_parser.py` | `docstring/parser.py` |
| `common/docstring/docstring_policy.py` | `docstring/policy.py` |
| `common/docstring/docstring_validation.py` | `docstring/validation.py` |
| `common/docstring/config.yaml` | `docstring/config.yaml` |
| `common/docstring/docstring_schema.py` | remove after migrating its deprecated imports |
| `common/docstring_parser.py`, `common/docstring_schema.py`, `common/docstring_validation.py` | remove after migrating all facade callers |
| `common/blueprint_authorization.py` | `blueprints/authorization.py` |
| `common/blueprint_graph.py` | `blueprints/graph.py` |
| `common/blueprint_inventory.py` | `blueprints/inventory.py` |
| `common/blueprint_template.py` | `blueprints/template.py` |
| `common/pooled_blueprint.py` | `blueprints/pooled.py` |
| `common/process_binding_compiler.py` | `blueprints/process_binding.py` |
| `common/interface_projection.py` | `blueprints/projection.py` |
| `blueprint_search.py` | `blueprints/search.py` |
| `common/certification_hashing.py` | `certification/hashing.py` |
| `common/certification_view.py` | `certification/view.py` |
| `common/certificate_records.py` | `certification/records.py` |
| `common/git_provenance.py` | `git/provenance.py`; the effectful Git state, ref-pinning, tree-materialization, and provenance subsystem is too substantial for `common` |
| `common/configured_schema.py` | `configuration/configured_schema.py` |
| `common/configuration.schema.json` | `configuration/schema.json` |
| `common/repository_configuration.py` | `configuration/repository.py` |
| `common/controller.py` | `controller/model.py` |
| `common/controller_protocol.py` | `controller/protocol.py` |
| `common/google_credentials.py` | `credentials/google.py` |
| `common/oauth_json.py` | `credentials/oauth.py` |
| `common/secret_store.py` | `credentials/secret_store.py` |
| `common/standard_extractor.py` | `standards/extractor.py` |
| `common/standard_query.py` | `standards/query.py` |
| `common/visualization/` | `visualization/`, preserving its internal filenames and subdirectory names in this reorganization |
| `common/discover_tests.py` | `repository/checks/discovery.py` |
| `repository_checks.py` | `repository/checks/runner.py` |
| `repo_checks/remote.py`, `repo_checks/remote_macos_windows.py` | `repository/checks/remote.py`, `repository/checks/remote_macos_windows.py` |
| `repo_checks/__init__.py` | `repository/checks/__init__.py`, rewritten as the nested package README with no re-exports |
| no current package | create `repository/__init__.py` as the registered module README; `main` remains owned by `repository/checks/runner.py` |
| `_validator_snapshot.py` | `validators/snapshot.py` |
| `common/blueprints/blueprint-{graph,inventory,template}.yaml`, `pooled-blueprint.yaml`, `process-binding-compiler.yaml` | `blueprints/blueprints/{graph,inventory,template,pooled,process-binding}.yaml` |
| `common/blueprints/certificate-records.yaml`, `certification-{hashing,view}.yaml` | `certification/blueprints/{records,hashing,view}.yaml` |
| `common/blueprints/repository-configuration.yaml` | `configuration/blueprints/repository.yaml` |
| `common/blueprints/google-credentials.yaml`, `oauth-json.yaml`, `secret-store.yaml` | `credentials/blueprints/{google,oauth,secret-store}.yaml` |
| `common/blueprints/standard-extractor.yaml`, `standard-query.yaml` | `standards/blueprints/{extractor,query}.yaml` |
| no current sidecar for `common/python_source_cache.py` | create `common/blueprints/python-source-cache.yaml` and register `common.source.python-source-cache` in the contracted `common/blueprint.yaml` |
| `common/blueprints/git-provenance.yaml` | `git/blueprints/provenance.yaml`; add the narrow `git-ignore` source interface while preserving the existing provenance interface and callers under the new identity |
| `common/blueprint.yaml` | retain and contract to the primitive sources and exports that remain in `common` |
| `common/__init__.py` | retain as the `common` package README; remove all lazy compatibility exports for moved domains |
| every retained or newly created `**/__init__.py` | make it README-only and replace its summary-only docstring with the package README contract |
| `common/README.md` | merge its still-current package guidance into `common/__init__.py`, move long-form domain material to `docs/officina/`, then remove the duplicate README |
| `common/visualization/html_renderer/README.md` | merge package-boundary and file-role guidance into `visualization/html_renderer/__init__.py`, retain long-form renderer material in `docs/officina/visualization.md`, then remove the duplicate README |
| repository-root `repo_checks.py` | retain as the deliberate source-checkout CLI bootstrap, but change its import to `officina.repository.checks.runner` |
| `common/atomic_files.py`, `codex_toml.py`, `dates.py`, `famulus_paths/`, `python_source_cache.py`, `repository_paths.py`, `toml_io.py` | remain in `common/` |

`dispatcher`, `install`, `runtime`, `validators`, and `wakeup` remain cohesive
top-level domains. Their internal imports and declarative paths change only as
needed to consume the moved shared domains. A later behavior-focused refactor
may reorganize their internals separately.

## Canonical addressing

Python callers use concrete owning modules:

```python
from officina.docstring.parser import parse_graph_block
from officina.blueprints.graph import load_repository_blueprint_graph
from officina.certification.hashing import compute_node_hash_states
from officina.configuration.repository import load_configuration
import officina.common.atomic_files as atomic_files
```

Within one domain, implementation modules use relative imports. Across domains,
production code imports the concrete module that owns the required name. The
package initializer documents ownership and does not re-export implementation
symbols.

The retained repository-root `repo_checks.py` is a CLI bootstrap for running
from an uninstalled source checkout, not an import compatibility facade. It
contains no repository-check behavior and imports `main` only from the
canonical `officina.repository.checks.runner` module. Python callers must not
import the bootstrap.

All old addresses for moved code are removed rather than forwarded. Examples
include:

```text
officina.common.docstring_parser
officina.common.docstring_schema
officina.common.docstring_validation
officina.common.blueprint_graph
officina.common.certification_hashing
officina.common.git_provenance
officina.common.visualization
officina.repository_checks
officina.repo_checks
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

## Package `__init__.py` as the module README

Every Python package under `src/officina/`, including the root `officina`
package and nested packages, uses its `__init__.py` module docstring as the
authoritative local README. A reader should be able to open that one file and
understand:

1. what responsibility the package owns;
2. what does and does not belong in the package;
3. which concrete modules own its public entry points; and
4. why every file owned by the package exists.

The docstring uses the existing module-docstring vocabulary rather than a new
format:

```python
"""Load, validate, and project Officina blueprint graphs.

Description
-----------
This package owns blueprint discovery, graph validation, authorization,
projection, and search. Certification policy and repository configuration
belong to their respective packages.

Includes
--------
- `__init__.py` — Documents this package and its owned files.
- `authorization.py` — Resolves interface authorization independently of dispatch.
- `graph.py` — Builds and validates the canonical repository blueprint graph.
- `blueprint.yaml` — Declares this package's Officina module boundary.
- `blueprints/graph.yaml` — Declares graph behavior, dependencies, and interfaces.
- `search.py` — Applies structured queries to canonical blueprint documents.
"""
```

The example illustrates shape, not the final blueprint file catalog. The
implementation must derive the final inventory from the files actually present
after each slice.

### Catalog rules

- The first line is a concise package-responsibility summary.
- Substantive domain packages use `Description` to explain the boundary,
  principal responsibilities, and intended public entry point. A small nested
  package may omit it only when the summary fully states that boundary. It does
  not repeat individual function docstrings.
- `Includes` contains one backtick-delimited package-relative path and one
  concrete relevance statement per entry.
- The catalog includes `__init__.py`, Python modules, `__main__.py`,
  `blueprint.yaml`, behavioral-source sidecars, schemas, configuration, data,
  templates, notices, and other tracked runtime inputs owned by the package.
- Entries are sorted by normalized relative path so changes produce stable,
  reviewable diffs.
- A direct child Python subpackage is listed once as `<subpackage>/`; that
  subpackage's own `__init__.py` must catalog its contents.
- A directory without its own `__init__.py` cannot delegate documentation.
  Its tracked files are listed recursively in the nearest package's `Includes`
  section. This applies to assets, templates, browser runtime files, vendored
  files, resources, and blueprint sidecars.
- Generated caches, bytecode, build outputs, and untracked local artifacts are
  excluded. A tracked generated or vendored input is included and identified
  as generated or vendored.
- A relevance statement says why the file belongs to the package; restating a
  filename or saying only "helper" is insufficient.
- Adding, deleting, renaming, or transferring ownership of a file updates the
  nearest owning `Includes` section in the same domain slice.

The package initializer contains only this docstring. Public imports and
`__all__` entries belong to concrete implementation modules, not package-level
facades.

### Long-form documentation

The `__init__.py` docstring replaces hand-maintained package-overview READMEs,
not all documentation. Detailed protocols, tutorials, operational manuals, and
architecture explanations may remain under `docs/officina/` or as clearly
specialized documents. The package docstring summarizes their relevance in
`Includes` when the package owns them; it does not duplicate hundreds of lines
of procedural material.

Current package-overview material in `common/README.md` and
`visualization/html_renderer/README.md` is split during migration: concise
purpose, boundary, gateway, and file-role material moves into the appropriate
`__init__.py`; genuinely long-form material moves to or merges with the active
`docs/officina/` domain document; the duplicate package README is then removed.
Specialized wakeup user and architecture documents remain distinct and are
cataloged by `wakeup/__init__.py`.

### Standard and enforcement changes

The live docstring standard already recognizes module summaries,
`Description`, and required `Includes` sections, but currently sets
`module.required: false`. This is an unenforced existing contract, not a need
for a new docstring format. Globally changing that flag would incorrectly
require an `Includes` catalog in every ordinary `.py` module.

Implementation activates the existing module contract only when the validated
path is `src/officina/__init__.py` or matches
`src/officina/**/__init__.py`. That package-init check requires:

- a module summary;
- the existing required `Includes` section;
- parseable catalog entries with non-empty relevance statements; and
- exact coverage of the package-owned tracked files under the delegation rules
  above.

The existing parser already exposes `Includes` through the parsed module
sections. The existing validator is therefore extended only with:

1. package-init selection and activation of the existing module requirements;
2. entry-shape validation for the existing `Includes` section; and
3. comparison between those entries and the tracked package-file inventory.

No second README parser, new section vocabulary, profile-schema expansion, or
standalone checker is created. The canonical standard changes only if
implementation proves that one of its existing fields cannot express this
contract; any such change must then follow the full standards-maintenance
revision, digest, generated-view, and validation workflow.

### Existing Officina identity rewrites

Existing graph identities move with their owning behavior. The identity suffix
is shortened where the new module name already supplies the domain context:

| Old identity | New identity |
|---|---|
| `common.source.blueprint-graph` | `blueprint.source.graph` |
| `common.source.blueprint-inventory` | `blueprint.source.inventory` |
| `common.source.blueprint-template` | `blueprint.source.template` |
| `common.source.pooled-blueprint` | `blueprint.source.pooled` |
| `common.source.process-binding-compiler` | `blueprint.source.process-binding` |
| `common.interface.blueprint-graph` | `blueprint.interface.graph` |
| `common.interface.blueprint-template` | `blueprint.interface.template` |
| `common.interface.pooled-blueprint` | `blueprint.interface.pooled` |
| `common.source.certificate-records` | `certification.source.records` |
| `common.source.certification-hashing` | `certification.source.hashing` |
| `common.source.certification-view` | `certification.source.view` |
| `common.interface.certificate-records` | `certification.interface.records` |
| `common.interface.certification-hashing` | `certification.interface.hashing` |
| `common.interface.certification-view` | `certification.interface.view` |
| `common.source.git-provenance` | `git.source.provenance` |
| `common.interface.git-provenance` | `git.interface.provenance` |
| `common.source.repository-configuration` | `configuration.source.repository` |
| `common.interface.repository-configuration` | `configuration.interface.repository` |
| `common.source.google-credentials` | `credentials.source.google` |
| `common.source.oauth-json` | `credentials.source.oauth` |
| `common.source.secret-store` | `credentials.source.secret-store` |
| `common.interface.google-credentials` | `credentials.interface.google` |
| `common.interface.oauth-json` | `credentials.interface.oauth` |
| `common.interface.secret-store` | `credentials.interface.secret-store` |
| `common.source.standard-extractor` | `standards.source.extractor` |
| `common.source.standard-query` | `standards.source.query` |
| `common.interface.standard-extractor` | `standards.interface.extractor` |
| `common.interface.query-standard` | `standards.interface.query` |

The source-interface suffix remains `.interface.<name>`, such as
`blueprint.source.graph.interface.python-api`. Newly registered behavior that
has no old graph identity uses the following source IDs:

| Module | Canonical source IDs added by this reorganization |
|---|---|
| `common` | `common.source.python-source-cache` for the retained `python_source_cache.py` primitive. |
| `blueprint` | `blueprint.source.projection`, `blueprint.source.search`; authorization remains owned with `blueprint.source.graph` as it is today. |
| `configuration` | `configuration.source.schema` for `configured_schema.py` and `schema.json`. |
| `controller` | `controller.source.model`, `controller.source.protocol`. |
| `docstring` | `docstring.source.parser`, `docstring.source.policy`, `docstring.source.validation`. |
| `visualization` | `visualization.source.core`, `visualization.source.blueprint`, `visualization.source.docstring`, `visualization.source.html`. |
| `repository` | `repository.source.check-runner`, `repository.source.remote-checks`, `repository.source.test-discovery`. |

Each row's source slug maps to `blueprints/<source-slug>.yaml` in its module;
for example, `repository.source.remote-checks` is declared by
`repository/blueprints/remote-checks.yaml`.

Moving a source does not create interface authority. Each existing source
interface preserves its current suffix, version, process binding, and contract
under the new module identity. Existing module exports preserve their caller
policies except for the exact identity replacements and newly cross-module
callers specified below. In particular,
`common.source.standard-query.interface.query-standard` becomes
`standards.source.query.interface.query-standard`; it does not become a generic
`python-api`. Sources whose live sidecars have `interfaces: {}`, including the
blueprint inventory and process-binding compiler, remain interface-free unless
one of the narrowly specified capabilities below applies.

The following are the only new cross-node capabilities authorized by this
reorganization. Their source interfaces must expose only the named contract,
begin at version `1`, use `allow_all_modules: false`, and list only the
registered callers shown:

| Module export | Implementing source interface | Contract | Allowed callers |
|---|---|---|---|
| `common.interface.python-source-cache` | `common.source.python-source-cache.interface.python-api` | `PythonSourceCache` | `repository` |
| `common.interface.source-resolution` | `common.source.repository-paths.interface.source-resolution` | `resolve_logical_module_path`, `resolve_python_source_path` | `visualization` |
| `blueprint.interface.projection` | `blueprint.source.projection.interface.project-consumer-interfaces` | `project_consumer_interfaces` | `skill-maker._rtx` |
| `blueprint.interface.gateway-language` | `blueprint.source.process-binding.interface.gateway-language` | `gateway_language_name` | `skill-certifier._rtx` |
| `certification.interface.read-only-view` | `certification.source.view.interface.read-only-view` | `CertificationView` and its `check_authorization`, `check_export`, and `certificate_for` protocol | `blueprint` |
| `configuration.interface.schema` | `configuration.source.schema.interface.python-api` | `ConfiguredSchemaBundle`, `ConfiguredSchemaError`, `configured_validator`, `load_configuration`, `load_configured_schema_bundle`, `schema_requires_configuration`, `validate_configuration` | `blueprint`, `certification`, `docstring`, `visualization`, `cloud-files._rtx`, `recurring-tasks._rtx` |
| `docstring.interface.parsing` | `docstring.source.parser.interface.python-api` | `FunctionSpec`, `PipelineSpec`, `parse_graph_block`, `parse_function_graphs`, `parse_pipeline`, `parse_pseudocode_dependency_ref` | `visualization` |
| `git.interface.ignore` | `git.source.provenance.interface.git-ignore` | `git_ignored_paths` | `blueprint` |
| `visualization.interface.rendering` | `visualization.source.core.interface.rendering` | `BaseVisualizer`, `ElkHtmlRenderer` | `math-dependency-graph._rtx` |

The provider batch must add the corresponding versioned `uses_interfaces`
edges to the consuming source sidecars: `blueprint.source.inventory` for
Git-ignore queries; `repository.source.check-runner` for the Python source cache;
`visualization.source.core` for source resolution;
`skill-maker._rtx.source.rtx-blueprint-syncer`;
`skill-certifier._rtx.source.rtx-certifier`; `blueprint.source.projection` and
`blueprint.source.pooled` for the read-only certification view;
`blueprint.source.graph`, `blueprint.source.template`,
`certification.source.hashing`, `docstring.source.policy`,
`visualization.source.blueprint`, `cloud-files._rtx.source.rtx-init`,
`cloud-files._rtx.source.rtx-ensure-oauth`, and
`recurring-tasks._rtx.source.rtx-jobs-config` for configuration schema access;
`visualization.source.docstring` for docstring parsing; and
`math-dependency-graph._rtx.source.rtx-graph-builder` for shared rendering.
Unregistered dispatcher, documentation-tooling, installer, and test consumers
still receive their Python import rewrites, but they do not acquire invented
Officina graph identities. No new `controller` or `repository` module export is
authorized until a registered cross-node caller is verified and the capability
is added explicitly to this specification.

Policy loading and validation are not added to `docstring.interface.parsing`
merely because their concrete modules expose them to ordinary Python users.

Splitting sources that currently call one another inside `common` also changes
caller identities without changing capability. Preserve every existing caller
on the following exports and make these exact caller-policy additions or
replacements in the same dependency-closed batch:

| Final module export | Caller-policy change required by the split |
|---|---|
| `common.interface.atomic-files` | Add `blueprint`, `certification`, `credentials`, and `git`. |
| `common.interface.famulus-paths` | Add `credentials`. |
| `git.interface.provenance` | Preserve every caller of `common.interface.git-provenance` under the new identity and add `certification`; blueprint receives only the narrower new `git.interface.ignore`. |
| `common.interface.repository-paths` | Add `blueprint`, `certification`, and `git`; visualization receives only the narrower new `common.interface.source-resolution`. |
| `common.interface.toml-io` | Add `configuration`. |
| `blueprint.interface.graph` | Add `certification` and `visualization`. |
| `blueprint.interface.template` | Add `certification`. |
| `credentials.interface.secret-store` | Add `certification`. |
| `standards.interface.extractor` | Replace the old owning caller `common` with the new owning caller `standards`; preserve all other callers. |

The corresponding source-side `uses_interfaces` rewrites are mandatory, not
implied by the caller allowlists. The known final edges are:

- `blueprint.source.graph` and `blueprint.source.inventory` to
  `common.interface.atomic-files`;
- `certification.source.records`, `certification.source.hashing`, and
  `certification.source.view` to `common.interface.atomic-files`;
- `credentials.source.oauth` to `common.interface.atomic-files` and
  `credentials.source.google` to `common.interface.famulus-paths`;
- `git.source.provenance` to `common.interface.atomic-files` and
  `common.interface.repository-paths`;
- `blueprint.source.inventory` to `git.interface.ignore`;
- `certification.source.hashing` and `certification.source.view` to
  `git.interface.provenance`;
- `blueprint.source.graph`, `certification.source.hashing`,
  and `certification.source.view` to `common.interface.repository-paths`;
- `visualization.source.core` to `common.interface.source-resolution`;
- `configuration.source.repository` to `common.interface.toml-io`;
- `certification.source.hashing`, `certification.source.view`, and
  `visualization.source.blueprint` to `blueprint.interface.graph`;
- `certification.source.view` to `blueprint.interface.template`;
- `certification.source.records` to `credentials.interface.secret-store`; and
- `standards.source.query` to `standards.interface.extractor`.

The per-batch caller manifest must extend this list if another live import or
declarative dependency is found; it may not silently grant `allow_all_modules`
or bypass an export through an implementation import.

`blueprint.source.projection` and `blueprint.source.pooled` use
`CertificationView` only as a postponed type annotation and a duck-typed
read-only protocol. During their move, their imports become `TYPE_CHECKING`-only
and their sidecars declare `certification.interface.read-only-view`. This
preserves runtime behavior while preventing concrete module imports from
forming an import cycle. Certification retains the one-way runtime dependency
on the blueprint graph and template exports.

Python users still consume those packages through concrete owning modules;
absence of an Officina module export does not make their Python API private.
If the caller audit proves another registered node needs a new export,
the export must use `<module>.interface.<capability>` and be added explicitly to
the specification before that slice is implemented. It must not reuse or alias
an old `common` identity.

The `common.source.atomic-files`, `common.source.codex-toml`,
`common.source.dates`, `common.source.famulus-paths`,
`common.source.repository-paths`, `common.source.toml-io`, and corresponding
existing export identities remain unchanged. `common.source.python-source-cache`
is newly registered around the retained primitive. Any dependency that still
points to one of these primitives is not a stale reference merely because it
starts with `common`.

## Reference migration contract

Every slice begins with an old-address manifest for that domain. The manifest
contains all old Python modules, repository-relative paths, module/source/
interface IDs, command targets, package-resource names, and generated metadata
values. The move is incomplete until each match is either rewritten or
explicitly classified as a preserved historical fixture. A plain import scan
is not sufficient.

### Verified live reference inventory

A repository audit on 2026-08-16 found active references in the following
areas. These are minimum known consumers, not a substitute for re-running the
manifest search immediately before each move.

| Moved domain | Known reference areas that must be reviewed |
|---|---|
| `docstring` | `docs/officina/docstring.md`; docstring standards and format declarations; certification-basis roots; configuration-consumer, parser, schema, and standard-extractor tests; the deprecated inner and outer schema facades. |
| `blueprint` | `docs/officina/`, `docs/plans/`, `docs_tooling/`, and `scripts/search_blueprints.py`; dispatcher and runtime modules; regenerate-blueprints, skill-certifier, skill-drift, and skill-maker code and blueprints; schema metadata; blueprint/authorization/projection/certification tests; blueprint validators. |
| `certification` | certification-basis roots; install-assistant-tools, skill-certifier, skill-drift, and skill-maker code, blueprints, and tests; dispatcher, installer, and runtime consumers; `test_support/`; certification, projection, and cross-platform tests and validators. |
| `configuration` | architecture, certification, and configured-schema docs; docs catalog; cloud-files and recurring-tasks consumers; dispatcher and installer consumers; schema, catalog, configuration, authorization, and performance tests. |
| `controller` | both modules' internal cross-references and `tests/test_controller_protocol.py`; no registered Officina source currently owns them. |
| `credentials` | cloud-files, connect-google, email-client, and g-calendar implementations, source blueprints, and tests; the Python-node standard; certification-basis roots; credential-specific tests. |
| `git` | blueprint inventory; certification hashing and view; skill-certifier and skill-drift blueprints; certification-basis roots; Git-provenance, graph, projection, and cross-platform tests and validators. |
| `standards` | authority disposition; refactor-node and skill-maker skill contracts, blueprints, and routing tests; standard consumer, extractor, and query tests. |
| `visualization` | documentation tooling; math-dependency-graph implementation and instructions; visualization and docstring docs; third-party notices and dependency audit; browser, graph, projection, and vendored-asset tests; generated `generated_by` metadata. |
| `repository/checks` and `validators/snapshot.py` | root `repo_checks.py`; testing docs; certification-basis roots; remote-check modules; repository-check entrypoint, selection, runner, and snapshot tests. |

The audit also found many valid `common` consumers for the primitives that
remain there, including atomic files, dates, Famulus paths, TOML helpers,
repository paths, and the Python source cache. Those references must not be
rewritten merely to eliminate the word `common`.

| Reference surface | Required change | Completion evidence |
|---|---|---|
| Static Python imports | Replace `officina.common.<moved-module>` and root-module imports with names from the concrete target module. Recalculate relative imports inside moved packages. | AST/import search finds no old module imports; every target implementation module imports cleanly. |
| Direct module surfaces | Preserve every public callable, class, constant, exception, and module-valued export in its moved implementation module; update that module's `__all__` where present. Do not add package-level re-exports. | A before/after public-surface manifest accounts for every evidenced public name and focused tests import it from the concrete new module. |
| Package README docstrings | Update every retained, moved, and new `__init__.py` with a module summary and complete `Includes` section; use `Description` for substantive domain boundaries; move or remove duplicate package-overview READMEs; update the catalog in the same slice as file ownership. | The canonical docstring validator reports no missing, stale, duplicate, malformed, or undocumented package-file entries. |
| Lazy and type-only imports | Rewrite imports inside functions, `TYPE_CHECKING` blocks, forward references, Sphinx roles, and fully qualified annotations. | Text search plus import tests under code paths that exercise lazy imports. |
| Dynamic Python references | Rewrite `importlib.import_module`, `spec_from_file_location`, plugin registries, `sys.modules` keys, pickle/import qualnames where applicable, and `mock.patch` or monkeypatch string targets. | Search for old dotted strings is empty outside approved historical fixtures; patch-based tests pass against the new lookup location. |
| Commands and process bindings | Rewrite `python -m`, console/launcher argv, subprocess lists, dispatcher gateway paths, module entry points, help examples, and shell aliases. Preserve arguments, cwd rules, output, and exit status. | Dry-run/route-smoke output resolves only new targets and command integration tests preserve behavior. |
| Module blueprints | Move source declarations out of `common/blueprint.yaml`; create target `blueprint.yaml` and source sidecars; update module IDs, source IDs, source-interface IDs, exports, `source_interface`, dependencies, `uses_interfaces`, allowed callers, children, namespace exports, content regexes, gateways, and blueprint paths. | Repository graph loads and validates with no old moved identity and no duplicate or unowned content. |
| Consumer blueprints and skill contracts | Rewrite every downstream `uses_interfaces` edge, generated interface block, caller expectation, blueprint test, and prose reference in `SKILL.md`. Preserve caller authorization under the new provider identity. | Graph authorization tests show the same allow/deny decisions; generated skill material is regenerated from the graph rather than hand-edited. |
| Filesystem and package-resource paths | Rewrite `Path` constants, repository-root joins, globs, schema paths, `importlib.resources.files(...)`, error labels, and resource package names. Relative sibling loads must still resolve after a move. | Resource-loading tests pass from both checkout and installed package; old moved paths are absent from active path manifests. |
| Configuration and schema references | Move `configuration.schema.json` to `configuration/schema.json`; update `$id` only if it represents the package location rather than the stable schema identity; rewrite docs, catalog entries, and configuration-consumer tests. | Configured-schema tests validate the same accepted and rejected documents from the new package resource. |
| Certification inputs | Rewrite `references/certification/certification-basis-roots.json`, node-hash policy paths, expected basis tests, certifier/drift imports, and blueprint dependency identities. Treat resulting hash and certificate drift as intentional path/identity drift. | Basis resolution contains every new canonical path and no removed path; expected hash changes are reviewed, not normalized away. |
| Validators and repository checks | Rewrite validator allowlists, test-discovery paths, snapshot-runner imports, validator file registries, remote-check entry points, and path-sensitive test selections. | Focused validator-runner tests and staged-snapshot tests pass from the new locations. |
| Packaging and installed runtime | Verify setuptools package discovery/data inclusion, entry points, managed-runtime copy/install logic, launcher templates, runtime pointers, and any cached installed tree. Reinstall or refresh generated runtime copies rather than retaining old paths. | Wheel/editable-install inspection includes moved Python and data files; clean-environment launcher smoke tests resolve the new package. |
| Documentation and examples | Rewrite active architecture docs, API examples, command snippets, docstring cross-references, README paths, docs catalog/site imports, and search-tool examples. | Documentation validators and executable snippets contain only canonical addresses. |
| Generated files | Change the owning source, then regenerate interface blocks, source maps, catalogs, previews, or other derived files. Never patch generated copies independently. | Regeneration is clean and a second regeneration produces no diff. |
| Package-docstring enforcement | Activate the existing module-summary and `Includes` contract for package `__init__.py` files; add entry-shape and tracked-file coverage checks in the existing validator. Change the canonical standard only if its current fields prove insufficient. | Focused package-docstring tests pass; if the standard changes, its revision/digest closure and registered views also validate. |
| Vendored visualization assets | Move notices and provenance paths with `html_renderer/vendor/`; preserve exact bytes, license records, and recorded hashes. | Vendored-asset provenance tests pass and byte hashes are unchanged. |
| Persisted or serialized addresses | Search JSON, YAML, TOML, cache/state files, generated graph payloads, and values such as `generated_by` for old dotted names. Migrate durable data when those values are read back as addresses; otherwise update descriptive producers and expectations. | Round-trip tests either migrate the old persisted value deliberately or prove it is non-authoritative metadata. |
| External personal callers | Produce a concise old-to-new migration table for scripts, notebooks, shell aliases, and configurations outside this repository. The user updates or identifies those callers before the corresponding old path is removed. | User confirms known external callers are migrated; absence of a facade is not used as a discovery mechanism. |

### Public Python surface baseline

The absence of `__all__` does not make every non-underscored binding public.
Before moving a domain, record a name in its public-surface manifest only when
at least one of these current-baseline sources establishes it as public:

1. an explicit implementation-module import or `__all__` entry;
2. a declared source-interface or module-export contract;
3. active documentation or examples that instruct users to import it; or
4. a verified repository or known external caller that imports or accesses it
   as part of the old module's API.

Imported implementation helpers and incidental module globals are not public
without such evidence. A leading-underscore name is likewise private unless
one of the four evidence sources explicitly establishes otherwise. For every
manifest entry, record the old module and name, the evidence, the new module
and name, and the focused compatibility assertion for signature and behavior.
Duplicate facade paths count as evidence for one capability, not as separate
capabilities to preserve. Any ambiguous name is resolved in the slice plan
before files move; implementation does not guess by exporting the whole module
namespace.

### Active references versus historical evidence

Active code, tests, blueprints, standards, configuration, generated contracts,
and current documentation must use canonical addresses. Versioned v4/v5
blueprint fixtures, frozen compatibility samples, and old plans are not
rewritten merely because they contain an old path: each such occurrence is
classified as one of the following:

1. a current expectation, which must change;
2. a generated derivative, which must be regenerated from its source;
3. a historical fixture whose old value is the behavior under test, which
   remains unchanged and is listed in the slice's search allowlist; or
4. stale documentation, which must be updated or explicitly marked archival.

The allowlist records an exact file and reason. It may not contain a broad
directory exemption such as all of `tests/fixtures/` or `docs/plans/`.

### Search closure

For each slice, search all tracked text—not only `src/`—for these old-address
forms:

- dotted Python modules and qualified symbols;
- slash-separated repository and package-data paths;
- hyphenated Officina module, source, and interface IDs;
- old filenames without their parent path where a registry or loader uses a
  relative location;
- `python -m`, dispatcher, launcher, and subprocess command strings;
- patch/import/resource loader strings; and
- public documentation names and generated metadata values.

The search covers `src/`, `skills/`, `tests/`, `test_support/`, `validators/`,
`references/`, `docs/`, `docs_tooling/`, `scripts/`, root configuration, build
metadata, and notice/license files. Ignored build outputs are not edited, but
the supported build and install workflows must regenerate them from the new
addresses.

A slice reaches reference closure only when:

1. every active match uses the new canonical address;
2. every remaining old match is an exact, justified historical-fixture entry;
3. no tracked Python file imports a moved implementation through `common`;
4. no consumer blueprint points at a removed interface or source ID;
5. no path manifest names a removed file; and
6. clean-checkout and installed-package smoke tests exercise the new addresses.

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
2. Define the new README-only package initializer and Officina module surface.
3. Update every repository consumer and invocation of that domain, including
   dispatcher, runtime, installer, validator, wakeup, skill, documentation,
   test, generated, and package-resource references.
4. Remove old files and old graph identities.
5. Run the domain's focused verification and graph checks.
6. Inspect the exact diff and present that stable slice for explicit commit
   approval before beginning another structural move.

The implementation plan must derive a provider-to-consumer graph from the live
caller manifests before choosing the order. A commit-sized structural batch is
valid only when it is dependency-closed: when a provider receives its new
identity, every registered consumer identity and `uses_interfaces` edge for
that provider reaches its final form in the same batch. Unregistered consumers
receive their final Python/path rewrites in that batch as well. Transitional
`common` caller grants, temporary interfaces, aliases, and later edge cleanup
are not permitted.

The known configuration consumers span the proposed `blueprint`,
`certification`, `docstring`, and `visualization` modules; visualization also
consumes docstring parsing; certification records consume the credentials
secret store; and blueprint plus certification consume the extracted `git`
domain. Those seven domains therefore form one minimum dependency-closed batch
unless the implementation plan proves a smaller valid cutover from the
complete live graph. Cyclic provider-consumer relationships belong to the same
batch. This constraint takes precedence over a convenient domain-by-domain
order.

Subject to that graph, the expected batch order is:

1. `controller` as a low-coupling pilot;
2. the dependency-closed core batch containing at least `configuration`,
   `docstring`, `blueprint`, `certification`, `credentials`, `git`, and
   `visualization`;
3. remaining dependency-closed domain batches, including `standards` where the
   graph permits;
4. repository checks and the final `common` contraction; and
5. repository-wide closure verification: rerun the global address searches,
   documentation/catalog checks, install smoke tests, and graph validation. It
   may not carry caller, identity, edge, or address migrations deferred from an
   earlier batch.

An old address may never be retained solely to make an incomplete batch pass.
Every consumer of a provider moves in that provider's dependency-closed batch.

This document is the umbrella architecture specification. Implementation is
planned and reviewed one domain slice at a time so each plan can enumerate its
exact files, caller manifest, graph rewrites, and verification commands without
mixing unrelated moves.

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
2. Import the new concrete owning modules and exercise their published names.
3. Validate each affected package `__init__.py` against the existing module
   docstring contract and its tracked-file `Includes` inventory.
4. Run the domain's focused unit, contract, and integration tests.
5. Validate the exact blueprint graph and interface routes affected by the
   move.
6. Run route-smoke checks for changed process bindings.
7. Run repository validators that enforce imports, blueprint relationships,
   platform neutrality, documentation references, and certification basis.
8. Confirm `git diff --check` and inspect the slice diff against the
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

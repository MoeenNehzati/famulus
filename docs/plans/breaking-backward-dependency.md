# Breaking Backward Dependency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Do not commit, certify, push, or change branches without separate authorization.

**Goal:** Simplify the repository by deleting executable blueprint v4/v5 paths and retired standard-version machinery while preserving the existing v6 behavior directly.

**Architecture:** Keep one active blueprint graph path and one active standard schema path, both version 6. Delete only version selection, migration branches, compatibility inputs, and historical fixtures around those paths; retain shared helper bodies that v6 currently executes. Certification and drift consume only v6 graphs, but certificate record formats remain readable because certificate payload version is independent of blueprint version.

**Tech Stack:** Python 3.11+, JSON Schema, YAML, pytest, Officina blueprint graph, standards, certification, and dispatcher packages.

**Spec:** This document is the binding scope. Its constraints and per-file budgets override broader cleanup opportunities discovered during implementation.

## Global Constraints

- All active repository blueprints use `schema_version: 6`.
- Blueprint loaders, sync, validation, search, projection, dispatch analysis, certification, and drift accept only version-6 graphs.
- Preserve the existing v6 bodies currently reached through legacy-named helpers such as `_load_v5_repository_blueprint_graph()` and the no-config branch of `_resolve_legacy_trace_metadata()`. Rename a helper only when needed for truthfulness; do not reimplement it.
- Canonical standards use only standard schema v6.
- Retain `standard_version`, `revision`, import-version equality, and digest pins. These identify the current standard contract and are not backward-compatibility mechanisms.
- Correct live canonical requirements that still prescribe blueprint v5. Rename their v5-labelled semantic IDs to v6 and update every exact reference through the existing standard validator; do not create aliases.
- Certification and drift drop blueprint v4/v5 graph routes only. Retain certificate record schema versions 1, 2, and 3, their signature/chain validation, and all existing ignored `.certificates/` state. The certifier continues emitting the current format for v6 graphs.
- Keep the current docstring standard only: delete the retired `docstring_format.yaml` fallback and legacy dependency-syntax switches, then bump the surviving docstring format once.
- Delete enumerated v4/v5 schema fixtures, migration builders, migration audits, fidelity fixtures, and version-specific product documentation. Do not archive or relocate them.
- Retain negative tests that prove v4/v5 inputs are rejected when they exercise a current public boundary rather than an old execution path.
- Preserve independent current formats: benchmark result schema v5, pooled-review schema v2, interface-projection schema v2, drift-output schema v2, runtime-pointer schemas, relocation manifests, installer manifests, and repository configuration.
- Do not delete behavior merely because an identifier contains `v4` or `v5`; first prove the behavior is unreachable from v6. Historical names inside otherwise current JSON Schema definitions may be renamed in place, but their shapes must remain unchanged.
- Do not add converters, aliases, fallbacks, compatibility errors, feature flags, migration commands, or abstraction layers.
- Generated `SKILL.md` blocks and `references/blueprint-schema/runtime_dependencies.json` must remain byte-identical. A generated diff stops implementation and requires a separately reviewed scope change.

## Ownership and reviewed-state gates

Before each task:

- [ ] Record `git rev-parse HEAD`, `git symbolic-ref HEAD`, and `git status --short`.
- [ ] Stop if HEAD is detached.
- [ ] Assign every overlapping dirty hunk to a named owner; do not overwrite or absorb unrelated work.
- [ ] Use the recorded HEAD as that task's budget baseline.

Before optional certification:

- [ ] Obtain separate authorization to commit the completed implementation.
- [ ] Review the exact commit and hold its full SHA stable.
- [ ] Invoke the public certifier only for that reviewed repository and SHA.
- [ ] Treat ignored certificate-log writes as external runtime state, not as files in this implementation diff.

## Three-dimensional line budgets

For each file, obtain added and removed counts from `git diff --numstat <task-base> -- <file>`. Compute:

- `M = min(added, removed)` — replaced lines.
- `N = added - M` — net-new lines.
- `D = removed - M` — deletion-only lines.

The tables below are hard per-file ceilings under that deterministic calculation. A full-file deletion has `D` equal to the tracked line count and `N=M=0`. No implementation file may appear in more than one task budget. Stop for review if a required file is absent or any ceiling is exceeded.

---

### Task 1: Collapse blueprint execution and synchronization onto v6

**Goal:** Remove blueprint-version selection while retaining the exact graph, authorization, projection, dispatcher, and injector behavior already used by v6.

**Interfaces:**

- Consumes: current schemas under `references/blueprint-schema/` and the v6 branches of `load_repository_blueprint_graph()`.
- Produces: v6-only graph loading, inventory, search, projection, dispatch tracing, blueprint synchronization, and repository validation.

- [ ] **Step 1: Freeze generated outputs.** Run:

  ```bash
  dispatcher --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints@1 --check
  ```

  Expected: exit 0 and no generated diff.

- [ ] **Step 2: Isolate the surviving graph path.** In `graph.py`, first identify every helper reached by `load_repository_blueprint_graph(..., expected_schema_version=6)`. Preserve those bodies. Remove the v4 loader and only the v5-specific conditionals from the shared v5/v6 loader; then give the surviving loader/helper names version-neutral or v6 names in place.
- [ ] **Step 3: Remove schema selection.** Delete `expected_schema_version`/`schema_version` selectors, migration-root lookup, `{4, 5, 6}` acceptance, v4 defaults, and v5-only branches from inventory, search, template, authorization, projection, and Python-machine analysis. Call the existing v6 path directly.
- [ ] **Step 4: Preserve v6 no-config dispatch.** Move or rename the existing v6 branch of `_resolve_legacy_trace_metadata()` before deleting its v4/v5 authorization branches. Both configured and no-config v6 tracing must retain their current results.
- [ ] **Step 5: Make the interface-description injector v6-only.** In `_blueprint_syncer.py`, remove `--schema-version`, migration-root selection, v5 discovery/facade branches, and v4 defaults. Preserve the current v6 rendering of used-interface descriptions, generated contract/interface blocks, and runtime dependencies without adding new rendering logic.
- [ ] **Step 6: Make validators and live callers v6-only.** Remove repository-version dispatch from the canonical blueprint validator, cross-platform validator, dispatch-caller validator, catalog, interface-facet migration script, relocation loader, repository-check runner, interface-ID validator, runtime-doc validator, and their callers. Preserve negative rejection at the public boundary.
- [ ] **Step 7: Correct active v6 contract wording.** Replace stale v4/v5 descriptions in the graph and pooled-blueprint source declarations, implementation, and pooled-review `canonical_source` metadata with version-neutral or v6 wording. Do not change pooled-review schema v2 or pooled-review behavior.
- [ ] **Step 8: Reduce owned tests.** Delete tests that execute v4/v5 paths from only the files in this task's table. Retain or rewrite rejection tests that prove old inputs fail at current boundaries.
- [ ] **Step 9: Run focused checks.** Run:

  ```bash
  ./repo_checks.py --task tests:shared --repository-view working --jobs 1 --selector tests/test_blueprint_inventory.py --selector tests/test_blueprint_search.py --selector tests/test_officina_blueprint_template.py --selector tests/test_officina_blueprint_authorization.py --selector tests/test_interface_projection.py --selector tests/test_officina_python_machine_interface.py --selector skills/skill-maker/_rtx/tests/test_blueprint_tools.py --selector tests/test_blueprint_catalog_schema.py --selector tests/test_direct_blueprint_v6_schemas.py --selector tests/test_repository_validator_checks.py --selector tests/test_v6_tooling_support.py --selector tests/validate_blueprints.py --selector tests/validate_dependencies.py --selector tests/validate_dispatch_caller_module.py
  ./repo_checks.py --suite validators --repository-view working --jobs 1 --validator skill-maker/blueprints --validator skill-maker/dependencies
  dispatcher --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints@1 --check
  ```

  Expected: both repository-check commands exit 0; sync exits 0; generated files remain byte-identical.

Relevant objects:

- `load_repository_blueprint_graph()`
- `_load_v4_repository_blueprint_graph()`
- `_load_v5_repository_blueprint_graph()` as the current shared v5/v6 body
- `repository_schema_version()`
- `collect_blueprints()` and `search_blueprints()`
- `project_consumer_interfaces()`
- `_resolve_dispatch_metadata_for_trace()` and `_resolve_legacy_trace_metadata()`
- `blueprints_from_graph()`, `_generated_export_binding()`, and `run_sync()`
- `validators.skill.blueprints.repository_schema_version()`

| File | D | N | M |
|---|---:|---:|---:|
| `src/officina/blueprints/graph.py` | 1,800 | 0 | 160 |
| `src/officina/blueprints/inventory.py` | 220 | 0 | 35 |
| `src/officina/blueprints/search.py` | 170 | 0 | 25 |
| `src/officina/blueprints/template.py` | 120 | 0 | 25 |
| `src/officina/blueprints/authorization.py` | 70 | 0 | 20 |
| `src/officina/blueprints/projection.py` | 170 | 0 | 25 |
| `src/officina/runtime/python_machine_interface.py` | 210 | 0 | 40 |
| `src/officina/dispatcher/core.py` | 90 | 0 | 35 |
| `src/officina/blueprints/pooled.py` | 0 | 0 | 2 |
| `src/officina/blueprints/blueprints/graph.yaml` | 0 | 0 | 1 |
| `src/officina/blueprints/blueprints/pooled.yaml` | 0 | 0 | 4 |
| `references/blueprint-schema/pooled-review.schema.json` | 0 | 0 | 1 |
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | 180 | 0 | 45 |
| `docs_tooling/catalog.py` | 10 | 0 | 10 |
| `scripts/migrate_interface_facets.py` | 5 | 0 | 8 |
| `skills/relocate-nodes/_rtx/_relocate_nodes.py` | 5 | 0 | 8 |
| `src/officina/repository/checks/runner.py` | 15 | 0 | 12 |
| `validators/skill/blueprints.py` | 40 | 0 | 20 |
| `validators/skill/interface_ids.py` | 5 | 0 | 8 |
| `validators/cross_platform.py` | 120 | 0 | 20 |
| `validators/skill/dispatch_caller_module.py` | 120 | 0 | 25 |
| `validators/skill/skill_md_dispatch.py` | 0 | 0 | 4 |
| `validators/skill_runtime_doc_references.py` | 5 | 0 | 8 |
| `validators/platform_neutral.py` | 0 | 0 | 3 |
| `scripts/search_blueprints.py` | 0 | 0 | 6 |
| `tests/test_blueprint_inventory.py` | 440 | 0 | 25 |
| `tests/test_blueprint_search.py` | 340 | 0 | 25 |
| `tests/test_officina_blueprint_template.py` | 260 | 0 | 25 |
| `tests/test_officina_blueprint_authorization.py` | 240 | 0 | 25 |
| `tests/test_interface_projection.py` | 380 | 0 | 25 |
| `tests/test_officina_python_machine_interface.py` | 560 | 0 | 40 |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | 300 | 0 | 30 |
| `tests/test_blueprint_catalog_schema.py` | 20 | 0 | 15 |
| `tests/test_direct_blueprint_v6_schemas.py` | 30 | 0 | 20 |
| `tests/test_repository_validator_checks.py` | 20 | 0 | 15 |
| `tests/test_v6_tooling_support.py` | 20 | 0 | 15 |
| `tests/validate_blueprints.py` | 160 | 0 | 20 |
| `tests/validate_dependencies.py` | 110 | 0 | 15 |
| `tests/validate_interface_ids.py` | 35 | 0 | 10 |
| `tests/validate_platform_neutral.py` | 45 | 0 | 10 |
| `tests/validate_dispatch_caller_module.py` | 140 | 0 | 20 |
| `tests/validate_cross_platform.py` | 90 | 0 | 15 |
| `tests/test_dispatcher_direct_blueprints.py` | 10 | 0 | 10 |

### Task 2: Make certification and drift consume only v6 graphs

**Goal:** Delete blueprint v4/v5 certification routes while preserving certificate record compatibility and current v6 issuance/currentness behavior.

**Interfaces:**

- Consumes: Task 1's v6-only graph.
- Produces: v6-only hashing, certification-state derivation, issuance, and drift selection; unchanged parsing of certificate record formats 1–3.

- [ ] **Step 1: Collapse check/basis selection.** Keep only the existing v6 certifier check registry and current certification-basis root. Remove `V4_CERTIFICATION_BASIS_MANIFEST`, `V5_CERTIFIER_CHECK_REGISTRY`, schema-version arguments, and migration-root selection.
- [ ] **Step 2: Delete migration certification.** Remove v4/v5 repository routes, migration-review flags, mechanical/overlay commits, semantic replay used only for the v4 migration, and old `-rtx` identity translation. Replace stale v4/v5 descriptions in the active hashing/view source declarations with v6 wording without changing their contracts.
- [ ] **Step 3: Preserve certificate record formats.** Do not change `certificate.schema.json`, `parse_certificate_log()`, previous-entry hashing, signature checks, or format-1/2/3 normalization. Do not delete or rewrite `.certificates/` directories.
- [ ] **Step 4: Simplify drift.** Remove `_schema_root_for_version()`, `_V4DerivedState`, v4-named wrappers, and version branches from the drift implementation. Call `derive_repository_certification_state()` with its existing v6 defaults.
- [ ] **Step 5: Remove obsolete Git provenance.** Delete v4 mechanical and source-overlay refs/helpers after their last certifier callers are gone; retain unrelated Git provenance utilities.
- [ ] **Step 6: Reduce owned tests.** Delete only graph-v4/v5 certification and migration-review cases. Retain certificate-format 1/2/3 parsing, signatures, chain integrity, dependency closure, facets, currentness, race, and failure-atomicity tests.
- [ ] **Step 7: Run focused checks.** Run:

  ```bash
  ./repo_checks.py --task tests:shared --repository-view working --jobs 1 --selector skills/node-certify/_rtx/tests/test_certifier.py --selector skills/node-drift/_rtx/tests/test_drift_check.py --selector tests/test_officina_certification_view.py --selector tests/test_officina_certification_hashing.py --selector tests/test_node_certification_hashing.py --selector tests/test_officina_certificate_records.py --selector tests/test_officina_git_provenance.py
  dispatcher --caller-skill node-drift node-drift._rtx.interface.drift-status@3 --repo-root . --json
  ```

  Expected: focused tests exit 0. Drift may report stale certificates because implementation inputs changed, but it must load the v6 graph and every existing certificate log without `invalid-certificate-schema`, signature, or chain errors.

Relevant objects:

- `certifier_check_registry()`
- `derive_repository_certification_state()`
- `RepositoryCertificationView`
- `_certify_repository()` and `_build_certificate_payload()`
- `parse_certificate_log()` — explicitly retained
- `_schema_root_for_version()` and `_derive_v4_repository_state()`
- `blueprint_v4_mechanical_commit()` and overlay-ref helpers

| File | D | N | M |
|---|---:|---:|---:|
| `src/officina/certification/hashing.py` | 300 | 0 | 45 |
| `src/officina/certification/view.py` | 360 | 0 | 55 |
| `src/officina/certification/blueprints/hashing.yaml` | 0 | 0 | 1 |
| `src/officina/certification/blueprints/view.yaml` | 0 | 0 | 2 |
| `skills/node-certify/_rtx/_node_certifier.py` | 1,200 | 0 | 150 |
| `skills/node-drift/_rtx/_check_drift_state.py` | 220 | 0 | 55 |
| `src/officina/git/provenance.py` | 130 | 0 | 15 |
| `skills/node-certify/_rtx/tests/test_certifier.py` | 1,500 | 0 | 60 |
| `skills/node-drift/_rtx/tests/test_drift_check.py` | 420 | 0 | 30 |
| `tests/test_officina_certification_view.py` | 900 | 0 | 45 |
| `tests/test_officina_certification_hashing.py` | 80 | 0 | 20 |
| `tests/test_node_certification_hashing.py` | 720 | 0 | 40 |
| `tests/test_officina_git_provenance.py` | 180 | 0 | 15 |
| `tests/test_officina_certificate_records.py` | 0 | 0 | 0 |
| `references/blueprint-schema/certificate.schema.json` | 0 | 0 | 0 |

### Task 3: Collapse canonical standards onto current v6 content

**Goal:** Remove conversion-history machinery from the v6 standard schema and correct the live standard requirements that still prescribe blueprint v5.

**Interfaces:**

- Consumes: canonical `*.standard.yaml` documents and the existing v6 validator/query/extractor.
- Produces: schema-v6-only canonical standards with exact current import pins and no migration archive model.

- [ ] **Step 1: Correct live blueprint requirements.** In `node.standard.yaml` and `module.standard.yaml`, change the live family/title/requirement and semantic IDs from v5 to v6. Update all exact references in those files; add no aliases.
- [ ] **Step 2: Update standard identities.** Bump `node.standard.yaml` and `module.standard.yaml` from `standard_version: 1.0.0` to `2.0.0` because their public semantic IDs and requirement change. Increment the revision of every edited standard, including import-only consumers, then propagate each new imported version, revision, and digest outward through the complete import closure. Retain each import-only consumer's own `standard_version` unless its own public IDs or requirements change.
- [ ] **Step 3: Delete migration-only schema fields.** Remove `sources`, `source_units`, `migration`, and origin/source-unit variants from `standard-v6.schema.json`. The schema itself describes these as removable conversion-audit data, and no canonical standard currently uses them.
- [ ] **Step 4: Delete existing support code.** Remove source/source-unit validation, coverage rendering, and the two extractor section registrations. Reuse the surviving generic validation, rendering, and extraction loops.
- [ ] **Step 5: Validate the exact closure.** Run:

  ```bash
  ./repo_checks.py --task tests:shared --repository-view working --jobs 1 --selector tests/test_standard_v6.py --selector tests/test_standard_query.py --selector tests/test_standard_extractor.py --selector tests/test_node_standards.py --selector tests/test_skill_refactoring_standard.py --selector tests/validate_standard_documents.py
  ```

  Expected: exit 0; every canonical standard resolves exact version, revision, and digest pins; no live requirement prescribes blueprint v5.

Relevant objects:

- `standard-v6.schema.json` properties `sources`, `source_units`, and `migration`
- `validate_standard_v6._maps()` and migration/source validation loops
- `render_standard_v6.py` source-unit coverage
- `standards.extractor._SECTION_KINDS`
- `skill-guidelines.module-behavioral-source-v5` and its exact references

| File | D | N | M |
|---|---:|---:|---:|
| `references/standards-schema/standard-v6.schema.json` | 1,200 | 0 | 35 |
| `references/standards-schema/validate_standard_v6.py` | 120 | 0 | 20 |
| `references/standards-schema/render_standard_v6.py` | 30 | 0 | 8 |
| `src/officina/standards/extractor.py` | 2 | 0 | 0 |
| `references/node-standards/node.standard.yaml` | 0 | 0 | 30 |
| `references/node-standards/module.standard.yaml` | 0 | 0 | 25 |
| `references/node-standards/behavioral-source.standard.yaml` | 0 | 0 | 8 |
| `references/node-standards/instruction-node.standard.yaml` | 0 | 0 | 8 |
| `references/node-standards/python-node.standard.yaml` | 0 | 0 | 8 |
| `references/node-standards/instruction-module.standard.yaml` | 0 | 0 | 8 |
| `references/node-standards/python-module.standard.yaml` | 0 | 0 | 8 |
| `references/node-standards/instruction-behavioral-source.standard.yaml` | 0 | 0 | 8 |
| `references/node-standards/python-behavioral-source.standard.yaml` | 0 | 0 | 8 |
| `tests/test_standard_v6.py` | 100 | 0 | 25 |
| `tests/test_standard_query.py` | 50 | 0 | 15 |
| `tests/test_standard_extractor.py` | 50 | 0 | 15 |
| `tests/test_node_standards.py` | 100 | 0 | 20 |
| `tests/test_skill_refactoring_standard.py` | 180 | 0 | 30 |

### Task 4: Keep only the current docstring standard format

**Goal:** Remove the v27 policy fallback and legacy dependency syntax so docstring tooling reads only the canonical current policy.

**Interfaces:**

- Consumes: `references/standards-schema/docstring.standard.yaml` and structured dependency sections already required by current configuration.
- Produces: one policy path, one version field, and unconditional structured parsing/validation.

- [ ] **Step 1: Delete the fallback policy.** Delete `docstring_format.yaml` and `DOCSTRING_LEGACY_FORMAT_FILE`; remove fallback discovery.
- [ ] **Step 2: Remove compatibility fields.** Delete `docstring_schema_version`, `allow_legacy_flat`, and `allow_legacy_string` from schemas, policy, repository configuration, dataclasses, parsers, validators, and tests. Preserve the existing strict branches as unconditional behavior.
- [ ] **Step 3: Bump the surviving format.** Change `docstring_format_version` from 30 to 31 and update direct current pins/examples only.
- [ ] **Step 4: Update ownership/docs.** Remove the fallback file from standards-schema blueprints and current docstring documentation.
- [ ] **Step 5: Run focused checks.** Run:

  ```bash
  ./repo_checks.py --task tests:docstrings --repository-view working --jobs 1
  ./repo_checks.py --task tests:shared --repository-view working --jobs 1 --selector tests/test_docstring_schema_dynamic_sections.py --selector tests/test_docstrings_validator.py
  ```

  Expected: exit 0 with structured dependency syntax enforced unconditionally.

Relevant objects:

- `DOCSTRING_STANDARD_FILE` and `DOCSTRING_LEGACY_FORMAT_FILE`
- `DependencySyntaxConfig.allow_legacy_flat`
- `DependencyWhyConfig.allow_legacy_string`
- `docstring_format_version`

| File | D | N | M |
|---|---:|---:|---:|
| `references/standards-schema/docstring_format.yaml` | 365 exact | 0 | 0 |
| `references/standards-schema/docstring_format.schema.json` | 35 | 0 | 15 |
| `references/standards-schema/docstring.standard.yaml` | 10 | 0 | 8 |
| `references/standards-schema/blueprint.yaml` | 3 | 0 | 5 |
| `references/standards-schema/blueprints/docstring-schema.yaml` | 3 | 0 | 5 |
| `src/officina/docstring/policy.py` | 240 | 0 | 55 |
| `src/officina/docstring/parser.py` | 120 | 0 | 35 |
| `src/officina/docstring/validation.py` | 100 | 0 | 30 |
| `src/officina/docstring/config.yaml` | 5 | 0 | 3 |
| `src/officina/configuration/schema.json` | 8 | 0 | 5 |
| `tests/test_docstring_schema_dynamic_sections.py` | 220 | 0 | 45 |
| `tests/test_docstrings_validator.py` | 100 | 0 | 25 |
| `docs/officina/docstring.md` | 15 | 0 | 10 |

### Task 5: Delete retired fixtures, migration evidence, and stale documentation

**Goal:** After all production callers are gone, delete the enumerated historical artifacts and reconcile shared metadata/tests once.

**Interfaces:**

- Consumes: Tasks 1–4.
- Produces: no executable v4/v5 fixture path, no standard-migration archive, and current metadata/documentation only.

- [ ] **Step 1: Prove callers are gone.** Search imports and path literals for every file scheduled for deletion. Stop if any production caller remains.
- [ ] **Step 2: Delete blueprint fixtures/builders.** Delete the v4/v5 schema bundles, the v5 example repository, both fixture builders, and the v5-only nested-schema test.
- [ ] **Step 3: Reconcile shared blueprint tests.** In the shared graph, pooled-blueprint, and visualization tests, delete old execution cases while retaining current v6 coverage and negative old-input rejection.
- [ ] **Step 4: Repair schema metadata and hooks.** Replace the deleted nested-v5 test reference in `schema-meta.json` with a surviving v6 test. Change live hook comments from v5 to v6; do not alter hook commands.
- [ ] **Step 5: Delete standard migration evidence.** Delete all 11 files under `tests/fixtures/standards/`, the fidelity-only test, and `authority-disposition.yaml`; remove only their exact ownership/basis references.
- [ ] **Step 6: Remove version-specific product documentation.** Delete v4/v5 support and migration promises from the live blueprint README and annotated authoring metadata. Preserve generic future migration guidance and unrelated historical plans/ledgers.
- [ ] **Step 7: Run shared cleanup checks.** Run:

  ```bash
  ./repo_checks.py --task tests:shared --repository-view working --jobs 1 --selector tests/test_officina_blueprint_graph.py --selector tests/test_officina_pooled_blueprint.py --selector tests/test_blueprint_visualization.py --selector tests/test_typed_blueprint_schemas.py --selector tests/test_blueprint_schema_metadata.py --selector tests/test_node_standards.py
  ./repo_checks.py --suite validators --repository-view working --jobs 1 --validator skill-maker/blueprints
  ./repo_checks.py --task tests:shared --repository-view working --jobs 1 --selector tests/validate_standard_documents.py
  ```

  Expected: exit 0.

Relevant objects:

- `tests/fixtures/blueprint_schemas/v4/` and `v5/`
- `tests/fixtures/blueprint_v5/`
- `test_support.v4_certification_fixtures`
- `test_support.v5_blueprint_fixtures`
- `schema-meta.json#/x-famulus/validation_rule_catalog`
- `tests/fixtures/standards/`
- `references/node-standards/authority-disposition.yaml`

| File | D | N | M |
|---|---:|---:|---:|
| Each file under `tests/fixtures/blueprint_schemas/v4/` and `v5/` | full file; aggregate 3,599 exact | 0 | 0 |
| Each file under `tests/fixtures/blueprint_v5/` | full file; aggregate 645 exact | 0 | 0 |
| `test_support/v4_certification_fixtures.py` | 813 exact | 0 | 0 |
| `test_support/v5_blueprint_fixtures.py` | 21 exact | 0 | 0 |
| `tests/test_nested_module_v5_schemas.py` | 318 exact | 0 | 0 |
| `tests/test_officina_blueprint_graph.py` | 850 | 0 | 40 |
| `tests/test_officina_pooled_blueprint.py` | 120 | 0 | 20 |
| `tests/test_blueprint_visualization.py` | 120 | 0 | 20 |
| `tests/test_typed_blueprint_schemas.py` | 1,100 | 0 | 30 |
| `tests/test_blueprint_schema_metadata.py` | 140 | 0 | 20 |
| `references/blueprint-schema/schema-meta.json` | 4 | 0 | 4 |
| `references/blueprint-schema/README.md` | 55 | 0 | 18 |
| `references/blueprint-schema/schema.annotated-draft.json` | 0 | 0 | 3 |
| `references/blueprint-schema/blueprints/schema-annotated-draft.yaml` | 0 | 0 | 4 |
| `.githooks/skill/check-blueprints` | 0 | 0 | 3 |
| `.githooks/skill/check-dependencies` | 0 | 0 | 2 |
| Each file under `tests/fixtures/standards/` | full file; aggregate 6,630 exact | 0 | 0 |
| `tests/test_migrated_standards_fidelity.py` | 329 exact | 0 | 0 |
| `references/node-standards/authority-disposition.yaml` | 287 exact | 0 | 0 |
| `references/node-standards/blueprints/standards.yaml` | 6 | 0 | 6 |
| `references/node-standards/blueprint.yaml` | 3 | 0 | 5 |
| `references/certification-policy/certification-basis-roots.json` | 1 | 0 | 1 |

### Task 6: Verify the deletion boundary and hand off optional certification

**Goal:** Prove that only current v6 paths remain, generated artifacts did not change, and no unrelated format or runtime state was removed.

**Interfaces:**

- Consumes: Tasks 1–5.
- Produces: a reviewed implementation diff and, only after separate commit/certification authorization, current certificates for the reviewed SHA.

- [ ] **Step 1: Check generated artifacts.** Run:

  ```bash
  dispatcher --caller-skill skill-maker skill-maker._rtx.interface.sync-blueprints@1 --check
  git diff --exit-code -- references/blueprint-schema/runtime_dependencies.json
  ```

  Expected: exit 0 and no generated diff.

- [ ] **Step 2: Run targeted absence scans.** Run:

  ```bash
  git grep -n -E 'expected_schema_version|repository_schema_version|--schema-version|legacy_v4|blueprint_v4_mechanical|blueprint_v4_source_overlay' -- src validators scripts docs_tooling skills/skill-maker/_rtx skills/node-certify/_rtx skills/node-drift/_rtx skills/relocate-nodes/_rtx tests
  git grep -n -E 'v4 blueprint|v5 blueprint|v4 graph|v5 graph|non-v4|version[- ]4 (blueprint|graph)|version[- ]5 (blueprint|graph)' -- src/officina/blueprints src/officina/certification references/blueprint-schema
  git grep -n -E 'schema_version: 5|Version 5 nested modules' -- references/node-standards references/blueprint-schema skills -- '*.yaml' '*.md'
  git grep -n -E 'docstring_format.yaml|docstring_schema_version|allow_legacy_flat|allow_legacy_string' -- src references tests
  ```

  Expected: each `git grep` exits 1 with no output. Do not broaden these scans to independent numeric schemas, negative rejection fixtures, historical plans/ledgers, or preserved certificate-format readers.

- [ ] **Step 3: Verify deleted paths.** Run `git status --short` and require every enumerated full-file deletion from Task 5, with no unbudgeted deletion.
- [ ] **Step 4: Enforce budgets.** For each task baseline and owned file, compute D/N/M exactly as defined above. Require every file to appear in one table and remain within its ceiling; generated artifacts remain `0/0/0`.
- [ ] **Step 5: Run repository checks.** Run:

  ```bash
  ./repo_checks.py --suite precommit --repository-view working --jobs 8
  git diff --check
  ```

  Expected: exit 0.

- [ ] **Step 6: Report certification state without writing.** Run:

  ```bash
  dispatcher --caller-skill node-drift node-drift._rtx.interface.drift-status@3 --repo-root . --json
  ```

  Expected: the v6 graph and existing format-1/2/3 logs load without schema, signature, or chain errors. Changed nodes may correctly report stale.

- [ ] **Step 7: Stop for certification authorization.** Do not commit or issue certificates as part of plan execution. If the user separately authorizes both, commit only reviewed owned paths, record the full SHA, follow `node-certify` against that exact SHA, and review ignored certificate writes separately. Do not reset old logs.

| File | D | N | M |
|---|---:|---:|---:|
| `references/blueprint-schema/runtime_dependencies.json` | 0 | 0 | 0 |
| Generated regions in all `SKILL.md` files | 0 | 0 | 0 |
| All ignored `.certificates/*.jsonl` files | 0 | 0 | 0 |
| All other files not owned by Tasks 1–5 | 0 | 0 | 0 |

## Explicit exclusions

- Certificate record-format retirement or certificate-log deletion/reset.
- Runtime-pointer, installer-manifest, benchmark-result, credential-store, recurring-state, visualization-output, pooled-review, interface-projection, and relocation-manifest versioning.
- New migration commands, converters, aliases, or compatibility diagnostics.
- General refactoring of graph, certifier, standards, docstring, or dispatcher modules beyond the deletion closure.
- Changes to current v6 blueprint semantics other than removing old-version selection.
- Deleting generic migration guidance, unrelated historical plans, performance ledgers, or negative old-input rejection tests.
- Retaining removed version material in an archive directory.

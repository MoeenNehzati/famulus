# Nested Modules Version 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add certified nested modules, make every repository-managed skill's `_rtx` directory its code module, migrate the repository atomically from blueprint v4 to v5, and preserve existing public skill interfaces and authorization.

**Architecture:** Build v5 behind an explicit noncanonical schema root and fixture loader while the live repository remains canonically v4. Extend the existing inventory and graph, add one shared authorization resolver, migrate every consumer against v5 fixtures, and rehearse a deterministic converter before one atomic canonical cutover. Existing ownership, hashing, validator-runner, dispatcher, certificate, and migration machinery remain authoritative except for the deltas named here.

**Tech Stack:** Python 3, JSON Schema draft 7, PyYAML, pytest, the existing Officina blueprint graph/dispatcher/runtime/certification packages, repository validators, and Git-backed migration fixtures.

## Global Constraints

- The normative design is `docs/plans/nested-module-behavior.md`.
- Keep the live canonical repository entirely v4 until Task 10; mixed live v4/v5 repositories are invalid.
- Develop v5 under `references/blueprint/v5/`; preserve a complete immutable converter-owned v4 bundle under `references/blueprint/migrations/v4/`.
- Registration must match nearest physical containment; deepest registered module ownership precedes behavioral-source ownership.
- Child exports are the authority ceiling. Namespace routes and facades may narrow access but never widen it or expose private source interfaces.
- A facade preserves a parent interface ID and the original caller while deriving contract, binding, and interface version from one exact `_rtx` child export.
- Bare caller IDs are global; leading-dot caller references resolve to one exact module through the certified registration tree.
- Graph validation, projection, dispatch, tracing, and currentness must consume one authorization result.
- Every repository-managed skill has exactly one non-discoverable `<skill-id>-rtx` child rooted at `_rtx/`; no v5 modules occur below `_rtx`.
- Preserve interface-contract versions when only ownership or qualified IDs move. Node versions and interface versions are separate maps.
- Preserve canonical v1 certificate histories and keys; emit only payload v2 after cutover.
- Use test-first red/green cycles for every behavior change. Each task ends at a passing focused and regression sanity gate.
- Do not stage, commit, push, append certificates, or alter signing material in the live repository without separate user authorization. Disposable isolated migration candidates may use their own Git index and commits for exact validation.

---

### Task 1: Install the shadow v5 schema contract and fixtures

**Files:**
- Create: `references/blueprint/v5/schema.json`
- Create: `references/blueprint/v5/module.schema.json`
- Create: `references/blueprint/v5/behavioral-source.schema.json`
- Create: `references/blueprint/v5/common.schema.json`
- Create: `references/blueprint/v5/caller-contract.schema.json`
- Create: `references/blueprint/v5/direct-io.schema.json`
- Create: `references/blueprint/v5/certificate.schema.json`
- Create: `references/blueprint/v5/interface-projection.schema.json`
- Create: `references/blueprint/v5/pooled-review.schema.json`
- Create: `references/blueprint/v5/schema-meta.json`
- Create: `references/blueprint/v5/schema.annotated-draft.json`
- Create: `references/blueprint/v5/template.yaml`
- Create: `references/blueprint/v5/README.md`
- Create immutable v4 copies: `references/blueprint/migrations/v4/schema.json`, `module.schema.json`, `behavioral-source.schema.json`, `common.schema.json`, `caller-contract.schema.json`, `direct-io.schema.json`, `certificate.schema.json`, `interface-projection.schema.json`, `pooled-review.schema.json`, `schema-meta.json`, `schema.annotated-draft.json`, and `template.yaml`
- Create: `references/skill-standards/skill-guidelines.v2.candidate.yaml`
- Create: `references/skill-standards/skill-guidelines.v2.candidate.md`
- Create: `tests/fixtures/blueprint_v5/`
- Create: `tests/test_nested_module_v5_schemas.py`
- Modify: `tests/test_blueprint_schema_metadata.py`
- Modify: `tests/test_migrated_standards_fidelity.py`

**Interfaces:**
- Produces: a closed v5 module schema with `children`, `namespace_exports`, source/facade export `oneOf`, and exact/relative caller references.
- Produces: payload-v1 historical plus payload-v2 current certificate validation.
- Produces: noncanonical standard v2.0.0 revision 1 and its deterministic rendered view.
- Preserves: the canonical `references/blueprint/*.schema.json` v4 contract.

- [ ] **Step 1: Write failing contract tests** for valid parent/child/facade documents; rejection of mixed export forms, repository-root child locators, malformed relative callers, invalid surfaces, and missing explicit topology fields; and a v2 standard that replaces live-v4 rules while preserving frozen historical fixtures.
- [ ] **Step 2: Run `python3 -m pytest -q -o pythonpath=src tests/test_nested_module_v5_schemas.py tests/test_blueprint_schema_metadata.py tests/test_migrated_standards_fidelity.py`** and confirm failures are caused by the absent v5 contract.
- [ ] **Step 3: Add the minimal shadow schemas, template, annotated entry point, metadata, frozen v4 bundle, and rendered shadow v2 standard** without changing canonical schema or standard selection.
- [ ] **Step 4: Run the focused tests** and require all v5 schema and metadata cases to pass.
- [ ] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_nested_module_v5_schemas.py tests/test_typed_blueprint_schemas.py tests/test_blueprint_schema_metadata.py tests/test_migrated_standards_fidelity.py` and `git diff --check`; require v5 tests green and all existing canonical v4 schema and standard tests unchanged.**

### Task 2: Extend inventory for explicit registered nesting

**Files:**
- Modify: `src/officina/common/blueprint_inventory.py`
- Modify: `src/officina/common/__init__.py`
- Modify: `tests/test_blueprint_inventory.py`
- Add fixtures under: `tests/fixtures/blueprint_v5/inventory/`

**Interfaces:**
- Produces: `collect_blueprints(repo_root, *, expected_schema_version: int = 4) -> BlueprintInventoryResult`.
- Produces: `BlueprintDocument.module_root`, with temporary read-only `owner_root` compatibility only where required by still-v4 consumers.
- Preserves: default v4 rejection of nested module roots.

- [ ] **Step 1: Write failing inventory tests** for registered nesting, unregistered markers, duplicate parents, cycles, wrong-nearest-parent registration, ignored paths, nested repositories, symlinks, and the derived `_rtx` ID exception.
- [ ] **Step 2: Run `python3 -m pytest -q -o pythonpath=src tests/test_blueprint_inventory.py`** and confirm the registered-nesting cases fail under the current flat inventory.
- [ ] **Step 3: Refactor the existing bounded marker walk into collect-then-reconcile behavior for `expected_schema_version=5`**, retaining existing ignore and filesystem-safety logic.
- [ ] **Step 4: Add `module_root`, retaining temporary read-only `owner_root` compatibility for consumers migrated in later tasks; do not add a second filesystem walker.**
- [ ] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_blueprint_inventory.py tests/test_officina_repository_paths.py`; require every new topology case and every existing v4/path-safety case to pass.**

### Task 3: Add v5 graph topology and the shared authorization resolver

**Files:**
- Create: `src/officina/common/blueprint_authorization.py`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/blueprint.yaml`
- Modify: `src/officina/common/blueprints/blueprint-graph.yaml`
- Modify: `src/officina/common/__init__.py`
- Modify: `tests/test_officina_blueprint_graph.py`
- Create: `tests/test_officina_blueprint_authorization.py`
- Add fixtures under: `tests/fixtures/blueprint_v5/authorization/`

**Interfaces:**
- Produces: `load_repository_blueprint_graph(repo_root, *, schema_root=None, expected_schema_version=4)`.
- Produces: `AuthorizationRequest(caller_module_id, caller_source_id, interface_id, version)`.
- Produces: one immutable authorization result containing the immediate caller, requested and terminal interfaces, implementing source, caller/target ancestry, LCA, crossed namespace gates, resolved callers, effective filters, decision and diagnostic, derived relations, and required certificates.
- Produces: `resolve_interface_authorization(graph, request) -> AuthorizationResult`.

- [ ] **Step 1: Write failing graph tests** for registered parents/children, deepest ownership, parent pruning, local `_rtx` segments, global child IDs, topology cycles, and parent/child authority overlap.
- [ ] **Step 2: Write failing authorization tests** covering self, parent, sibling, cross-branch, descendant-to-ancestor, unrelated, exact caller, relative caller, private target, facade self evaluation at both owners, explicit parent admission at the child, no implicit namespace route from a facade, and `all`/`only` namespace routes.
- [ ] **Step 3: Run the two focused test files** and verify the failures identify missing v5 topology and resolver behavior.
- [ ] **Step 4: Add v5 graph indexing and topology relations**, keeping v4 loading behavior unchanged by default.
- [ ] **Step 5: Implement the pure resolver and use it in the graph's relationship-validation pass.** Materialize every active routed interface and terminal-module hash, including `all` surfaces; emit the design's five topology/routing/facade relations and exact certificate requirements while preserving local parent hashing.
- [ ] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_officina_blueprint_graph.py tests/test_officina_blueprint_authorization.py tests/test_blueprint_inventory.py`; require no duplicate authorization implementation in the graph tests or fixtures.**

### Task 4: Migrate projection, search, templates, and generated interface views

**Files:**
- Modify: `src/officina/common/interface_projection.py`
- Modify: `src/officina/common/blueprint_template.py`
- Modify: `src/officina/blueprint_search.py`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `tests/test_interface_projection.py`
- Modify: `tests/test_blueprint_search.py`
- Modify: `tests/test_officina_blueprint_template.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`

**Interfaces:**
- Consumes: Task 3 graph and `AuthorizationResult`.
- Produces: facade projections that derive child contracts without copying them.
- Produces: search results keyed by global node ID and registered ancestry.
- Preserves: parent-only `SKILL.md` generated blocks and skill discovery.

- [ ] **Step 1: Write failing tests** for facade projection, helper closure through facades, child ancestry search, parent-only generated blocks, hidden `_rtx` discovery, and child runtime-dependency inclusion.
- [ ] **Step 2: Run the focused projection/search/template/syncer tests** and confirm failures arise from flat-root and source-export assumptions.
- [ ] **Step 3: Replace direct export/path assumptions with graph and resolver results** while retaining existing size, definition, digest, and generated-view checks. The existing generator must create the parent blueprint, `_rtx/blueprint.yaml`, and `_rtx/__init__.py` together.
- [ ] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_interface_projection.py tests/test_blueprint_search.py tests/test_officina_blueprint_template.py skills/skill-maker/tests/test_blueprint_tools.py`; require v4 projections and generated views to remain byte-stable.**

### Task 5: Make dispatcher and Python runtime module-root aware

**Files:**
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/dispatcher/cli.py`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `src/officina/runtime/python_machine_interface_runner.py`
- Modify: `tests/test_officina_dispatcher.py`
- Modify: `tests/test_dispatcher_route_smoke.py`
- Modify: `tests/test_officina_python_machine_interface.py`

**Interfaces:**
- Consumes: Task 3 `AuthorizationResult`.
- Produces: internal `caller_module_id` and `target_module_id` metadata.
- Produces: Python binding fields for physical root/path, collision-free logical package, and logical entrypoint.
- Preserves: host-facing `--caller-skill` for discoverable parent calls and physical `__file__`/compile filenames.

- [ ] **Step 1: Write failing dispatcher tests** for deepest caller attribution, parent versus `<skill-id>-rtx` callers, facade admission, direct child admission, and global target IDs.
- [ ] **Step 2: Write failing runtime tests** for two `_rtx` packages in one trace, relative imports, descriptor/snapshot parity, hostile cache state, physical `__file__`, and sibling-resource lookup.
- [ ] **Step 3: Run the focused tests** and confirm current parent-root path and `_rtx` basename assumptions cause the expected failures.
- [ ] **Step 4: Route admission through the shared resolver and migrate internal metadata to module IDs.**
- [ ] **Step 5: Carry and load distinct physical/logical entrypoint identities**, preserving existing confinement, snapshot, fallback, and tracing behavior.
- [ ] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_officina_dispatcher.py tests/test_dispatcher_route_smoke.py tests/test_officina_python_machine_interface.py tests/test_process_binding_compiler.py`; require all existing v4 routes plus new child routes to pass.**

### Task 6: Extend certification, drift, and bootstrap for v5

**Files:**
- Modify: `src/officina/common/certificate_records.py`
- Modify: `src/officina/common/certification_hashing.py`
- Modify: `src/officina/common/certification_view.py`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: `skills/skill-drift/_rtx/_check_drift_state.py`
- Create shadow: `references/certification/certification-basis-roots.v5.json`
- Modify: `tests/test_officina_certificate_records.py`
- Modify: `tests/test_officina_certification_hashing.py`
- Modify: `tests/test_officina_certification_view.py`
- Modify: `tests/test_node_certification_hashing.py`
- Modify: `skills/skill-certifier/tests/test_certifier.py`
- Modify: `skills/skill-drift/tests/test_drift_check.py`

**Interfaces:**
- Consumes: Task 3 certification relations and required-certificate set.
- Produces: closed v1/v2 history reading, v2-only issuance, and v5 currentness.
- Produces a shadow/version-selected v5 registry: `v5-deterministic` v1, `route-smoke-dependencies` v2, `blueprint-accuracy` v2.
- Produces a shadow/version-selected v5 basis manifest, installed at the canonical unversioned path only in Task 10.

- [x] **Step 1: Write failing tests** for route/facade/topology proof edges, materialized `all` surfaces, local-hash stability, mixed v1/v2 histories, v1-stale-under-v5, v5 check registry, and validator-file basis coverage.
- [x] **Step 2: Write failing bootstrap tests** for the exact certifier route exception, recursive `certification_target_postorder`, migrated empty-prefix history, corrupt-history rejection, stable parent-level keys and secret namespace, sole `skill-certifier-rtx` mutation authority, and the read-only skill-maker synchronization fallback through its v5 facade.
- [x] **Step 3: Run focused certification tests** and confirm failures are limited to the absent v5 relations, payload, registry, basis, and bootstrap behavior.
- [x] **Step 4: Implement the v5 certificate/currentness delta** without changing v1 historical validation or ordinary append-only semantics.
- [x] **Step 5: Build the shadow basis manifest and cover every new enforcement input**, retaining the existing coverage test as the owner of completeness.
- [x] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_officina_certificate_records.py tests/test_officina_certification_hashing.py tests/test_officina_certification_view.py tests/test_node_certification_hashing.py skills/skill-certifier/tests/test_certifier.py skills/skill-drift/tests/test_drift_check.py`; require no certificate files or signing material to change.**

### Task 7: Prepare v5 validators, hooks, and repository tooling

**Files:**
- Modify: `validators/runner.py`
- Prepare cutover move: `skills/skill-maker/validators/` to `validators/skill/`
- Rename at cutover: `dispatch_caller_skill.py` to `dispatch_caller_module.py`
- Modify: `.githooks/skill/check-blueprints`
- Modify: `.githooks/skill/check-runtime-files`
- Modify: `validators/skill_runtime_files.py`
- Modify: `validators/cross_platform.py`
- Modify: `validators/platform_neutral.py`
- Modify: `validators/skill_runtime_doc_references.py`
- Modify before relocation: `skills/skill-maker/validators/boundaries.py`
- Modify before relocation: `skills/skill-maker/validators/dependencies.py`
- Modify before relocation: `skills/skill-maker/validators/skill_body_execution.py`
- Modify before relocation: `skills/skill-maker/validators/skill_md_dispatch.py`
- Modify: `scripts/run-python-tests.py`
- Modify: validator and runner tests under `tests/validate_*.py`, `tests/test_validator_runner.py`, and `tests/test_run_python_tests.py`

**Interfaces:**
- Consumes: Task 3 graph preflight and Task 4 generated-view behavior.
- Produces: one graph-preflight owner with a shared read-only validated graph for downstream checks.
- Preserves: the current validator runner, staged-index isolation, pre-commit dispatcher, and frozen v1 standard fixtures.

- [x] **Step 1: Write failing tests** for one graph load, nested topology diagnostics, `_rtx` non-executable artifacts, child-aware validators, parent-only prose/discovery checks, relocated validator discovery, and parent/child test roots.
- [x] **Step 2: Run focused validator tests** and verify the expected failures precede implementation.
- [x] **Step 3: Implement backward-compatible runner and validator changes** while leaving physical relocation for Task 10.
- [x] **Step 4: Update existing hook wrappers only where their current delegated validator ID changes.**
- [x] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_validator_runner.py tests/test_run_python_tests.py tests/test_migrated_standards_fidelity.py tests/validate_blueprints.py tests/validate_blueprint_relationships.py tests/validate_interface_ids.py tests/validate_dispatch_caller_skill.py tests/validate_skill_runtime_files.py`; then run `python3 validators/runner.py` against a disposable candidate whose index exactly contains the tested changes; require canonical v4 standards and hooks still green.**

### Task 8: Build the deterministic v4-to-v5 converter

**Files:**
- Create: `src/officina/common/nested_module_migration.py`
- Create: `src/officina/common/migration_candidate.py`
- Modify: `src/officina/common/interface_injection_migration.py`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/blueprint.yaml`
- Create: `src/officina/common/blueprints/nested-module-migration.yaml`
- Create: `scripts/migrate-blueprints-v5.py`
- Create: `tests/test_nested_module_migration.py`
- Create fixtures under: `tests/fixtures/nested_module_migration/`
- Modify: `validators/runner.py`
- Modify before relocation: `skills/skill-maker/validators/blueprints.py`
- Modify before relocation: `skills/skill-maker/validators/blueprint_relationships.py`
- Modify before relocation: `skills/skill-maker/validators/dependencies.py`
- Modify before relocation: `skills/skill-maker/validators/interface_ids.py`
- Modify before relocation: `skills/skill-maker/validators/skill_md_dispatch.py`

**Interfaces:**
- Consumes: frozen v4 bundle from Task 1 and v5 loader from Tasks 2-3.
- Produces: `build_nested_module_migration(repo_root) -> NestedModuleMigration`.
- Produces: a deterministic isolated candidate commit, dry-run rendering, exact Git cutover manifest, and post-write validation inside the candidate.
- Produces separate node-version, interface-version, access, identity, path,
  import, authority-disposition, and history maps.
- Produces a complete committed-file disposition map, rejects nonempty
  `unclassified_files`, and records hash-bound non-Git state operations for
  ignored histories that must leave active discovery at authorized cutover.

- [x] **Step 1: Write failing converter tests** for one code-bearing skill, one instruction-only skill, the exact repository-skill predicate and partial-combination failures, `.system` exclusion, mandatory `_rtx/__init__.py`, parent/facade/child access, every access/version/history rule in design §§5.1–5.3, path rebasing, relative imports, validator relocation, archive exclusion, and complete-file hashes.
- [x] **Step 2: Write failing safety tests** for ambiguous ownership, unresolved imports, private facade targets, overlapping authority, corrupt histories, candidate overwrite attempts, idempotence, and dry-run/candidate manifest equality.
- [x] **Step 3: Run `python3 -m pytest -q -o pythonpath=src tests/test_nested_module_migration.py`** and verify failures arise from the missing converter.
- [x] **Step 4: Implement the smallest pure planning/mapping layer**, reusing the existing migration owner's isolated-candidate, canonical-serialization, manifest, and Git evidence mechanisms.
- [x] **Step 5: Materialize only into a disposable isolated candidate after dry-run output is complete; do not add a live-tree writer or a second rollback engine.**
- [x] **Sanity gate: run `python3 -m pytest -q -o pythonpath=src tests/test_nested_module_migration.py tests/test_interface_injection_migration.py`; run the new CLI twice in dry-run mode on its fixture and require byte-identical manifests and no worktree changes.**

### Task 9: Rehearse the complete repository migration without cutover

**Files:**
- Modify: `tests/test_nested_module_migration.py`
- No live blueprint writes or checked-in narrative manifest.

**Interfaces:**
- Consumes: Tasks 1-8.
- Produces: a reviewed full-repository manifest bound to one complete migrated candidate commit/tree.

- [x] **Step 1: Add a repository-inventory assertion** for exactly 19 existing `_rtx` roots, 16 minimal children, all non-skill nodes, every ID/version/access rewrite, and zero output collisions.
- [x] **Step 2: Run the converter in full-repository dry-run mode** and inspect every unexpected move, unclassified file, native import, authority overlap, access expansion, and pin rewrite.
- [x] **Step 3: Materialize the migration into an isolated temporary Git checkout**, never the working repository; regenerate canonical views and manifests there; then stage and commit the complete candidate and run the explicit v5 graph/validator/test entry points against that exact index/tree.
- [x] **Step 4: Resolve every converter or design defect through a failing regression test before changing converter code.**
- [x] **Sanity gate: require a deterministic second dry run, zero unclassified files, zero live-tree writes, a valid all-v5 temporary graph, passing focused v5 tests, and exact live index/worktree equality before versus after the rehearsal.**

### Task 10: Install and verify the exact reviewed canonical v5 cutover

**Files:**
- Replace canonical schema family under: `references/blueprint/`
- Replace canonical standard: `references/skill-standards/skill-guidelines.standard.yaml`
- Regenerate: `references/skill-standards/skill-guidelines.md`
- Install canonical certification basis: `references/certification/certification-basis-roots.json`
- Remove superseded basis: `skills/skill-drift/references/certification-basis-roots.json`
- Move: `skills/skill-maker/validators/` to `validators/skill/`
- Migrate: every repository-managed `skills/*/blueprint.yaml`, source blueprint, `_rtx` child, code-owned file, test, authority, dependency, and export selected by the reviewed Task 9 manifest
- Modify: graph-owned blueprints/runtime dependency manifests for every changed implementation file
- Modify: `docs/architecture.md`
- Modify: `docs/skill-blueprints.md`
- Modify: `docs/certification_and_drift.md`
- Modify: `docs/blueprint_search.md`
- Modify: `README.md`
- Modify: `TESTING.md`
- Modify: `docs/contributors/README.md`
- Modify: `skills/skill-maker/SKILL.md`
- Modify: `skills/skill-certifier/SKILL.md`
- Modify: `skills/skill-drift/README.md`

**Interfaces:**
- Consumes: the reviewed Task 9 manifest; no hand-written migration deviations.
- Produces: one canonical all-v5 repository with 35 registered `_rtx` children and zero v4 authoring entry points.

- [ ] **Step 1: Stop for explicit cutover authorization, then re-run the Task 9 dry run and require the reviewed candidate commit/tree and manifest hash** before any live write.
- [x] **Step 2: Install that exact reviewed candidate through Git**, including schema/standard selection, validator relocation, all node/interface/access/path/import/history mappings, and generated artifacts. Switch canonical loader defaults to v5, remove temporary `owner_root`, and forbid mixed live authoring. Retain explicit v4 parsing only for the frozen converter, migration fixtures, historical regression tests, and compatibility checks that request the frozen schema family directly.
- [x] **Step 3: Run exact-reference searches** for live v4 schema instructions, old validator paths/names, the superseded certification-basis path, parent-root `_rtx/*.py` bindings, old moved source IDs, and unregistered nested markers; classify only immutable migration evidence as allowed.
- [ ] **Step 4: Re-run canonical view and runtime-dependency generation in check-only mode and require zero diff from the reviewed candidate.**
- [ ] **Step 5: Run the full Python suite, staged-index validators, pre-commit hook, converter idempotence, live-v4 reference search, and unintended-change review against the exact cutover index/tree.**
- [ ] **Step 6: With the separately authorized clean cutover commit in place, certify it in canonical dependency-first order and verify post-write currentness; do not alter signing material.**
- [ ] **Sanity gate: require all tests and validators green, an empty post-cutover migration plan, zero live v4 nodes or authoring references outside the frozen converter/explicit compatibility tests, a clean `git diff --check`, no unrelated changes, and verified current certificates for the cutover commit.**

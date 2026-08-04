# Direct-Blueprint Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace snapshot/catalog dispatch with direct, route-local blueprint lookup and authorization using canonical dotted module IDs and repository-owned `officina.toml` roots.

**Architecture:** Keep the existing v5 implementation operational while new v6 schemas and a direct resolver are built against synthetic fixtures. Then migrate the repository in one checked cutover, switch CLI/runtime launch to v6 direct resolution, and delete routing snapshots, catalogs, synchronizer coupling, facades, and ambient root discovery. Offline graph, certification, projection, and validation tooling remain comprehensive but never run on the dispatcher path.

**Tech Stack:** Python 3.11+, PyYAML with LibYAML `CSafeLoader`, stdlib `tomllib` through `officina.common.toml_io`, JSON Schema, pytest, existing Officina process-binding/runtime APIs.

## Global Constraints

- Dispatcher only routes, checks permission, compiles the selected binding, and launches it.
- Dispatcher performs no repository walk, graph build, synchronization, repair, Git operation, hash derivation, network call, or routing write.
- Certification is advisory: missing, stale, expired, malformed, unknown, or unavailable status emits warnings and never denies an otherwise authorized call.
- Runtime lookup roots come only from the exact absolute `officina.toml` path supplied by the launcher/runtime context; `$AI`, cwd, and parent searches are forbidden.
- Dispatchable module IDs equal dotted directory paths beneath configured roots; direct children must be explicitly registered.
- Child exports use their own canonical IDs; facades and `surface.all` are removed.
- Authorization preserves existing ancestry, sibling, relative-caller, lowest-common-ancestor, and hop-local caller-replacement semantics.
- Runtime work is proportional to configured-root count plus caller/target depth, evaluated relative-reference paths, one source blueprint, and actually imported module files.
- Frozen migration schemas, historical standards fixtures, and historical certificates are not rewritten.
- Every production behavior change follows red-green-refactor; each task ends with focused green tests and a scoped commit.

---

### Task 1: Repository configuration boundary

**Files:**
- Create: `officina.toml`
- Create: `src/officina/common/repository_configuration.py`
- Modify: `src/officina/common/configuration.schema.json`
- Modify: `src/officina/common/blueprint.yaml`
- Create: `src/officina/common/blueprints/repository-configuration.yaml`
- Create: `tests/test_officina_repository_configuration.py`
- Modify: `tests/test_configuration_consumers.py`
- Modify: `tests/validate_toml_io_boundary.py`

**Interfaces:**
- Produces: `RepositoryConfiguration(schema_version: int, config_path: Path, repository_root: Path, module_roots: tuple[Path, ...])`.
- Produces: `load_repository_configuration(config_path: Path) -> RepositoryConfiguration`.
- Produces: `RepositoryConfigurationError(ValueError)` for all malformed, missing, escaping, duplicate, symlinked, or unsupported inputs.

- [ ] **Step 1: Write failing behavioral tests.** Cover the exact v1 document, alternate relative roots, unknown keys/version, empty/duplicate/absolute/escaping roots, symlink components, non-absolute config input, and independence from cwd and `$AI`. Assert returned absolute paths and stable error classes rather than source text.
- [ ] **Step 2: Run the tests and verify RED.** Run `env PYTHONPATH=src python3 -m pytest tests/test_officina_repository_configuration.py tests/test_configuration_consumers.py tests/validate_toml_io_boundary.py`; expected failure is the missing module/config family.
- [ ] **Step 3: Implement the minimal loader.** Parse only through `toml_io.open(config_path.parent, config_path.name)`, use `tomllib.loads`, reject all fields outside `schema_version` and `modules.roots`, and validate each path component without following symlinks.
- [ ] **Step 4: Add the central schema family and blueprint export.** Define the strict TOML-parsed mapping in `configuration.schema.json`, expose it through the common module, and keep dispatcher runtime validation free of `jsonschema` imports.
- [ ] **Step 5: Run focused tests and refactor.** Require all Task 1 tests green and `git diff --check` clean.
- [ ] **Step 6: Commit.** Stage only Task 1 files and commit `feat(config): add repository module roots`.

### Task 2: V6 identity, child registration, and namespace schema

**Files:**
- Create: `references/blueprint/migrations/v6/common.schema.json`
- Create: `references/blueprint/migrations/v6/module.schema.json`
- Create: `references/blueprint/migrations/v6/behavioral-source.schema.json`
- Create: `references/blueprint/migrations/v6/schema.json`
- Create: `references/blueprint/migrations/v6/schema-meta.json`
- Create: `references/blueprint/migrations/v6/template.yaml`
- Create: `tests/fixtures/blueprint_v6/direct-routing/`
- Create: `tests/test_direct_blueprint_v6_schemas.py`
- Modify: `tests/test_typed_blueprint_schemas.py`

**Interfaces:**
- Produces: v6 module IDs matching `segment(.segment)*`, with `.interface.` and `.source.` reserved as delimiters.
- Produces: `children: {<local-segment>: {}}`, no child locators, no `facade_interface`, and required nonempty `namespace_exports.<child>.surface.only`.
- Preserves: access objects, exact and leading-dot caller references, source exports, process bindings, authority, discovery, and dependency declarations.

- [ ] **Step 1: Write failing schema tests.** Use literal valid/invalid documents for dotted IDs, local child keys, mismatched descendant IDs, forbidden locators/facades/`all`, empty `only`, private source exposure, and exact interface version pins.
- [ ] **Step 2: Run and verify RED.** Run `env PYTHONPATH=src python3 -m pytest tests/test_direct_blueprint_v6_schemas.py tests/test_typed_blueprint_schemas.py`; expected failure is absent v6 schemas.
- [ ] **Step 3: Implement closed v6 schemas.** Copy only still-valid v5 definitions, replace identity/topology/export alternatives, and keep v5/frozen fixtures unchanged.
- [ ] **Step 4: Add representative three-level fixtures.** Include `root`, `root.alpha`, `root.alpha.leaf`, sibling `root.beta`, an unrelated caller, explicit namespace surfaces, relative callers `._rtx` and `..beta`, and one source binding.
- [ ] **Step 5: Run focused schema tests and commit.** Require green, then commit Task 2 files as `feat(blueprint): define direct-routing v6 schema`.

### Task 3: Direct module locator and route-local blueprint parser

**Files:**
- Create: `src/officina/dispatcher/direct_blueprints.py`
- Create: `tests/test_dispatcher_direct_blueprints.py`
- Modify: `src/officina/dispatcher/errors.py`

**Interfaces:**
- Consumes: `RepositoryConfiguration` from Task 1 and v6 fixtures from Task 2.
- Produces: `parse_interface_id(interface_id: str) -> tuple[str, str]`.
- Produces: `DirectModule(module_id: str, root: Path, blueprint_path: Path, declaration: Mapping[str, object])`.
- Produces: `DirectBlueprintRepository(configuration).load_module(module_id: str) -> DirectModule` and `.load_ancestry(module_id: str) -> tuple[DirectModule, ...]`.
- Produces: stable `InvocationError` codes for invalid IDs, zero/multiple root matches, missing registrations, schema-version mismatch, identity mismatch, unsafe paths, and malformed relevant YAML.

- [ ] **Step 1: Write failing locator tests.** Assert exact root probes, top-level collision rejection, dotted path derivation, registration at every hop, no directory enumeration, route-local malformed-state isolation, and rejection of symlink/non-regular blueprint paths.
- [ ] **Step 2: Run and verify RED.** Run `env PYTHONPATH=src python3 -m pytest tests/test_dispatcher_direct_blueprints.py`; expected failure is missing `direct_blueprints`.
- [ ] **Step 3: Implement the strict parser and locator.** Use `yaml.load(..., Loader=yaml.CSafeLoader)`, enforce v6 route-local structural fields without importing repository JSON Schema, memoize only within one resolver instance/invocation, and perform no writes.
- [ ] **Step 4: Add operation guards.** Patch `Path.iterdir`, `os.walk`, globbing, Git/subprocess, and graph-loading entry points to raise; prove direct lookup still succeeds.
- [ ] **Step 5: Run focused tests and commit.** Require green and commit `feat(dispatcher): resolve modules directly from blueprints`.

### Task 4: Direct authorization and process binding

**Files:**
- Create: `src/officina/dispatcher/direct_authorization.py`
- Create: `tests/test_dispatcher_direct_authorization.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/dispatcher/__init__.py`

**Interfaces:**
- Consumes: `DirectBlueprintRepository` and existing `compile_process_binding`.
- Produces: `resolve_direct_invocation(*, configuration, caller_module_id, interface_id, interface_version, argv, stdin_requested, certification_status=None) -> ResolvedInvocationMetadata`.
- Preserves: the access predicate `caller == owner or allow_all_modules or ancestry(caller) intersects resolved(allowed_callers)`.
- Preserves: target-side namespace gates below the caller/target LCA and hop-local namespace-owner replacement.

- [ ] **Step 1: Write failing authorization table tests.** Cover self, public, private, exact caller, allowed ancestor, descendant asymmetry, parent/child/sibling, unrelated/cross-branch, `._rtx`, `..beta`, missing namespace surface, version mismatch, interface-specific narrowing, and outsider-to-owner-to-child delegation.
- [ ] **Step 2: Verify RED.** Run `env PYTHONPATH=src python3 -m pytest tests/test_dispatcher_direct_authorization.py`; expected failure is absent direct authorization.
- [ ] **Step 3: Implement ancestry and relative-reference resolution.** Derive ancestry from IDs but verify each registration chain; calculate LCA; evaluate only crossed target gates; replace the hop-local caller only after each accepted gate.
- [ ] **Step 4: Implement terminal/source resolution.** Require the exact target in `surface.only`, evaluate terminal access, follow the terminal module's exact source locator, verify source/interface versions, and call the existing binding compiler.
- [ ] **Step 5: Implement advisory diagnostics.** Consult only an optional already-verified mapping for relevant route nodes; absent/incomplete data yields `certification-status-unavailable`, and every non-current status remains a warning.
- [ ] **Step 6: Run focused plus existing authorization tests and commit.** Run the new suite and `tests/test_officina_blueprint_authorization.py`; commit `feat(dispatcher): authorize direct blueprint routes`.

### Task 5: Explicit repository-config propagation and direct host dispatch

**Files:**
- Modify: `src/officina/install/runtime_pointer.py`
- Modify: `src/officina/install/resolvers/launch.py`
- Modify: `src/officina/install/launcher_entry.py`
- Modify: `src/officina/dispatcher/cli.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `tests/test_officina_runtime_pointer.py`
- Modify: `tests/test_officina_launcher_entry.py`
- Modify: `tests/test_officina_dispatcher.py`

**Interfaces:**
- Produces: runtime pointer schema v2 with absolute `repository_config` in addition to `release_id`, `runtime_source`, and `python_bin`.
- Produces: fixed launcher argument `--repository-config <absolute-path>` inserted before user arguments.
- Extends: `RuntimeDispatchContext(repository_config: Path | None)`; nested `DispatchCall` reuses it.
- Changes: host `_resolve_host_dispatch_metadata` calls `resolve_direct_invocation` and never falls back to `$AI`, cwd, repository parents, snapshots, catalogs, or graph construction.

- [ ] **Step 1: Write failing pointer and resolver tests.** Cover v2 serialization, missing/outside config rejection, fixed-argument injection, user override rejection, and parity between deployed resolver and `runtime_pointer.py`.
- [ ] **Step 2: Verify RED.** Run the three focused test modules; expected failures are schema-v1-only pointer and absent CLI argument.
- [ ] **Step 3: Implement pointer/resolver/CLI propagation.** Validate the config at activation, preserve it across load, inject it from the stable resolver, and require it in host CLI parsing without exposing it as target argv.
- [ ] **Step 4: Write and verify failing host/nested tests.** Prove cwd and `$AI` changes do not alter resolution; nested dispatch preserves the exact path; snapshot/catalog/graph functions patched to fail are untouched.
- [ ] **Step 5: Switch host resolution to the direct resolver.** Keep explicit graph injection only for offline/test APIs until Task 9 removes obsolete host branches.
- [ ] **Step 6: Run focused tests and commit.** Commit `feat(dispatcher): route hosts through repository config`.

### Task 6: Lazy confined Python imports

**Files:**
- Modify: `src/officina/runtime/python_machine_interface_runner.py`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `tests/test_officina_python_machine_interface.py`
- Modify: `tests/test_dispatcher_route_smoke.py`

**Interfaces:**
- Produces: a lazy `MetaPathFinder`/`Loader` for the synthetic package returned by `logical_python_package_name(module_id)`.
- Consumes: an opened, confined module root and logical entrypoint instead of a recursive package snapshot.
- Allows: module-local package/relative imports, stdlib, pinned Officina, and managed-environment third-party distributions.
- Rejects: repository modules outside the selected module, symlink/path escapes, non-regular files, flat sibling aliases, and gateway `sys.path` mutation.

- [ ] **Step 1: Write failing importer tests.** Exercise a multi-file package, lazy read counts, duplicate `_rtx` names under distinct synthetic packages, stdlib/PyYAML imports, symlink escapes, another repository module, and a file that mutates `sys.path`.
- [ ] **Step 2: Verify RED.** Run `env PYTHONPATH=src python3 -m pytest tests/test_officina_python_machine_interface.py`; expected failures show snapshot-only/eager behavior.
- [ ] **Step 3: Implement descriptor-safe lazy loading.** Resolve and open only requested `.py`/`__init__.py` files beneath the bound module root, execute the opened bytes, and preserve synthetic package identity and module-cache isolation.
- [ ] **Step 4: Remove runtime package-snapshot requirements.** Delete snapshot arguments from the direct runner path while retaining frozen decoding helpers only where historical fixtures still require them.
- [ ] **Step 5: Run importer and route-smoke tests, then commit.** Commit `refactor(runtime): load gateway packages lazily`.

### Task 7: Managed runtime artifact and dependency manifest v2

**Files:**
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
- Modify: `references/blueprint/runtime_dependencies.json`
- Modify: `src/officina/install/managed_runtime.py`
- Modify: `src/officina/install/blueprints/managed-runtime.yaml`
- Modify: `tests/test_officina_managed_runtime.py`
- Modify: `tests/test_install_lifecycle.py`
- Modify: `tests/test_officina_launcher_entry.py`

**Interfaces:**
- Produces: runtime-dependency manifest v2 keyed by canonical interface ID and grouped beneath each installed top-level skill.
- Aggregates: every executable interface owned by that skill or any registered descendant, regardless of namespace exposure.
- Produces: candidate build inputs for a pinned Officina wheel/core dependency set plus manifest-derived module dependencies.
- Activates only after wheel identity, source revision, `yaml.CSafeLoader`, repository configuration, and clean-environment `officina.dispatcher.cli` execution validate.

- [ ] **Step 1: Write failing v2 generation tests.** Include two descendants with the same local interface name, a private child-only PyYAML dependency, and a namespace-hidden child; assert no overwrite and complete aggregation.
- [ ] **Step 2: Verify RED.** Run the syncer and managed-runtime focused suites; expected failures expose v1 local-name keys and child omission.
- [ ] **Step 3: Implement v2 generation and consumption.** Change the syncer output atomically, reject v1 at the new installer boundary, deduplicate exact package specs, and keep the manifest free of routing/authorization facts.
- [ ] **Step 4: Write failing clean-release tests.** Build a candidate without checkout `PYTHONPATH`; assert it imports `officina.dispatcher.cli`, provides `CSafeLoader`, and preserves the prior pointer on any artifact/config validation failure.
- [ ] **Step 5: Build/install and verify the Officina wheel.** Record artifact/source identity in release metadata, install core dependencies before module dependencies, and activate the pointer only after the clean launch probe.
- [ ] **Step 6: Run focused tests and commit.** Commit `feat(install): build direct dispatcher runtime`.

### Task 8: Offline graph, projection, standards, and certification v6

**Files:**
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/blueprint_authorization.py`
- Modify: `src/officina/common/interface_projection.py`
- Modify: `src/officina/common/certification_hashing.py`
- Modify: `references/certification/certification-basis-roots.json`
- Modify: `references/node-standards/module.standard.yaml`
- Modify: `references/blueprint/schema.json`
- Modify: `references/blueprint/module.schema.json`
- Modify: `references/blueprint/common.schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `references/blueprint/template.yaml`
- Modify: `docs/architecture.md`
- Modify: `docs/skill-blueprints.md`
- Modify: relevant graph/projection/certification/standards tests under `tests/`

**Interfaces:**
- Makes v6 the sole live blueprint schema while retaining frozen v4/v5 migration artifacts.
- Removes facade and `all` graph relations; derives topology from canonical ID plus explicit registrations.
- Updates interface projections to publish canonical descendant IDs and explicit `only` surfaces.
- Adds new versioned certifier checks and assurance coverage; pre-v6 certificates evaluate stale/advisory.

- [ ] **Step 1: Write failing offline-consumer tests.** Cover v6 inventory, unregistered physical children, global runtime/offline ID collisions, namespace edges, projection output, removed facade relations, frozen-fixture immutability, and check-registry selection.
- [ ] **Step 2: Verify RED.** Run the focused graph, projection, schema, and certification tests and confirm failures identify v5-only assumptions.
- [ ] **Step 3: Implement v6 offline consumers.** Keep scans confined to explicit offline commands; use configured roots for runtime-module identity and existing marker inventory for offline nodes.
- [ ] **Step 4: Update normative standards and assurance mappings.** State direct identity, child registration, explicit surfaces, hop-local delegation, no facades, and warning-only certification; bump check versions and basis roots.
- [ ] **Step 5: Run focused standards and consumer suites and commit.** Commit `feat(blueprint): adopt direct-routing v6 conventions`.

### Task 9: Atomic live repository migration

**Files:**
- Create: `references/blueprint/migrations/v6/facade-cutover.json`
- Modify: all live `skills/*/blueprint.yaml`, registered child blueprints, and source blueprints selected by the cutover inventory
- Modify: all affected `SKILL.md` generated interface blocks
- Modify: affected nested `DispatchCall` declarations, scripts, hooks, recurring jobs, documentation, and tests
- Modify: affected `_rtx/*.py` flat sibling imports

**Interfaces:**
- Maps: `<skill>-rtx` to `<skill>._rtx` and every old facade interface to its canonical child interface.
- Removes: all live `facade_interface`, `<skill>-rtx` IDs, child locators, `surface.all`, and module-local `sys.path` mutation.
- Preserves: direct parent-owned exports and all existing effective authorization under hop-local v6 rules.

- [ ] **Step 1: Generate and inspect the cutover inventory offline.** The JSON records each old ID, new ID, and every repository consumer; it is migration evidence and is never read by dispatcher.
- [ ] **Step 2: Add failing completeness tests.** Assert every listed old ID has consumers rewritten, no unlisted old/facade form remains live, every child is registered by segment, and every namespace surface is explicit.
- [ ] **Step 3: Verify RED.** Run the migration/completeness suite and capture failures before rewriting live files.
- [ ] **Step 4: Apply the mechanical ID/topology rewrite.** Update blueprints, callers, generated contracts, docs, fixtures designated live, and runtime metadata; do not edit frozen historical fixtures.
- [ ] **Step 5: Rewrite flat imports package-relatively.** Replace module-local absolute sibling imports with relative imports and remove local `sys.path` edits.
- [ ] **Step 6: Regenerate projections and dependency manifest through their owning workflow.** Inspect the diff for access expansion, missing dependencies, or unplanned files.
- [ ] **Step 7: Run full schema, graph, projection, route-smoke, and migration suites.** Require green before committing `refactor(blueprint): migrate modules to dotted child ids`.

### Task 10: Remove snapshot, catalog, synchronizer, and repair routing

**Files:**
- Delete: `src/officina/install/dispatch_snapshot.py`
- Delete: `src/officina/install/dispatch_snapshot_builder.py`
- Delete: `tests/test_dispatcher_snapshot.py`
- Delete or reduce to non-routing compatibility only: `src/officina/dispatcher/catalog.py`
- Delete or replace: `tests/test_dispatcher_catalog.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/install/managed_runtime.py`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: related installer/syncer/dispatcher tests and blueprints

**Interfaces:**
- Host dispatch has one path: explicit config -> direct module lookup -> hop-local authorization -> exact source binding -> launch.
- Syncer and installer validate authored state but create no dispatch snapshot/catalog and expose no dispatcher repair path.

- [ ] **Step 1: Write failing forbidden-operation tests.** Replace snapshot/catalog/synchronizer/graph/Git/hash/network/write functions with hard failures and invoke first/repeated dry-runs and executions.
- [ ] **Step 2: Verify RED against remaining legacy calls.** Run dispatcher, install, and syncer focused tests.
- [ ] **Step 3: Delete legacy runtime state and callers.** Remove snapshot activation, route catalogs, negative expiry, repair messages, builder entry points, and synchronization coupling.
- [ ] **Step 4: Preserve only offline validation entry points.** Ensure blueprint synchronization never runs implicitly from dispatcher and missing/malformed relevant state returns a stable error.
- [ ] **Step 5: Run focused suites and commit.** Commit `refactor(dispatcher): remove generated routing state`.

### Task 11: Performance, documentation, and final verification

**Files:**
- Modify: `tests/test_dispatcher_route_smoke.py`
- Create: `tests/test_dispatcher_performance.py`
- Modify: `docs/superpowers/specs/2026-08-04-fast-dispatcher-design.md`
- Modify: `docs/superpowers/plans/2026-08-04-fast-dispatcher.md`
- Modify: any remaining live documentation selected by validation failures

**Interfaces:**
- Enforces: warm in-process median below 50 ms, fresh-process dry-run median below 100 ms, and p95 below 150 ms on the reference host.
- Enforces: read/probe counts independent of unrelated repository size and no first-route work beyond ordinary filesystem-cache variation.

- [ ] **Step 1: Write the performance and scaling tests.** Exercise one top-level route and one `_rtx` route; duplicate unrelated modules at increasing counts and assert identical relevant open/probe counts.
- [ ] **Step 2: Run benchmarks and record exact measurements.** Measure fresh processes, exclude gateway/external-service execution, and fail if thresholds are exceeded.
- [ ] **Step 3: Trace a representative route.** Prove no repository walk, glob, graph construction, snapshot/catalog read, Git, certification derivation, network, lock, or routing write.
- [ ] **Step 4: Update status and architecture documentation.** Mark the design implemented only after all checks pass; document exact `officina.toml`, dotted IDs, failure behavior, runtime installation, and advisory certification contracts.
- [ ] **Step 5: Run the complete repository gate.** Run `python3 scripts/run-python-tests.py`, all repository validators invoked by the commit hook, `git diff --check`, and the fresh-process benchmark.
- [ ] **Step 6: Audit final scope.** Confirm the main checkout's unrelated standards/docstring changes are untouched; confirm this worktree contains only planned dispatcher/v6 migration changes.
- [ ] **Step 7: Exercise the installed dispatcher interactively.** Run one real `--dry-run` and one harmless read-only interface through the installed stable launcher, plus one nested dispatch path. Verify the exact config path, authorization decisions/warnings, compiled argv, stdout/stderr, exit status, absence of writes, and wall-clock latency from observed behavior rather than test doubles.
- [ ] **Step 8: Run a fresh implementation audit.** Use `superpowers:requesting-code-review` to compare the complete diff, interactive results, and measured runtime behavior against every design invariant, current blueprint/node conventions, authorization fixtures, and launcher/install contracts. Classify concrete holes by severity and retain the audit evidence.
- [ ] **Step 9: Fix every confirmed hole through a new red-green cycle.** Add a behavior test that fails for each gap, implement the smallest correction, rerun focused suites, interactive smoke tests, benchmarks, and the complete repository gate, then repeat the audit until no blocking or material issue remains.
- [ ] **Step 10: Use verification-before-completion, stage exact files, and commit.** Commit `feat(dispatcher): complete direct blueprint routing`; do not push. Report final-ready only from the post-audit verification evidence.

# V6 Used-Interface Injection Cleanup Plan

> Implement only after separate authorization. Preserve unrelated dirty work; do not commit unless asked.

## Goal

Make `BEGIN BLUEPRINT INTERFACES` describe the interfaces directly declared by the skill gateway's `uses_interfaces`, then delete the obsolete contract/alternate-projection code and syncer-local pre-v6 compatibility.

## Minimal architecture

Reuse the existing validated `RepositoryBlueprintGraph`, `_host_gateway_source()`, and the current process/instruction renderer. Do not add a second parser, resolver, renderer, or validation owner.

The change has three implementation tasks:

1. correct interface injection;
2. delete machinery made obsolete by that projection;
3. remove backward-compatibility branches from the syncer only.

Generated files are measured, then updated once during final verification.

## Scope constraints

- V6 only for the syncer's public path.
- Direct gateway uses only; exclude descendant exports and transitive uses.
- Every rendered use includes `id@version`, its owning description, and existing invocation guidance.
- Preserve versionless dispatcher targets.
- Preserve frozen v4/v5 schemas, fixtures, and repository-wide migration validation.
- No general validator consolidation, standards refactor, graph redesign, projection-library redesign, or proactive namespace version bump.
- Change a blueprint description, marker consumer, or schema-metadata rule only when it directly describes deleted behavior.
- Before implementation, place the existing dirty prerequisite hunks for `blueprints_from_graph()` and `validate_sync_state()` under named ownership in a reviewed commit or patch. A clean worktree from `HEAD` alone is not a valid base.

## Three-dimensional budgets

- **D:** lines deleted without replacement.
- **N:** genuinely new lines.
- **M:** existing lines replaced in place, counted once per replacement pair.

Each table is a hard per-file ceiling. Stop before exceeding a cell.

## Task 1: Inject descriptions for declared gateway uses

**Goal:** Make the generated interface block contain the information an LLM needs to invoke every interface the gateway directly declares.

**How achieved:**

1. Add focused failing tests for a public process use, a private instruction use, an unused descendant export, a transitive non-use, missing description, deterministic order, and a zero-use gateway.
2. In `generated_interface_block()`, reuse `_host_gateway_source()` to select the gateway source.
3. Read that source's already-validated edges from `RepositoryBlueprintGraph.node_edges`.
4. Select only `uses-export` and `uses-private-interface` edges whose `source_id` is the gateway source.
5. Resolve each target with `graph.exports.get(target_id) or graph.source_interfaces.get(target_id)`.
6. Reuse the resolved `InterfaceExport.declaration` and the existing process/instruction rendering branches.
7. Render `target_id@required_version — description`; leave the dispatcher target versionless.
8. Raise `BlueprintError` for an unresolved edge or blank description. Do not reparse raw uses, versions, or authorization.
9. Always render exactly one interface block. For no uses, render `Used Interfaces: none`.
10. Run the focused syncer tests.

Relevant objects:

- `generated_interface_block()`
- `_host_gateway_source()`
- `RepositoryBlueprintGraph.node_edges`
- `BlueprintEdge.source_id`, `.relation`, `.target_id`, `.required_version`
- `RepositoryBlueprintGraph.exports`
- `RepositoryBlueprintGraph.source_interfaces`
- `InterfaceExport.declaration`

| File | D | N | M |
|---|---:|---:|---:|
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | 10 | 20 | 10 |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | 0 | 60 | 8 |

No blueprint, validator, dispatcher, hook, relocation, or generated `SKILL.md` file changes in this task.

## Task 2: Delete obsolete projection and contract machinery

**Goal:** Leave the compact interface block as the only generated Markdown projection and remove live dependencies on contract markers.

**How achieved:**

1. First delete the verified-unreachable alternate projection and its focused tests:
   - `generated_used_interfaces_block()`;
   - `sync_used_interfaces_block()`;
   - `plan_consumer_interface_updates()`;
   - `plan_projected_consumer_interface_updates()`;
   - `apply_consumer_interface_updates()`;
   - `CertificationView`;
   - `project_consumer_interfaces()`;
   - `atomic_replace_bytes()`;
   - their syncer-local `stat`, `yaml`, certification, projection, and atomic-file imports.
2. Stop if any live caller is found; do not activate or adapt that subsystem.
3. After those references are gone, delete `USED_INTERFACES_START/END`, `CONTRACT_START/END`, `module_discovery()`, `generated_contract_block()`, and `sync_contract_block()`.
4. Remove only the contract-first branch from `sync_interface_block()`; preserve interface replacement and frontmatter insertion.
5. Make `_validate_generated_markers()` validate exactly one interface-marker pair and no contract markers.
6. Remove contract alternatives from `validators.skill_md_body._GENERATED_BLOCK_RE` while preserving interface-block stripping.
7. Update only direct marker consumers:
   - the two managed-skill messages in `inject_dispatcher_context.py`;
   - `_compact_relocation._mechanical()` and its tests;
   - the dispatcher CLI docstring;
   - the pre-commit hook comments.
8. Rename the live `generated-contract-block` schema-metadata rule and its four descriptive fields to the surviving interface-block behavior. Do not change frozen v4/v5 metadata or tests.
9. Remove `common.interface.atomic-files` from the syncer source/interface declarations only if deletion leaves it unused; update those declarations and the skill-maker gateway text only where they explicitly promise contract or atomic projection.
10. Run syncer, marker, body, hook, relocation, and live schema-metadata tests. Do not mutate generated `SKILL.md` files yet.

Relevant objects:

- alternate-projection objects listed above
- `generated_contract_block()`
- `sync_contract_block()`
- `sync_interface_block()`
- `validators.skill.blueprints._validate_generated_markers()`
- `validators.skill_md_body._GENERATED_BLOCK_RE`
- `llmhooks.inject_dispatcher_context.DISPATCHER_CORE`
- `llmhooks.inject_dispatcher_context.CONTEXT_DISPATCHER_MISSING`
- `relocate_nodes._compact_relocation._mechanical()`

| File | D | N | M |
|---|---:|---:|---:|
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | 298 | 0 | 8 |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | 238 | 0 | 5 |
| `validators/skill/blueprints.py` | 7 | 0 | 3 |
| `tests/validate_blueprints.py` | 0 | 0 | 3 |
| `validators/skill_md_body.py` | 2 | 0 | 5 |
| `tests/validate_skill_body_execution.py` | 3 | 0 | 1 |
| `llmhooks/inject_dispatcher_context.py` | 0 | 0 | 2 |
| `skills/relocate-nodes/_rtx/_compact_relocation.py` | 0 | 0 | 1 |
| `skills/relocate-nodes/_rtx/tests/test_compact_relocation.py` | 0 | 12 | 0 |
| `skills/relocate-nodes/tests/test_relocate_nodes_contract.py` | 0 | 0 | 2 |
| `src/officina/dispatcher/cli.py` | 0 | 0 | 2 |
| `.githooks/skill/check-blueprints` | 0 | 0 | 2 |
| `references/blueprint-schema/schema-meta.json` | 0 | 0 | 5 |
| `skills/skill-maker/_rtx/blueprints/rtx-blueprint-syncer.yaml` | 6 | 1 | 12 |
| `skills/skill-maker/blueprints/gateway.yaml` | 0 | 0 | 2 |

Do not change general standards, fidelity maps, parent namespace blueprints, unrelated validators, or `src/officina/blueprints/projection.py`.

## Task 3: Remove syncer-local pre-v6 compatibility

**Goal:** Make the active syncer expose and execute one schema path: v6.

**How achieved:**

1. Fix `load_blueprints()` to `BLUEPRINT_SCHEMA_ROOT` and `expected_schema_version=6`; remove schema parameters and its migration-root branch.
2. Remove requested-version arguments and the v5 discovery filter from `blueprints_from_graph()`; require graph schema 6.
3. Replace the remaining runtime-manifest `_generated_export_binding()` call with direct v6 `export.declaration` and `export.source_node_id`, then delete the helper.
4. Remove `schema_version` from `validate_sync_state()`.
5. In `validators.skill.blueprints.validate_with_graph()`, call sync-state validation only when the loaded graph is v6. Preserve v5 graph/migration preflight validation.
6. Delete `--schema-version` from `Interface.build_parser()`, `Interface.run()`, and `run_sync()`.
7. Delete only syncer-specific legacy tests; retain shared graph migration fixtures and tests.
8. Run focused syncer and blueprint-validator tests.

Relevant objects:

- `load_blueprints()`
- `blueprints_from_graph()`
- `_generated_export_binding()`
- `generated_runtime_dependencies_manifest()`
- `validate_sync_state()`
- `Interface.build_parser()`
- `Interface.run()`
- `run_sync()`

| File | D | N | M |
|---|---:|---:|---:|
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | 60 | 5 | 14 |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | 105 | 2 | 7 |
| `validators/skill/blueprints.py` | 2 | 0 | 3 |
| `tests/validate_blueprints.py` | 2 | 0 | 4 |

No repository-wide schema retirement or migration-fixture rewrite is in scope.

## Task 4: Measure generated changes, apply once, and verify

**Goal:** Prove that only generated blocks/manifests change and that the corrected public interface is usable.

**How achieved:**

1. Run all focused tests from Tasks 1–3 before generated-file mutation.
2. Compute the complete expected `SKILL.md` diff read-only and record one D/N/M row for each of the 42 files.
3. Require aggregate D=1,071: 730 contract lines plus 341 existing interface lines. For each file, M=0 outside generated markers.
4. Check each exact N against the renderer:
   - zero uses: `5`;
   - process only: `8 + 2p + q`;
   - instruction only: `7 + i`;
   - mixed: `11 + 2p + i + q`;
   where `p` is process uses, `i` is instruction uses, and `q` is pattern-note lines.
5. Measure any runtime-dependency-manifest change caused by removal of `atomic-files`; require an exact reviewed D/N/M row before writing.
6. In one controlled generated-artifact apply, remove contract blocks and invoke the exact checkout's public sync interface once. Verify checkout and repository-config provenance first; do not substitute a private module or ambient installation.
7. Rerun the public interface with `--check`.
8. Verify representative process, instruction, and zero-use skills. Confirm descriptions and versions are present; unused descendants and transitive interfaces are absent.
9. Search tracked live implementation, current tests, skills, and live metadata for every obsolete marker, generator/sync function, planner/applier, certification/projection helper, and removed import named in Task 2.
10. Run blueprint, skill-Markdown, hook, relocation, live metadata, and focused repository checks, then `git diff --check`.
11. Compare the actual diff with every task budget and the measured per-generated-file rows.

Relevant objects:

- `Interface`
- `run_sync()`
- `sync_module()`
- `validate_sync_state()`
- `sync_runtime_dependencies_manifest()`

| File | D | N | M |
|---|---:|---:|---:|
| Each of 42 tracked `skills/*/SKILL.md` files | exact measured current contract + interface lines | exact measured renderer output bounded by its formula | 0 outside generated markers |
| `references/blueprint-schema/runtime_dependencies.json` | exact measured removal, or 0 | exact measured replacement, or 0 | exact measured replacement, or 0 |
| Handwritten skill bodies | 0 | 0 | 0 |
| All other files | 0 | 0 | 0 |

The reviewed measurement table is a hard gate. Any unbudgeted file, handwritten-body edit, unexpected version cascade, or standards change stops the apply.

## Explicitly excluded

- General generated-content validator consolidation.
- `skill_md_dispatch` cleanup or renaming.
- Deletion of `skill_md_body.generated_interface_block()`.
- Broad standards, fidelity-map, or digest propagation.
- Proactive interface/module/namespace version bumps.
- Math-dependency-graph changes.
- Transactionality or concurrency redesign.
- Repository-wide removal of historical v4/v5 material.

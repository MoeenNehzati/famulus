# Fast Transactional Node Relocation Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Use `famulus:git-workflow` before any Git mutation. Do not commit without explicit user authorization.

**Goal:** Make registered-node relocation a seconds-scale, three-stage transaction: deterministic mechanical planning, bounded human/LLM Markdown augmentation, and one failure-atomic publication of the combined recipe.

**Architecture:** Replace field-name-based blueprint rewriting, global context replacement, repeated scans, and full shadow-tree materialization with a shared canonical inventory and an external content-addressed transaction. Mechanical and accepted editorial edits compile to exact byte patches. Validation reads a projected repository view; journaled publication provides rollback and crash recovery.

**Tech stack:** Python 3.11+, PyYAML, jsonschema, standard-library `ast`, `tomllib`, hashing, filesystem primitives, pytest, Officina blueprint inventory/graph APIs, dispatcher machine interface.

**Design:** `docs/superpowers/specs/2026-08-27-fast-transactional-node-relocation-design.md`

## Scope controls

- Preserve all unrelated dirty files, especially `skills/math-dependency-graph/**` and `src/officina/visualization/graph_specification.schema.json`.
- Treat the current uncommitted changes in `_relocation_engine.py`, `relocation.schema.json`, and `test_relocation_engine.py` as diagnostic evidence. Review each hunk before superseding it; do not silently discard or bundle it.
- Do not change blueprint schema versions. Add only non-validating relocation annotations.
- Do not rename `skill-certifier` or `skill-drift` until the transaction engine passes focused tests and timing gates.
- Use only the registered dispatcher interface for end-to-end relocation.

## Task 1: Freeze the transaction data contract

**Files:**

- Create: `skills/relocate-nodes/_rtx/schemas/transaction.schema.json`
- Create: `skills/relocate-nodes/_rtx/_relocation_transaction.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_transaction.py`
- Modify: `skills/relocate-nodes/_rtx/blueprints/rtx-relocate-nodes.yaml`

**Steps:**

1. Write failing tests for deterministic transaction IDs, repository-relative path confinement, baseline fingerprints, exact byte-patch validation, allowed state transitions, overlap rejection, and mechanically projected editorial coordinates.
2. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_transaction.py
   ```

   Expected: failures because the transaction model does not exist.
3. Define schema-versioned records for repository identity, relocation facts, baselines, moves, byte patches, review units, decisions, validation evidence, state, and journal metadata.
4. Implement canonical JSON serialization and content-addressed transaction IDs. Exclude mutable state, evidence, backups, and journal records from the planning digest.
5. Enforce path confinement, digest syntax, exact old-byte checks, deterministic ordering, identical-patch deduplication, and conflicting/overlapping-patch rejection.
6. Add the transaction schema and module to the registered source content list.
7. Rerun the focused test and require it to pass.

## Task 2: Make blueprint relocation schema-directed

**Files:**

- Modify: `references/blueprint-schema/common.schema.json`
- Modify: `references/blueprint-schema/module.schema.json`
- Modify: `references/blueprint-schema/behavioral-source.schema.json`
- Modify: `references/blueprint-schema/caller-contract.schema.json`
- Modify: `references/blueprint-schema/direct-io.schema.json`
- Modify: `references/blueprint-schema/interface-projection.schema.json`
- Create: `src/officina/blueprints/relocation.py`
- Create: `tests/test_blueprint_relocation_metadata.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_engine.py`
- Modify: `skills/relocate-nodes/_rtx/tests/test_relocation_engine.py`

**Steps:**

1. Write failing schema tests proving `x-officina-relocation` is discoverable on every current identity/path-bearing location and does not affect validation.
2. Add one synthetic annotated identity position in a test schema and require relocation discovery without a relocator code change.
3. Add non-validating annotations with address kind, scalar/list/key location, match rule, and relative/locator behavior.
4. Implement a schema walker that resolves local `$ref` entries, follows the instance and schema together, and emits typed instance locations.
5. Use PyYAML node marks to translate each location into an exact YAML scalar or mapping-key byte span. Preserve comments, anchors, quoting, ordering, whitespace, newlines, and unrelated values.
6. Replace `_relocation_engine.py` identity field-name sets with the schema-directed patch planner. Remove any diagnostic identity-key additions made obsolete by this path.
7. Run:

   ```console
   .venv/bin/pytest -q tests/test_blueprint_relocation_metadata.py skills/relocate-nodes/_rtx/tests/test_relocation_engine.py tests/test_typed_blueprint_schemas.py tests/test_blueprint_schema_metadata.py
   ```

8. Require byte-preservation tests and existing typed-schema tests to pass.

## Task 3: Build one canonical relocation inventory

**Files:**

- Create: `skills/relocate-nodes/_rtx/_relocation_inventory.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_inventory.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_addresses.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_engine.py`

**Steps:**

1. Write failing tests that count repository walks and blueprint parses and reject linked-worktree, `.git`, ignored cache/build, virtual-environment, generated-graph, runtime-log, and transaction-artifact traversal.
2. Define one immutable inventory containing selected source entries, blueprint documents, graph facts, package configuration, baseline path kinds/modes/digests, and explicit excluded boundaries.
3. Build it from configured roots and the existing Officina inventory/graph APIs. Load the repository graph once and derive descendant module, source, interface, export, namespace, ownership, and path mappings from it.
4. Rewire address derivation and planning to consume the shared inventory rather than walking or parsing independently.
5. Reject duplicate or malformed graph state before producing a review packet.
6. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_inventory.py skills/relocate-nodes/_rtx/tests/test_relocation_addresses.py
   ```

7. Require one-walk/one-parse counters to pass.

## Task 4: Derive Python moves and import patches mechanically

**Files:**

- Create: `skills/relocate-nodes/_rtx/_relocation_python.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_python.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_engine.py`
- Modify: `skills/relocate-nodes/_rtx/schemas/relocation.schema.json`

**Steps:**

1. Write failing tests for `package-dir`, package discovery roots, explicit packages, namespace packages, aliases, multiline imports, same-root and cross-root moves, relative imports, strings/comments, near-prefix names, unsupported packaging, and explicit overrides.
2. Derive old/new Python module prefixes from repository packaging configuration and moved paths.
3. Search candidates by the literal old prefix, parse each candidate once with `ast`, and convert absolute import node coordinates into byte patches.
4. Leave strings, comments, relative imports, and ambiguous packaging as review evidence or `ambiguous-mechanical-map`, never guessed edits.
5. Retain `python_modules` only as a narrow override for unsupported or ambiguous packaging.
6. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_python.py skills/relocate-nodes/_rtx/tests/test_relocation_engine.py
   ```

## Task 5: Parse mechanical Markdown and construct the human review packet

**Files:**

- Create: `skills/relocate-nodes/_rtx/_relocation_markdown.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_markdown.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_semantics.py`
- Modify: `skills/relocate-nodes/_rtx/tests/test_relocation_semantics.py`

**Steps:**

1. Write failing tests for frontmatter `name`, generated contract blocks, internal link destinations, dispatcher command tokens, complete typed code tokens, headings, prose, tables, comments, repeated blocks, long lines, duplicate names, and historical sections.
2. Implement a bounded structural Markdown scanner using byte offsets. Do not add a general Markdown-rendering dependency unless tests prove the standard-library scanner cannot preserve bytes.
3. Compile only the approved mechanical regions to patches. Classify all other retired-address occurrences by file, heading section, block type, and span.
4. Replace the per-mapping regex loop with one longest-first multi-pattern matcher over the selected inventory. Record exactly one residual pass.
5. Emit file-level review units by default and section-level units only for files marked `mixed`.
6. Detect non-UTF-8 and binary candidates without treating them as prose.
7. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_markdown.py skills/relocate-nodes/_rtx/tests/test_relocation_semantics.py
   ```

## Task 6: Compile human/LLM decisions into exact editorial patches

**Files:**

- Create: `skills/relocate-nodes/_rtx/_relocation_review.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_review.py`
- Modify: `skills/relocate-nodes/_rtx/schemas/transaction.schema.json`
- Modify: `skills/relocate-nodes/_rtx/schemas/relocation.schema.json`

**Steps:**

1. Write failing tests for whole-file `relevant`, `historical`, `irrelevant`, and `mixed` decisions; section subdivision; accepted/edited/rejected LLM proposals; stale section digests; and attempts to touch mechanical/generated regions.
2. Define schema-validated review decisions and bounded block proposals against mechanically projected content.
3. Compile accepted proposals into exact byte patches against the mechanically projected digest. Reject edits outside approved relevant units and incompatible overlaps with mechanical regions.
4. Preserve historical and irrelevant bodies byte-for-byte.
5. Add an explicit legacy `semantic_decisions` converter that produces exact patches. Remove execution by global enclosing-text replacement.
6. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_review.py skills/relocate-nodes/_rtx/tests/test_relocation_engine.py
   ```

## Task 7: Replace the full shadow tree with projected validation

**Files:**

- Create: `skills/relocate-nodes/_rtx/_relocation_projection.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_projection.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_closure.py`
- Modify: `skills/relocate-nodes/_rtx/tests/test_relocation_closure.py`

**Steps:**

1. Write failing tests that validate reads from projected bytes for moved, rewritten, generated, unchanged, deleted, and symlink entries while asserting zero full-tree copies.
2. Implement a read-only projection abstraction over the physical root plus transaction moves and patches.
3. Adapt affected-closure blueprint synchronization/validation to the projection. If an existing validator strictly requires `Path`, add the smallest adapter at its file-read boundary; do not materialize the repository.
4. Validate touched Python syntax, graph topology, source/interface resolution, namespace access, generated blocks, and residual functional addresses once.
5. Delete shadow materialization and full-tree snapshot/reconciliation paths after parity tests pass.
6. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_projection.py skills/relocate-nodes/_rtx/tests/test_relocation_closure.py
   ```

## Task 8: Implement journaled failure-atomic publication and recovery

**Files:**

- Create: `skills/relocate-nodes/_rtx/_relocation_publish.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_publish.py`
- Modify: `skills/relocate-nodes/_rtx/_relocation_engine.py`

**Steps:**

1. Write parameterized failure-injection tests before the first mutation and after every replace/move/delete/fsync operation.
2. Implement a repository relocation lock, relevant-state revalidation, confined sibling preparation, durable external journal, content/mode/symlink backups, ordered `os.replace`, directory fsync, rollback, and recovery detection.
3. Guarantee these observable outcomes: no writes on stale/validation failure; complete baseline restoration after ordinary publication failure; `recovery-required` after interrupted publication; deterministic complete-or-rollback recovery before accepting a new transaction.
4. Preserve journal and backups on recovery failure.
5. Remove the old per-file apply path once failure-injection coverage proves the replacement.
6. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests/test_relocation_publish.py
   ```

## Task 9: Expose the three-stage workflow through the existing interface

**Files:**

- Modify: `skills/relocate-nodes/_rtx/_relocate_nodes.py`
- Modify: `skills/relocate-nodes/_rtx/blueprints/rtx-relocate-nodes.yaml`
- Modify: `skills/relocate-nodes/blueprint.yaml`
- Modify: `skills/relocate-nodes/SKILL.md`
- Modify: `skills/relocate-nodes/tests/test_relocate_nodes_contract.py`
- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_workflow.py`

**Steps:**

1. Write failing contract tests for stage-1 planning to an external transaction, review-required output, accepted transaction application, stale rejection, recovery handling, and external-only transaction/report paths.
2. Keep the registered `relocate` export. Evolve arguments so stage 1 accepts a request manifest plus transaction path, while stage 3 accepts an existing accepted transaction plus `--apply`.
3. Ensure stages 1 and 2 perform no repository writes. Require explicit accepted state before `--apply`.
4. Update direct-I/O, consistency, mutation-safety, error, and caller-warning contracts to describe rollback/recovery instead of possible partial effects.
5. Update `SKILL.md` so the agent presents grouped file/section decisions, obtains human relevance classification, optionally requests bounded LLM proposals, writes accepted decisions to the bundle, and invokes stage 3 once.
6. Regenerate owned blueprint projections using the registered synchronizer only if the repository contract requires it.
7. Run:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/tests/test_relocate_nodes_contract.py skills/relocate-nodes/_rtx/tests/test_relocation_workflow.py
   ```

## Task 10: Enforce structural and wall-clock performance budgets

**Files:**

- Create: `skills/relocate-nodes/_rtx/tests/test_relocation_performance.py`
- Create: `skills/relocate-nodes/_rtx/tests/fixtures/performance/README.md`
- Modify: `skills/relocate-nodes/_rtx/blueprints/rtx-relocate-nodes.yaml`

**Steps:**

1. Generate deterministic in-test repositories with 100, 1,000, and 10,000 blueprints; do not check in large generated trees.
2. Instrument entries visited, bytes read, inventory passes, residual passes, YAML parses, Python parses, Markdown parses, and copied bytes.
3. Assert one inventory pass, one residual pass, at most one parse per candidate, and zero full-tree copied bytes.
4. Record cold/warm p50 and p95 separately for planning, review compilation, publication, and postflight.
5. Enforce the pinned-runner relative ceiling in CI-safe tests. Provide an explicit local benchmark selector for the current-repository 5-second p95 and 10-second cold gates.
6. Run the structural test on every focused suite; run wall-clock benchmarks on a stable runner before declaring the redesign complete.

## Task 11: Run the requested two-node rename as the acceptance transaction

**Files:**

- Transaction input outside repository: `/tmp/relocate-skill-nodes.yaml`
- Transaction bundle outside repository: `/tmp/relocate-skill-nodes.transaction.json`
- Repository files: only paths selected by the accepted transaction

**Steps:**

1. Run the complete focused suite:

   ```console
   .venv/bin/pytest -q skills/relocate-nodes/_rtx/tests skills/relocate-nodes/tests/test_relocate_nodes_contract.py tests/test_typed_blueprint_schemas.py tests/test_blueprint_schema_metadata.py
   ```

2. Run the registered dispatcher in stage 1 for both mappings in one request:

   ```text
   skill-certifier -> node-certifier
   skill-drift -> node-drift
   ```

3. Record mechanical planning time and verify it meets the stage-1 budget.
4. Present the grouped Markdown file/section packet to the human. Preserve excluded, irrelevant, and historical bodies; accept or edit only relevant proposals.
5. Compile the accepted combined transaction and run stage-3 preflight without writes.
6. Before publication, show exact touched paths and confirm that unrelated dirty files are absent. Obtain explicit authorization for the repository mutation if not already supplied for this exact transaction.
7. Publish once through the registered dispatcher.
8. Record stage-3 time and require the budget, clean recovery state, empty identical-transaction postflight, no unresolved functional retired addresses, and passing focused tests.
9. Report the rename separately from any diagnostic relocator edits. Do not stage or commit unrelated files.

## Final verification

Run the repository-owned focused checks for every modified source/blueprint path, followed by:

```console
.venv/bin/pytest -q skills/relocate-nodes/_rtx/tests skills/relocate-nodes/tests/test_relocate_nodes_contract.py tests/test_typed_blueprint_schemas.py tests/test_blueprint_schema_metadata.py tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py
```

Completion requires current evidence for correctness, rollback/recovery, structural scan budgets, wall-clock budgets, and the two-node acceptance relocation. Green tests alone do not waive any of those conditions.

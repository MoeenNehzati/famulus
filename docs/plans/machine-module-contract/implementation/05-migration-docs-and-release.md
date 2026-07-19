# Migration, Documentation, and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

**Goal:** Derive target v3 modules/exports from live Python interfaces, update
standards and generated views, certify injectable exports, and retire obsolete
blueprint authority.

**Architecture:** A deterministic migration inventory groups live gateways,
preserves public IDs, requires explicit collision/disposition maps, then
validates and regenerates all derived artifacts before obsolete declarations
are retired.

**Tech Stack:** Python migration tooling, YAML, schemas, pytest, skill-maker,
repository validators and hooks.

**Primary requirements:** `MIG-001`, `MIG-002`, `MIG-003`, `INJ-007`, all `DEF-*`
dispositions, and the zero-reference/removal gates for obsolete declarations.

## Preconditions and required reading

- Phases 1 through 4 are accepted and all target infrastructure gates pass.
- The user has explicitly authorized Phase 5. Passing Phases 1-4 does not
  authorize migration.
- Read `../IMPLEMENT.md`, the migration and deferral entries in
  `../01-decision-ledger.md`, and `Migration grouping` in
  `../03-inventory-graph-and-injection.md`.
- Read `../06-legacy-crosswalk.md` for the required disposition of retained,
  superseded, and deferred material.
- Read the migration and repository-completion rows/gates in
  `../05-verification-matrix.md` and the current
  `docs/plans/migrate_audit_to_certification.md` cutover tasks.

## Phase stop conditions

Stop if the migration inventory is incomplete, any public export lacks an
explicit disposition, current certification cannot be produced, or an
obsolete-declaration zero-reference proof fails. Do not suppress a
rule, certificate failure, or unrelated worktree conflict to complete release.

## Task 1: Inventory and migration map

**Files:**

- Create: `references/blueprint/machine-module-migration.yaml`
- Create: `src/officina/common/machine_module_migration.py`
- Test: `tests/test_machine_module_migration.py`

- [ ] Inventory every live Python interface from gateway code, focused tests,
  behavior sources, and skill content. Use existing blueprint declarations only
  as non-authoritative hints. Group by owner plus normalized gateway identity.
- [ ] Propose deterministic module IDs/paths and preserve every public interface
  ID/version. Require explicit map entries for collisions, intentional splits,
  and every injection disposition from Plan 3.
- [ ] Freeze every target path in
  `references/blueprint/machine-module-migration.yaml`; later tasks may mutate
  only paths listed there. Record exact `verification_paths` for every group.
  Report hint/new paths, module/export IDs, shared/local tools, ownership,
  helper edges, and behavior evidence without writing files.
- [ ] Map every legacy unprefixed, `$repo`, `$home`, `$tmp`, and absolute path
  root according to `MIG-003`; fail rather than preserve an untyped or
  undeclared absolute root.
- [ ] Add tests for deterministic ordering, grouping, collisions, incomplete
  dispositions, and public-ID preservation.

## Task 2: Migrate the two evidence-backed samples first

**Files:**

- Inspect as hints: `skills/skill-drift/_rtx/._check_drift_state.py.drift-status.blueprint.yaml`
- Inspect as hints: `skills/skill-drift/_rtx/._check_drift_state.py.compute-hashes.blueprint.yaml`
- Inspect as hints: `skills/email-triage/_rtx/._watermark_writer.py.blueprint.yaml`
- Derive from: `skills/skill-drift/_rtx/_check_drift_state.py`
- Derive from: `skills/email-triage/_rtx/_watermark_writer.py`
- Test: `skills/skill-drift/tests/test_drift_check.py`
- Test: `skills/skill-drift/tests/test_drift_hash.py`
- Test: `skills/email-triage/tests/test_watermark.py`
- Regenerate assessment outputs under `/tmp/interface-contract-reading-samples`

- [ ] Convert watermark behavior without strengthening its two-write order,
  concurrency, uncertain-completion, partial-effect, rollback, or verification
  claims.
- [ ] Convert `_check_drift_state.py` into one module exporting selected-skill,
  exact-root, and all-observed-skill hashing. Fix selectors and JSON output in
  each export rather than exposing mode/format flags.
- [ ] Preserve path/file/dir, syntax, symlink, flag/boolean, output/outcome, and
  direct-I/O evidence from completed sample Tasks 1-7.
- [ ] Validate both modules; run the three named sample tests plus
  `pytest tests/test_interface_conformance.py -q`; prove the historical
  shared-gateway validation error is gone.
- [ ] Regenerate full-blueprint, selected-YAML, one combined hook, and comparison
  files under `/tmp`; keep them non-authoritative.

## Task 3: Migrate remaining live interfaces and edges

**Files:**

- Modify only: target paths listed by the reviewed
  `references/blueprint/machine-module-migration.yaml`

- [ ] Group each gateway, move shared facts to the module, move `direct_io` and
  public facts to exports, and preserve ownership as private unless evidence
  proves intentional sharing.
- [ ] Put a direct tool at module scope only when every export requires it;
  otherwise keep it interface-local. Keep helpers only on the using export.
- [ ] Emit every target relationship with nested public IDs and exact versions.
  Do not create sibling/transitive authority.
- [ ] Apply the injection disposition report so every formerly exposed export
  is directly granted, deliberately uninjected, or retired.
- [ ] Run each group's checked `verification_paths` after that group and
  `.venv/bin/python tests/validate_blueprints.py` after every skill.

## Task 4: Update docs, standards, templates, and generated blocks

**Files:**

- Modify: `references/blueprint/README.md`
- Modify: `docs/skill-blueprints.md`
- Modify: `docs/certification_and_drift.md`
- Modify if its target payload changes:
  `docs/plans/migrate_audit_to_certification.md`
- Modify: `references/skill-standards/skill-guidelines.standard.yaml`
- Regenerate: `references/skill-standards/skill-guidelines.md`
- Modify: `src/officina/common/blueprint_template.py`
- Regenerate only: consumer gateway paths listed in
  `references/blueprint/machine-module-migration.yaml`

- [ ] Document module/export identity, version/certificate granularity,
  invocation scopes, simple-interface admissibility, direct-I/O/ownership,
  helpers, inventory, injection, and the tmp/log exception once in their
  canonical locations.
- [ ] Replace stale machine-interface/call-based language with target v3 module
  language. Keep role/kind/display metadata work deferred.
- [ ] Regenerate all consumer-local blocks and prove named LLM dependencies no
  longer accumulate in root `SKILL.md`.
- [ ] Generate the canonical rule index/standard view and verify no schema rule
  reference is unresolved.

## Task 5: Certify and gate injectable exports

- [ ] Run machine admissibility and conformance for every migrated module/export.
- [ ] Resolve every failed/checker-error finding; do not suppress rules to make
  migration pass.
- [ ] Run semantic certification, sign module certificates with per-export
  evidence, and verify injection accepts only current passing exports.
- [ ] Record operational-health failures separately from contract/certificate
  results.

## Task 6: Remove obsolete blueprint authority

**Files:**

- Remove after zero-reference proof: `references/blueprint/machine-interface.schema.json`
- Remove obsolete v2 and singular-prototype authoring branches after migration
- Modify: `references/blueprint/schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `references/blueprint/template.yaml`
- Modify: `tests/test_typed_blueprint_schemas.py`
- Modify: `tests/test_blueprint_schema_metadata.py`

- [ ] Search tracked files for obsolete `node_type: machine-interface`, call-based
  contract fields, stale schema paths, removed tags, and
  dispatcher consequences. Classify every remaining hit as history/test
  rejection or remove it.
- [ ] Prove the migration map has no pending entries and every module/export is
  reachable or explicitly allowed by the repository model.

## Task 7: Full release verification

- [ ] Rerun the exact Phase 1-4 gate commands and
  `pytest tests/test_machine_module_migration.py -q`.
- [ ] Run `.venv/bin/python validators/runner.py`.
- [ ] Run `bash .githooks/skill/check-blueprints`.
- [ ] Run skill-maker synchronization in check mode.
- [ ] Run canonical standards generation/checks.
- [ ] Run `bash .githooks/pre-commit`.
- [ ] Run `git diff --check`, `git status --short`, and exact-path diff review.
- [ ] Document any unrelated pre-existing failure with command and output; no
  historical shared-gateway exception is acceptable after migration.

## Phase completion evidence

Report the migration map and final dispositions, modules/exports migrated,
certificates produced, obsolete authority removed, documentation/generated
artifacts refreshed, every focused and repository-wide command with counts,
known unrelated failures, and exact final worktree scope. This is the package's
release handoff.

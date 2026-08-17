# Blueprint-aware Source Relocation Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable manifest-driven relocation engine and use the current `src/officina` reorganization as its one-pass acceptance case.

**Architecture:** A generic Python engine parses and validates a typed YAML manifest, projects all filesystem, reference, blueprint, package-catalog, and digest changes in memory, then publishes the complete change set only after validation. A repository-specific manifest describes the approved Officina relocation; a thin command invokes the engine in preflight or apply mode.

**Tech Stack:** Python 3.11+, PyYAML, pathlib, dataclasses, pytest, Officina blueprint schema v6.

## Execution Status

- [x] Typed manifest, projected change set, ownership transfer, catalogs, digest closure, and CLI implemented.
- [x] Officina acceptance manifest passes read-only preflight.
- [x] Temporary-copy application passed 409 focused tests.
- [x] Initial relocation application and one audited generated-block closure application completed.
- [x] Final real-tree verification: zero-change idempotency report, 410 focused tests, empty retired-address search, clean diff check, and 32/32 validators in a tracked-equivalent audit copy.
- [x] Within-module reuse trial: moved the standards extractor and its sidecar in a disposable repository copy, passed 15 focused tests, and reached zero-change idempotency.

## Global Constraints

- Mechanical relocation only; do not modify implementation bodies except address- and location-dependent lines.
- Do not create compatibility modules, aliases, or package import facades.
- Preserve unrelated dirty files byte-for-byte apart from declared address rewrites and directory relocation.
- Each declared change set is applied to the real worktree only after the same manifest passes in a temporary copy.
- Do not stage, commit, push, or delete useful source or long-form documentation.
- Typed declarations must distinguish paths, Python modules, source IDs, and interface IDs.
- Ownership transfers may derive blueprint structure but may not invent authority, dependencies, exports, or callers.

---

### Task 1: Manifest model and projected change set

**Files:**
- Create: `src/officina/refactor/__init__.py`
- Create: `src/officina/refactor/relocation.py`
- Create: `src/officina/refactor/relocation.schema.json`
- Test: `tests/test_officina_relocation.py`

**Interfaces:**
- Produces: `load_manifest(path: Path) -> RelocationManifest`, `plan_relocation(root: Path, manifest: RelocationManifest) -> ChangeSet`, and `ChangeSet.report() -> dict[str, object]`.

- [x] Add failing tests proving typed move, module, source, and interface declarations parse; malformed, escaping, duplicate, and ambiguous declarations fail before filesystem writes.
- [x] Define frozen manifest dataclasses and validate YAML against `relocation.schema.json` without accepting undeclared keys.
- [x] Implement an in-memory `ChangeSet` containing moves, writes, generated files, digest refreshes, unresolved references, and a stable JSON report.
- [x] Add tests proving preflight produces deterministic reports and leaves every input byte unchanged.

### Task 2: Blueprint ownership transfer

**Files:**
- Modify: `src/officina/refactor/relocation.py`
- Test: `tests/test_officina_relocation.py`

**Interfaces:**
- Consumes: `RelocationManifest`, `ChangeSet`.
- Produces: projected module-blueprint and behavioral-source updates through `plan_relocation`.

- [x] Add failing fixtures with an old module blueprint, target module blueprint, source sidecar, export, dependency, and caller authorization.
- [x] Implement first-class ownership transfer that removes old `content`/`sources`/`exports`, creates or updates the target declarations, retargets sidecar gateway/content/source/interface IDs, and rewrites repository `uses_interfaces` references.
- [x] Require all new callers and non-derivable identities in the manifest; reject missing, duplicate, or authority-broadening transfers.
- [x] Prove contracts, versions, direct I/O, dependencies, and unrelated YAML records remain semantically unchanged.

### Task 3: Reference rewriting, package catalogs, and digest closure

**Files:**
- Modify: `src/officina/refactor/relocation.py`
- Test: `tests/test_officina_relocation.py`

**Interfaces:**
- Consumes: typed renames and declared README packages.
- Produces: projected repository text rewrites, README-only initializers, and refreshed standard-import digests.

- [x] Add failing tests proving one typed rename derives dotted, slash-path, source-ID, and interface-ID substitutions without substring corruption.
- [x] Implement UTF-8 text discovery with explicit exclusions, exact exceptional rewrites with mandatory preconditions, and Python import closure checks.
- [x] Generate stable `__init__.py` catalogs that describe each directly owned file or child package and contain no executable facade code.
- [x] Refresh pinned standard-import SHA-256 digests to a fixed point and reject missing or cyclicly unstable references.

### Task 4: Atomic application and command

**Files:**
- Modify: `src/officina/refactor/relocation.py`
- Create: `scripts/relocate_officina_sources.py`
- Test: `tests/test_officina_relocation.py`

**Interfaces:**
- Produces: `apply_change_set(change_set: ChangeSet) -> None` and command options `--root`, `--manifest`, `--report`, and `--apply`.

- [x] Add failing tests proving a late validation failure performs zero writes and an accepted change set publishes each projected file exactly once.
- [x] Implement temporary-file publication for writes and ordered renames after complete projected-tree validation.
- [x] Make read-only preflight the default and emit the same stable JSON report for preflight and application.
- [x] Verify repeated preflight/application recognizes already-completed moves and produces no additional changes.

### Task 5: Officina relocation acceptance manifest

**Files:**
- Create: `refactors/officina-source-relocation.yaml`
- Modify: `scripts/relocate_officina_sources.py`
- Test: `tests/test_officina_source_relocation_manifest.py`

**Interfaces:**
- Consumes: the generic relocation command and manifest schema.
- Produces: the complete approved source-tree move, blueprint retargeting, dependency rewrites, package catalogs, and active-address closure.

- [x] Encode every approved old/new path, Python module, source/interface identity, ownership transfer, caller authorization, README package, active-text exclusion, and exact location-dependent rewrite.
- [x] Add an acceptance test that applies the manifest to a mixed-state fixture and asserts final paths, blueprint ownership, direct imports, package catalogs, and no retired active addresses.
- [x] Remove all repository-specific rules from the generic engine; they appear only in this manifest or the thin command default.
- [x] Run the manifest preflight against the live mixed-state worktree and inspect its JSON report.

### Task 6: Temporary-copy audit and real application

**Files:**
- Modify through the manifest only: declared `src/officina`, repository callers, tests, docs, blueprints, manifests, and pinned standards.
- Modify: `docs/superpowers/plans/2026-08-16-officina-source-relocation.md`
- Modify: `docs/superpowers/specs/2026-08-16-officina-source-organization-design.md`

**Interfaces:**
- Consumes: `refactors/officina-source-relocation.yaml`.
- Produces: the final organized source tree with direct implementation imports.

- [x] Copy the dirty working tree to a temporary audit directory while excluding `.git`, virtual environments, caches, and build outputs.
- [x] Run the relocation command once with `--apply` in the temporary copy.
- [x] Run relocation-engine tests plus focused standards, visualization, repository-check, validator, blueprint, certification, credential, configuration, controller, and Git-provenance tests in the temporary copy.
- [x] Inspect the temporary-copy report, retired-address scan, Python syntax, blueprint graph, README-only initializers, and `git diff --check` equivalent.
- [x] Run the audited manifest in the real worktree, then run one narrow manifest closure pass after the tracked audit exposed generated-block ordering.
- [x] Run the same focused verification and repository-supported broader checks; update both relocation documents' progress.
- [x] Report exact changed scope and unrelated pre-existing dirty files without staging or committing.

# Rutter Node Branch Master Merge Resolution Implementation Plan

> **For agentic workers:** Execute this plan in the active merge worktree. Do not replace the merge with a semantic reconstruction or a new worktree. Preserve the frozen branch tips and stop if the live merge state differs from this plan.

**Goal:** Resolve the active merge of `master` into `feat/rutter-node-entry-core`, validate the combined result, create one merge commit on the feature branch, then fast-forward `master` to that verified commit.

**Architecture:** The feature branch is authoritative for the Rutter node-entry redesign and the replacement math-dependency pipeline. Current `master` is authoritative for repository-wide infrastructure added after the branch diverged. Conflicts are resolved by composing those authorities at their explicit seams: newer master installer structure plus editable development installs, source Rutter structure plus target portability safeguards, and unions only where both registrations remain valid.

**Tech stack:** Git merge, Python 3, pytest, YAML blueprints, `repo_checks.py`.

**Specification:** The active merge state; `docs/plans/rutter-design/01-core-design.md`; `docs/plans/rutter-design/06-core-reimplementation-plan.md`; and the user decisions recorded below.

## Frozen state and non-negotiable decisions

- Command working directory: the root of the `feat/rutter-node-entry-core` worktree; run every command block below from that directory unless a step explicitly names the main worktree.
- Branch: `feat/rutter-node-entry-core`
- Frozen `master`: `a7cbdf86b966250c70bf74d2c745802751b01ae7`
- Frozen feature tip: `e62dcdae8da84d5ca0203cf43fe829375dcdb4dd`
- Merge base: `cdc3e23f21700518254076daa1050bfd974f2d40`
- The current `git merge --no-commit --no-ff master` remains active. Do not abort, reset, rebase, or start another merge.
- Project version remains `0.0.0`.
- The feature branch's deletions and replacement endpoint define truth for `skills/math-dependency-graph`.
- Development mode installs the repository editably; standard mode installs an immutable wheel.
- `docs/superpowers/**` may remain on disk but must not be tracked. The merged `.gitignore` rule is `docs/superpowers/**`.
- Preserve unrelated auto-merged changes from both branches.
- There can be no partial commits during an active merge. Create exactly one merge commit after all checks pass.

## Resolution matrix

| Conflict path | Resolution authority | Required result |
|---|---|---|
| `docs/plans/rutter-design/01-core-design.md` | feature | Keep the feature design. Remove markers; do not splice the older master design into it. |
| `docs/plans/rutter-design/06-core-reimplementation-plan.md` | feature | Keep the feature plan. It already contains master commit `04948108`'s portable main-worktree lookup. |
| `skills/install-assistant-tools/_rtx/_phase_entry.py` | master structure plus feature behavior | Start from master. Derive editable installation from `context.mode == "development"`; do not thread the older pre-context `dev_mode` through `apply()`. |
| `skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml` | master structure plus feature behavior | Preserve current schema/metadata and document development-mode forwarding. |
| `skills/install-assistant-tools/_rtx/tests/test_install.py` | master suite plus feature assertions | Preserve master dry-run semantics. Add editable assertions to non-dry-run candidate wiring: development passes true and standard passes false. |
| `skills/math-dependency-graph/_rtx/_tex_macro_reader.py` | feature deletion | Delete. |
| `skills/math-dependency-graph/_rtx/tests/test_extraction_phase_driver.py` | feature deletion | Delete. |
| `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py` | feature deletion | Delete. |
| `skills/math-dependency-graph/_rtx/tests/test_semantic_graph_compiler.py` | feature deletion | Delete. |
| `skills/math-dependency-graph/_rtx/tests/test_semantic_ir_validator.py` | feature replacement | Keep the feature semantic-validator suite. Do not merge in the renamed legacy batch-merger suite. Port master host/byte-stability fixes at the exact surviving replacement boundaries named in Task 2. |
| `src/officina/common/blueprint.yaml` | union | Keep both `math-dependency-graph._rtx` and `launchers` as allowed callers of `common.interface.atomic-files.access`, with all existing callers. |
| `src/officina/install/blueprints/managed-runtime.yaml` | master structure plus feature behavior | Preserve master contract; add optional `editable` boolean and require `repo_root` when true. |
| `src/officina/install/managed_runtime.py` | master structure plus feature behavior | Preserve master context/pointer/resolver machinery. Treat `InstallationContext.mode` as authoritative when supplied; reject an explicit contradictory editable value. Install `repo_root` with `--no-build-isolation --editable` only in development mode; otherwise install the wheel. Reject editable mode without `repo_root`. |
| `src/officina/rutter/blueprints/storage.yaml` | composed versions | Keep the source Rutter storage interfaces at version 2 and source contracts; use master `common.interface.atomic-files` version 2 for every dependency/call site. |
| `tests/test_officina_blueprint_graph.py` | feature for conflicted block | Resolve the incoming Rutter block to the feature's empty side because it asserts the obsolete four-file/interface-v1 model. Preserve all unrelated master tests outside the conflict. Equivalent redesigned coverage belongs in the feature-owned Rutter suite. |
| `tests/test_officina_managed_runtime.py` | master suite plus feature cases | Preserve master suite; add wheel/editable parameterized coverage and exact editable install argv. |
| `tests/test_rutter_storage.py` | feature deletion plus safeguard port | Delete the obsolete root test. Port master commit `c2c59f90`'s Windows rooted-path and `reparse point` safeguards to the equivalent owned suite under `src/officina/rutter/tests/`. |

## Task 1: Prove the live merge still matches the frozen state

Run:

```bash
git status --short
git rev-parse HEAD MERGE_HEAD
git diff --name-only --diff-filter=U
```

Expected: `HEAD` is the frozen feature tip, `MERGE_HEAD` is the frozen master tip, and the unresolved set is exactly the 17 paths in the matrix. Stop on any mismatch.

## Task 2: Resolve source-authoritative removals and untrack Superpowers records

1. Remove the four deleted math paths and obsolete root Rutter test from the index and worktree as merge resolutions.
2. Resolve `test_semantic_ir_validator.py` to the feature version. Do not restore legacy pipeline modules merely to retain their tests.
3. Port master's byte-stability fix at the surviving boundary by changing the source-packet fixture in `test_inventory_chunk_pooler.py` from `write_text(...)` to `write_bytes(...encode("utf-8"))`.
4. Port master's host-independent path comparison at the surviving boundary in `test_semantic_to_canonical_json.py`: compare `Path(report["out"]).resolve()` with `out_path.resolve()`.
5. Remove all `docs/superpowers/**` paths from the index while preserving the six feature records on disk. Confirm `.gitignore` hides them.

Verify:

```bash
git ls-files 'docs/superpowers/**'
git check-ignore -v docs/superpowers/specs/2026-08-25-dev-mode-live-python-runtime.md
git diff --name-only --diff-filter=U
```

Expected: the first command prints nothing; the second identifies `.gitignore`; none of the six resolved delete/replacement paths remain unresolved.

## Task 3: Resolve the two Rutter design documents

1. Use the feature content for both add/add conflicts.
2. Confirm `06-core-reimplementation-plan.md` retains its existing portable `MAIN=$(git worktree list --porcelain ...)` lookup from master commit `04948108`; no manual splice is expected.
3. Search both documents for conflict markers and stale hard-coded checkout commands.

Verify:

```bash
rg -n '^(<<<<<<<|=======|>>>>>>>)|/home/[^/]+/' docs/plans/rutter-design/01-core-design.md docs/plans/rutter-design/06-core-reimplementation-plan.md
```

Expected: no conflict markers; no executable instructions depend on the original checkout path.

## Task 4: Resolve blueprint registration and Rutter storage contracts

1. Union the atomic-files allowed callers in `src/officina/common/blueprint.yaml`.
2. Resolve all three storage blueprint conflict blocks to atomic-files version 2 plus Rutter storage interface version 2.
3. Preserve the feature descriptions, effects, and transaction guarantees unless a master assertion remains semantically valid for the redesigned graph.

Verify with the focused blueprint tests selected by repository tooling, plus direct YAML/relationship validation in Task 8.

## Task 5: Port the master Rutter safeguards into the owned feature tests

1. Keep the already auto-merged `or bool(path.anchor)` guard in `src/officina/rutter/storage.py`.
2. In `test_store_rejects_symlink_lock`, change only the lock-path expectation to accept `symbolic link|reparse point`; do not broaden `test_store_rejects_symlink_parent`.
3. Keep `test_confined_path_rejects_out_of_root_aliases`, whose `Path("/tmp/x.reckoning")` case exercises the rooted-relative safeguard on Windows. Add no duplicate test unless this case is removed during resolution.
4. Delete `tests/test_rutter_storage.py`; do not keep duplicate ownership.

Verify:

```bash
pytest -q src/officina/rutter/tests/test_rutter_storage.py
```

## Task 6: Adapt editable development installs onto the current master installer

For each of the six installer conflicts, use the current master side as the structural base and adapt the behavioral delta from feature commit `93ffe9b0` to master's `InstallationContext` architecture:

1. In `_build_managed_runtime_candidate`, derive `editable = context.mode == "development"` and pass it to the managed-runtime entry point. Keep `apply()` context-based; do not add a parallel `dev_mode` input.
2. Let the public managed-runtime entry point represent an unspecified editable value separately from explicit true/false. When an `installation_context` is supplied, derive the default from `context.mode` and reject an explicit mismatch. Without a context, preserve standard/wheel mode as the default.
3. Require `repo_root` for editable mode.
4. Install the repository with `--no-build-isolation --editable` in development mode; install the built wheel in standard mode.
5. Preserve master `InstallationContext`, pointer, doctor, assistant-access, resolver-generation, manifest, and state behavior.
6. Preserve master's `test_dev_mode_with_repo_path_chains_dev_link` as a dry-run/no-calls test. Put development `editable=True` coverage in a new non-dry-run `apply()` test; keep standard `editable=False` coverage in the existing candidate-wiring test.
7. Add `test_editable_candidate_requires_explicit_repo_root` and explicit standard/development context-mismatch tests.
8. If the pinned real `uv` is available, extend the gated integration coverage to assert the managed interpreter resolves `officina.__file__` beneath `<repo>/src/officina` in development mode. A skip remains a capability limitation, not passing product evidence.

Verify:

```bash
pytest -q tests/test_officina_managed_runtime.py skills/install-assistant-tools/_rtx/tests/test_install.py
```

## Task 7: Resolve root Rutter graph coverage and update the owned blueprint test

1. Resolve the conflicted Rutter insertion in `tests/test_officina_blueprint_graph.py` to the feature's empty side. Preserve every unrelated master test outside that block.
2. Update the clean feature-owned `src/officina/rutter/tests/test_blueprint_contract.py` for master changes that the merge did not flag:

   - replace the renamed schema location `references/blueprint` with `references/blueprint-schema`;
   - update the expected `common.interface.atomic-files` dependency version from 1 to 2;
   - preserve its redesigned 11-source graph and outcome-model assertions.

Verify:

```bash
pytest -q tests/test_officina_blueprint_graph.py src/officina/rutter/tests/test_blueprint_contract.py
```

## Task 8: Mechanical and focused integration verification

Run in this order and fix only failures caused by this merge:

```bash
git diff --check
git diff --cached --check
rg -n '^(<<<<<<<|=======|>>>>>>>)' .
git diff --name-only --diff-filter=U
pytest -q src/officina/rutter/tests tests/test_officina_blueprint_graph.py tests/test_officina_managed_runtime.py skills/install-assistant-tools/_rtx/tests/test_install.py skills/math-dependency-graph/_rtx/tests/test_semantic_ir_validator.py skills/math-dependency-graph/_rtx/tests/test_inventory_chunk_pooler.py skills/math-dependency-graph/_rtx/tests/test_semantic_to_canonical_json.py
```

Expected: no conflict markers, no unresolved paths, no whitespace errors, and focused tests pass.

Then verify release and tracking invariants:

```bash
rg -n '^version = "0.0.0"$' pyproject.toml
rg -n 'jsonschema>=4,<5' pyproject.toml
rg -n '"version": "0.0.0"' .claude-plugin/plugin.json .codex-plugin/plugin.json
git ls-files 'docs/superpowers/**'
git status --short
```

## Task 9: Run authoritative repository checks

Use `repo_checks.py` focused selectors first for the changed domains, then the staged pre-commit gate and full authoritative gate. Record any sandbox/tool failure separately from product failures. Do not weaken, skip, or rewrite checks to obtain green output.

First stage this plan explicitly after its audits are incorporated; all conflict resolutions should already have been staged path-by-path:

```bash
git add docs/plans/2026-08-26-rutter-node-master-merge-resolution.md
```

Then run:

```bash
./.githooks/pre-commit
./repo_checks.py --task tests:shared --repository-view staged --selector src/officina/rutter/tests/test_rutter_storage.py --selector src/officina/rutter/tests/test_blueprint_contract.py --selector tests/test_officina_blueprint_graph.py --selector tests/test_officina_managed_runtime.py --selector skills/install-assistant-tools/_rtx/tests/test_install.py --selector skills/math-dependency-graph/_rtx/tests/test_semantic_ir_validator.py --selector skills/math-dependency-graph/_rtx/tests/test_inventory_chunk_pooler.py --selector skills/math-dependency-graph/_rtx/tests/test_semantic_to_canonical_json.py
./repo_checks.py --suite precommit --repository-view staged
./repo_checks.py --suite full --repository-view staged
```

The actual hook comes first because it may regenerate and stage documentation or synchronize release manifests. The full gate may duplicate focused coverage; it is the final integration gate, not an investigation step.

Before committing, review:

```bash
git diff --cached --stat
git diff --cached --name-status
git diff --cached
git write-tree
```

Confirm that all 17 conflicts match the matrix and that unrelated auto-merges remain intact. Record the final `write-tree` hash as the reviewed and fully tested tree.

## Task 10: Commit the verified merge and integrate it into master

1. Create one merge commit on `feat/rutter-node-entry-core`; do not use `--no-verify` without separate explicit authorization.
2. Compare `git rev-parse 'HEAD^{tree}'` with the recorded reviewed tree hash. The commit-time hook should be idempotent. If the hashes differ, stop before touching `master`, inspect the hook-generated delta, and repeat the review and full gate on the committed tree.
3. Re-run the focused smoke checks against the merge commit.
4. Confirm the main worktree is still clean and `master` still equals the frozen target tip.
5. In the main worktree, fast-forward only: `git merge --ff-only feat/rutter-node-entry-core`.
6. Verify `master` and the feature branch resolve to the same merge commit, and inspect final status in both worktrees.

If `master` moved or the main worktree is dirty, stop before the fast-forward and report the exact mismatch.

## Audit checklist

- Every conflict path has one named authority and a testable expected result.
- Source-authoritative deletions are not silently resurrected through rename detection.
- Master infrastructure is not overwritten by older feature snapshots.
- Editable mode changes installation behavior only in development mode.
- The Rutter Windows safeguard survives relocation into the owned suite, and the owned blueprint test follows the renamed schema root and atomic-files v2.
- `docs/superpowers/**` is untracked and ignored, not necessarily deleted from disk.
- Version is exactly `0.0.0`.
- No merge commit is created before focused and authoritative verification.
- Integration into `master` is fast-forward-only from the verified feature merge commit.

# Deferred Fixes Master Integration Implementation Plan

> Execute each task sequentially in the clean integration worktree. Use a fresh
> high-reasoning implementation agent and an independent high-reasoning reviewer
> for each behavioral task. Do not proceed from a task until its focused checks
> and review are green.

**Goal:** Merge `codex/deferred-fixes` with the latest committed `master`, close
the confirmed contract and test-infrastructure defects, certify the exact final
content commit, and publish it without altering the dirty root checkout.

**Architecture:** The clean integration worktree is the only build and merge
surface. Each defect stays at its existing contract boundary: instruction prose
for Git authorization, one existing standards gateway plus its generated view,
and packaging-test fixtures for environmental isolation. Repository standards
change only if a later verified defect demonstrates a missing canonical rule.

**Verification:** Every behavioral correction begins with a failing focused test
or fresh-agent pressure scenario, then passes the same check after the minimum
patch. The full validator and pre-commit suites run after the merged range;
repository-wide certification and drift checking target the final content
commit, after which no tracked file changes are allowed.

**Review correction rule:** For Tasks 0–6, every reviewer-requested edit resets
the task gate: restage only that task's exact paths, rerun all task-specific
checks plus `validators/runner.py` against the staged candidate and
`git diff --cached --check`, then obtain the required review before committing.

---

## Task 0: Commit and reverify the execution documents

**Files:**

- Verify: `tests/test_docstrings_validator.py`
- Verify: `docs/superpowers/specs/2026-08-05-deferred-fixes-master-integration-design.md`
- Commit: `docs/superpowers/plans/2026-08-05-deferred-fixes-master-integration.md`

1. Confirm prerequisite commit `18faacb` contains only the approved nested-clone
   isolation fix and integration design.
2. Re-run the staged-docstring test normally and with an explicit hostile
   `GIT_INDEX_FILE`; retain the prior pre-fix failure as RED evidence and require
   both post-fix invocations plus the real commit hook to be green.
3. Stage only the design amendment and this implementation plan. Run
   `validators/runner.py`, `git diff --cached --check`, and independent plan
   review, then commit them before Task 1 so the worktree is clean.

## Task 1: Merge and inspect deferred history

**Files:**

- Merge: `codex/deferred-fixes`
- Inspect: `skills/recurring-tasks/_rtx/blueprint.yaml`
- Inspect: `skills/recurring-tasks/_rtx/blueprints/rtx-healthcheck-probe.yaml`
- Inspect: `skills/recurring-tasks/_rtx/blueprints/rtx-setup-runner.yaml`

1. Fetch `origin`; confirm the clean integration branch contains root `master`
   tip `ab7de8f`, record the current remote tip, and require no working-tree
   changes. If either committed tip advanced, integrate it before proceeding.
2. Merge `codex/deferred-fixes` with `--no-ff --no-commit`.
3. Resolve any textual conflict by preserving both the recurring-task independent
   failure reporting from `master` and the deferred node-contract/refactor work.
4. Compare each of the three overlapping blueprints against both parents. Check
   inputs, outputs, effects, failure modes, dependencies, and evidence rather
   than accepting an automatic textual merge on appearance alone.
5. Stage only the merge result. Run recurring-task focused tests,
   blueprint/interface projection checks, `validators/runner.py` against the
   staged candidate, `git diff --cached --check`, and committed-range
   `git diff --check`.
6. Obtain independent merge-resolution review, fix any finding, rerun the focused
   checks, then commit the merge.

## Task 2: Correct detached-HEAD authorization

**Files:**

- Modify: `skills/git-workflow/SKILL.md`
- Exercise: disposable Git repository created by the pressure-test agent

1. Before editing, give a fresh agent the current skill in a detached disposable
   repository and record the incorrect mutation or redundant-authorization
   behavior as RED evidence.
2. Change the instruction so detached-HEAD detection remains read-only: permit
   inspection, block edits until an exact checkout/create action is authorized,
   and proceed directly when that branch action was already authorized.
3. Repeat the identical pressure scenarios and require correct behavior.
4. Run skill validation and repository instruction-contract checks.
5. Stage only the named task paths; run `validators/runner.py` against the staged
   candidate and `git diff --cached --check`.
6. Obtain independent behavioral and scope review, correct findings, rerun, and
   commit only the Git workflow change and its evidence artifact if tracked.

## Task 3: Correct standards audit/update authority

**Files:**

- Modify: `skills/update-standards/SKILL.md`
- Modify: `skills/update-standards/blueprints/gateway.yaml`
- Modify: `tests/test_interface_projection.py`
- Synchronize: generated interface prose selected by the repository generator

1. Add public graph-contract tests proving: audit findings are effect-free;
   `updated` and `partial` are reachable only from an explicit update request;
   and prewrite scope expansion returns effect-free `needs-direction`.
2. Run those focused tests and record their pre-change failure.
3. Preserve the single public interface. Define audit/check/review as effect-free;
   authorize an explicit update only for the named semantic unit plus preflight-
   enumerated pinned dependents, registered views, and directly declared
   evidence/enforcement consequences; add the `audit-findings` outcome; retain
   `partial` only for expansion discovered after authorized writes.
4. Regenerate the gateway-owned interface block using the repository generator.
5. Run the focused tests, interface projection suite, skill validation, and
   fresh-agent audit/update/expansion pressure scenarios.
6. Stage only the named task paths; run `validators/runner.py` against the staged
   candidate and `git diff --cached --check`.
7. Obtain independent contract and standards-scope review. Do not modify a
   canonical standard unless the reviewer proves an uncovered standards defect.
   Fix findings, rerun, and commit the skill, gateway, tests, and generated view.

## Task 4: Reproduce and isolate packaging-test environment failures

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/tests/install_test_utils.py`
- Modify: packaging tests that invoke `install_minimum_scaffold`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py`

1. On the clean merged baseline, construct a controlled hostile environment for
   every packaging test that invokes the minimum scaffold: disable ambient DBus
   and native keyring access, and record the resulting subprocess-persistence
   failure as deterministic RED evidence. If no meaningful RED can be produced,
   stop for independent scope review rather than patching blindly.
2. Add a test-owned persistent keyring inside the temporary home,
   with private permissions and subprocess-stable storage. Ensure packaging
   subprocesses ignore ambient DBus variables. Keep the strict native-keyring
   roundtrip test unchanged and separate.
3. Re-run all affected local/copied-package tests with no usable DBus session.
4. Reproduce the live GitHub Claude installer resolver failure against the
   currently published branch. Align only its setup with the existing local v5
   test: minimum scaffold, real-`uv` guard, minimal managed runtime, and an
   assertion that execution reaches v5's expected `ModuleNotFoundError` rather
   than resolver `FileNotFoundError`.
5. Do not backport fast-dispatcher/v6 source-revision, wheel, or runtime changes.
6. Stage only the named test paths; run `validators/runner.py` against the staged
   candidate and `git diff --cached --check`.
7. Obtain independent test-contract review, fix findings, rerun affected tests,
   and create a separate test-only commit. Treat the GitHub-default test as a
   post-push health check, not a pre-push candidate gate.

## Task 5: Close stale documentation

**Files:**

- Modify: `docs/superpowers/plans/2026-08-05-refactor-lessons-and-five-skills.md`

1. Replace the stale sentence claiming the closure commit remains with a concise
   closed-state statement that does not refer to itself as future work.
2. Stage only the named document; run documentation validators,
   `validators/runner.py` against the staged candidate, and
   `git diff --cached --check`.
3. Obtain independent factual review and commit this documentation-only change.

## Task 6: Validate and review the complete content range

1. Confirm the worktree is clean and record the candidate commit.
2. Run `python3 validators/runner.py`.
3. Run `python3 scripts/run-python-tests.py --suite precommit`.
4. Run `python3 scripts/run-python-tests.py --suite full --verbose`.
5. Run any additional focused suites identified by Tasks 1–4 and a committed-
   range `git diff --check`.
6. Have an independent reviewer inspect the complete range from the historical
   merge base through the candidate commit for correctness, missed conflicts,
   scope creep, and unvalidated outputs.
7. Fix every blocking finding in a new content commit: stage only its exact
   correction paths, run staged-candidate validators and
   `git diff --cached --check`, then repeat precommit, the full suite, the
   committed-range check, and whole-range review until green. The last such
   commit becomes the final content commit.

## Task 7: Certify the exact final content commit

1. Record the final content commit and require a clean tracked worktree.
2. Invoke the public `skill-certifier` dispatcher interface repository-wide for
   that exact reviewed repository and commit.
3. Invoke the public `skill-drift` interface for the affected skill roots and
   confirm current certificates against the same commit.
4. Separate cache warnings from certificate/drift results. Do not edit tracked
   files after certification, and retain the integration worktree because its
   ignored certificate logs are local assurance records.

## Task 8: Preserve dirty work, promote master, and publish

1. Quiesce external writers to the dirty root checkout. Record its branch,
   `HEAD`, staged/unstaged/untracked inventory, raw index hash, binary patches,
   and a recoverable archive of dirty and untracked paths. Recheck the index and
   file hashes before changing refs.
2. Rename the root checkout's `master` to a unique `wip/master-dirty-*` branch,
   then, before `git status` or any other root-worktree Git inspection, verify
   both the raw index hash and the archived dirty/untracked file hash manifest.
   Unset that WIP branch's inherited upstream. Do not clean or reset it.
3. Rename the validated integration branch to `master`, set `origin/master` as
   upstream, fetch the remote again, and recheck the recorded root `master` tip.
4. If the root committed tip or `origin/master` advanced, reopen merge, full
   validation, review, and certification. Require
   `git merge-base --is-ancestor origin/master master` to succeed before pushing.
   Otherwise push ordinary `master:master` without force.
5. Verify the remote ref equals local `master` exactly.
6. Run the live GitHub installer health check against the newly published
   default branch. Classify any failure: report a verified external/environmental
   failure separately; a candidate defect reopens correction, full validation,
   review, certification, and publication before the result can be final.
7. Retain the WIP branch, deferred branch, dirty checkout, and final master
   worktree; cleanup requires separate authorization.

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

### Task 1 discovered prerequisites

The first no-commit merge was safely captured and aborted after current master's
whole-module staged-docstring validator found 726 real findings in ten Python
files: 687 on their first-parent versions and 39 added by deferred changes. The
following prerequisites run before repeating Task 1; the validator is not
weakened or bypassed.

First stage only this plan/spec addendum, run `validators/runner.py` against the
staged candidate and `git diff --cached --check`, obtain independent review, and
commit it. Task 1A then begins from a clean worktree and remains a single-file
commit.

#### Task 1A: Correct the healthcheck dependency declaration

**File:**

- Modify: `skills/recurring-tasks/_rtx/blueprints/rtx-healthcheck-probe.yaml`

1. Add the missing `rtx-run-record` dependency required by master's
   `read_latest_run_record` import; do not add the deferred `rtx-jobs-config`
   dependency before that implementation is merged.
2. Run recurring-task dependency, relationship, blueprint, and interface checks.
3. Stage only the blueprint, run staged validators and cached diff checks, obtain
   independent contract review, fix and repeat as required, then commit.

#### Task 1B: Remediate first-parent docstring debt

Before the per-file loop, complete Task 1B0. The useful first
`blueprint_graph.py` prose patch is held unstaged and preserved at
`/tmp/blueprint-graph-docstrings-pre-v31.patch` with SHA256
`5216a2681b97193a26a0b0ca4e80ba7ae86a384a9e8240c04669120eab57630e`;
do not commit or discard it until the corrected contract is available.

##### Task 1B0: Repair the docstring contract narrowly

**Files:**

- Modify: `references/standards/docstring.standard.yaml`
- Modify: `references/standards/docstring_format.schema.json`
- Delete: `references/standards/docstring_format.yaml`
- Modify: `src/officina/common/docstring/docstring_policy.py`
- Modify: `src/officina/validators/docstring_validator.py`
- Modify: `docs/docstring.md`
- Modify: `validators/standard_documents.py`
- Modify: `tests/test_docstring_schema_dynamic_sections.py`
- Modify: `tests/validate_standard_documents.py`
- Synchronize: pinned dependents and registered views discovered by the public
  `update-standards` workflow

1. Commit this reviewed plan/spec addendum alone before changing policy or
   enforcement. Immediately before that commit, require
   `src/officina/common/blueprint_graph.py` to be absent from the index, verify
   its diff still hashes to the recorded SHA256, and require
   `git apply --reverse --check` to prove the held patch can recover the exact
   working-tree change.
2. Add RED tests proving:
   - summary-only classes whose decorator resolves to stdlib
     `dataclasses.dataclass` (direct, imported alias, or qualified) and whose
     bodies contain only a docstring and annotated instance fields (including
     non-call defaults and resolved stdlib `dataclasses.field(...)`), and direct
     builtin-exception subclasses whose
     bodies contain only a docstring/pass/ellipsis, are accepted; spoofed
     dataclass decorators, extra decorators, methods, properties, descriptors,
     nested declarations, any other call-valued default, other executable class-
     body statements, project-derived exceptions, and ordinary empty classes
     retain the full profile;
   - local imports are visible in their callable, nested closures inherit
     enclosing imports, current-scope imports and assignments shadow inherited aliases,
     sibling/class-body imports do not leak, and parent callables do not absorb
     nested callable dependencies;
   - repo-local call results passed positionally or by keyword to another
     repo-local call are products, but builtin/stdlib/unknown consumers do not
     create that classification;
   - the canonical standard is schema-validated, unsupported compact kinds fail
     both schema validation and runtime-loader parsing, missing-file fallback
     equals canonical policy, tracked-legacy absence works, an explicitly
     supplied external legacy fixture remains loadable, and no-
     argument resolution does not autodiscover a legacy file.
3. Through `update-standards`, add one concise callable setting for exactly two
   compact structural kinds: classes whose sole decorator resolves to stdlib
   `dataclasses.dataclass` (including imported aliases and qualified use) and
   whose bodies contain only a docstring plus annotated instance-field
   declarations (non-call defaults and resolved stdlib `dataclasses.field(...)`
   are allowed), and undecorated subclasses with exactly one direct builtin-exception
   base whose bodies contain only a docstring/pass/ellipsis. Spoofed or additional
   decorators, methods, properties, descriptors, nested declarations, project-
   exception bases, and any other executable class-body statement disqualify
   compact treatment. Compact declarations
   require a meaningful summary but waive `Intent`, `Rationale`, `Pseudocode`,
   and `Wraps`. Bump `docstring_format_version` from 30 to 31 and update only the
   discovered pinned closure/generated view.
4. Implement lexical-scope import/dependency analysis: inherit module and valid
   enclosing-function imports; include current-function imports; prevent sibling
   and class-body leakage; prune nested callable/class bodies from parent walks.
5. Treat repo-call results passed to recognized repo-local calls as products,
   preserving existing return/raise/assignment/container/collector cases and
   excluding builtin, standard-library, logging, and unknown third-party sinks.
6. Delete the stale tracked v27 `docstring_format.yaml` and remove legacy-file
   no-argument autodiscovery. Preserve legacy loading only when callers supply an
   explicit path, schema-validate the canonical YAML, update `docs/docstring.md`,
   retain and align the built-in compatibility fallback with v31, and make exact
   canonical parity a required validator/test invariant so the mirror cannot
   drift independently.
7. Run focused RED/GREEN tests, standards validation, direct canonical checks,
   staged `validators/runner.py`, `git diff --cached --check`, and affected
   docstring suites. Obtain independent standard, validator, and test-quality
   review; correct and repeat every gate before a single contract-repair commit.
8. Verify the held patch or recover the working-tree change from it, then revise
   `blueprint_graph.py` under v31: compact only qualifying
   structural declarations; remove the three now-inferred `[implicit]` markers;
   retain corrected shallow-frozen wording and coherent pseudocode. Then resume
   the one-file Task 1B gate below.

**Files and baseline finding counts:**

- `src/officina/common/blueprint_graph.py` — 253
- `skills/list-manager/_rtx/_yaml_store.py` — 221
- `src/officina/wakeup/policies.py` — 47
- `skills/skill-drift/_rtx/_check_drift_state.py` — 53
- `skills/cloud-files/_rtx/_drive_gateway.py` — 44
- `docs_tooling/catalog.py` — 42
- `validators/skill_runtime_doc_references.py` — 13
- `skills/math-dependency-graph/_rtx/_graph_server.py` — 8
- `skills/list-manager/_rtx/tests/test_lists.py` — 5
- `src/officina/wakeup/tests/test_features.py` — 1

For each file, sequentially:

1. Invoke `refactor-node` through a fresh high-reasoning implementation agent,
   including relevant private runtime/test evidence where the owned node requires
   it. Limit changes to accurate, informative module/callable docstrings and
   explanatory comments needed by the current validator; do not change behavior
   or mechanically pad prose.
2. Require the canonical docstring checker to report zero findings for the file,
   and run its owning focused tests.
3. Stage only that file, run `validators/runner.py` against the staged candidate
   and `git diff --cached --check`, then obtain independent quality review.
4. Fix every reviewer finding and repeat the file's complete gate until approved.
5. Commit the single-file remediation before moving to the next file.

After all ten commits, run the root validator and the affected focused suites,
then repeat Task 1. The observed deferred delta is 39 findings across four files,
but remeasure the actual merged candidate after conflict resolution. During the
active merge, correct and independently review each affected file sequentially
with its direct canonical docstring check and focused tests; do not attempt an
intermediate commit or claim the staged root gate is green per file. After every
actual finding is resolved, run the complete staged validator and cached-diff
gate once for the whole merge candidate, then obtain final merge review and
commit.

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

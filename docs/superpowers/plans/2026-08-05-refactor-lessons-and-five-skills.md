# Refactor Lessons and Five Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the agreed minimal `refactor-node` workflow correction, then use
it to audit/refactor five more skills sequentially with validation and independent
review before every commit.

**Architecture:** Canonical standards remain unchanged. The router gains a
dispatcher-runtime preflight and explicit evidence classification. Every skill
iteration is an isolated decision: diagnose a concrete standards-backed pressure,
make at most one coherent move, validate it fully, review it independently, and
commit it before the next skill begins. No-churn is a valid completed iteration.

**Tech Stack:** Markdown instruction sources, Python/pytest, blueprint schema v5,
dispatcher standards queries, Git.

## Global constraints

- Work only in the reviewed `codex/deferred-fixes` worktree; preserve unrelated
  main-checkout changes.
- Before every standards query, retain its dispatcher dry-run. Resolve
  `cwd/python_target.gateway_path` and require it to equal
  `<reviewed-root>/skills/refactor-node/_rtx/_closure_engine.py`; this identifies
  the selected gateway, not the complete imported runtime closure. On mismatch,
  rerun through the installed wrapper with `AI=<reviewed-root>` and proceed only
  after the gateway path matches. Before execution, also verify that the rendered
  `command` exactly represents the intended target, repository root, facts, view,
  and refs.
- Whole-node audits include every registered implementation child and every
  returned supported behavioral source, including approved private sources.
- Preserve public interfaces, arguments, outputs, effects, ordering,
  authorization, branch outcomes, and generated/authored ownership boundaries.
- This pass authorizes behavior-preserving refactors only. If diagnosis reveals a
  behavioral defect, report and stop that move; a fix needs separately approved
  scope and genuine RED/GREEN evidence. Structural work requires concrete
  standards-backed design pressure plus green characterization before and after;
  never manufacture an implementation-shape behavioral test.
- Query every affected normative ref in every owner partition plus diagnosis and
  remedy refs. Classify evidence as canonical returned, supplemental
  change-relevant (with actual owner and limitations, including affected
  consumers), or an unmapped requested normative ref.
- Fix validator failures within the accepted move and rerun; if repair requires
  new scope, revert the move and stop. Never treat an unvalidated changed skill
  as final output or as the next task's base.
- Git behavior tests use disposable repositories only; do not checkout, stash,
  reset, commit, or push the working repository except for this plan's commits.
  LaTeX checks use fake/temp fixtures, not a user project. `connect-google` checks
  use offline temp configuration only: no network, browser, credential install,
  secret inspection, or real configuration writes. The `update-standards` audit
  does not edit canonical standards.
- Commit each accepted changed skill separately. Do not push.
- The user delegated each pre-mutation choice to independent subagent consensus
  for this pass. Record proposal approval before editing; do not pause for another
  user approval unless the proposed move exceeds this plan.

---

### Task 0: Record the baseline and evidence workspace

- [ ] Record base HEAD `0bea7c6b09e446e2256fb480cb85d44653f61516` and the
      initial status showing only this plan and design as untracked.
- [ ] Establish the exact-HEAD green baseline with
      `pytest -q skills/refactor-node/tests/test_refactor_node_routing.py` and
      `pytest -q tests/test_standard_consumers.py`, followed by
      `python3 validators/runner.py`; retain commands, output, and exit status in
      the Task 0 report.
- [ ] Create the ignored SDD workspace and ledger at
      `.superpowers/sdd/2026-08-05-refactor-lessons-and-five-skills/`. Give every
      task a durable `task-N-<skill>.md` report containing dry-run payloads, query
      refs/results, preservation map, evidence classes, proposal approval,
      validators, review, and commit or no-churn outcome.

### Task 1: Correct the `refactor-node` workflow

**Files:**
- Modify: `skills/refactor-node/SKILL.md`
- Test: `skills/refactor-node/tests/test_refactor_node_routing.py`
- Commit with:
  `docs/superpowers/specs/2026-08-05-refactor-node-evidence-preflight-design.md`
  and
  `docs/superpowers/plans/2026-08-05-refactor-lessons-and-five-skills.md`

- [ ] Add a failing focused routing-contract wording test for dry-run checkout
      rejection, complete affected-ref evidence retrieval, evidence
      classification, and structural versus defect RED handling.
- [ ] Obtain independent consensus approval for the exact router move before
      mutation.
- [ ] Trim lower-value extraction examples and implement the minimum router change.
      Keep the authored `SKILL.md` below the existing 700-word consumer limit.
- [ ] Run live acceptance commands showing that the unqualified dry-run exposes
      a mismatch which the workflow rejects, then that an
      `AI=<reviewed-root>` dry-run matches; execute only the matched command after
      verifying its target, repository root, facts, view, and refs.
- [ ] Run live-worktree tests with
      `pytest -q skills/refactor-node/tests/test_refactor_node_routing.py`, then
      `pytest -q skills/refactor-node/tests` and
      `pytest -q tests/test_standard_consumers.py`. Stage exactly the Task 1
      files, run `python3 validators/runner.py` against that candidate index, and
      run `git diff --check --cached`. Distinguish any failure from the
      verified-green baseline; fix only within approved scope, otherwise revert
      and stop.
- [ ] Obtain independent spec and quality approval. After every review-driven
      edit, rerun focused tests, restage all four Task 1 paths, rerun
      `python3 validators/runner.py` and `git diff --check --cached`, and re-review.
- [ ] Commit the router correction and plan/spec only.

### Task 2: Refactor `loose-mode`

- [ ] Query and inspect the complete registered node; record dry-run gateway
      selection, preservation map, evidence classes, and concrete pressure or
      no-churn basis.
- [ ] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [ ] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [ ] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [ ] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 3: Refactor `git-workflow`

- [ ] Query and inspect the complete registered node; preserve branch-safety,
      scope, staging, commit, and push boundaries.
- [ ] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [ ] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [ ] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [ ] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 4: Refactor `latex-workshop`

- [ ] Query and inspect the complete registered node; preserve workspace/user
      configuration precedence, recipe discovery, output paths, and fallback behavior.
- [ ] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [ ] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [ ] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [ ] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 5: Refactor `connect-google`

- [ ] Query both the instruction node and registered `_rtx` child, then read every
      returned supported source and affected reverse consumer.
- [ ] Preserve OAuth/client configuration, authorization, filesystem effects,
      errors, platform behavior, and machine-visible output while avoiding live OAuth.
- [ ] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [ ] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [ ] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [ ] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 6: Refactor `update-standards`

- [ ] Query and inspect the complete registered node; preserve authority,
      revision/digest cascades, generated views, declared evidence, and validation.
- [ ] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [ ] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [ ] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [ ] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 7: Findings and combined verification

- [ ] Document each skill's pressure, move or no-churn basis, validators, review
      outcome, and any reusable lesson in this tracked completion record; do not
      promote isolated mistakes to policy.
- [ ] Audit all accepted commits against this plan and design, including standards
      inflation and information-density checks over
      `0bea7c6b09e446e2256fb480cb85d44653f61516..HEAD`.
- [ ] Run exact-HEAD focused and repository-wide validation plus
      `python3 scripts/run-python-tests.py --suite full --verbose`,
      `python3 validators/runner.py`, and
      `git diff --check 0bea7c6b09e446e2256fb480cb85d44653f61516..HEAD`.
- [ ] Obtain a final independent whole-branch review. Any implementation finding
      requires an approved correction, focused tests, exact staging, staged
      validation, cached diff check, correction commit, and re-review before the
      completion record is updated and committed separately.
- [ ] Re-run exact-HEAD validation after the documentation commit and leave the
      branch clean and unpushed, proven by `git status --short --branch`.

## Completion record

Pending implementation.

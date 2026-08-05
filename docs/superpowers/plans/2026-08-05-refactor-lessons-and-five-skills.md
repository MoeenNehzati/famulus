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

- [x] Record base HEAD `0bea7c6b09e446e2256fb480cb85d44653f61516` and the
      initial status showing only this plan and design as untracked.
- [x] Establish the exact-HEAD green baseline with
      `pytest -q skills/refactor-node/tests/test_refactor_node_routing.py` and
      `pytest -q tests/test_standard_consumers.py`, followed by
      `python3 validators/runner.py`; retain commands, output, and exit status in
      the Task 0 report.
- [x] Create the ignored SDD workspace and ledger at
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

- [x] Add a failing focused routing-contract wording test for dry-run checkout
      rejection, complete affected-ref evidence retrieval, evidence
      classification, and structural versus defect RED handling.
- [x] Obtain independent consensus approval for the exact router move before
      mutation.
- [x] Trim lower-value extraction examples and implement the minimum router change.
      Keep the authored `SKILL.md` below the existing 700-word consumer limit.
- [x] Run live acceptance commands showing that the unqualified dry-run exposes
      a mismatch which the workflow rejects, then that an
      `AI=<reviewed-root>` dry-run matches; execute only the matched command after
      verifying its target, repository root, facts, view, and refs.
- [x] Run live-worktree tests with
      `pytest -q skills/refactor-node/tests/test_refactor_node_routing.py`, then
      `pytest -q skills/refactor-node/tests` and
      `pytest -q tests/test_standard_consumers.py`. Stage exactly the Task 1
      files, run `python3 validators/runner.py` against that candidate index, and
      run `git diff --check --cached`. Distinguish any failure from the
      verified-green baseline; fix only within approved scope, otherwise revert
      and stop.
- [x] Obtain independent spec and quality approval. After every review-driven
      edit, rerun focused tests, restage all four Task 1 paths, rerun
      `python3 validators/runner.py` and `git diff --check --cached`, and re-review.
- [x] Commit the router correction and plan/spec only.

### Task 2: Refactor `loose-mode`

- [x] Query and inspect the complete registered node; record dry-run gateway
      selection, preservation map, evidence classes, and concrete pressure or
      no-churn basis.
- [x] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [x] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [x] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [x] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 3: Refactor `git-workflow`

- [x] Query and inspect the complete registered node; preserve branch-safety,
      scope, staging, commit, and push boundaries.
- [x] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [x] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [x] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [x] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 4: Refactor `latex-workshop`

- [x] Query and inspect the complete registered node; preserve workspace/user
      configuration precedence, recipe discovery, output paths, and fallback behavior.
- [x] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [x] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [x] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [x] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 5: Refactor `connect-google`

- [x] Query both the instruction node and registered `_rtx` child, then read every
      returned supported source and affected reverse consumer.
- [x] Preserve OAuth/client configuration, authorization, filesystem effects,
      errors, platform behavior, and machine-visible output while avoiding live OAuth.
- [x] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [x] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [x] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [x] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 6: Refactor `update-standards`

- [x] Query and inspect the complete registered node; preserve authority,
      revision/digest cascades, generated views, declared evidence, and validation.
- [x] Obtain independent consensus approval for one proposed move or no-churn
      decision before mutation.
- [x] Apply at most one coherent move, run all relevant validators until green,
      and obtain independent approval.
- [x] For a changed target, stage exactly the accepted task files and run
      `python3 validators/runner.py` plus `git diff --check --cached` before commit.
- [x] Commit only an accepted change; record a reviewed no-churn outcome otherwise.

### Task 7: Findings and combined verification

- [x] Document each skill's pressure, move or no-churn basis, validators, review
      outcome, and any reusable lesson in this tracked completion record; do not
      promote isolated mistakes to policy.
- [x] Audit all accepted commits against this plan and design, including standards
      inflation and information-density checks over
      `0bea7c6b09e446e2256fb480cb85d44653f61516..HEAD`.
- [x] Run exact-HEAD focused and repository-wide validation plus
      `python3 scripts/run-python-tests.py --suite full --verbose`,
      `python3 validators/runner.py`, and
      `git diff --check 0bea7c6b09e446e2256fb480cb85d44653f61516..HEAD`.
- [x] Obtain a final independent whole-branch review. Any implementation finding
      requires an approved correction, focused tests, exact staging, staged
      validation, cached diff check, correction commit, and re-review before the
      completion record is updated and committed separately.
- [x] Re-run exact-HEAD validation after the documentation commit and leave the
      branch clean and unpushed, proven by `git status --short --branch`.

## Completion record

Implementation and independent review covered base
`0bea7c6b09e446e2256fb480cb85d44653f61516` through committed HEAD
`cac5ba9e1fce351121fa9ba908df71b7da870f02`:

- Task 1 committed `66e2b4c6fcc1ac9aa7029c3c551a1debc096fc18`
  (`refactor(refactor-node): verify query provenance and evidence`). The router
  now retains and rejects mismatched dispatcher dry-runs, verifies the exact
  request before replay, classifies canonical/supplemental/unmapped evidence,
  and separates behavioral-defect RED from structural characterization. Initial
  review replaced an editorial nineteen-clause snapshot with four outcome-scoped
  tests. Final review then found that compression had dropped the requirements
  to perform returned semantic reviews, open only returned artifacts, and
  compare the exact diff against the preservation map. Correction commit
  `cc1dfdef1d5ec629abf94458b8f200837014fcd6`
  (`fix(refactor-node): preserve review and diff gates`) restores both
  obligations with two additional outcome-scoped tests; the router is 698
  words. Both Task 1 commit hooks completed green, and the scoped final re-review
  returned `FINAL PASS` with no new Critical or Important finding.
- `loose-mode` was reviewed no-churn. Its proposed sentence deletion was
  rejected because “Move fast, cover ground” is behavior-directing and the
  preservation premise failed; mechanical baseline checks passed 14 tests and
  the independent review required no mutation.
- `git-workflow` was blocked/no-churn. Automatic checkout from detached HEAD
  conflicts with the declared explicit-authorization boundary for Git mutation.
  The focused supplemental suite passed 27 tests, but repair requires a separate
  behavior decision and genuine RED/GREEN coverage.
- `latex-workshop` committed
  `799e06237e0170f212f94da8fd047c446dfb6d58`
  (`refactor(latex-workshop): make activation trigger-only`). The accepted move
  changed only the frontmatter trigger and its two generator-owned catalog
  views; the authored workflow and declarations are byte-identical. Its focused
  checks passed 5 catalog/documentation tests, 22 blueprint/dispatch tests, two
  standalone validators, the staged validator runner, and cached diff check.
  Its commit hook also completed green.
- `connect-google` was reviewed no-churn, not blocked. Retried remedy queries
  returned successfully; neither the parent nor registered private child had a
  proved smell or unique behavior-preserving move. The audit stayed offline and
  read-only. The later exact-HEAD full run passed its 40 isolated tests.
- `update-standards` was blocked/no-churn. One unattended interface admits both
  alignment audits and repository-writing updates without deciding audit-on-
  divergence authorization. Supplemental validators passed and the focused
  suite passed 54 tests, but route decomposition cannot be proved preserving
  until that behavior is selected and tested.

No canonical standard, schema, implementation runtime, validator, or generated
standard view changed. The combined range has only the approved router/test/
plan/design paths and the three `latex-workshop` paths; review found no standards
inflation or unrelated scope, but did find the Task 1 information-density
regression described above. The committed correction restores the two identified
operational gates without changing another obligation. Reusable query lessons
are operational rather than new policy: retain the exact dry-run; match the
reviewed gateway and request fields before replay; distinguish returned,
supplemental, and unmapped evidence; do not infer a smell from an available
remedy; and retry a transient missing projection before declaring a blocker.
The recurring catalog-persistence and certificate-currentness warnings limit
assurance but did not prevent matched query results.

Fresh correction evidence: router contract `16 passed`, full router node
`43 passed`, direct consumers `2 passed`, and the returned runtime-documentation
artifact `16 passed`; `python3 validators/runner.py` and
`git diff --check --cached` both exited 0. The exact full-suite command remains
non-green: its repository suite passed `1424` tests with `14` skips, but the
later installer suite ended
`152 passed, 1 failed` because the host secret-service session rejected
certificate-signing-material setup with
`org.qtproject.QtDBus.Error.InvalidObjectPath` (“Can't find session ...”). No
changed-path assertion failed, and this correction did not broaden scope to
repair the host/session failure.

The completion record was committed as
`cac5ba9e1fce351121fa9ba908df71b7da870f02`
(`docs(plan): record refactor calibration findings`), and its full commit hook
completed green. Post-commit `python3 validators/runner.py` and
`git diff --check 0bea7c6b09e446e2256fb480cb85d44653f61516..HEAD`
both exited 0. The resulting status was exactly `## codex/deferred-fixes`; the
branch was unpushed and had no configured upstream. All plan tasks are complete.
Only the closure commit that records this final checkbox and evidence remains.

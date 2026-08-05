# Five More Skill Refactors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete five additional behavior-preserving skill refactors, each
validator-gated, independently reviewed, and committed before the next begins.

**Architecture:** Each task audits one complete registered skill node through
`refactor-node.interface.query-standards`, builds an affected-only preservation
map, demonstrates a concrete baseline deficiency, and applies at most one
coherent refactoring move. A fresh high-reasoning implementer owns the change;
an independent high-reasoning reviewer gates it before the controller commits.

**Tech Stack:** Markdown instruction sources, Python, blueprint schema v5,
dispatcher standards queries, pytest, Git.

## Global Constraints

- Preserve public interfaces, arguments, outputs, effects, ordering,
  authorization, branch outcomes, and generated/authored ownership boundaries.
- Treat every relevant returned validator as a finalization gate. Fix and rerun
  within approved scope or revert; never hand off or consume an unvalidated
  changed skill as final output.
- Start each task from a clean worktree and record its exact base commit.
- Run one current-skill baseline scenario before editing and record the concrete
  deficiency. For Python behavior, add or identify a test that would detect the
  proposed regression and verify the required RED/GREEN evidence.
- Apply the smallest justified move. Do not manufacture churn when the queried
  evidence does not support a change.
- Do not edit generated SKILL.md contract/interface blocks by hand. Invoke
  `skill-maker.interface.sync-blueprints` when authored blueprints change.
- Do not read private skill runtime scripts merely to discover interfaces; use
  dispatcher contracts and only inspect owned implementation sources after the
  standards query establishes scope.
- Do not run live email, calendar, cloud, OAuth, scheduler, or signing actions.
- A reusable standards or `refactor-node` gap may be changed only when the task
  supplies discriminating RED evidence, a minimal fix, and independent approval.
- Commit each completed skill iteration separately; do not push.

---

### Task 1: Refactor `prepare-handoff`

**Files:**
- Audit: `skills/prepare-handoff/`
- Test: target-owned tests plus returned standards/blueprint validators
- Report: task-owned SDD report

- [x] Query the complete registered node and resolve every material unknown.
- [x] Record the preservation map and current-skill baseline deficiency.
- [x] Apply one smallest standards-backed instruction or implementation move.
- [x] Run all relevant validators until green.
- [x] Obtain independent approval, fix/re-review findings, and commit only this
      iteration plus this plan document.

### Task 2: Refactor `wrap-up`

**Files:**
- Audit: `skills/wrap-up/`
- Test: target-owned tests plus returned standards/blueprint validators
- Report: task-owned SDD report

- [x] Query the complete registered node and resolve every material unknown.
- [x] Map each affected cross-skill producer, authorized consumer, branch outcome,
      and verification owner before compressing instructions.
- [x] Demonstrate the baseline deficiency and apply one smallest justified move.
- [x] Run all relevant validators until green.
- [x] Obtain independent approval, fix/re-review findings, and commit only this
      iteration.

### Task 3: Refactor `skill-maker`

**Files:**
- Audit: `skills/skill-maker/`
- Test: target-owned tests plus returned standards/blueprint validators
- Report: task-owned SDD report

- [x] Query the complete registered node and resolve every material unknown.
- [x] Preserve standards-query, authoring, generated-view, and validation
      boundaries in the preservation map.
- [x] Demonstrate the baseline deficiency and apply one smallest justified move.
- [x] Run all relevant validators until green.
- [x] Obtain independent approval, fix/re-review findings, and commit only this
      iteration.

### Task 4: Refactor `llm-wakeup`

**Files:**
- Audit: `skills/llm-wakeup/`
- Test: target-owned tests plus returned standards/blueprint validators
- Report: task-owned SDD report

- [x] Query both instruction and Python partitions and resolve every material
      unknown.
- [x] Preserve session identity, scheduling outcomes, machine-visible plans,
      platform branches, errors, and reverse callers in the preservation map.
- [x] Demonstrate the baseline deficiency and apply one smallest justified move.
- [x] Run all relevant validators until green.
- [x] Obtain independent approval, fix/re-review findings, and commit only this
      iteration.

### Task 5: Refactor `skill-certifier`

**Files:**
- Audit: `skills/skill-certifier/`
- Test: target-owned tests plus returned standards/blueprint validators
- Report: task-owned SDD report

- [x] Confirm the formerly blocked standards route now returns a valid complete
      closure and resolve every material unknown.
- [x] Map certification results/errors, graph and hash evidence, signing effects,
      route-smoke behavior, callers, and reverse tests before selecting a move.
- [x] Demonstrate the baseline deficiency and apply one smallest justified move.
- [x] Run all relevant validators until green.
- [x] Obtain independent approval, fix/re-review findings, and commit only this
      iteration.

### Final Review

- [x] Audit all five commits together for behavior preservation, scope, validation
      completeness, standards inflation, and any unrecorded repeated failure.
- [x] Run repository precommit verification and leave the branch clean and
      unpushed.

## Completion Record

- Refactored and committed `prepare-handoff`, `wrap-up`, and `skill-maker`.
- Independently validated no-churn outcomes for `llm-wakeup` and
  `skill-certifier` after complete owned-source audits.
- Patched `refactor-node` so whole-node audits query registered implementation
  children and inspect every returned supported source before move selection.
- Fixed the `wakeup` status path to honor its no-effect contract and corrected
  canonical ownership resolution to exclude Python cache artifacts.
- Canonical standards were not expanded. Changed skill bodies are 75 words
  shorter in aggregate; `refactor-node` grew by 11 necessary words.
- Final independent audit and exact-HEAD validators are green. The branch was
  not pushed; live certification/recertification was not performed.

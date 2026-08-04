# Ten-Skill Refactor Pass 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve ten additional registered skills without changing their supported behavior, public interfaces, ownership boundaries, or runtime contracts.

**Architecture:** Each task is one whole registered node. A fresh high-reasoning implementer resolves the node through `refactor-node`, proves a concrete current defect before editing, applies the smallest behavior-preserving improvement, and records live-worktree evidence. A fresh independent reviewer then gates both requirements compliance and quality; the controller commits only after approval.

**Tech Stack:** Markdown instruction sources, schema-v5 YAML blueprints, Python 3, pytest, repository validators, dispatcher-backed standards queries, Git.

## Global Constraints

- Process nodes strictly in the listed order; never run two implementers concurrently.
- Use `dispatcher --caller-skill refactor-node refactor-node.interface.query-standards <target> --repo-root . --facts-json '{"task":{"kind":"refactor"}}' --view requirements` from the worktree root as the sole repository-policy source. Preserve every returned owner, partition, exclusion, gateway family, and standard reference.
- Before editing, inventory the node's current observable behavior, exact discovery vocabulary, public interfaces, callers/consumers, generated catalog output, tests, and current diff. A proposed change needs a concrete defect demonstrated against the unmodified node; otherwise report that no justified edit exists.
- Apply the smallest coherent improvement. Preserve functionality and public contracts; do not add features, migrate architecture, broaden ownership, or perform opportunistic cleanup.
- Keep standards concise. Change a canonical standard only after a repeated cross-node gap is proven, and then amend the smallest existing authoritative rule/remedy/evidence link without duplication. Use `update-standards` for any such change.
- Skill frontmatter descriptions must begin with one standalone sentence-terminated `Use when...` summary that remains meaningful when generated catalog tooling extracts only the first sentence. Preserve the complete trigger and exclusion inventory after that summary.
- Do not trust staged-tree validators as evidence for an unstaged worktree. Exercise the live changed artifact and its generated/downstream consumers directly before staging; then run staged validators after staging.
- Tests must catch an observable regression, not merely assert source text or restate the implementation. Record the specific production mutation each new test would catch and observe the test fail for the intended reason before implementing the fix.
- Use `apply_patch` for file edits. Do not edit generated blueprint contract blocks by hand; synchronize them through `dispatcher --caller-skill skill-maker skill-maker.interface.sync-blueprints` when authored blueprint changes require it.
- Implementers return exactly one status: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`. They do not commit. Their report must contain scope, standards refs, baseline defect evidence, exact changes, before/after behavior inventory, live-worktree verification commands/output, diff self-review, and concerns.
- Reviewers receive only the task brief, implementer report, and a full diff package. Approval requires `Spec: PASS` and `Quality: APPROVED`; any other result enters a fix loop.
- When a reviewer identifies a real mistake, first ask the original implementer why it made that mistake. Record the causal explanation, then ask it to fix and reverify. Resume the same implementer for rounds 1-3; cap at five reviewed rounds.
- Commit only after independent green-light. Stage exact task-owned paths and use `refactor(<skill>): sharpen node design` unless the reviewed change has a more precise conventional-commit subject.
- Known baseline limitation: the full suite has one pre-existing failure in `test_repository_inventory_matches_reviewed_v5_cutover_surface`; commit/precommit verification intentionally deselects that stale post-cutover inventory assertion. No task may add failures beyond it.

---

### Task 1: Refactor `daily-plan`

**Files:**
- Inspect and modify only standards-selected files under `skills/daily-plan/` plus directly required tests or generated catalog artifacts.
- Test: `skills/daily-plan/_rtx/tests/` and standards-selected repository evidence.

**Interfaces:** Preserve every exported `daily-plan` instruction and runtime interface exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run `python3 -m pytest -q -o pythonpath=src skills/daily-plan/_rtx/tests/` plus returned evidence and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 2: Refactor `email-triage`

**Files:**
- Inspect and modify only standards-selected files under `skills/email-triage/` plus directly required tests or generated catalog artifacts.
- Test: `skills/email-triage/tests/`, `skills/email-triage/_rtx/tests/`, and standards-selected repository evidence.

**Interfaces:** Preserve triage, rescan, failure, metrics, and watermark contracts exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run `python3 -m pytest -q -o pythonpath=src skills/email-triage/tests/ skills/email-triage/_rtx/tests/` plus returned evidence and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 3: Refactor `list-manager`

**Files:**
- Inspect and modify only standards-selected files under `skills/list-manager/` plus directly required tests or generated catalog artifacts.
- Test: `skills/list-manager/_rtx/tests/` and standards-selected repository evidence.

**Interfaces:** Preserve list storage, category cache, cloud transport, rendering, and instruction contracts exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run `python3 -m pytest -q -o pythonpath=src skills/list-manager/_rtx/tests/` plus returned evidence and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 4: Refactor `make-tex-docstring`

**Files:**
- Inspect and modify only standards-selected files under `skills/make-tex-docstring/` plus directly required tests or generated catalog artifacts.
- Test: standards-selected instruction and catalog evidence.

**Interfaces:** Preserve the document-profile proposal and approval boundary exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Run a focused baseline pressure scenario against the unmodified instruction source and record the failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run returned evidence, live catalog rendering, metadata validation, and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 5: Refactor `pdf-to-markdown`

**Files:**
- Inspect and modify only standards-selected files under `skills/pdf-to-markdown/` plus directly required tests or generated catalog artifacts.
- Test: standards-selected repository evidence and any task-added focused regression test.

**Interfaces:** Preserve source fetching, local-path handling, Marker probing, and output contracts exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run the task-added focused regression test plus returned evidence, live catalog rendering, metadata validation, and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 6: Refactor `math-dependency-graph`

**Files:**
- Inspect and modify only standards-selected files under `skills/math-dependency-graph/` plus directly required tests or generated catalog artifacts.
- Test: `skills/math-dependency-graph/_rtx/tests/` and standards-selected repository evidence.

**Interfaces:** Preserve extraction, graph building, macro reading, visualization serving, and instruction contracts exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run `python3 -m pytest -q -o pythonpath=src skills/math-dependency-graph/_rtx/tests/` plus returned evidence and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 7: Refactor `recurring-tasks`

**Files:**
- Inspect and modify only standards-selected files under `skills/recurring-tasks/` plus directly required tests or generated catalog artifacts.
- Test: `skills/recurring-tasks/_rtx/tests/`, `skills/recurring-tasks/tests/`, and standards-selected repository evidence.

**Interfaces:** Preserve job configuration, scheduling, execution, health, notification, setup, and instruction contracts exactly across supported hosts.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run `python3 -m pytest -q -o pythonpath=src skills/recurring-tasks/_rtx/tests/ skills/recurring-tasks/tests/` plus returned evidence and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 8: Refactor `llm-wakeup`

**Files:**
- Inspect and modify only standards-selected files under `skills/llm-wakeup/` plus directly required tests or generated catalog artifacts.
- Test: standards-selected instruction and catalog evidence.

**Interfaces:** Preserve supported-session wakeup inference, scheduling, cancellation, and safety boundaries exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Run a focused baseline pressure scenario against the unmodified instruction source and record the failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run returned evidence, live catalog rendering, metadata validation, and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 9: Refactor `regenerate-blueprints`

**Files:**
- Inspect and modify only standards-selected files under `skills/regenerate-blueprints/` plus directly required tests or generated catalog artifacts.
- Test: standards-selected repository evidence and any task-added focused regression test.

**Interfaces:** Preserve `/tmp`-only regeneration, source immutability, validation, and output contracts exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run the task-added focused regression test plus returned evidence, live catalog rendering, metadata validation, and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 10: Refactor `skill-drift`

**Files:**
- Inspect and modify only standards-selected files under `skills/skill-drift/` plus directly required tests or generated catalog artifacts.
- Test: `skills/skill-drift/_rtx/tests/` and standards-selected repository evidence.

**Interfaces:** Preserve certificate-currentness and canonical-node-hash read contracts exactly.

- [ ] Query requirements/evidence/remedies, demonstrate the current defect, and record the pre-edit contract.
- [ ] Add or identify a focused regression/pressure test and observe the intended baseline failure.
- [ ] Apply one minimal refactor move; inspect the exact diff and generated catalog summary.
- [ ] Run `python3 -m pytest -q -o pythonpath=src skills/skill-drift/_rtx/tests/` plus returned evidence and blueprint checks.
- [ ] Obtain independent spec and quality approval, complete any why/fix/re-review loop, then commit exact reviewed paths.

### Task 11: Whole-Pass Certification and Integration

**Files:** Review the complete branch range from `07218847c29f406a88e55150ed1ef147fc43f31e` through branch `HEAD`.

- [ ] Dispatch one fresh high-reasoning whole-branch reviewer with the plan, ledger, and full diff package.
- [ ] If findings exist, dispatch one fix wave, ask each responsible implementer why for any newly identified mistake, and run one scoped independent re-review.
- [ ] Run `python3 scripts/run-python-tests.py --suite precommit --verbose`, all task-specific suites, blueprint synchronization checks, validators, and `git diff --check`.
- [ ] Verify the only full-suite failure is the documented pre-existing stale inventory assertion.
- [ ] Fast-forward `master`, fetch and confirm remote ancestry, push `master`, verify `master == origin/master`, and remove only this pass's worktree/branch/scratch workspace.

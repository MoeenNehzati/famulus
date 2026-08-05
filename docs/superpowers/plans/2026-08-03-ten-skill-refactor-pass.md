# Ten-Skill Refactor Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve ten representative skills one at a time while using implementation mistakes and independent review to make `refactor-node` and its effective standards more self-contained.

**Architecture:** Each task targets one registered skill node. A fresh high-reasoning implementer queries the effective standards through `refactor-node`, identifies the highest-value concrete defect, applies the smallest behavior-preserving improvement, and records evidence. The controller audits the diff, conducts a correction dialogue when needed, obtains a fresh independent green-light review, and only then commits that skill before starting the next task.

**Tech Stack:** Markdown skill instructions, v5 `blueprint.yaml` node metadata, Python runtime modules where owned by the target, `dispatcher`, repository validators, pytest, and Git.

## Global Constraints

- Preserve every public interface, caller-visible behavior, node identifier, ownership boundary, and generated-artifact contract unless the current effective standards explicitly prove a repair is required.
- Use `dispatcher --caller-skill refactor-node refactor-node._rtx.interface.query-standards <target> --repo-root <worktree> --facts-json '{"task":{"kind":"refactor"}}' --view requirements` as the sole repository-policy source.
- Resolve every material `requirements.unknown` fact before mutation; query only relevant indexed context, evidence, and remedies.
- Make one coherent, behavior-preserving refactoring move per skill. Do not bundle features, bug fixes, public-API redesign, certification, or unrelated cleanup.
- Prefer deletion, consolidation, sharper boundaries, and clearer control flow over added prose or abstraction. Do not increase length without a concrete information or correctness gain.
- Do not edit generated `SKILL.md` contract/interface blocks by hand. Use canonical repository synchronization only when required by an approved source change.
- An implementer may edit and test but must not commit. The controller commits only after its own diff audit and an independent reviewer reports both spec compliance and quality approval.
- A standards change requires evidence that an implementer mistake exposes a general, recurring omission. Keep the amendment concise, place it in the existing authoritative section, remove any resulting duplication, and review it independently with the target refactor.
- If the current node already satisfies the effective standards and no material simplification is justified, revert speculative edits and record a reviewed no-op rather than manufacturing churn.
- Commit scope is one target skill plus only directly required shared-standard, generated-view, or test changes. Each accepted task gets one commit before the next implementer starts.

---

### Task 1: Refactor `find-handoff-candidates`

**Files:** Target `skills/find-handoff-candidates/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; require a report with baseline behavior, exact diff, and test evidence.
- [ ] Audit the working diff; conduct correction rounds with the implementer for every substantive defect or unjustified addition.
- [ ] Obtain independent spec and quality approval, run fresh node/repository checks, and commit as `refactor(find-handoff-candidates): sharpen node design`.

### Task 2: Refactor `formal-prose-review`

**Files:** Target `skills/formal-prose-review/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; require a report with baseline behavior, exact diff, and test evidence.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(formal-prose-review): sharpen node design` only after approval.

### Task 3: Refactor `notation-review`

**Files:** Target `skills/notation-review/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve the mathematical-review scope and output contract.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(notation-review): sharpen node design` only after approval.

### Task 4: Refactor `proof-audit`

**Files:** Target `skills/proof-audit/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve proof-audit labels, epistemic boundaries, and artifact behavior.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(proof-audit): sharpen node design` only after approval.

### Task 5: Refactor `technical-flow-review`

**Files:** Target `skills/technical-flow-review/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve the distinction between flow review and prose editing.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(technical-flow-review): sharpen node design` only after approval.

### Task 6: Refactor `tool-applicability`

**Files:** Target `skills/tool-applicability/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve verified/likely/speculative/gap conclusions and the nearest-valid-result route.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(tool-applicability): sharpen node design` only after approval.

### Task 7: Refactor `hook-maker`

**Files:** Target `skills/hook-maker/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve host-specific lifecycle and output-schema boundaries.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(hook-maker): sharpen node design` only after approval.

### Task 8: Refactor `get-weather`

**Files:** Target `skills/get-weather/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve dispatcher use and weather output behavior.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(get-weather): sharpen node design` only after approval.

### Task 9: Refactor `fix-bisync`

**Files:** Target `skills/fix-bisync/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve read-only diagnosis, approval boundaries, and repair-command behavior.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(fix-bisync): sharpen node design` only after approval.

### Task 10: Refactor `bib-audit`

**Files:** Target `skills/bib-audit/`; shared standards or tests only if the Global Constraints' evidence gate is met.

- [ ] Query effective requirements, resolve unknown facts, and retrieve only relevant context/evidence/remedies.
- [ ] Delegate one smallest verified improvement to a fresh high-reasoning implementer; preserve syntax/style/metadata audit separation and approval-gated corrections.
- [ ] Audit the working diff, complete correction rounds, obtain independent spec and quality approval, and run fresh node/repository checks.
- [ ] Commit as `refactor(bib-audit): sharpen node design` only after approval.

### Final Review: Pass-Level Coherence

**Files:** Review all commits from the setup baseline through Task 10; modify only through one reviewed final fix wave if required.

- [ ] Generate one whole-pass review package and dispatch a fresh high-reasoning reviewer.
- [ ] Verify that all ten tasks preserve behavior, reduce real complexity, contain no gratuitous standards growth, and remain coherent together.
- [ ] Run the full repository precommit suite and validators, integrate the branch into `master`, and push without force.

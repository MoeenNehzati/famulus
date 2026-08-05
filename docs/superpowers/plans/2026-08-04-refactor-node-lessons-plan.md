# Refactor-Node Lessons Implementation Plan

**Goal:** Make refactoring standards extraction and behavior preservation reliable
without inflating canonical policy.

## Scope

- `references/node-standards/refactoring.standard.yaml`
- Direct and transitive pinned dependents whose revision/digest changes are required
- `skills/refactor-node/SKILL.md`
- `skills/refactor-node/instructions/instruction-refactoring.md`
- `skills/refactor-node/instructions/python-refactoring.md`
- Focused refactor-node tests and the approved design/plan documents

## Tasks

- [x] Record baseline pressure-test omissions from the current skill.
- [x] Strengthen `characterize-first` and the validator-finalization assertion
      without adding a standards family.
- [x] Reorganize the router into resolve, characterize, select, retrieve, and change
      stages while removing redundant prose.
- [x] Add narrow instruction-route and Python-route preservation inventories.
- [x] Update the standard revision and every required pinned digest outward.
- [x] Repeat pressure scenarios and run focused plus repository validation.
- [x] Keep each changed skill partial until all relevant validators pass; repair and
      rerun within approved scope, otherwise revert and stop.
- [x] Obtain a fresh subagent audit against the design and observed failure reports.
- [x] Correct findings, rerun validation, stage exact reviewed files, and commit.

## Acceptance Criteria

- A refactor cannot reach mutation without a concrete preservation map.
- The router explicitly narrows follow-up extraction after characterizing the live
  scope; it does not load the full closure as routine guidance.
- Instruction compression accounts for canonical ownership, removed directives,
  branch outcomes, and cross-module authorization/evidence paths.
- Python refactoring accounts for machine-observable metadata, branches, callers,
  authorization, and reverse integration evidence.
- No new query interface or broad standards family is introduced.
- No refactored skill is treated, handed off, or consumed as final output while a
  relevant validator is failing or unrun.
- All changed canonical standards and pinned dependents validate, and the independent
  audit reports no unresolved plan deviation.

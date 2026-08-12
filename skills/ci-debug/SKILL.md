---
name: ci-debug
description: Use when GitHub Actions CI is red, matrix failures need isolated repair, or repeated full reruns make remote diagnosis inefficient.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: software-development; topics: repository-workflow, task-automation, assistant-assurance; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 1

Uses Interfaces:
- `ci-debug.source.gateway -> ci-debug._rtx.interface.run-ci@1`
- `ci-debug.source.gateway -> ci-debug.source.instructions-repair-element.interface.repair-element@1`
- `ci-debug.source.gateway -> git-workflow.interface.default@1`
- `ci-debug.source.instructions-repair-element -> ci-debug._rtx.interface.run-targeted-tests@1`
- `ci-debug.source.instructions-repair-element -> git-workflow.interface.default@1`

Public Interfaces:
- `ci-debug.interface.default`
- `ci-debug.interface.repair-element`
<!-- END BLUEPRINT CONTRACT -->
# CI Debug

Use `ci-debug._rtx.interface.run-ci` for the exact pushed candidate.

While its report is red:

1. Group failures by matrix element. Give each repair subagent one element, its
   failing selectors, the report, and an allowed path scope.
2. Run independent repair elements in bounded parallel through
   `ci-debug.interface.repair-element`; use a sequential fallback when workers
   are unavailable.
3. Review returned commits, diffs, and targeted-test evidence. Integrate accepted
   patches sequentially under `git-workflow.interface.default`.
4. Push the integrated candidate, record its exact SHA, and use
   `ci-debug._rtx.interface.run-ci` again.

Stop only when the full report is green or a repair element returns a concrete
blocked reason. Targeted tests and whole-element tests never establish overall
green. Machine reports are evidence, not Git authority; machine interfaces do
not create branches, commit, push, integrate, or clean worktrees.

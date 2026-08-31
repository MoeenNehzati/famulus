---
name: ci-debug
description: Use when GitHub Actions CI is red, matrix failures need isolated repair, or repeated full reruns make remote diagnosis inefficient.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `ci-debug._rtx.interface.run-ci@1` — Run the complete remote CI matrix for an exact pushed candidate.
  - `dispatcher --caller-skill ci-debug ci-debug._rtx.interface.run-ci --repo-root REPO --ref REF --expected-sha SHA --context DIR [--timeout SECONDS]`
- `ci-debug._rtx.interface.run-targeted-tests@1` — Run one selected failure set or complete matrix element for an exact candidate.
  - `dispatcher --caller-skill ci-debug ci-debug._rtx.interface.run-targeted-tests --repo-root REPO --ref REF --expected-sha SHA --os OS --task TASK (--selectors-json JSON | --selector NODE | --from-report PATH | --whole-element) --context DIR [--jobs N] [--profile PROFILE] [--timeout SECONDS]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `ci-debug.source.instructions-repair-element.interface.repair-element@1` — Repair and verify one assigned CI matrix element without integrating it or claiming overall CI success.
- `git-workflow.interface.default@1` — Check branch and ownership boundaries first, then perform only explicitly authorized and exactly scoped Git mutations.
<!-- END BLUEPRINT INTERFACES -->
# CI Debug

Create or reuse one non-secret debug context and supply it to every
`ci-debug._rtx.interface.run-ci` and repair-element invocation. The context
owns stable repository/workflow identity and immutable request-scoped reports;
it never owns credentials, credential-bearing URLs, or raw authentication
output. Persisted setup is a handoff aid, not authority: each invocation must
still revalidate authentication, repository identity, and the exact pushed
candidate. Keep the coordinator's failure ledger, branch assignments, and agent
state outside the machine-owned context; do not extend its schema ad hoc.

Use `ci-debug._rtx.interface.run-ci` for the exact pushed candidate.

Retire superseded runs before dispatching replacement work through the
already-authorized CI control surface. If cancellation authority is
unavailable, record the capacity blocker and do not duplicate the full run.
While a matrix remains active, consume completed matrix-element reports and
logs as soon as an already-authorized CI surface exposes them. Do not wait for
the enclosing matrix before diagnosing a completed failure or routing a stalled
element into the failure ledger.

While its report is red:

1. Group failures by matrix element. Give each repair subagent one element, the
   shared debug context, smallest selector set containing its known failures,
   the report, and an allowed path scope. Prefer exact failing test nodes, then
   the smallest set of containing test files when exact nodes are unavailable.
   Do not include selectors already known to pass.
2. Run independent repair elements in bounded parallel through
   `ci-debug.interface.repair-element`; use a sequential fallback when workers
   are unavailable.
3. Review returned commits, diffs, and targeted-test evidence. Integrate accepted
   patches sequentially under `git-workflow.interface.default`.
4. Push the integrated candidate and record its exact SHA.
5. Before the next complete matrix, use
   `ci-debug._rtx.interface.run-targeted-tests` on the exact integrated
   candidate for every affected matrix element. Start with the smallest
   selectors needed to detect integration interactions, then run each whole
   affected element. Return new failures to the ledger.
6. Only after every affected matrix element is green, use
   `ci-debug._rtx.interface.run-ci` again for the complete matrix.

Stop only when the full report is green or a repair element returns a concrete
blocked reason. Targeted tests and whole-element tests never establish overall
green. Machine reports are evidence, not Git authority; machine interfaces do
not create branches, commit, push, integrate, or clean worktrees.

After the full report is green, **REQUIRED:** read [prevention.md](prevention.md)
and complete its prevention review before closing the CI repair.

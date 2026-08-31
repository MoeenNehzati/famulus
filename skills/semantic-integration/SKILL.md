---
name: semantic-integration
description: >-
  Use when integrating substantially diverged Git branches and merge or rebase is inadequate because it produces broad structural conflicts, or because mechanical application would place source changes into structures the target architecture has replaced and thereby lose their intent. Do not use when direct application and localized conflict resolution can preserve both branches' intended behavior.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `git-workflow.interface.default@1` — Check branch and ownership boundaries first, then perform only explicitly authorized and exactly scoped Git mutations.
<!-- END BLUEPRINT INTERFACES -->
# Semantic integration

**REQUIRED INTERFACE:** Use `git-workflow.interface.default@1` before any Git
mutation.
Proceed only when it reports that the named branch and worktree are safe for the
requested mutation. Treat any reported safety concern, unrelated change, or
required approval as a stop condition until resolved by the user.

Freeze exact `source` and `target` tips, then integrate through a separate
integration branch. Treat the target as the current architectural authority and
every observable source effect as evidence that must be resolved. Keep source
unchanged and do not move target until the approved closure. Do not silently
select only changes that appear relevant.

## Core mechanism

The first branch is `target`; the second branch is `source`. The mechanism is:

1. Create an isolated integration branch from target.
2. Determine what source intended and account for every observable source effect.
3. Recreate each accepted effect as new commits against target's current
   architecture. Do not merge source content into the integration tree.
4. Prove that the reconstructed tree preserves accepted source behavior and
   target guarantees.
5. Create a vacuous merge on the integration branch whose tree remains exactly
   the reconstructed tree and whose second parent is the frozen source tip.
6. Validate that exact merge commit, obtain closure approval, and fast-forward
   target to it.

Thus semantic reconstruction combines content; the final vacuous merge changes
only ancestry. It records which frozen source history was integrated without
mechanically applying that history's tree or patches.

Read `references/git/run-ledger-template.md` at the start. Keep one concise run
ledger at a user-approved location and update it at phase boundaries, after each
slice, and whenever a stall or deviation changes the plan. The ledger is the
workflow state and preservation proof, not a transcript of commands.

## Inputs

Require explicit roles:

- `target`: branch whose current architecture and guarantees must remain
  authoritative.
- `source`: branch whose intended effects must be preserved, adapted,
  superseded, or explicitly rejected.

Closure is not selectable: semantic integration always finishes through a
vacuous merge commit that retains the semantic integration tree and records the
frozen source tip as its other parent. Rebasing is permitted only to refresh the
isolated semantic commits onto an approved target tip before constructing that
merge marker; it never substitutes for merge closure.

Never infer the roles from branch order or names. Never rewrite either branch.
Keep source fixed and do not move target before approved closure.

## Phase 0: Freeze and recover

Before content changes:

1. Verify the repository, named current branch, and worktree state.
2. Resolve and record exact target and source commit IDs and their merge base.
3. Inspect attached worktrees. Report unrelated or uncommitted changes without
   stashing, deleting, or modifying them.
4. Create recovery refs for both frozen tips. Add a portable bundle when risk is
   high, branches may later be deleted, or the user requests one.
5. Create a dedicated integration branch from the frozen target.
6. Identify repository-owned validation entrypoints without running expensive
   suites yet.

Stop if an input ref moves unexpectedly. Ask whether to retain the frozen run or
restart from the new tip.

## Phase 1: Establish the preservation contract

Write short, evidence-based statements of:

- what the target currently guarantees and which architecture must survive;
- what the source was trying to add, remove, repair, or restructure.

Build four linked inventories:

1. A source-only commit ledger. For every commit, derive its purpose from the
   patch rather than its message alone. Cite at least one concrete diff anchor -
   a changed path plus a relevant symbol, test, configuration key, migration, or
   deletion - and record affected modules and dependencies on other source
   commits.
2. A merge-base-to-source endpoint inventory. Cover additions, modifications,
   deletions, renames, file modes, dependencies, configuration, public
   interfaces, tests, fixtures, generated artifacts, migrations, and persistent
   state effects.
3. A merge-base-to-target protection inventory. Index every target-only commit
   and endpoint effect that the integration must retain. Keep entries concise
   because the integration branch already starts from target.
4. A changed-on-both inventory. Give every path, interface, dependency, test,
   migration, or state surface changed by both branches an explicit semantic
   resolution and evidence; clean textual application is not sufficient.

Commit history, endpoint state, and overlap are independent checks. None may
replace another. Where practical, record existing continuous-integration evidence or
run bounded baseline checks on both frozen tips. Separate pre-existing failures
from integration failures.

### Gate 1 - preservation contract

Present frozen tips, both intent statements, all inventories, baseline evidence,
and unresolved questions. Do not plan reconstruction until the user approves.

## Phase 2: Plan semantic slices

Group work by coherent behavior, not chronology alone. For each slice record:

- source commits and endpoint items resolved;
- intended observable behavior;
- target modules likely to change;
- direct preservation versus architectural adaptation;
- focused validation evidence;
- dependencies, planned paths or modules, active-time estimate, and a hard stop.

Give every source commit exactly one final disposition:

- `preserved`: materially the same behavior remains;
- `adapted`: the behavior remains through a target-native implementation;
- `superseded`: the target already provides an equal or stronger result;
- `rejected`: the effect is intentionally excluded.

Every `superseded` or `rejected` source commit, endpoint item, or source test
requires concrete evidence, consequences, and explicit user approval.
Uncertainty remains unresolved; it never means irrelevant.

Prefer the smallest slices that remain coherent. Do not force one integration
commit per source commit, and do not create batches so broad that failures lose
attribution.

### Gate 2 - reconstruction authority

Present the slice plan, proposed dispositions, validation plan, estimates, and
expected conflict areas. Do not edit integration content until approved.

## Phase 3: Reconstruct in slices

For each approved slice:

1. Reconstruct accepted behavior against the target's current architecture.
   Express every content change as a new semantic integration commit; never use
   a content merge or mechanical cherry-pick from source as a shortcut.
2. Treat source tests as requirements: carry them over, adapt them, replace them
   with stronger coverage, or obtain approval to reject them.
3. Run the cheapest focused check capable of testing the slice.
4. Commit one coherent checkpoint with normal hooks enabled.
5. Map its source commits and endpoint items to the integration commit and
   evidence immediately.
6. Report completed coverage, elapsed active time, and the next slice.

Stop on an unknown architectural choice, an accepted effect that cannot be
preserved, unexpected repository changes, or evidence that invalidates the plan.
Fix and revalidate an unproven slice before proceeding.

Maintain one source-test row per changed source test or test file. Record the
resulting assertion, its disposition, focused result, and evidence that the
repository's actual runner collects it. A green full run does not prove that a
nested or custom-named test was collected.

If shared fixtures, runners, installers, schemas, migrations, or other
cross-cutting test infrastructure changes, run the complete affected subsystem
before the repository-wide gate. Do not discover predictable stale expectations
one file at a time through repeated full runs.

## Phase 4: Prove completeness

Require this traceability for every accepted source effect:

`source commit -> intended behavior -> endpoint items -> target modules -> integration commit -> evidence`

No source commit, source endpoint, target protection, changed-on-both item, or
source-test row may remain unresolved.

Perform two bounded, independent audits, in parallel when independent agents
are available:

1. Source-preservation audit: search for missing behavior, tests, files,
   deletions, interfaces, dependencies, migrations, and side effects.
2. Target-regression audit: search for weakened guarantees, architectural
   regressions, accidental compatibility layers, and validation gaps.

Give auditors the frozen refs, integration diff, and ledger rather than a desired
verdict. If independent agents are unavailable, perform two explicitly separate
passes and record the lack of independence as a residual limitation requiring
explicit user acceptance at Gate 3. Resolve findings with focused checks.

Prepare the exact vacuous-merge candidate before final validation. First establish the
approved target tip. If target moved from its frozen tip, stop, inventory and
approve the new target and overlap effects, then rebase only the semantic
integration commits onto that tip and refresh affected slice evidence and both
audits. The resulting semantic tip must contain every approved target effect
before candidate construction.

On the integration branch, create one vacuous merge whose first parent is the
completed semantic integration tip and whose second parent is the frozen source
tip. Use an ancestry-recording strategy that keeps the current integration tree,
such as `git merge -s ours --no-ff <frozen-source>`; do not confuse this with the
`-X ours` conflict preference. Prove that the candidate tree exactly equals its
first-parent tree and that the approved target tip is an ancestor of the first
parent. The merge message must name the frozen source tip, semantic integration
tip, and run ledger. This vacuous merge commit is the closure candidate.

Refresh affected accounting and both audits after any rebase conflict or newly
approved target delta. Record the exact closure-candidate commit and tree, then
run the repository's complete required integration gates once from that committed
state. Record commands, results, durations, skips, warnings, and environmental
limitations. Any later change to the candidate commit or tree, or any target
movement other than the authorized fast-forward to that exact candidate,
invalidates those results and Gate 3; refresh them before closure.

### Gate 3 - closure authority

Present accounting completeness, audit findings and resolutions, final validation,
residual risks, elapsed time, and exact proposed vacuous-merge closure. Do not alter target
history until the user approves.

## Phase 5: Close

Verify that target still names the approved tip, then fast-forward it to the exact
tested closure candidate. This fast-forward is the only permitted target mutation
and must create no post-gate commit. Verify the vacuous merge's parents, unchanged
tree, source pointer, target ancestry, and message evidence before and after the
fast-forward.

After closure verify that target equals the tested closure-candidate commit
and tree, expected ancestry holds, the worktree is clean, and recovery evidence
remains available. Do not delete branches or push without separate explicit
authorization.

## Assurance without drag

- Generate commit and endpoint inventories once; reuse them throughout.
- Deep-read changed interfaces, tests, configuration, dependencies, migrations,
  stateful behavior, conflict areas, and unclear dispositions. Do not default to
  line-by-line re-analysis of an already-accounted patch.
- Run focused checks per slice, complete affected-subsystem checks after
  cross-cutting changes, and repository-wide gates only for the final candidate
  unless diagnosis requires a rerun.
- Run independent audits concurrently when possible.
- Keep ledger entries evidence-linked and concise; never paste full patches or
  command logs.
- Use five percent of active integration time as a diagnostic target for control
  overhead: ledger maintenance, approval packaging, and progress reporting beyond
  the semantic analysis, reconstruction, and validation the merge itself needs.
  If overhead grows beyond that, compress prose and reporting, but never reduce
  accounting or assurance coverage merely to meet the target.

## Progress and deviation

Treat progress monitoring as a lightweight control loop, not another assurance
audit, approval gate, or artifact:

1. Observe accounting completed, active time, current blocker, retries,
   validation state, and unplanned scope.
2. Compare them with the approved slice, estimate, and hard stops.
3. Choose one action: continue, split the slice, narrow or change validation,
   revise the affected plan, or stop for user input.

Reuse the existing ledger rows and evidence. Do not regenerate inventories,
repeat passed checks, or write a separate progress document merely to monitor the
run.

Report at phase boundaries and after each slice. Also report when 30 active
minutes pass without closing a slice or ledger item, or when a phase reaches
twice its estimate.

A progress report contains only current phase, completed versus total accounting,
current slice or blocker, active time versus estimate, validation state, whether
the plan remains credible, and the next adjustment.

On a stall, stop accumulating process work. Decide whether the slice is too broad,
an architecture assumption is wrong, validation is mis-scoped, or user input is
required. Replan only the affected portion.

At Gate 2 set configurable slice, phase, total-active-time, and repeated-failure
limits. Stop and require reapproval when the same blocker fails twice, a limit is
reached, repository state drifts, or work expands materially beyond approved
paths, modules, or behavior. Do not normalize scope drift as implementation
detail.

## Non-negotiable safeguards

- Never modify, reset, delete, rebase, or force-update source, and never rewrite
  either frozen input commit. Do not move target before Gate 3; at closure only
  fast-forward it to the exact tested candidate.
- Never discard, stash, or overwrite unrelated changes.
- Never bypass hooks or required checks without explicit authorization.
- Never classify an uncertain source effect as irrelevant.
- Never claim preservation solely because target tests pass.
- Never retain Gate 3 approval after the tested candidate changes or target moves,
  except for the authorized fast-forward to that exact candidate.
- Never attach to target, delete branches, or push through implied approval.

Completion requires frozen recovery refs, complete commit and endpoint accounting,
resolved source-test evidence, two clean assurance audits, accepted final
validation, target-tree equality, correct closure ancestry, a clean worktree, and
enough ledger evidence to investigate a later-discovered omission.

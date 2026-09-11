# Qualify CI

Follow this algorithm in order. The branch lifecycle surrounds the existing
failure-ledger and repair-element logic; it does not replace it. Machine reports
are evidence, not Git authority. Machine interfaces never create branches,
commit, push, integrate, or clean worktrees; perform those operations only under
`git-workflow.interface.default`.

## 0. Resolve the requested terminal state

Structured arguments take precedence over conflicting prose in `request`.
Resolve `branch` from its structured value; when omitted, the branch defaults
to `master`. Resolve `push` from its structured value when present. When `push` is
omitted, infer false only from an unambiguous request for a green local branch
without target publication, and infer true only from an unambiguous request to
push or publish the green target. Otherwise ask whether to stop with a green
local branch or also push it to `origin/<branch>`.

Finish this decision before any Git mutation or remote CI dispatch. If the
interaction cannot obtain a required answer, return `blocked`; never silently
choose the target-publication state.

## 1. Snapshot the repository and protect existing state

Under `git-workflow.interface.default`, establish the repository and live
`origin` identity, the selected named local branch and its exact tip, whether
`origin/<branch>` exists and its live tip, every worktree that checks out the
target, in-progress Git operations, and staged, unstaged, and untracked state.
Reject an absent or unresolved local target. Do not stash, reset, clean, stage,
commit, or otherwise absorb unrelated state.

Return `failed` only when repository, origin, branch, authentication, workflow,
or runner validation fails before any side effect. After candidate creation or
any other side effect, route every non-success terminal condition to `blocked`
and report the completed effects.

Create or reuse one durable invocation record and one non-secret debug context
outside every temporary worktree. The invocation record owns the starting refs
and the identities and expected tips of every candidate worktree, candidate
branch, repair branch, and repair worktree created by this invocation. The CI
context owns stable
repository/workflow identity and immutable request-scoped reports; it never
owns credentials, credential-bearing URLs, or raw authentication output. Keep
the coordinator's failure ledger, branch assignments, and agent state outside
the machine-owned context; keep resource ownership there too, and do not extend
the context schema ad hoc.

Leave existing local and remote branches unchanged during qualification.

## 2. Create a fresh isolated candidate

Create a new collision-resistant candidate branch in the
`ci-debug/<branch>/<unique-id>` namespace and an isolated worktree from the
selected local target tip. Record both as invocation-owned before repair work
begins. If the proposed local or remote name already exists, choose a new name
regardless of its SHA; name or SHA similarity never proves ownership and never
authorizes adoption or deletion.

All source edits, generated changes, repair commits, and integrations occur on
the invocation-owned candidate or its invocation-owned repair branches. The
original target branch remains untouched until step 7.

## 3. Publish only the candidate and bind the context

Push the new candidate branch to a new branch of the same name on `origin`
without force, then record its exact SHA and remote tip. This temporary remote
candidate is required even when `push` is false because GitHub CI can qualify
only a reachable remote ref.

Supply the same context to every full-matrix, targeted-test, and repair-element
invocation. Persisted setup is a handoff aid, not authority: every invocation
must still revalidate authentication, repository identity, ref, and exact SHA. Never
place the context or invocation record inside a worktree that success cleanup
may remove.

## 4. Run and observe the complete exact-SHA matrix

Use `ci-debug._rtx.interface.run-ci` for the exact pushed candidate. When it
returns `state=pending`, invoke it again with the identical repository, ref,
SHA, context, and timeout until it returns a terminal report. Do not create a
new context merely to bypass an active request.

Retire superseded runs before dispatching replacement work through the
already-authorized CI control surface. If cancellation authority is
unavailable, record a capacity blocker and do not duplicate the full run. While
a matrix remains active, consume completed matrix-element reports and logs as
soon as an already-authorized CI surface exposes them. Do not wait for the
enclosing matrix: route completed failures and stalled elements into the
failure ledger immediately.

## 5. Preserve the existing red-matrix repair loop

While its report is red:

1. Group failures by matrix element. Give each repair subagent one element, the
   shared debug context, smallest selector set containing its known failures,
   the report, an invocation-owned repair branch and isolated repair worktree,
   and an allowed path scope. Before bounded-parallel dispatch, create and record
   one collision-resistant repair branch and worktree per element from the exact
   current candidate SHA. Apply the candidate no-adoption, collision, and
   expected-tip rules to every local and remote repair ref, and pass both the
   assigned branch and worktree to that element.
   Prefer exact failing test nodes, then the smallest set of containing test
   files when exact nodes are unavailable. Do not include selectors already
   known to pass, and retain every unresolved or unprobed failure in the ledger.
2. Run independent repair elements in bounded parallel through
   `ci-debug.interface.repair-element`; use a sequential fallback when workers
   are unavailable.
3. Review returned commits, diffs, and targeted-test evidence. Integrate accepted
   patches sequentially into the candidate under
   `git-workflow.interface.default`.
4. Push the integrated candidate without force and record its exact SHA in the
   invocation record.
5. Before the next complete matrix, use
   `ci-debug._rtx.interface.run-targeted-tests` on the exact integrated
   candidate for every affected matrix element. Start with the smallest
   selectors needed to detect integration interactions, then run each whole
   affected element. Verify every requested selector actually executed and
   return new or repeated failures to the ledger.
6. Only after every affected matrix element is green, use
   `ci-debug._rtx.interface.run-ci` again for the complete matrix.

Treat stalls as bounded failure classes and preserve the repair-element rule
that a repeated unchanged failure set returns a concrete blocked reason rather
than looping indefinitely. Pending, red, targeted-green, and whole-element-green
are nonterminal; targeted tests and whole-element tests never establish overall
green. Qualification stops only when the complete report is green for the exact
current candidate tip, or when a repair element or CI-capacity boundary returns
a concrete blocker.

## 6. Complete the existing prevention review

After the full report is green, **REQUIRED:** read [prevention.md](../prevention.md)
and complete its report-only prevention review before promotion. Do not modify
production code, tests, or suite selection from that review until the user
explicitly approves its proposal. If approved prevention work changes the
candidate SHA, return to the affected-element checks and complete exact-SHA
matrix loop before continuing.

## 7. Promote the green candidate locally by fast-forward only

Refresh the local target. Its current tip must be an ancestor of the exact green
candidate; a compatible advance may be included by a true fast-forward. If the
target diverged from or is already beyond the candidate, ask the user for
guidance without merging, rebasing, overwriting, or discarding either line.

When the target is checked out in exactly one worktree, immediately verify that
the worktree is still on the target branch, has no in-progress Git operation or
staged or unstaged tracked changes, and has no untracked collision with the
candidate. Use a fast-forward-only merge there. When the target is not checked
out, use a compare-and-swap ref update from its freshly observed current tip to
the green SHA. If it is checked out in multiple worktrees, Git refuses the
operation, or the preconditions change concurrently, ask the user for guidance.

This is the first point at which an existing local branch may change. A blocked
promotion preserves the candidate, context, reports, and recovery coordinates.

## 8. Optionally publish the exact green target

When `push` is false, leave `origin/<branch>` unchanged and return
`green-local` after step 9 cleanup.

When `push` is true, fetch the live `origin/<branch>`. If it exists, its current
tip must be an ancestor of the exact green SHA; a compatible advance may be
included by a true fast-forward. If it does not exist, the final push may create
it. Use an ordinary, non-force push of the exact green local target, then verify
that `origin/<branch>` equals the exact green SHA and return `green-pushed`
after cleanup.

If the remote is divergent, the ordinary push is rejected, or the verified ref
does not equal the green SHA, ask the user for guidance. Never integrate the
remote change or force-push. If the push may have succeeded but live
verification becomes unavailable, return `blocked` and report that the remote
target may already have changed.

## 9. Clean owned resources or preserve recovery evidence

On success, remove only invocation-owned temporary resources: candidate and
repair worktrees plus local and remote candidate and repair branches that the
invocation record proves this invocation created and that still have their
expected tips. If a resource is dirty, its tip changed,
or ownership is uncertain, retain it and report the exact cleanup gap without
changing the green result. Retain the durable context and final reports outside
the removed worktrees.

Every `green-local` and `green-pushed` response includes the repository
identity, target branch, starting local and remote SHAs, exact green SHA,
candidate ref, CI run, report location, publication status, and cleanup
disposition.

On any blocked or uncertain outcome, preserve the candidate, context,
reports, and recovery coordinates together with invocation-owned branches. Report the exact
repository, target branch, starting and current refs, candidate SHA, CI run and
report, completed Git effects, unresolved ledger entries, and failed
precondition. Never clean unrelated worktrees, branches, files, or contexts.

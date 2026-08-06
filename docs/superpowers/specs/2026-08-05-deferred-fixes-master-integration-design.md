# Deferred Fixes Master Integration Design

## Goal

Integrate `codex/deferred-fixes` with committed `master`, close the remaining
confirmed defects, validate and certify the exact result, and publish it through
an ordinary fast-forwardable push without altering the concurrently dirty root
checkout.

## Repository topology

Fresh remote inspection places `origin/master` at the common base of local
`master` and `codex/deferred-fixes`. Local committed `master` advanced once
during design review, from `b002c4c` to `ab7de8f`; the integration branch must
therefore refresh to that exact tip immediately before merging. The historical
merge base remains `b9ab63f`. The refreshed branches overlap in three
recurring-task blueprint paths, so the merge requires explicit conflict and
semantic-overlap review rather than relying on the earlier no-overlap result.
The root checkout is different: it has staged, unstaged, and untracked work that
overlaps the deferred branch and is changing concurrently. Integration therefore
occurs only in a clean worktree. A second remote fetch and base check is required
immediately before publication; any further advance reopens integration and
validation rather than being overwritten.

After validation, the dirty checkout's branch is renamed to a unique WIP branch.
That ref-only operation preserves its worktree and index. The validated clean
branch then becomes local `master` and is pushed without force. The WIP branch,
the deferred branch, and both worktrees remain until separately authorized
cleanup.

## Contract corrections

### Git workflow

Detached HEAD detection is read-only. Read-only inspection may continue, but
editing stops until an exact branch checkout or creation is authorized. An
already authorized branch action proceeds without a redundant prompt. The
existing blueprint already expresses this mutation boundary, so only authored
instruction prose changes.

### Standards maintenance

The single public interface remains. It classifies the requested outcome before
work begins:

- audit, check, or review is effect-free even when divergence is found;
- an explicit update authorizes the named semantic unit and the mechanical
  closure enumerated during preflight: pinned dependents, registered views, and
  directly declared evidence or enforcement consequences;
- expansion discovered before writes returns effect-free `needs-direction`;
- expansion discovered after an authorized write returns `partial` and stops.

The gateway gains an effect-free `audit-findings` outcome and an audit-or-update
description. Generated interface prose is synchronized from the blueprint. No
canonical standard or new interface is added.

## Installer test correction

The observed DBus failure proves an irrelevant host dependency even when the
transient host fault does not recur. A controlled clean-baseline run with
ambient DBus/keyring access disabled supplies deterministic RED evidence. The
correction lands in a separate test-only commit: subprocess packaging tests that
run scaffold use a test-owned keyring stored with private permissions inside
each temporary home. It persists across subprocesses and ignores ambient
desktop-secret-service routing. The separate strict native keyring test
continues to exercise the real host backend.

The live GitHub packaging test adopts the resolver setup already used by the
local v5 packaging test: create the minimum scaffold, build a minimal managed
runtime when `uv` is available, and verify that dispatcher reaches the managed
interpreter rather than failing at the resolver. This is test setup only; the
v6 fast-dispatcher implementation is not backported.

Because the live test installs the published default branch, local packaging
tests are the pre-push gate and the GitHub-default test runs after publication.

## Commit-hook isolation prerequisite

The staged-docstring validator integration test currently runs a raw nested
`git clone`. During a real commit hook that subprocess inherits the hook's Git
index routing, so the nested validator mirror can be built from the wrong index
and lose the `officina` package. Test setup must invoke that clone through the
repository's existing ambient-isolating Git test wrapper. The hostile-index
failure and the real commit hook are the RED/GREEN evidence; production Git or
validator behavior does not change.

## Staged-docstring compatibility prerequisite

The first provisional deferred merge exposed 726 genuine whole-module findings
under master's staged-docstring contract. Independent comparison found 687 on
the current first-parent versions of the same ten modules and 39 added by the
deferred versions. This is neither a merge false positive nor a reason to weaken
the validator. Preserve the provisional merge evidence, abort it, remediate each
first-parent module as its own validated and reviewed documentation-only commit,
then re-merge and correct the remaining deferred additions. The recurring-task
healthcheck's missing run-record blueprint dependency is a separate first-parent
contract omission and lands before the re-merge.

The first remediation also exposed a narrower enforcement defect. Requiring the
full four-section callable template for passive structural declarations produced
low-information pseudocode and `Wraps: none` boilerplate, while module-wide
import analysis missed lexical local/closure dependencies and repo-call products
passed to repo-local consumers. Before remediating the remaining modules, make
one concise compatibility update: classes whose sole decorator resolves to
stdlib `dataclasses.dataclass` and whose bodies contain only a docstring and
annotated instance fields with non-call defaults or resolved stdlib
`dataclasses.field(...)`, and undecorated subclasses with exactly one direct
builtin-exception base whose bodies contain only a docstring/pass/ellipsis, use a
summary-only structural kind;
behaviorful classes and every function/method retain the full profile. Repair
lexical dependency and product analysis in enforcement, remove the stale tracked
v27 policy duplicate and its no-argument autodiscovery while preserving explicit
legacy-path loading, and enforce exact canonical parity for the built-in
no-argument compatibility fallback. Do not add a broad private-helper exemption
or a parallel profile subsystem.

The second remediation shows that schema-first drafting must also be source-led.
`refactor-node` therefore gains one concise Python preflight: before the first
documentation draft, read the effective schema/grammar/config consumed by the
selected scope when present, and independently derive a relevance-filtered
ledger of branches, effects, exceptions, repo calls, and result flow, including
conditional aliases, lambdas, comprehensions,
generators, projections, and subscripts. Enforcement gains focused support for
lambda attribution and product propagation through output/projection positions,
with negative controls for operation-only positions. Path-sensitive conditional
import modeling remains deferred; truthful `[implicit]` declarations plus
semantic review cover it meanwhile. No canonical policy text changes.

## Documentation and assurance

The refactor completion record is changed from its stale self-referential final
sentence to a closed-state statement. Each behavioral correction follows a
real RED/GREEN cycle and receives independent spec and quality review. The
merged range then receives repository validation, pre-commit tests, a whole-
branch review, and repository-wide certification against the last content
commit. Drift is checked after certificate issuance, and no content changes
follow certification.

Catalog rebuild warnings are expected cold-cache diagnostics. Catalog write
failures caused by the sandbox's unwritable user cache are operational and need
no repository patch. Certification unavailability is closed by the final
certification step.

## Success criteria

- The committed master and deferred histories are present in the final graph.
- Detached-head and standards-audit behavior obey explicit mutation authority.
- Packaging tests are independent of ambient DBus state and the GitHub test no
  longer mistakes a missing test resolver for a product failure.
- Focused checks, validators, pre-commit tests, final review, certification, and
  drift checks are green for the exact published commit and certificate state.
- Local and remote `master` name the same commit after a non-force push.
- The dirty root checkout retains identical index and file content on its WIP
  branch, with no cleanup of unrelated branches or worktrees.

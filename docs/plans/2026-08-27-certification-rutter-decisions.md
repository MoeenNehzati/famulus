# Certification Rutter design

Status: scoped-guard correction implemented and validated in
`feat/certification-audit-pool`, 2026-09-13. Rutter contract repairs are committed
as `1b1c4512`; scoped guards, regressions and documentation form this separate
follow-up. Live certificate issuance remains unverified.

Version-6 preparation, signing and currentness now share an evidence scope:
selected nodes and prerequisites, certification authorities and Voyage machinery,
canonical basis, and declarations needed to resolve them. Unrelated tracked
edits and unrelated signing-incomplete sources no longer create a blanket veto.
Complete graph validity and repository mechanical validators still apply.
Whole-graph requests retain committed module markers, so deleting an independent
root cannot silently omit it. Legacy version-4/5 guards remain unchanged.
The basis explicitly covers runtime configuration and the new Voyage/Rutter
machinery, so committed changes to that machinery invalidate old certificates.

Validation: the working-tree precommit test suite with eight workers passed
3,908 tests, with 21 skipped and one existing fork warning (212.09 seconds).
The subsequently added stale-scope signer-entry regression passed separately
(1.52 seconds). All 32 validators passed (37.31 seconds), and generated artifacts
are synchronized. Three independent subagents returned GREEN on the implementation;
the final scope API/dependency declaration and signer-entry test were also reviewed
GREEN. Coverage includes unrelated staged edits and incomplete contracts,
authority/target/index/scope-membership drift, whole-graph deleted roots, and
agreement between scoped issuance and fresh currentness.
Final Ponytail reviews are GREEN after removing four redundant ancestry-walk
lines. The independent contract-repair commit passed its normal staged hook
suite: 3,895 tests passed, 21 skipped, and no secret-scan findings.
Staged validation then exposed missing dependency and scope docstrings that the
earlier working-tree run had not checked. Those declarations are corrected in
this follow-up; the other 31 staged validators passed.
The corrected staged docstring gate passed separately (539.10 seconds).

The first live trial exposed 45 missing contract sections in five Rutter APIs;
those declarations are repaired. The final subagent trial selected
`common.source.toml-io`, whose prerequisite is uncertified
`common.source.atomic-files`. Public initiation exited 2 with
`tracked certification input changed before certification`, naming 12 changed
authority/basis inputs. The plan, test files and dependency-registry edit are
absent from that refusal. No Voyage ID, worker dispatch or certificate was
produced. At that trial, the certifier's relevant inputs were still uncommitted.
The refusal identifies the actual evidence scope rather than applying the former
whole-repository cleanliness rule.

The installed dispatcher does not expose this branch's module name, so live
trials use the branch's public Voyage CLI. Host-keychain storage was explicitly
approved; public material, tasks and exact trial/validation evidence remain in
the worktree under `_build/interactive-certification/`. Live issuance and the
current-node skip run must still be verified from the committed repair state.

All three reviewers approved revision
`85d347f33772666bc65bf1b38d33ed72a7facec336abc9951f387b702d80f78c`:
architecture/Ponytail reuse, LLM responsibility boundaries, and correctness.
The first review found missing structural-child ordering; it was corrected and
the complete revision was re-reviewed by all three and committed as `656030c6`.

Implementation follow-up: all three reviewers returned GREEN after fixes for
certificate-loss drift, signer-bound dependency/facet/basis identity, bounded
worker file reads, inline prerequisite declarations, raw abort forwarding and
large mechanical-only continuations. Their verdicts are code/contract review;
test results are recorded separately. The branch-specific clarifications below
were incorporated during implementation.

Implementation validation before the live-trial repairs: working-tree precommit
suite with 8 workers: 3,894 passed,
21 skipped (198.49 seconds); all 32 validators passed (40.32 seconds).
Generated contracts are synchronized
and `git diff --check` passes. Three independent reviewers are GREEN. The plan
commit remains `656030c6`; the original implementation and audit follow-up were
committed together as `bdb28609`. The scope correction described above is
the separate follow-up in this plan update. No live certification
was issued from the dirty worktree. The historical scheduler blueprint filename
now owns the Voyage declaration; its old public scheduler route is removed.

Post-implementation audit found and corrected two gaps: replay after a successful
append could reject the source's replaced own-facet certificate, and malformed
reports could omit the failed assignment/node identity. Replay now reconciles
current same-owner evidence while retaining external prerequisite checks; failure
receipts carry the known task and node IDs. A regression reproduced the replay
failure before the correction. All three independent follow-up reviews are GREEN.

Acceptance coverage now also exercises settled assignments using the current
entrance, missing/duplicate real prerequisite consumption, propagated-currentness
restoration, actual exact-writer dependency rejection/issuance, and valid schema-v6
structural-child/cross-source ordering. The controller boundary is verified through
code, instructions and declared interfaces; no live host certification was run.

Test optimization followed `docs/contributors/optimizing-code-tests.md` from the
newer main checkout, read-only. The large mechanical-only fixture uses 51 nodes
instead of 62 while retaining over 100 actual machine records, zero worker audits,
complete exact-node coverage and all existing assertions. Three alternating
eight-worker benchmark pairs reduced median focused-runner time from 12.456 to
10.692 seconds and aggregate test work from 11.584 to 9.202 seconds, exceeding the
predeclared 0.5-second threshold. All 25 selected cases remain. Host timings varied;
this is a focused-selection result, not a full-suite speedup claim. Raw timings and
the evidence/contract map remain in worktree-local `_build/certification-*` artifacts.

## Objective and ownership

Certify dependencies before dependents through one durable certification Voyage.
The only LLM work is dispatching fresh workers and performing semantic audits.

| Actor | Responsibility |
| --- | --- |
| Dispatch agent | Pass supplied packets unchanged to fresh workers, retain handles, wait, relay exact raw results or host failures, and cancel/reap workers. |
| Semantic worker | Judge the assigned declaration against implementation/instructions and supplied prerequisite evidence; return the canonical audit report. |
| Machine evolutions | Resolve inputs, select work, authenticate evidence, account for capacity, parse/validate results, check drift, sign exact nodes, and produce terminal results. |

The dispatcher never selects an audit interface, constructs evidence, reads the
DAG, interprets or repairs reports, approves signing, or audits anything.
Workers never recurse, schedule, delegate, mutate repository/workflow state, or
sign. Missing semantic evidence produces `abort`, not additional audit subjects
or a controller review.

This revision supersedes owner projection, lexical `get-root`, and the routine
`validate`-then-`next` loop. Incremental exact-node signing remains required.

## Reuse and implementation baseline

Implementation scope, 2026-09-12: the user restricted all session changes to
the existing `feat/certification-audit-pool` branch and worktree. Use its
`skill-certifier`/`skill-drift` APIs throughout; the `node-certify`/`node-drift`
names below identify their counterparts on the newer baseline. Substitute the
branch-local names in paths, interface IDs and schema IDs. A repository-wide
rename or integration of other branches is outside this implementation scope.

Reuse these existing components:

- `src/officina/certification/hashing.py:certification_target_postorder`:
  strict canonical node ordering.
- `src/officina/certification/dependency_dag.py`: neutral audit DAG, interface
  prerequisites, module source/child relationships and validation.
- `skills/node-certify/_rtx/_semantic_audit_scheduler.py`: readiness, capacity,
  packet construction and report/dependency validation.
- `src/officina/certification/view.py`: authenticated currentness, facet reuse
  and intrinsic-versus-propagated renewal decisions.
- The existing certifier: mechanical checks, canonical hashing/manifests, freeze
  guards, signing, append-only history and post-write verification.
- Rutter: Reckoning history, locks, contextual validation and repeat-safe effects.

Reuse useful scheduler functions in machine callbacks. Remove the separate
scheduler state-file lifecycle and public route when switching certification to
the Voyage; migrate all declared callers and generated contracts in that change.
Do not wrap the old scheduler or build another scheduler/signing framework.

## Initialization and state

The dispatcher supplies the user's repository/targets and available host worker
slots. Code resolves and binds in the Charter:

- exact repository and current reviewed commit;
- registered module or behavioral-source targets; omission means the whole graph;
- fresh run identity and prefix;
- positive integer worker capacity excluding the controller; use one if unknown;
- positive per-Voyage retry interval, default 10 seconds, optionally overridden;
- validated dependency closure/DAG, selected input manifests and audit identities.
- shared certification-scope identity and whether the request covers the whole graph.

Unknown/interface targets, invalid capacity/graphs and inputs the certifier cannot
sign fail before workers start. Dirty tracked inputs within the evidence scope must not be audited for
issuance against a different committed version. Permitted local inputs retain
the existing byte-stability requirements.

The same scope is used for completeness, readiness, append guards and currentness.
It includes certification machinery as well as the targets; selecting a smaller
target does not authorize an uncommitted signer. Whole-graph mode also binds the
committed inventory markers. Unrelated edits outside this scope are permitted.

Reckoning is the sole persisted workflow state. Bound inputs, machine assignment
records and accepted events reconstruct outstanding tasks, passing reports and
issued/current nodes. Packet artifacts are derived data, not another state store.
Only the controlling session retains worker handles and buffered raw completions.
An interrupted session starts a fresh Voyage; currentness skips completed work.

Code computes each audited-input identity from the canonical selected content and
input manifests actually supplied for review, including declarations, bindings
and permitted local inputs. Reuse canonical hash/manifest rules, not a parallel
hashing policy. Certificate-history appends are not source-input drift.
Authenticate prerequisite evidence separately.

## Ordering and incremental signing

Resolve requested closure including module sources and children. Reuse strict
canonical dependency traversal, adding the neutral DAG's structural-child
constraints when forming certifiable node order; extend the existing ordering
helper narrowly where necessary. Retain the neutral DAG for audit prerequisites.
Before dispatch, verify coverage of every cross-node prerequisite and reject
cycles or inconsistent inputs. Do not use read-only drift's fallback ordering as
certification authority. Valid-fixture tests must prove this coverage, including
a parent with an otherwise unreferenced structural child module.

Select the first non-current certifiable node in that deterministic order.
No second owner graph, mutable root graph or lexical `get-root` is needed.
For the selected node, use existing facet-reuse/currentness rules and neutral
prerequisite closure to select required audits. Preserve intra-source interface
edges. Assign only ready tasks, sorted deterministically, up to free capacity.

External prerequisites require authenticated current certificate/facet evidence;
in-run prerequisites require accepted passing reports for the bound inputs.
Interfaces are audit facets, never certificate subjects. Source audits follow
required interface audits; module audits follow required child evidence.
Missing mechanical evidence fails before dispatch; semantic insufficiency found
by a worker produces `abort`.

After required audits pass, sign exactly that node and verify currentness before
proceeding. Reconcile currentness between nodes: propagated staleness can disappear
after a dependency is renewed without another audit or certificate append.
Never recursively certify the original target after auditing only one dependency.

## Worker packet and event contracts

Publish one strict `node-certify.semantic-audit-task/v1` schema with these required
fields and no additional properties. Reuse existing content/manifest structures
and canonical report schema; do not duplicate their definitions.

| Field | Contract |
| --- | --- |
| `schema_version` | Constant `node-certify.semantic-audit-task/v1`. |
| `task_id` | Nonempty run-scoped assignment ID minted by code; distinct from target ID, bound in Reckoning to this packet and input identity. |
| `kind` | `audit-interface`, `audit-behavioral-source` or `audit-module`. |
| `instruction_interface` | Exact allowlisted instruction interface and version selected by a static code mapping. |
| `owner_node_id`, `target_id` | Exact registered IDs; a module owns its module audit. |
| `reviewed_repository`, `reviewed_commit` | Bound repository and commit. |
| `audited_input_identity` | Canonical selected-input digest. |
| `input_manifest` | Canonical manifest identifying readable owned inputs and their digests. |
| `selected_content` | Existing content-selection data specifying declarations, bindings, instruction/implementation content and supplied boundary context to read. |
| `prerequisite_declarations` | Exact selected declarations/contracts for all direct prerequisites, without child implementation content. |
| `prerequisite_reports` | Full canonical direct in-run passing reports, with evidence, summaries and run-scoped task IDs. |
| `prerequisite_certificates` | Authenticated reusable evidence: subject/facet IDs, exact certificate identity, readable reference/digest, and selected facet/manifest/dependency facts needed for semantic composition. |

Code verifies references resolve to bound evidence/inputs before dispatch and
again before signing. IDs, digests and pass flags alone are not usable evidence.
Module packets contain bounded child evidence, not child implementation.
The dispatcher never discovers files or assembles context.

Code checks registration/version identity, signatures, hashes, currentness,
reusable facets and prerequisite completion. Update worker instructions to consume
these checked facts rather than repeat mechanical verification. Workers retain
semantic judgments about compatibility, effects, omissions, authority and
composition; mechanical validity does not establish those facts.

Workers return the existing `node-certify.semantic-audit-result/v1` report with the
packet's assignment `task_id`. The controller forwards exact raw text without
parsing, correction, extraction, summarization or verdict handling.
The dispatch evolution accepts exactly one strict envelope:

~~~text
{outcome: "worker-completed", task_id: string, raw_output: string}
{outcome: "worker-failed", task_id: string, reason: nonempty string}
~~~

Empty/malformed raw output passes the envelope boundary so code can classify the
failed report. `worker-failed` is only a host-reported spawn failure or worker loss,
never a controller judgment about report contents.

Contextual envelope validation checks the current evolution entrance and one
outstanding assignment belonging to this run. Invalid envelopes and unknown, old,
duplicate or settled assignments are rejected without mutation. Then machine code
parses the raw report and validates canonical schema, exact assignment identity
and exact unique consumed in-run dependencies. Malformed reports, mismatched
report identity/dependencies, `reject` or `abort` fail the run. Certificate reuse
is validated separately, not represented as fake worker reports.
An old raw report cannot be relabelled as a current assignment.

## Three nonterminal evolutions

1. **prepare-and-reconcile (machine):** bind inputs on first entry, reconstruct
   progress, check currentness, select the next node and record ready assignments
   up to capacity minus outstanding workers before exposing packets. Route to
   dispatch, signing if same-input reports suffice, or terminal outcome. When
   workers remain but no new packet is ready, request waiting. If unfinished work
   has neither outstanding tasks nor possible progress, fail rather than spin.
2. **assign-audit (dispatch agent):** spawn fresh workers using each packet's exact
   instruction interface, pass packets unchanged, wait for one completion/loss,
   and relay its raw envelope. Retain host handles and other already-arrived raw
   completions for later turns. Never reuse a worker.
3. **accept-audit-and-certify (machine):** parse/validate the event, fail or retain
   the passing report, and sign the selected node when its required audits pass.
   Recheck inputs/dependencies through the signer, verify currentness, and return
   to reconciliation. Partial interface completion returns without source signing.

Machine evolutions are repeat-safe: no duplicate assignments or settled results,
and no second append when the intended exact certificate is already current.
Accepted reports stay in Reckoning; certificates need not embed their digests.

## Exact-node signer boundary

Finish a narrow public exact-node mode of the existing certifier.
Inputs include node, reviewed repository/commit and expected audited-input
identity from the bound passing audit set: canonical node hash, input manifest,
dependency hashes, facets and certification basis hash. Local node hashes do not
substitute for dependency identity.
The Voyage also supplies its bound scope identity, original requested targets
and whole-graph mode. The signer verifies this scope at entry and around appends;
including authority nodes in the evidence scope never authorizes signing them.

Inside its existing frozen-input issuance path, the signer must:

1. Compare expected audited inputs with the actual inputs it freezes, closing the
   gap between caller precheck and signer entry.
2. Require canonical dependencies authenticated/current and reusable evidence
   still valid, without renewing dependencies or expanding module targets.
3. Retain mechanical checks and existing freeze/drift guards through append.
4. Issue at most the selected source/module, or skip if already current.
5. Verify that exact certificate's currentness before returning success.

Retain checks for commit changes, changed local inputs, source drift and dependency
drift. Do not weaken them for the unfinished worktree. External append is not a
rollback transaction; earlier valid certificates remain when a later task fails.

## Controller operation and terminal results

Ordinary operation uses initialization and `next` only. `next` takes an optional
envelope and `responding_to` entrance, validates during advancement, settles machine
work and returns a versioned typed dispatch message, terminal result, fault, or
`retry-later`. Preserve existing public operations; `validate` remains diagnostic,
not a required per-result tool call.

Use existing transaction machinery so status decisions, response validation and
advancement agree atomically. Concurrent calls may block on the existing lock.
If an engine exposes a live claimed machine effect that cannot safely proceed, return
`retry-later` with the configured delay without consuming a supplied response.
The controller waits and resubmits the unchanged envelope and entrance; code
rejects stale entrances. No controller state inspection/readiness judgment is
required. This retry is unrelated to waiting for semantic workers.

Verified branch implementation detail: this store holds the same lock throughout
machine effects and exposes no live pending claim. Concurrent `next` calls block
then revalidate; an uncertain non-repeat-safe effect returns a fault. Keep the
typed retry variant and calibration knob, but do not invent a claimed state or
nonblocking protocol merely to exercise that variant. Pending-effect retry is
conditional on an engine that can expose such a state safely.

The existing 100-step automatic continuation limit must also cover a large
mechanical-only closure. Keep 100 for other workflows; certification binds a
positive `automatic_transition_limit` of `max(100, 3 * node_count + 3)` from its
finite selected order. This permits completion without another LLM turn or an
unbounded continuation loop.

One live session owns dispatch for each Voyage; multiple independent controllers
and automatic recovery of old worker handles are unsupported.

`complete` contains requested targets, issued nodes, already-current nodes and
audit counts. `failed` contains exact task/node and reason. Worker failure, semantic
failure, invalid raw report or drift fails immediately; no LLM reviews or repairs
it. On terminal result or unrecoverable fault, cancel/reap remaining workers,
discard buffered events and return the machine result unchanged.
There is no final LLM certification judgment.

## Implementation order and acceptance

1. Reconcile unfinished branch changes with current names/APIs. Implement atomic
   `next` and the narrow signer boundary using existing code.
2. Implement the three evolutions, packet/envelope contracts and reused scheduler
   functions with Reckoning as sole workflow state.
3. Replace manual orchestration instructions and mechanical worker obligations.
   Update authoritative blueprints, generated contracts, canonical docs and
   installation inventory. Remove the old scheduler route and close its callers.
4. Extend existing focused tests with these scenarios, without a new framework:
   - Interface -> source -> module, structural children and cross-source edges:
     usable evidence, valid node-order coverage, exact dependency signing before
     dependents, no sibling/dependency renewal.
   - Two ready audits at bounded capacity: fresh assignments, out-of-order buffered
     completions settled once, concurrent `next` blocking and stale entrance safety,
     positive typed retry-delay validation, and uncertain-effect faults. Live
     claimed-effect retry applies only if an engine exposes a safely pending claim.
   - Invalid/old/duplicate assignments rejected without mutation; malformed raw
     reports, wrong report IDs, missing/duplicate dependency consumption, semantic
     reject/abort, spawn failure and worker loss fail without signing.
   - Changed inputs after audit and at signer entry, dependency drift, and mutation
     during issuance rejected by existing guards.
   - Repeat-safe re-entry and fresh runs after interruption skip current
     certificates, including currentness restored by dependency renewal.
     A mechanical-only closure exceeding 100 transitions completes without
     dispatching a worker or requiring another controller call.
   - Controller trace contains only initialization, packet dispatch, raw event
     forwarding and host lifecycle. Code alone parses/classifies/signs and builds
     final results. Public/generated-contract checks cover the new route.

Run supported focused/validator checks on the final implementation and retain
evidence before claiming completion. Green plan review is not implementation
validation.

## Non-goals

No daemon, persisted runner, leases, heartbeat, resume protocol, automatic semantic
retry, secondary journal, generic scheduler, nonblocking-lock redesign, extra LLM
reviewer, batch completion protocol, worker acknowledgement evolution, optimized
root selection, independent interface certificates or cryptographic report
embedding. Do not replace incremental signing with a final recursive signing pass.
Keep the per-Voyage retry calibration knob.

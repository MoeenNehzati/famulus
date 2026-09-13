# Certification Rutter design

Status: cleanup and full/unchanged-repeat benchmarks complete in
`feat/certification-audit-pool`, 2026-09-13. Small-change speedup is not yet
verified: the live incremental scope check below exposed a planner/reuse gap
and a separate target contract blocker. Production cleanup is committed as
`5efa8e37`; its independent reviews and normal hooks were GREEN. Fresh reducer
certification took 258.633 seconds:
61.359 machine, 197.275 LLM/host/controller combined. A mechanically chained
current-certificate repeat took 10.312 seconds with zero audits or issuance.
Pure model-inference time is not exposed by this host. Details and limits are
in “Cleanup and measured repeat” below; earlier runs remain historical evidence.

Scoped-guard correction was implemented and validated earlier. Rutter contract repairs are committed
as `1b1c4512`; scoped guards, regressions and documentation are committed as
`48560387`. Default standalone docstring validation became opt-in in `e55a3851`.
The small live certification trial is complete at `3c660108`.

The successful target is `tight-mode`, with the previously uncertified
`tight-mode.source.gateway` ownership prerequisite. Retained machine history
records source issuance before module issuance. Three fresh workers performed
the interface, source and module audits; the controller forwarded their raw
reports unchanged and machine code handled selection, validation and signing.
The public terminal receipt issued both nodes. A fresh second Voyage returned
both already current, with zero audits, zero new certificates and no dispatch.
At that commit, independent read-only drift verification reported both `certificate-current`,
with no concerns or stale work. Both certificate logs still contain one entry.
This verifies an ownership dependency, not a cross-module dependency.

Evidence under `_build/interactive-certification/`:

- `small-tight-mode-next-3-terminal.jsonl`: successful issuance receipt.
- `small-tight-mode-skip-next-0-terminal.jsonl`: fresh current-node skip receipt.
- `tight-mode-issuance-order.json`: ordered issuance records from the Reckoning.
- `tight-mode-currentness.json`: summary of independent read-only drift output.

Certificates remain under `skills/tight-mode/.certificates/`; both Voyages,
exact packets and raw response envelopes are retained in the worktree.

A subsequent monitored trial selected `send-feedback._rtx`: a 207-line Python
source with two independent interfaces, followed by source and child-module
audits. This adds real interface concurrency and Python behavior while remaining
an ownership-dependency case. The top-level feedback skill was excluded because
its email dependency closure would broaden the trial.

The first run failed closed on the host's second-worker thread-limit refusal.
Removing the extra controller agent let the main agent dispatch both fresh
semantic workers directly. The machine selected the packets, and the controller
passed them unchanged. The first raw completion rejected `file-issue`: publication
precedes local confirmation, so failure cannot guarantee that nothing was
published. The unchanged report produced terminal `failed` with no machine fault
or issuance. The sibling `check-route` worker aborted for insufficient evidence
supporting the declared absence of network effects; its report was retained but
not submitted after termination. Independent review is GREEN on rejection handling
and the publication finding, not on target certification. Read-only currentness
confirms both selected nodes still lack certificate logs. No external issue was
created. Source/module auditing, signing and the skip path were not reached.

The direct run took 257.785 seconds: initiation 61.567, first dispatch 59.227,
and rejection processing 0.890 seconds. The remaining 136.102 seconds cover host
dispatch, semantic audits, output-format clarification and raw-report transport;
they are not a measurement of model inference. Both fresh workers were admitted
and their recorded work overlapped, but exact individual spawn/final-receipt
times were not captured. Both needed the result schema's location because the
audit instructions did not give its complete JSON shape.

A separate profile of the shared read-only state derivation took 154.920 seconds
under instrumentation. Currentness evaluation consumed 89.370 seconds, including
298 scope builds taking 58.170 seconds and 946 readiness checks taking 28.572
seconds; 2,850 Git-runner calls took 27.405 seconds. These cumulative timings
overlap and are not additive or comparable to the unprofiled baseline as a speedup.
That profile preceded the production optimizations described below. Its bounded
candidates were explicit result formatting in the existing worker instructions,
reuse of invariant scope/path work within one observation, and equivalent batched
Git readiness. Evidence freshness and signing guards remain required.

All monitored receipts, packet files, raw reports, timing summaries and the profile
are under `_build/complex-certification/`. The direct Voyage is
`complex-feedback-direct/r-0cd9b481bba349d7865c6d65e07ba163/1`; its terminal receipt is
`feedback-direct-next-1.stdout.jsonl`. `performance-summary.json`,
`repository-currentness-profile.txt` and `selected-currentness.json` contain the
measurements and independent state evidence. This result update remains uncommitted.

### Accuracy and performance adjustment, 2026-09-13

Commit `efe078b7` reuses path conversions within each scope observation, hashes
already-selected canonical basis paths, and reuses the existing batch Git readers
for per-path readiness. It adds no persistent evidence cache or scheduling layer.
Fresh bytes, modes, index stages, object IDs, scope identity and signing guards
remain checked. The three existing semantic audit instructions now include the
complete result JSON shape and dependency-consumption shape.

Controlled local comparisons retained exact results: all 298 scope objects matched
while construction fell from 16.665 to 4.270 seconds. With those scope changes held
constant, batching retained complete canonical-state/currentness equality and
reduced an unprofiled observation from 41.966 to 20.228 seconds (51.8% less), with
provenance Git calls reduced from 2,846 to 11. These are single comparable pairs,
not medians or whole-Voyage speedups. Mixed clean, dirty, staged, conflicted,
missing, mismatched, untracked, outside-root and path-alias inputs also retained
per-path readiness results. Evidence: `scope-performance-comparison.json`,
`readiness-batching-observation.json`, and `readiness-batching-parity.json`.

Independent correctness and Ponytail reviews are GREEN. Focused verification
passed the two scope/basis checks and 69 batching-related tests. The normal commit
hooks passed 3,921 tests with 21 skips and one existing fork warning. No optional
docstring validator or additional full validator suite was run for these changes.

Commit `9beb79a4` corrects the feedback contract: issue publication may precede
an unsuccessful local confirmation, and authentication probing may contact the
configured GitHub hosts. It changes declarations/docstrings only; no issue was
created. Independent review is GREEN and normal hooks passed 3,921 tests with
21 skips. Feedback remains uncertified: dependency/access and runtime-binary
metadata gaps require separate work. Registering its wider dependency closure
would overscope this experiment; existing PythonMachineInterface code is guarded
by the certification basis, and whether it requires separate ownership remains
a policy question. Evidence: `feedback-dependency-scope.json`.

The bounded repeat therefore selects the existing `rutter.source.authoring`
target, with `rutter.source.values` and `rutter.source.history` prerequisites:
three sources totaling 2,286 lines. Independent review approved this scope,
without preapproving its semantic audits. The trial uses committed HEAD
`9beb79a42d28cd1a7007e974f9e394b4482db10c`; earlier tight-mode currentness is
historical because certification-authority changes can stale earlier certificates.
Live receipts and per-call timings use the `authoring-*` prefix. Dispatch and
completion-handling tool boundaries are in `repeat-controller-events.jsonl`;
these are not exact inference or host final-message-arrival measurements.

The repeat completed: the machine issued values, then history, then authoring
after six fresh semantic workers. All six passed without result-format
clarification. The controller forwarded their raw reports; retained packet and
raw-envelope equality against the Reckoning passed for all six assignments.
This transport check compares saved artifacts with machine history, not an
independent export of host final messages. It is not an additional semantic vote.
The machine terminal is `complete`, with those three issued nodes and six audits.
A fresh Voyage returned all three already current, with zero audits, zero new
certificates and no worker dispatch. Exact receipts:
`authoring-next-6.stdout.jsonl` and `authoring-skip-next-0.stdout.jsonl`.

The first full run took 1,071.100 seconds (17m51s): 251.105 seconds in public
machine CLI calls and 819.995 seconds outside them. The latter includes semantic
work, host operations, controller handling and context compaction; it is not an
inference measurement and must not be excluded from user-visible latency.
Initialization took 6.690 seconds, first dispatch 6.558, interface-report
transitions 13.558–14.602, and source-report/signing transitions 63.514–67.796.
The fresh skip took 34.511 seconds end to end, including 14.803 seconds in its two
CLI calls. These live targets differ from the failed feedback baseline, so no
whole-run speedup is inferred. Machine preparation improved in controlled
comparisons, but this trial does not establish minimum end-to-end latency:
outside-CLI time dominates, and source-report/signing transitions are the longest
measured CLI calls. Signing itself was not timed separately.
Evidence and runnable transport/timing extraction: `authoring-monitor-summary.json`
and `summarize-authoring.py`.

One worker's initial milestone command used an incorrect `scripts/` path;
the correct `_rtx/_milestone_writer.py` path was supplied. No start record was
fabricated. The first independent reporting helper also failed because the
skill-drift CLI's displayed skill nodes exclude these non-skill source targets;
the raw CLI report is retained as `authoring-currentness.stdout.json`. Exact
source verification therefore uses the existing shared read-only currentness API,
without changing the production reporting interface or rerunning validators.
That fresh observation passed in 6.313 seconds: all three exact sources are
current, have no concerns, and each certificate log contains one entry after the
skip. The observed source commit remains `9beb79a4`. Evidence:
`authoring-exact-currentness.json` and `check-authoring-currentness.py`.
Independent final evidence/Ponytail review is GREEN after distinguishing whole
CLI transition duration from signing duration. No further production changes or
experiments are required for this bounded iteration; the plan result update is
the only remaining tracked modification and is uncommitted.

### Ultra Ponytail and readiness audit, 2026-09-13

The follow-up audit found no architectural blocker. Optional cleanup can remove
approximately 123 production lines with no dependency change: reuse the shared
confined-file reader in the signer, remove its three thin batch-reader wrappers,
drop already-covered scope declarations, and reuse already-selected basis paths
at signer entry. Two smaller candidates remove a redundant task-membership filter
and a second DAG validation after closure validation. These are review findings,
not applied changes or readiness requirements. Preserve distinct readiness error
diagnostics, fresh observations, append guards, retry calibration and the complete
worker-local result shapes; none is replaced by a new framework.

The superseded 2026-08-26 scheduler plan now points here instead of advertising its
retired public scheduler and pending synchronization. No production code changed
during this audit, and no tests, optional docstring check or additional validator
suite was rerun. Existing test coverage was inspected against plan acceptance;
the previous live receipts and currentness evidence were reviewed as retained
evidence, not described as new executions.

Readiness is bounded to this branch's Linux public CLI. An independent
correctness review of orchestration and a separate signer/scope review are both
GREEN, with no actionable blocker in the reviewed changes. A fresh installed
dispatcher dry-run for `skill-certifier._rtx.interface.certification-voyage@1`
returned `dispatcher.module_not_found`, `Module not found: skill-certifier`.
Installed-MCP availability therefore remains unresolved; no plugin was changed.
Native Windows/macOS signing execution and minimum end-to-end latency remain
unverified. Feedback/atomic-files/loose-mode declaration findings remain target
work, not waived requirements or certifier defects. Both plan updates remain
uncommitted; the committed implementation stays at `9beb79a4`.

### Cleanup and measured repeat

The subsequent requested cleanup implements those cuts: shared confined reads and
Git batches, already-selected basis paths, redundant scope additions, DAG check
and task filter. Production code is reduced by 116 lines. The duplicate full
observation after exact signing is removed: the exact signer still verifies
authenticated currentness and frozen inputs, and immediate reconciliation still
observes the whole selected scope before any next dispatch or terminal result.
No validator result is cached across nodes; unrelated repository changes may
legitimately occur between scoped observations.

Independent correctness/Ponytail reviews are GREEN. Existing focused owners pass:
100 signer/hash/readiness checks and 23 Voyage checks. The existing drift case now
also mutates a different bound node immediately after signing and requires failure
before further dispatch. Other test identities and physical-boundary evidence are
retained; no test-runtime optimization claim is made.

Cleanup commit `5efa8e37` passed its normal hooks: 3,922 tests passed, 21 skipped,
with one existing fork warning; pytest 63.72 seconds, runner 64.64 seconds.
Secret scanning passed. No optional docstring validator or extra validator suite
was run. The test-standard query used `task.optimizes-test-performance=false`.

The same authoring chain then needed only mechanical renewal: existing semantic
evidence was reusable. The machine issued values, history and authoring in order,
with zero audits and no worker dispatch. To measure fresh semantic work without
deleting certificates, the independently approved bounded example was
`rutter.source.reducer`: 182 lines implementing immutable run-tree transformations,
with history and values prerequisites already current. Two fresh workers passed
its interface and source audits. The machine issued only reducer. Saved packets
and raw envelopes exactly match the corresponding Reckoning records.

Measured on this Linux host, committed inputs at `5efa8e37`, one worker slot,
with no overlapping test suite. Totals run from initialization subprocess start
to terminal subprocess exit; later verification and report delivery are excluded.
These are individual live measurements, not medians or cross-target speedups.

| Case | Total wall | Machine CLI | LLM/host/controller combined | Semantic audits |
| --- | ---: | ---: | ---: | ---: |
| Renew authoring chain with reusable evidence | 153.076s | 137.378s | 15.697s | 0 |
| Certify previously uncertified reducer | 258.633s | 61.359s | 197.275s | 2 |
| Reducer already current, chained operations | 10.312s | 10.233s | 0.079s | 0 |

For reducer, machine calls took 5.209 seconds to initialize, 5.178 to dispatch,
10.248 to process the interface report, and 40.723 for the source report and
signing. Inclusive exact-signer time was 29.892 seconds, including 12.577 seconds
of mechanical checks. Five full observations totaled 25.542 seconds outside
the signer; the remaining CLI time includes initialization and transport.
Mechanical-check duration is nested inside signer duration and must not be added
again. The source transition has two observations, retaining pre-sign and
next-reconciliation checks after removal of the redundant middle scan.

The workers' first-to-last tool spans totaled 49.334 seconds (23.722 and 25.612).
They include semantic work and file/log tools but exclude final JSON generation
after the last tool call. The remaining 147.940 seconds include that generation,
spawn/delivery, controller reasoning and raw-report forwarding. Neither number is
pure inference time or complete subagent runtime; 197.275 seconds is the honest
combined non-machine elapsed time exposed by this host.

An initial warm repeat took 63.444 seconds despite only 10.241 seconds of machine
work. That exposed avoidable controller/tool escalation gaps. Chaining the two
existing initialization/next calls mechanically in one tool orchestration, using
the default sandbox for this current-certificate path, reduced the repeat to
10.312 seconds. Both runs had zero audits and zero issuance. Use this batching
where the next operation is determined by typed machine output; do not insert an
LLM turn for bookkeeping. Signing calls still use the authorized host-key access.

The measured duplicate machine work is removed. No further actionable machine
hotspot was identified by the bounded review; this is not proof of minimum
latency for every repository. Per-node mechanical validators, fresh byte/scope
checks, locks and append verification remain required. Full semantic runs still
incur substantial report-generation and host/controller time.

Independent read-only currentness verification reports values, history, authoring
and reducer current, without concerns. Their log entry counts are respectively
2, 2, 2 and 1; the skip runs appended nothing. External timer wrappers preserve
arguments/results and live only in `_build/readiness-benchmark/`, alongside exact
receipts, `authoring-summary.json`, `reducer-summary.json`, `fast-skip-summary.json`,
phase JSONL files, raw packets/envelopes and `currentness-before-doc-commit.json`.
`summarize.py` checks transport equality and interval accounting. No production
instrumentation, persistent evidence cache or new scheduler was added.
Independent benchmark review is GREEN, including phase nesting, partial worker
timing limits and the distinction between first certification and evidence reuse.

The committed-input trial at `e55a3851` initialized successfully and dispatched
one fresh `audit-interface@3` worker for
`common.source.atomic-files.interface.python-api`. Its unchanged raw report
produced terminal `failed`: the atomic-files contract misstated mode defaults,
missing-predecessor support and conflict permission effects, and Windows compare
replace/delete omitted the promised predecessor ACL checks. No certificate was
issued. Two independent reviewers confirmed the findings and approved a narrow
repair: correct the declarations and reuse the existing ACL helper at the Windows
mutation boundaries. The repair was committed as `3c660108`; four focused cases
passed and its normal hooks passed 3,913 tests with 21 skips. Native Windows
execution remains unverified. A fresh atomic-files audit then found further
invocation, failure-effect and temporary-file declarations missing. Those
remaining YAML-only repairs were left outside this small trial. A `loose-mode`
trial also stopped on undeclared host-selected planning-skill delegation; its
behavior was preserved. Failed Voyages and raw reports remain retained under
the `committed-toml-io-*`, `repaired-toml-io-*` and `small-loose-mode-*` prefixes.

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
An optional staged docstring check exposed missing dependency and scope docstrings that the
earlier working-tree run had not checked. Those declarations are corrected in
this follow-up; the other 31 staged validators passed.
The corrected staged docstring gate passed separately (539.10 seconds).

The first live trial exposed 45 missing contract sections in five Rutter APIs;
those declarations are repaired. An earlier dirty-input subagent trial selected
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
the worktree. The successful trial verifies this branch's public CLI route;
installed-MCP invocation remains unverified because that dispatcher lacks the
branch's module name.

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
structural-child/cross-source ordering. At that earlier audit, verification covered
code, instructions and declared interfaces; the successful live CLI trial is
recorded above.

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

### Incremental benchmark scope check, 2026-09-13

A live reducer source-description experiment at `70981223` changed only its
remainder facet, but also changed the global certification basis because that
basis includes Rutter blueprints. The Voyage selected five semantic tasks:
values interface/source, history interface/source, and reducer source. It
requested the values interface first. No worker was dispatched and no
certificate was issued; the Voyage stopped before semantic worker execution
and is retained as diagnostic evidence
under `_build/incremental-benchmark/`. This is not a completed speed benchmark.
The experimental reducer description was restored to avoid leaving existing
Rutter certificates stale merely for measurement. An ordinary source outside
the certification basis is needed to isolate incremental audit savings.

Independent code review found a fail-closed planner/reuse mismatch in that
Rutter trial. `semantic_stale_vertices()` selects only reducer's remainder
when it sees that meaningful facet drift, omitting its unchanged interface.
However, `_certification_support._certificate()` disallows reuse when the
owning source still has `certification-basis-mismatch`. After the four planned
prerequisite audits, reducer packet construction would therefore reject the
required interface evidence. That later failure was inferred from the code;
the four audits were not run. The five-task selection and first values-interface
dispatch were observed live. This remains an unresolved implementation gap;
the earlier GREEN cleanup review does not establish readiness for this case.

The fallback `common.source.dates` trial targeted a 60-line, dependency-free
source outside the certification basis. Its first fresh interface worker
rejected the contract: `format` accepts Python date/datetime objects and `parse`
returns a Python date, while the structured input/output types declare strings.
The machine consumed the unchanged raw rejection and returned terminal `failed`
without issuing a certificate. Reinterpreting the schema's `date_formats` as
Python-object union descriptions was not independently justified; this benchmark
does not change the schema, runtime, or audit standard to obtain a pass.

No successful full-versus-small-change pair was completed, so no audit latency
reduction or speedup percentage is claimed. The prior 258.633-second cold run
and 10.312-second unchanged skip do not prove small-change speedup. Retained
artifacts include exact init/dispatch/terminal receipts, the raw rejection
envelope, timing records, and before/changed/restored canonical snapshots in
`_build/incremental-benchmark/`. After restoring the reducer description,
reducer/history/values certificates were verified current with no concerns and
unchanged certificate-entry counts. No certifier production code was changed.

## Non-goals

No daemon, persisted runner, leases, heartbeat, resume protocol, automatic semantic
retry, secondary journal, generic scheduler, nonblocking-lock redesign, extra LLM
reviewer, batch completion protocol, worker acknowledgement evolution, optimized
root selection, independent interface certificates or cryptographic report
embedding. Do not replace incremental signing with a final recursive signing pass.
Keep the per-Voyage retry calibration knob.

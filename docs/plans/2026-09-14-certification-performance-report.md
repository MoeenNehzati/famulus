# Certification performance investigation — 2026-09-14

**The hybrid check split is functional; the few-second machine target remains unmet.**
The final measured cohort took 12.394 → 13.373 seconds for full certification
and 8.201 → 7.883 seconds after a small declaration change. Full runs consistently
incurred extra cost; variability prevents claiming a small-change speedup.
Local validator gates averaged 168 ms in full runs and 139 ms in small runs.

Worktree: `.worktrees/certification-audit-pool`; branch:
`feat/certification-audit-pool`. Benchmark baseline HEAD:
`b315d24f0b507469dd47a0b90fa657c3338941df`.

## Workload and limits

The main example is `email-triage._rtx.source.rtx-decision-sink`, with dependencies
`common.source.famulus-paths` and `email-triage._rtx.source.rtx-init`: three actual
repository sources, four owned files, approximately 14 KB of implementation.
This exercises a Python process interface, route smoke, dependency ordering,
certificate publication, and selective semantic scheduling.

Each independent trial starts a new Python process in a committed disposable
snapshot. Within that process it runs full certification, commits a source-only
description edit, then runs small-change and unchanged certification. Small and
unchanged are therefore **warm follow-ups**, not independent cold trials.

Synthetic semantic replies remove LLM and host scheduling variation from these
machine measurements. All normal machine gates and signed certificate writes
still execute. Fixture creation, interpreter/import startup, real worker time,
MCP transport and LLM controller time are outside these numbers. Do not interpret
them as user-visible certification duration or new semantic confidence.

Each within-cohort comparison checks common committed file bytes and modes.
Absolute timings differ across cohorts; they must not be treated as one
continuous controlled experiment. Rutter time is elapsed `next` minus timed
certification callbacks, not isolated engine CPU time or LLM controller time.

## Retained implementation

- Seal canonical claims, packets and mechanical evidence once; authenticate
  current inputs and certificate histories on continuation. The standalone
  signer retains its complete self-validating path.
- Share graph-dependent validators, deterministic checks and two independent
  route-smoke traces across nodes requiring renewal. Run the remaining local
  validators before each stale node's audit, and require its signed receipt
  before issuance. Current dependencies supply context. The existing graph
  marker partitions validators; mixed validators stay intact.
- Select each rule's relevant subjects before reads/parsing. Complete graph and
  contract context remain available; repository-wide CI defaults are unchanged.
- Reuse the canonical graph in fresh route-smoke children through private stdin,
  with repository/type checks and the original freshness guards.
- Use ancestor lookup for ownership, exact tracked membership for literal basis
  paths, and the installed safe C YAML loader with Python fallback diagnostics.
- Reuse the existing in-process validator lifecycle for local checks. Keep one
  private graph copy per validator and compare it with the retained original;
  the second comparison copy was redundant. Mutation/isolation tests remain.
- Remove duplicate Reckoning validation and history-prefix construction. Use
  the dispenser's returned contract instead of a conflicting Compass recipe.
- Keep the controller limited to dispatch and raw-result forwarding. Its
  instructions avoid truncated reads, inherited parent history where supported,
  protocol rediscovery and duplicate JSON output. Semantic criteria are unchanged.
- Correct the `famulus-paths` declaration's unused filesystem-read and frozen
  environment claims; this changes the contract, not runtime behavior.

No service, persistent graph cache, second scheduler, unsigned reuse, or relaxed
signing boundary was introduced. Detailed algorithm:
[Certification and Drift](../officina/certification_and_drift.md).

## Final hybrid measurement

A separate B,A,A,B,B,A cohort compared the pre-hybrid baseline with the hybrid
and redundant-copy cleanup. The three varying production files were
`_certification_preparation.py`, `_certification_support.py`, and `runner.py`.
All 1,301 common committed files and modes matched; common tree digest:
`41fbb40b450fa3255a386258554bb64660408d4f4cfc421b368e72221df2fa53`.
The measured bytes are retained in `hybrid-validators-frozen-v2`. A subsequent
essentiality review removed an unreachable standalone-signing fallback and
adapted its test fixture; these timings precede that behavior-preserving cleanup.

| Final cohort measure | Before | Hybrid with copy cleanup |
| --- | ---: | ---: |
| Full machine median | 12.394 s | 13.373 s |
| Small-change machine median | 8.201 s | 7.883 s |
| Unchanged machine median | 5.844 s | 5.805 s |
| Full upfront validator median | 2.765 s | 2.097 s |
| Full all-validator median | 2.765 s | 2.644 s |
| Small-change all-validator median | 2.492 s | 1.917 s |
| Full average `next` | 865 ms | 1,120 ms |
| Full average Rutter time outside callbacks | 81 ms | 112 ms |

Full-run ranges were 10.650–12.687 seconds before and 11.853–13.923 seconds
after. The unchanged control and baseline validators also slowed relative to
the preceding cohort. Do not attribute cross-cohort timing differences to the
one-line cleanup or claim a general speedup. In the final full runs, local gates
total 0.524 seconds and average 168 ms; fresh fingerprints add 0.338 seconds.
The entire local-receipt operation totals 1.042 seconds. Small-change local gates
average 139 ms. The intentional lazy checks and their freshness guards have a
cost, even though total validator time is lower in this cohort.
Paired small-change differences were +0.504, -0.671 and +0.587 seconds; no reliable
small-change speedup is established. All three paired full runs were slower by
0.686–1.529 seconds.

An actual selected-node failure probe against the final runtime passed all
assertions: adding an unused function with an undefined name left the graph
gate passing (15 items), then failed the local gate (10 passing items, one
undefined-name failure) before any semantic dispatch or signature. Both gates
selected decision-sink alone; its current prerequisites remained context.
All certificate logs stayed byte-identical. Evidence is retained in
`hybrid-fault-summary-v2.json`.

All 18 cases completed with the expected 5/1/0 semantic counts. Full hybrid
runs executed one graph gate for three nodes and one local gate per node.
Small runs selected only decision-sink in both groups. Unchanged runs executed
neither group. Graph gates executed 15 items across 14 validators; each local
gate executed 11 items. Optional docstring validation was excluded.

Sealing the local receipts took 0.0029 seconds across a full run. Full Rutter time grew
from 0.485 to 0.674 seconds, while the Reckoning grew from 237 to 529 KB as it
retained updated preparations. The size increase is a plausible contributor,
not an isolated causal measurement. Nested durations must not be added to
their parent timings; medians of components need not sum to the median total.

## Earlier cohorts and rejected shortcuts

These are separate within-cohort comparisons, not estimates for today's final
runtime. Their raw evidence remains under `_build/certification-release/`.

| Change/cohort | Full before → after | Small before → after | Primary evidence |
| --- | ---: | ---: | --- |
| Route-smoke graph reuse, ancestor ownership, safe C YAML | 22.166 → 15.526 s | 18.421 → 12.705 s | `overnight-summary.json` |
| Basis lookup and Rutter simplifications | 18.360 → 17.761 s | 14.919 → 14.425 s | `simple-cuts-summary.json` |
| Initial five-file validator scope | 17.579 → 14.980 s | 14.583 → 11.340 s | scoped-validator trial records |
| Complete DFS subject selection | 14.108 → 11.170 s | 10.616 → 7.654 s | `dfs-validators-summary-v2.json` |
| First hybrid, before graph-copy cleanup | 10.760 → 11.802 s | 7.377 → 7.683 s | `hybrid-validators-summary.json` |

Initial isolated experiments found two route-smoke traces fell from 5.102 to
0.776 seconds, owner lookup reduced graph building from 2.19–2.23 to 1.45–1.63
seconds, and standards validation fell from 1.97–2.03 to 0.77–0.78 seconds.
Trace mappings and complete graphs matched; malformed YAML diagnostics retained
their original fallback. These measurements do not isolate total-run effects.

The first lazy implementation launched a process for each local gate: a full
pilot took 14.594 seconds, with roughly one second per local gate despite only
16 ms of item work. Reusing the existing in-process lifecycle reduced local
gates to roughly 145 ms in the first matched hybrid cohort.

Serial graph checks did not beat eight workers in two pre-copy-cleanup trials
(full graph gates 2.142/2.166 seconds versus parallel median 1.905). That does
not establish the optimal worker count after copy cleanup or for larger
closures. Eight graph workers and one local worker were retained without a new
threshold or configuration policy. A scheduler switch, deferred freshness
checks, shared mutable ASTs, and unsigned unchanged-state reuse were rejected.

### Function accounting for the 17.761-second full run

The actual median full trial (`simple-after-triage-v2-3`) provides the following
non-overlapping totals. This uses one run so the components sum exactly, rather
than combining independent component medians.

| Recorded function or residual | Calls | Total |
|---|---:|---:|
| `run_suite` — repository validators | 1 | 8.750 s |
| `accept_and_certify` — report acceptance, evidence refresh and conditional issuance | 5 | 2.933 s |
| `observe` — upfront canonical observation | 1 | 2.507 s |
| `prepare` — continuation reconciliation and scheduling | 6 | 1.726 s |
| `require_stable_dependencies` — both route-smoke traces | 1 | 0.927 s |
| Rutter residual outside certification callbacks | 6 continuations | 0.502 s |
| `input_fingerprint` during initialization | 2 | 0.204 s |
| Remaining initialization, registry creation and small callbacks | — | 0.211 s |

Initialization accounts for 12.588 seconds; registry creation takes 0.009
seconds, and six `next` calls account for 5.164 seconds. Across initialization
and continuation, `input_fingerprint` runs **19 times for 1.902 seconds total**.
That time is already included above, largely inside acceptance and preparation.
The trace does not separately time the internal validator functions, canonical
observation functions or signature operations. The 2.933-second acceptance
total must not be labeled cryptographic signing time. Exact accounting is in
`full-function-breakdown.json`, generated by `full-function-breakdown.py`.

## Actual LLM work

Eight fresh-worker rollouts from the real earlier runs were examined using
task-start/task-complete events and public tool-call intervals. All used
`gpt-6-astra` with high reasoning effort.

| Actual semantic worker | Task interval | Tool calls | Tool-call intervals |
|---|---:|---:|---:|
| Rutter values interface | 36.568 s | 4 | 0.500 s |
| Rutter values source | 51.073 s | 6 | 1.345 s |
| Rutter history interface | 52.620 s | 7 | 0.867 s |
| Rutter history source | 50.305 s | 6 | 0.773 s |
| Rutter authoring interface | 49.296 s | 4 | 0.657 s |
| Rutter authoring source | 46.794 s | 4 | 0.567 s |
| Marker interface, rejected | 86.728 s | 10 | 1.304 s |
| Famulus paths interface, rejected | 63.476 s | 4 | 0.633 s |

The six Rutter workers total **286.656 seconds**, including **4.709 seconds of
tool-call intervals**. Their median task interval is **49.801 seconds**. Time
outside recorded tools includes model work, host scheduling and final delivery;
it cannot be labeled pure inference. The original Rutter run additionally had
533.339 seconds outside both its machine calls and actual worker task intervals,
including controller handling, host delivery, waits and compaction. That is
historical orchestration overhead, not an estimate for the current protocol.

Observed avoidable work:

- Some workers guessed a nonexistent milestone-writer path, read its help/source,
  or searched the repository to rediscover the protocol. Exact verified task-local
  commands avoid those recovery turns while retaining required milestones.
- History workers requested enough output from the inner file-read tool, but
  the enclosing orchestration output was capped at 10,000 tokens. Actual logs
  show truncation followed by additional reads. Those follow-up reads were
  necessary for correctness; preventing the truncation avoids the repeat work.
- The paths worker wrote its complete JSON to a file and produced it again as
  its final answer. Only one final report is required. Removing that duplicate
  lowers output work, but does **not** eliminate a whole model turn because
  mandatory completion logging shared the file-writing call.
- Initial input was approximately 25,000 tokens even without parent history,
  mostly shared host instructions/catalogue. A shorter task prompt alone cannot
  remove that fixed context. Much of it was reported cached; token totals across
  turns are not unique input size or a measure of inference duration.

The canonical semantic checklists are already scoped to one subject and tell
workers not to repeat hashes, signatures, schemas or graph checks. The inspected
workers did not rerun those mechanical checks. Source/interface audits have
different responsibilities; overlapping file reads alone do not prove duplicate
semantic judgment.

The controller skill now specifies no inherited parent history when supported,
self-contained scope and exact packet/instruction references, verified local
bookkeeping commands, complete batched reads with sufficient enclosing output
space, and one final JSON forwarded unchanged. All semantic criteria and the
right to read additional evidence remain intact. This change passed independent
review, but its latency gain is **unmeasured**, not an A/B result.

Fresh semantic A/B spawning failed with `collab spawn failed: agent thread limit
reached`; a worktree-local CLI alternative failed with `Read-only file system
(os error 30)`. Existing reviewers were not presented as fresh certification
workers. The instruction changes have no newly measured latency gain.

## Validation and release limits

Focused checks and independent implementation, simplicity, docs and test-design
reviews passed. The physical preparation test covers stale/wrong-node receipts,
input mutation, prerequisite replacement, no-signing failure behavior, and reuse.
The graph-copy cut passed the physical mutation/isolation test plus 82 related
runner tests. The full validator suite and focused tests remain distinct evidence.

At the overnight host check, the connected installation lacked the Voyage
interface (`dispatcher.interface_not_found`); no host refresh was performed.
An isolated candidate MCP server subsequently completed three unchanged
`initiate`/`next` pairs in 6.098/6.223/6.341 seconds after scoped local-IPC approval,
plus 0.565 seconds startup. Root-process spans left only 11–16 ms per call outside
recorded code. Those runs used synthetic fixture certificates, issued nothing,
and measured no workers or parent LLM; they do not qualify the installed host.

Outstanding release evidence remains a fresh semantic-instruction A/B, a full
post-change real-worker run, and current-host availability. Machine time remains
above target. This report does not claim release completion.

Primary artifacts under `_build/certification-release/`:

- Final timing/method/scope records: `hybrid-validators-matrix-v2.json`,
  `hybrid-validators-summary-v2.json`, `hybrid-fault-summary-v2.json`, frozen
  snapshots, per-gate timings and their benchmark scripts.
- Earlier comparisons: `overnight-summary.json`, `simple-cuts-summary.json`,
  `dfs-validators-summary-v2.json`, `hybrid-validators-summary.json`,
  `hybrid-pilot-summary.json`, `hybrid-graph-one-summary.json`.
- Attribution and host evidence: `worker-trace-analysis.json`,
  `full-function-breakdown.json`, `preparation-spans.json`,
  `smoke-child-compare.json`, `graph-ancestors-timing.json`,
  `graph-standards-spans.json`, `candidate-mcp/{results,summary}.json`.

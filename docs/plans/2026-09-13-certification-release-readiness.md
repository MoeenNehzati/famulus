# Rutter certification release readiness

Status: tasks 1–3 complete with independent functionality, Ponytail and simplicity
reviews GREEN, 2026-09-13. Installed-host certification and selective reuse passed.
Task 4 remains open until the frozen candidate passes complete exact-SHA remote CI;
the user authorized scoped feature publication and CI qualification.
Final freeze and qualification receipts belong under `_build/certification-release/`
after this document is committed, avoiding a commit that records its own final SHA.

## Outcome and boundaries

Make the new `node-certify` Rutter workflow release-ready: the supported host
route certifies a committed node and its uncertified prerequisites, produces
independently current signed certificates, and makes a smaller audit meaningfully
faster. Release readiness requires the final candidate's existing CI matrix green.
This plan does not publish a release or merge the feature into `master`.

Work only in the repository's `.worktrees/certification-audit-pool` checkout, on
`feat/certification-audit-pool`. Starting implementation: `e638a8fd`.
Keep one task/evidence ledger and final non-secret result summary under
`_build/certification-release/`. Finish tracked documentation before freezing the
candidate; do not create a new unqualified commit merely to record its own final
SHA or CI result. Preserve prior certificates and benchmark evidence.

Reuse the existing dispatcher, Voyage, signer, drift reader, benchmark helpers,
and CI runner. Fix only demonstrated release blockers. No new scheduler, service,
cache, certificate format, migration framework, whole-graph certification campaign,
optional docstring suite, or unrelated cleanup.

Existing evidence: normal merge hooks passed (3,979 tests, 22 skips); independent
merge reviews were green. Earlier CLI trials proved issuance, dependency order,
zero-audit repeats and selective savings, but precede the merge. The initial installed
MCP dry-run returned `dispatcher.interface_not_found` for the Voyage route; task 1
resolved that failure through the approved scoped refresh.

## 1. Establish a usable candidate host route

- [x] Record full candidate SHA, clean tracked state, runtime/plugin root and
  executable path. Read current installation/runtime bindings and construct the
  smallest supported way for a fresh host session to load this candidate.
  `scripts/famulus-refresh` can archive a dirty tracked snapshot: a refresh receipt
  alone does not prove that the host runs the recorded commit.
- [x] Resolve the observed ownership mismatch before dispatching auditors:
  installed Voyage state follows its plugin root, while the exact signer requires
  execution from the reviewed repository. Prefer an existing checkout-bound host
  binding. If none works, fix the existing boundary minimally after independent
  review; preserve candidate-byte verification and scoped input guards. Do not
  disguise a direct Python invocation as an installed-host test.
  The binding must be a documented supported user path from plugin installation.
  A one-off developer override is diagnostic only; if ordinary installed use still
  fails, fix the existing delegation/ownership boundary and retest that user path.
- [x] Prepare any necessary host registration change as an exact configuration
  delta with restoration instructions. The standing worktree-only rule still
  applies: request its narrow exception only after the change is reviewable.
  Do not change the main checkout, global plugin settings, or installed cache
  merely to make a smoke test pass. Keep public keys, certificates and Voyage
  records in the certification worktree.
- [x] Verify renamed signing storage: current namespace is `node-certify`, public
  material is under `skills/node-certify/.certificates/public-keys`. Old
  `skill-certifier` material is not automatically discovered. Preserve it and copy
  its valid `<key-id>.pub` verification files into the renamed canonical public-key root
  before checking retained certificate logs; never overwrite a conflicting key ID.
  Do not copy the old `active-key-id` selector into a different secret namespace.
  Record the selected active public key identity. Reuse the current namespace's key when present;
  otherwise use the already-authorized host signing-key creation through the normal
  signer and explicitly record the new identity. Never copy private keys into files.
- [x] From the fresh candidate-bound host, call `famulus_dispatcher.invoke` with
  caller `node-certify`, interface
  `node-certify._rtx.interface.certification-voyage`, version `2`, arguments
  `{"positionals":["help"],"options":{},"stdin":null}`. Require real success,
  resolved candidate provenance, and confinement of the selected runtime/state.
  A dry-run or CLI-only success is insufficient.

Gate: host route and signer execution/state ownership are compatible with the
committed candidate, without weakening guards. If installation authorization or
host capability is missing, retain a concrete blocker; do not call release-ready.

## 2. Complete one monitored dependency trial

- [x] Resolve the observed missing-runtime-package failure through the existing
  bootstrap owner-selected repair route, using the certifier's exact declared
  Python packages and the dedicated dispatcher's interpreter. Host-runtime writes
  need a separate narrow exception to the worktree-only scope; the approved Codex
  cache/registration refresh does not cover them. Preserve ignored old skill
  artifacts outside the live skills namespace after the rename. Require the
  existing validator gate to pass before spending more semantic-worker time.
- [x] Use `tight-mode` as the bounded target, including its source prerequisite.
  Snapshot exact hashes, basis, currentness and certificate-log lengths first.
  Require a prerequisite without a currently acceptable certificate; distinguish
  absent from stale evidence. Do not delete existing certificates to manufacture it.
  If already current, select one comparably small existing target using machine
  graph/currentness output; record its closure before starting, without expanding
  to unrelated service dependencies.
- [x] Before this run, choose the same target and source-description delta for
  step 3. Require the baseline to need the full intended semantic audit set,
  including the interface that the delta should reuse. Otherwise select a small
  still-uncertified comparable target now; a zero-audit current-node skip is not a
  full baseline. Confirm the planned delta lies outside the certification basis.
  Prepare the updated timing harness and start instrumentation described in step 3
  before initialization; do not reconstruct baseline intervals afterward.
- [x] Invoke the public host route with `initiate`, `--repository` set to the
  worktree, `--targets tight-mode` (or the recorded replacement), and bounded
  `--worker-capacity 1`. Use only `next` afterward, retaining `--repository` on
  every stateful call. Follow the current
  [node-certify instructions](../../skills/node-certify/SKILL.md).
- [x] Controller only dispatches fresh semantic workers and forwards their exact
  raw completions. Machines select dependencies, validate reports, decide reuse,
  sign exact nodes and produce the terminal receipt. Keep monitoring outside those
  decisions; inspect the completed trace rather than directing audits manually.
- [x] Require successful terminal receipt, prerequisite issuance before its dependent,
  exact selected-node coverage, no unrelated issuance, and independent read-only
  drift verification of signatures/currentness with no concerns. Record the actual
  dependency type; an ownership edge does not prove a cross-module trial.
- [x] Start one fresh unchanged Voyage for the same target. Require zero semantic
  assignments and zero appended certificates. Retain terminal and log-count evidence.

Gate: fresh host invocation completes the full machine/worker/signing/currentness
loop. Reject/abort/lost-worker behavior already has focused tests; add a live failure
trial only if this run exposes an uncovered integration failure.

## 3. Verify selective audit correctness and speed

- [x] Reuse the existing ignored benchmark scripts with current `node-certify`
  names and supported milestone protocol. Omit optional unsafe task-derived run IDs.
  Time the actual host route, not a separate local import harness. Preserve old
  measurements unchanged and label new ones with their actual candidate SHA.
- [x] Compare the same target, host, worker capacity and audit instructions before
  and after one committed, truthful source-description clarification outside the
  certification basis. Use step 2's full run as the baseline; do not switch to its
  now-current source as a supposed fresh baseline or remove certificates.
  The delta must leave the global basis and changed source's outgoing dependencies,
  runtime bytes and interface facets identical while changing its remainder.
  Its containing module's dependency claim will change and requires a module audit.
  Snapshot these facts mechanically.
- [x] Require unchanged interface evidence reused, fewer semantic assignments,
  all changed source/module obligations still audited, and independently current
  signed results. Never narrow the audit merely to hit a timing target.
- [x] Measure initiation start to terminal receipt; separately report machine
  invocation intervals, worker dispatch-to-final-receipt intervals, and residual
  controller/transport/wait time. Worker intervals include tools and output;
  pure inference time is unavailable. Do not double-count nested signer/check time
  or sum overlapping workers as elapsed time.
  Separate server/process timing from MCP round-trip timing when both are exposed;
  if only round-trip timing is available, label it as such, not pure machine time.
- [x] Acceptance target: at least 25% lower observed end-to-end time for the narrow
  run, alongside fewer audits. One pair is a release smoke comparison, not a median
  or statistical guarantee. If it misses, inspect recorded intervals; allow one
  equivalent pair to resolve observed host noise only when its full baseline can
  again be demonstrated without deleting evidence or making artificial changes.
  A current-target rerun is not equivalent; otherwise retain the noisy comparison
  as unresolved. Persistent failure remains a
  performance blocker; fix only the measured cause, then repeat affected evidence.
- [x] Keep a useful truthful clarification; otherwise restore it in a new commit
  and renew affected certificates. No uncommitted certification targets or temporary
  benchmark edits may remain in the final candidate. Record any final SHA change.

Gate: accuracy and visible savings both pass; earlier 45.1% savings remain historical
evidence, not a substitute for this merged-host comparison.

## 4. Freeze and qualify the release candidate

- [ ] Finish only demonstrated fixes and their documentation; obtain independent
  correctness and Ponytail reviews, resolving findings until both are green.
  Use the existing focused regression for each actual code change and normal commit
  hooks. Do not rerun broad local suites already green without a relevant change.
- [ ] Freeze final full SHA and clean worktree. Verify the final host loads that
  candidate and independent drift still reports the trial certificates current.
  If runtime, basis, route or audited inputs changed after the live trials, refresh
  the host and repeat the affected trials; do not transfer stale timing evidence.
- [ ] Prepare a non-force push of this feature branch and the existing full CI
  request. Publishing a ref/running remote CI is outside the worktree-only scope;
  obtain the narrow authorization before either external action. No new candidate
  branches, worktrees, promotion, or unrelated CI project are needed here.
- [ ] After authorization, push the exact feature tip using normal pre-push hooks,
  verify the remote SHA, and invoke `ci-debug._rtx.interface.run-ci@2` through
  `famulus_dispatcher.invoke` with caller `ci-debug`: `--repo-root` = worktree,
  `--ref` = `feat/certification-audit-pool`, `--expected-sha` = final full SHA,
  `--context` = `_build/certification-release/ci` (absolute path).
  Use the existing machine runner directly within this fixed branch scope; do not
  invoke the broader candidate/promotion lifecycle of `qualify-ci`.
- [ ] Poll the same request/context to terminal status. Require the complete current
  matrix, including native keyring coverage, green for that SHA. Pending, targeted
  green and local hooks are insufficient. Fix only actual failures, test their
  smallest selectors, then obtain a complete green matrix on the resulting final
  candidate. Preserve run IDs and check that the local/remote tips still match.

## Completion record

The first remote matrix found two Codex TOML success fixtures that used ordinary
file creation without the restrictive native ACL required by Windows guarded
replacement. Prepare those predecessors with the existing secure atomic creator;
retain all assertions and the production fail-closed ACL policy. The repaired
candidate still requires a complete exact-SHA matrix. CI's separate existing MCP
wrapper output-capture defect is recorded in the ignored completion evidence;
the same durable request can be completed by its canonical repository runner.

### Verified local execution

The scoped Codex refresh installed the public Voyage route from `7351d659`.
That installed route delegated subsequent stateful calls to the committed candidate
checkout. The separately authorized runtime repair installed only the certifier's
already-declared missing packages and their dependencies; all 26 required validators
then passed in the dedicated interpreter. Its fingerprint remained unchanged.
The initial failed trial issued nothing and is excluded from timing comparisons.

The full baseline at `81d91a7c` certified `tight-mode.source.gateway` before
`tight-mode`. The prerequisite had retained valid historical signatures, but its
final signing key was no longer active; its evidence was stale, not absent. This
is a `contains-source` ownership dependency trial, not a cross-module trial.
Independent public drift returned both nodes current with no concerns.

The useful source-description clarification was committed as `9c965bd7`.
Machine assertions confirmed identical basis, runtime content, source outgoing
dependencies and interface facet claim. The source remainder/hash and the module's
signed dependency claim changed; the module's local hash remained unchanged.
The small run reused the exact authenticated interface evidence, audited both
remaining obligations, and renewed both certificates in dependency order. Public
drift again returned current with no concerns. Certificate logs grew from 3/1 to
4/2 in the full run, stayed 4/2 in the unchanged run, and became 5/3 in the small run.

| Run | Semantic audits | Elapsed | Machine process | Worker intervals | Controller and transport |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full baseline | 3 | 409.58 s | 96.26 s | 229.44 s | 83.88 s |
| Unchanged repeat | 0 | 32.22 s | 10.32 s | 0.00 s | 21.90 s |
| Small change | 2 | 274.55 s | 86.24 s | 131.61 s | 56.71 s |

The small run was 32.97% faster in this one observed pair, exceeding the 25%
acceptance threshold. Savings came primarily from skipping the 91.75-second
interface worker; the source worker itself took 62.68 seconds full versus 64.64
seconds small. This does not establish a median or uniformly faster individual
audits. Worker intervals include tools, scheduling and final delivery; pure
inference time is unavailable. Controller delays remain visible in elapsed time.
Root process spans are counted once, without adding their nested signing/check spans.

Evidence under `_build/certification-release/`: `full-summary.json`,
`unchanged-summary.json`, `small-summary.json`, `comparison.json`,
`tight-small-assertions.json`, public drift receipts `live/11-payload.json` and
`live/18-payload.json`, and the per-task independent review records. Both reviewers,
`merge_core` and `merge_certifier`, returned GREEN for each completed task.

Release-ready only when every execution gate above passes. In the ignored completion
report and user-facing handoff, record the final SHA,
host runtime identity, Voyage IDs/receipts, public-key identity, exact certified
nodes and dependency type, currentness results, full/small audit counts and timing
breakdown, unchanged-repeat result, CI run/report, reviewer verdicts, and any
remaining limitation. Link raw evidence; keep secrets and private keys out.

Plan-review green means the plan is executable and bounded. It does not mean the
system is release-ready until those execution results exist. Publication remains
a separate user decision.

## Independent plan review

- Correctness and host delivery: GREEN after revision, `merge_certifier`, 2026-09-13.
- Ponytail, performance and release evidence: GREEN after revision, `merge_core`, 2026-09-13.

## Performance follow-up, 2026-09-14

The follow-up seals reusable certification evidence, authenticates only selected
certificate histories, and selects validator subjects before reads. Graph checks
cover renewal nodes once; local checks run at each stale node's audit turn.
Standalone certification remains independently checked. The controller chains
initialization to the first `next` and forwards worker results unchanged.
Ownership lookup, YAML loading, graph transport and Rutter bookkeeping also
avoid repeated work. Implementation and simplicity reviews are green.

The [performance report](2026-09-14-certification-performance-report.md) retains
the matched trials, real worker-log analysis, failure probes and limits. Final
hybrid machine medians were 12.394 → 13.373 seconds full and 8.201 → 7.883 seconds
after a small change; variability prevents claiming an overall speedup.
The 5/1/0 audit counts in timing fixtures used synthetic reports and memory keys.

The few-second target, fresh semantic instruction A/B and full post-change
real-worker qualification remain open. The overnight connected host lacked the
Voyage interface; the separate candidate MCP transport probe did not refresh
that installation. Earlier release results above qualify their historical bytes,
not these follow-up changes. No host configuration change is part of this work.

# Rutter certification release readiness

Status: independent plan reviews GREEN, 2026-09-13; execution pending.

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
zero-audit repeats and selective savings, but precede the merge. A current installed
MCP dry-run returns `dispatcher.interface_not_found` for the Voyage route.

## 1. Establish a usable candidate host route

- [ ] Record full candidate SHA, clean tracked state, runtime/plugin root and
  executable path. Read current installation/runtime bindings and construct the
  smallest supported way for a fresh host session to load this candidate.
  `scripts/famulus-refresh` can archive a dirty tracked snapshot: a refresh receipt
  alone does not prove that the host runs the recorded commit.
- [ ] Resolve the observed ownership mismatch before dispatching auditors:
  installed Voyage state follows its plugin root, while the exact signer requires
  execution from the reviewed repository. Prefer an existing checkout-bound host
  binding. If none works, fix the existing boundary minimally after independent
  review; preserve candidate-byte verification and scoped input guards. Do not
  disguise a direct Python invocation as an installed-host test.
  The binding must be a documented supported user path from plugin installation.
  A one-off developer override is diagnostic only; if ordinary installed use still
  fails, fix the existing delegation/ownership boundary and retest that user path.
- [ ] Prepare any necessary host registration change as an exact configuration
  delta with restoration instructions. The standing worktree-only rule still
  applies: request its narrow exception only after the change is reviewable.
  Do not change the main checkout, global plugin settings, or installed cache
  merely to make a smoke test pass. Keep public keys, certificates and Voyage
  records in the certification worktree.
- [ ] Verify renamed signing storage: current namespace is `node-certify`, public
  material is under `skills/node-certify/.certificates/public-keys`. Old
  `skill-certifier` material is not automatically discovered. Preserve it and copy
  its valid `<key-id>.pub` verification files into the renamed canonical public-key root
  before checking retained certificate logs; never overwrite a conflicting key ID.
  Do not copy the old `active-key-id` selector into a different secret namespace.
  Record the selected active public key identity. Reuse the current namespace's key when present;
  otherwise use the already-authorized host signing-key creation through the normal
  signer and explicitly record the new identity. Never copy private keys into files.
- [ ] From the fresh candidate-bound host, call `famulus_dispatcher.invoke` with
  caller `node-certify`, interface
  `node-certify._rtx.interface.certification-voyage`, version `1`, arguments
  `{"positionals":["help"],"options":{},"stdin":null}`. Require real success,
  resolved candidate provenance, and confinement of the selected runtime/state.
  A dry-run or CLI-only success is insufficient.

Gate: host route and signer execution/state ownership are compatible with the
committed candidate, without weakening guards. If installation authorization or
host capability is missing, retain a concrete blocker; do not call release-ready.

## 2. Complete one monitored dependency trial

- [ ] Use `tight-mode` as the bounded target, including its source prerequisite.
  Snapshot exact hashes, basis, currentness and certificate-log lengths first.
  Require a prerequisite without a currently acceptable certificate; distinguish
  absent from stale evidence. Do not delete existing certificates to manufacture it.
  If already current, select one comparably small existing target using machine
  graph/currentness output; record its closure before starting, without expanding
  to unrelated service dependencies.
- [ ] Before this run, choose the same target and source-description delta for
  step 3. Require the baseline to need the full intended semantic audit set,
  including the interface that the delta should reuse. Otherwise select a small
  still-uncertified comparable target now; a zero-audit current-node skip is not a
  full baseline. Confirm the planned delta lies outside the certification basis.
  Prepare the updated timing harness and start instrumentation described in step 3
  before initialization; do not reconstruct baseline intervals afterward.
- [ ] Invoke the public host route with `initiate`, `--repository` set to the
  worktree, `--targets tight-mode` (or the recorded replacement), and bounded
  `--worker-capacity 1`. Use only `next` afterward. Follow the current
  [node-certify instructions](../../skills/node-certify/SKILL.md).
- [ ] Controller only dispatches fresh semantic workers and forwards their exact
  raw completions. Machines select dependencies, validate reports, decide reuse,
  sign exact nodes and produce the terminal receipt. Keep monitoring outside those
  decisions; inspect the completed trace rather than directing audits manually.
- [ ] Require successful terminal receipt, prerequisite issuance before its dependent,
  exact selected-node coverage, no unrelated issuance, and independent read-only
  drift verification of signatures/currentness with no concerns. Record the actual
  dependency type; an ownership edge does not prove a cross-module trial.
- [ ] Start one fresh unchanged Voyage for the same target. Require zero semantic
  assignments and zero appended certificates. Retain terminal and log-count evidence.

Gate: fresh host invocation completes the full machine/worker/signing/currentness
loop. Reject/abort/lost-worker behavior already has focused tests; add a live failure
trial only if this run exposes an uncovered integration failure.

## 3. Verify selective audit correctness and speed

- [ ] Reuse the existing ignored benchmark scripts with current `node-certify`
  names and supported milestone protocol. Omit optional unsafe task-derived run IDs.
  Time the actual host route, not a separate local import harness. Preserve old
  measurements unchanged and label new ones with their actual candidate SHA.
- [ ] Compare the same target, host, worker capacity and audit instructions before
  and after one committed, truthful source-description clarification outside the
  certification basis. Use step 2's full run as the baseline; do not switch to its
  now-current source as a supposed fresh baseline or remove certificates.
  The delta must leave the global basis and changed source's outgoing dependencies,
  runtime bytes and interface facets identical while changing its remainder.
  Its containing module's dependency claim will change and requires a module audit.
  Snapshot these facts mechanically.
- [ ] Require unchanged interface evidence reused, fewer semantic assignments,
  all changed source/module obligations still audited, and independently current
  signed results. Never narrow the audit merely to hit a timing target.
- [ ] Measure initiation start to terminal receipt; separately report machine
  invocation intervals, worker dispatch-to-final-receipt intervals, and residual
  controller/transport/wait time. Worker intervals include tools and output;
  pure inference time is unavailable. Do not double-count nested signer/check time
  or sum overlapping workers as elapsed time.
  Separate server/process timing from MCP round-trip timing when both are exposed;
  if only round-trip timing is available, label it as such, not pure machine time.
- [ ] Acceptance target: at least 25% lower observed end-to-end time for the narrow
  run, alongside fewer audits. One pair is a release smoke comparison, not a median
  or statistical guarantee. If it misses, inspect recorded intervals; allow one
  equivalent pair to resolve observed host noise only when its full baseline can
  again be demonstrated without deleting evidence or making artificial changes.
  A current-target rerun is not equivalent; otherwise retain the noisy comparison
  as unresolved. Persistent failure remains a
  performance blocker; fix only the measured cause, then repeat affected evidence.
- [ ] Keep a useful truthful clarification; otherwise restore it in a new commit
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

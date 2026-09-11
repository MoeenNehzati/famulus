# CI-debug performance and reliability design

## Objective

Prevent another multi-hour green-to-green repair episode while keeping the
repository's existing CI, repository-check runner, targeted-probe interface,
failure ledger, and exact-SHA qualification model. The first implementation
wave removes demonstrated delay and missing test ownership; it does not add a
new scheduler, test taxonomy, attestation system, or CI authority layer.

## Evidence from the 2026-09-10 episode

- Target-branch drift was discovered only after roughly 3 hours 45 minutes of
  candidate qualification and diagnosis. The existing final promotion gate
  caught the drift correctly, but too late to avoid that work.
- Independent selectors were often dispatched serially even though
  `run-targeted-tests` already accepts a selector list and ci-debug already
  supports bounded parallel element repair.
- Nine direct-Chrome tests lived in shared-suite modules: eight benchmark
  launcher tests and one visualization bootstrap test. The repository already
  has a serial `tests:browser` task, but currently populates it only through the
  `test_*_browser.py` module naming convention.
- Two Rutter lock tests used bounded joins without proving that the worker had
  terminated or propagating a worker exception.
- Every matrix and probe job installed Node.js plus Claude and Codex CLIs even
  though the workflow does not enable the opt-in live-client tests.
- A deletion-only temporary-branch cleanup still invoked the complete local
  pre-push suite.

## Binding constraints

1. Preserve exact-SHA, complete-matrix green as the qualification authority.
   Pending, targeted-green, and affected-element-green remain nonterminal.
2. Preserve the numbered ci-debug lifecycle, isolated candidate worktree,
   failure ledger, prevention review, and fast-forward/compare-and-swap
   promotion gates.
3. Prefer configuration and instruction changes around existing interfaces.
   Add no new service, queue, daemon, suite family, registry, or generalized
   execution layer in the first wave.
4. Preserve unique native, portability, browser, performance, subprocess,
   filesystem, and concurrency evidence. Performance comes from eliminating
   duplicated work and idle serialization, not deleting distinct evidence.
5. Keep `tests:shared` as the default inventory. Do not create a general
   `tests:integration` suite from this incident.
6. Do not weaken Git safety: no force push, `--no-verify`, automatic divergent
   merge/rebase, global SSH configuration, or promotion of a newly created SHA
   without qualification.
7. Do not change existing local or remote branches while qualifying a
   candidate. Recheck both target tips before every expensive remote wave and
   before promotion.
8. Use the repository's normal eight-worker local validation where parallel
   safe. Browser and performance owners retain their canonical serial policy.
9. Each first-wave change must have a focused regression test and an observable
   acceptance measure. If a claimed saving cannot be measured, do not broaden
   the implementation to obtain it.

## First-wave design

### 1. Make ci-debug spend remote time deliberately

Amend the existing numbered algorithm rather than adding a coordinator:

1. Record local and live remote target tips at the initial snapshot.
2. Before every full matrix and every new batch of remote probes, refresh both
   tips. Continue only when they still satisfy the candidate's recorded
   promotion preconditions. Preserve the candidate and ask when they do not.
3. Record a concise failure signature from the exception/assertion category,
   normalized message, and terminal project stack frame. Remove only
   nondeterministic temporary roots, run IDs, timestamps, and durations; do not
   add a parser or signature service. Identical signatures share one repair
   owner, while every affected element retains a validation ledger entry.
4. For one element, send all independent known selectors in one existing
   `--selectors-json` request. Dispatch independent OS/task elements in the
   existing bounded-parallel route.
5. After integration, run the smallest selector batch, then retain one complete
   run of every affected element. Selector batching removes duplicate requests;
   it does not remove the element-wide safety gate.
6. Run exactly one complete matrix for the current candidate after all affected
   evidence is green.
7. Report elapsed wall time, drift-check count, targeted request count, whole
   element count, full-matrix count, repair rounds, and repeated unchanged
   failure signatures from the invocation record. These are orchestration
   metrics, not a new machine-report schema.

The existing `run-targeted-tests` interface remains synchronous in the first
wave. Batching reduces calls without changing its contract. Durable targeted
requests become eligible only if post-wave measurements still show adapter
timeouts or lost probe ownership.

### 2. Restore local ownership of environment-sensitive tests

- Keep the nine direct-Chrome function selectors, covering eleven parametrized
  cases, in their current modules, but add their exact node IDs to the existing
  serial `tests:browser` selection and exclusion policy. This preserves the
  documented stable Chrome gates on Ubuntu and Windows without a new matrix
  element. Their current execution inside macOS shared is accidental and
  conflicts with the handbook's deliberate rule that hosted macOS Chrome is
  not a rendering gate. Do not reclassify either whole module or add a suite.
- Extend `tests/test_browser_parallel_policy.py` to reject a direct bare-name
  `require_chrome()` call unless its module follows the browser naming
  convention or its exact node is in the serial Chrome-node allowlist. This is
  intentionally a narrow contract, not a general subprocess/browser detector.
- Change the two identified Rutter lock tests to capture worker exceptions,
  join after lock release, and assert the worker is no longer alive. Do not add
  a repository-wide static rule for every `join(timeout)`.

### 3. Remove demonstrated setup and cleanup overhead

- Remove only the unconditional Claude/Codex CLI install from both matrix and
  probe jobs after a workflow regression test establishes that ordinary CI does
  not opt into live-client tests. Retain Node setup only for `combined` and
  `tests:shared`, because their shared MathJax syntax test invokes
  `node --check`; validators, performance, browser, and other probes do not
  install it.
- Enable `actions/setup-python`'s existing pip download cache using
  `requirements-ci.txt` as the dependency path. This does not cache a virtual
  environment or claim that the current dependency range is a lockfile.
- Teach `.githooks/pre-push` to read Git's standard update records. Exit without
  validation only when at least one well-formed update is present and every
  local object ID is all zero, meaning a deletion-only push. Mixed updates,
  malformed input, missing standard hook arguments, TTY stdin, and
  direct/no-input invocation still run the full pre-push suite without waiting
  for input.

### 4. Establish server-side admission separately

The repository already runs the Python workflow on pull requests to `master`.
Branch protection or a repository ruleset should require that workflow before
ordinary changes reach `master`. This is a separate, explicitly authorized
GitHub administration action, not a local-hook feature and not part of the
first code commit. Direct exact-green-SHA promotion remains available only if
the configured server-side rule permits it without weakening required checks.

## Explicitly deferred work

- Durable targeted-probe request state.
- Same-SHA reuse between candidate and target push checks.
- Local exact-tree attestations.
- SSH keepalive changes.
- Timezone canonicalization or a timezone-specific suite.
- New test suites, semantic registries, or generalized static test validators.
- Whole-environment or `site-packages` caches.
- Whole-suite rerun-to-green or nightly repetition.
- Dependency pin changes unrelated to a measured failure.

Each deferred item requires a new observation showing that the first wave left
its specific failure mode materially unresolved. It then receives its own
small design review rather than being appended opportunistically.

## Acceptance criteria

The first wave is acceptable only when:

1. Instruction tests prove drift is checked before every expensive remote wave,
   identical failures are clustered, selectors are batched through the current
   interface, whole-element escalation is conditional, and the final response
   contains the orchestration counters.
2. Repository-runner tests prove all nine physical Chrome selectors (eleven
   collected cases) are absent from parallel shared execution and present in
   the existing serial browser task on the documented Ubuntu and Windows
   Chrome gates; non-Chrome tests in the same modules remain shared.
3. The browser ownership policy rejects a new misplaced direct Chrome call and
   accepts browser-named modules and the exact serial Chrome-node allowlist.
4. Focused Rutter tests prove worker completion and propagate worker failures.
5. Workflow tests prove only combined/shared jobs retain Node, no job installs
   assistant CLIs, exact-SHA and evidence behavior remain, and setup-python adds
   only the pip download cache.
6. Hook tests cover deletion-only, mixed, ordinary update, malformed, and
   direct/no-input cases.
7. Focused tests, `python3 repo_checks.py --suite precommit --jobs 8`, and
   `git diff --check` are green from a clean exact-revision worktree.
8. A before/after timing report distinguishes shared, performance, browser,
   full-suite, CI setup, targeted-request, whole-element, full-matrix, and total
   wall time. Timing from unmatched or single noisy samples is observational;
   no correctness claim depends on an unmeasured estimate.

# CI-debug Performance and Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce CI-debug wall time and prevent locally invisible CI failures through small changes to existing orchestration, test ownership, workflow setup, and hook behavior.

**Architecture:** Keep the current exact-SHA candidate lifecycle, repository-check suites, GitHub workflow, and targeted-probe interface. The first wave changes their policies and call patterns only; server-side branch admission and any measurement-gated follow-up remain separate decisions.

**Tech Stack:** Markdown skill instructions, Python/pytest, Bash pre-push hook, GitHub Actions YAML, existing `repo_checks.py` and ci-debug remote interfaces.

**Spec:** `docs/plans/2026-09-10-ci-debug-performance-and-reliability-design.md`

## Global Constraints

- Complete exact-SHA matrix green remains the only CI qualification authority.
- Add no new service, queue, daemon, suite family, registry, or generalized execution layer in the first wave.
- Preserve the isolated candidate, failure ledger, prevention review, native/portability/browser/performance evidence, and fast-forward/compare-and-swap safety.
- Use existing `run-targeted-tests --selectors-json` and bounded parallel element dispatch before changing machine interfaces.
- Do not implement any explicitly deferred design item without a new reviewed plan.
- Keep branch-protection administration outside code commits and require separate authorization.
- Use eight workers for parallel-safe repository validation; retain canonical serial browser and performance execution.

---

### Task 0: Freeze a comparable baseline before editing

**Files:** No repository files change.

**Interfaces:**

- Consumes: the exact implementation base, a clean worktree, the current
  ci-debug context/report mechanism, GitHub job-step timestamps, and
  `repo_checks.py` timing output.
- Produces: a named isolated implementation branch/worktree, exact-base local
  timing artifacts, and an exact-base full-matrix report in a durable context
  outside every implementation/candidate/repair worktree.

- [ ] **Step 1: Record the exact base and evidence root**

  Record local and live remote target tips, the exact implementation base SHA,
  and an absolute durable ci-debug context outside all Git worktrees. Save these
  in the invocation record before any edit.

- [ ] **Step 2: Create the named isolated implementation worktree**

  Use `superpowers:using-git-worktrees` under `famulus:git-workflow` to create a
  collision-resistant `perf/ci-debug-first-wave/<unique-id>` branch and linked
  worktree from the exact local target tip. Record both as invocation-owned.
  All Task 1-5 edits and commits occur there; the original target remains
  unchanged. Resolve final publication as `push=false` unless the user
  separately authorizes a push.

- [ ] **Step 3: Measure the exact-base local gates once**

  From a clean exact-base worktree, run:

  ```bash
  python3 repo_checks.py --suite precommit --jobs 8 --timing-output "$CI_CONTEXT/baseline-precommit.json"
  python3 repo_checks.py --suite full --jobs 8 --timing-output "$CI_CONTEXT/baseline-full.json"
  ```

  Expected: green. Preserve a red result; do not rerun it into a baseline.

- [ ] **Step 4: Obtain an exact-base full-matrix baseline**

  Refresh the local and live remote target tips after the local baselines and
  before accepting or dispatching any remote baseline. Continue only if the
  recorded candidate/promotion preconditions still hold. If they changed,
  preserve the evidence and stop to select or approve a new base.

  Reuse an already completed full-matrix report only if its tested SHA equals
  the implementation base. Otherwise use the current `ci-debug` isolated
  candidate route to dispatch exactly one base matrix. Persist the matrix report
  in `CI_CONTEXT`.

- [ ] **Step 5: Capture existing GitHub step timing without changing schemas**

  Use the read-only GitHub run view for that exact run and save job/step
  `startedAt` and `completedAt` values beside the existing report. Record the
  dependency-install and assistant-CLI-install duration for each element. Do
  not extend the ci-debug machine-report schema.

- [ ] **Step 6: Stop on an invalid baseline**

  If target drift occurred, or the exact-base local gate or matrix is red,
  repair or choose a new base under a separate ci-debug cycle before beginning
  Task 1. Do not compare a stale/red base, a different SHA, or unlike matrix
  topology with the first wave.

### Task 1: Tighten the existing ci-debug algorithm

**Files:**

- Modify: `skills/ci-debug/SKILL.md`
- Modify: `skills/ci-debug/tests/test_ci_debug_instructions.py`

**Interfaces:**

- Consumes: existing `ci-debug._rtx.interface.run-ci`, `ci-debug._rtx.interface.run-targeted-tests`, invocation record, debug context, and repair-element bounded parallelism.
- Produces: an instruction-only orchestration policy with early drift gates,
  normalized failure clustering, selector batching, mandatory affected-element
  verification, and final counters.

- [ ] **Step 1: Add failing instruction-contract assertions**

  Add focused assertions that the numbered algorithm requires:

  ```python
  assert "before every complete matrix and remote probe batch" in text
  assert "normalized failure signature" in text
  assert "--selectors-json" in text
  assert "one complete run of every affected element" in text
  for counter in (
      "elapsed wall time",
      "drift-check count",
      "targeted request count",
      "whole-element count",
      "full-matrix count",
      "repair rounds",
      "repeated unchanged failure signatures",
  ):
      assert counter in text
  ```

  Make these section/order-sensitive assertions: the drift gate precedes the
  initial matrix and every later probe/matrix dispatch; the signature contains
  the category, normalized message, and terminal project frame; normalization
  removes only the listed nondeterministic values; one repair owner retains all
  per-element validation entries; selector batches use the existing interface;
  every affected element still runs once; and all counters are present per
  candidate SHA.

- [ ] **Step 2: Observe the red contract**

  Run:

  ```bash
  python3 -m pytest -q skills/ci-debug/tests/test_ci_debug_instructions.py
  ```

  Expected: only the new orchestration assertions fail.

- [ ] **Step 3: Amend the numbered algorithm without adding an interface**

  In steps 4 and 5 of `SKILL.md`, insert the design's exact sequence:
  refresh local and remote target tips before each remote wave; block and
  preserve recovery evidence on violated promotion preconditions; cluster the
  ledger using the design's manual signature rule; assign one repair owner to
  an identical cross-element signature while retaining validation entries for
  the other elements; treat clustering only as a scheduling hint that cannot
  clear another element's ledger entry; submit independent selectors in one
  `--selectors-json` request; dispatch independent elements through existing
  bounded parallelism; retain one complete run of every affected element; and
  emit the specified counters per candidate SHA in every terminal response.

- [ ] **Step 4: Verify instruction behavior**

  Run:

  ```bash
  python3 -m pytest -q skills/ci-debug/tests/test_ci_debug_instructions.py
  python3 repo_checks.py --suite validators --jobs 8
  ```

  Expected: both commands pass; `instructions/repair-element.md` and generated
  interface/blueprint files are unchanged because their safety contract and no
  public interface changed.

- [ ] **Step 5: Commit only Task 1**

  ```bash
  git add skills/ci-debug/SKILL.md skills/ci-debug/tests/test_ci_debug_instructions.py
  git commit -m "perf(ci-debug): gate and batch remote diagnosis"
  ```

### Task 2: Route direct-Chrome nodes through the existing browser task

**Files:**

- Modify: `tests/test_browser_parallel_policy.py`
- Modify: `src/officina/repository/checks/runner.py`
- Modify: `tests/test_repository_test_checks.py`
- Update: the affected rows in `~/Desktop/famulus-test-optimization-ledger.md`

**Interfaces:**

- Consumes: `discover_browser_tests`, `CHROME_TESTS`, the exact-node deselection
  pattern already used by precommit policy, the existing serial
  `tests:browser` task, the documented Ubuntu/Windows Chrome-gate policy, and
  the completed test-optimization ledger.
- Produces: a closed exact-node set for direct Chrome calls plus an AST policy
  preventing future direct Chrome calls in pooled shared modules.

- [ ] **Step 1: Reopen only the affected optimization rows**

  Reopen the ledger rows for `tests/test_benchmark_html_renderer.py` and
  `tests/test_visualization_bootstrap.py` because suite selection changes their
  ownership. Record that this plan supersedes the older benchmark task's owner
  only for the exact Chrome selectors; pause overlapping optimization of those
  selectors until this routing decision is settled. Apply
  `docs/contributors/optimizing-code-tests.md` and leave all other settled rows
  unchanged.

- [ ] **Step 2: Add a failing ownership-policy case**

  Add a test-local AST helper that maps each direct bare-name
  `require_chrome()` call to its enclosing test function. Create temporary
  modules and an exact-node allowlist, then assert:

  ```python
  assert chrome_owner_violations(root, chrome_nodes=set()) == [
      "tests/test_misplaced.py::test_real calls require_chrome outside serial Chrome ownership"
  ]
  assert chrome_owner_violations(
      root, chrome_nodes={"tests/test_owned.py::test_real"}
  ) == []
  ```

  Include a `test_owned_browser.py` case and keep the current checks that
  browser-named modules use `run_html` and portable paths. State explicitly
  that aliases and qualified calls are outside this narrow contract.

- [ ] **Step 3: Observe the misplaced live selectors**

  Run:

  ```bash
  python3 -m pytest -q tests/test_browser_parallel_policy.py
  ```

  Expected: the repository scan reports the nine current function selectors
  covering eleven collected cases: eight selectors in
  `tests/test_benchmark_html_renderer.py` and one in
  `tests/test_visualization_bootstrap.py`.

- [ ] **Step 4: Add exact nodes to existing browser selection**

  In `runner.py`, introduce `CHROME_NODE_TESTS` containing the exact nine node
  IDs discovered in Step 3. Define `CHROME_TESTS` as the union of discovered
  browser modules and `CHROME_NODE_TESTS`, so the current specialized task and
  suite deselection logic route them serially through the documented Ubuntu
  and Windows Chrome gates without a new suite, runner, or matrix element. Keep
  both mixed source modules and their test bodies unchanged.

  ```python
  CHROME_NODE_TESTS = {
      "tests/test_benchmark_html_renderer.py::test_trial_measures_a_real_synchronous_stall",
      "tests/test_benchmark_html_renderer.py::test_real_time_launcher_bounds_a_page_without_a_result",
      "tests/test_benchmark_html_renderer.py::test_real_time_launcher_does_not_wait_for_reverse_dns",
      "tests/test_benchmark_html_renderer.py::test_real_time_launcher_retries_inflight_profile_cleanup",
      "tests/test_benchmark_html_renderer.py::test_real_time_launcher_serves_large_pages_without_transfer_timeouts",
      "tests/test_benchmark_html_renderer.py::test_trial_records_action_wide_frame_and_long_task_maxima",
      "tests/test_benchmark_html_renderer.py::test_fast_full_graph_page_collects_the_completed_head_input_timer",
      "tests/test_benchmark_html_renderer.py::test_trial_times_out_candidate_completion_and_math_diagnostics",
      "tests/test_visualization_bootstrap.py::test_initial_layout_runs_in_one_native_worker_without_fallback_warning",
  }
  ```

- [ ] **Step 5: Require and verify stable-host Chrome execution**

  Add runner assertions that all exact nodes are deselected from shared and
  precommit, selected by `tests:browser`, and expand to exactly eleven
  collected cases, while non-Chrome tests from both mixed modules remain
  shared. Preserve the parsed-workflow contract that Ubuntu combined and
  Windows browser require Chrome while macOS does not gate it. Then run:

  ```bash
  python3 -m pytest -q tests/test_browser_parallel_policy.py tests/test_repository_test_checks.py
  FAMULUS_REQUIRE_BROWSER=1 python3 repo_checks.py --suite full --task tests:browser --jobs 1
  ```

  Expected: policy/runner tests pass with no skips and all eleven cases execute
  in the serial browser task. Compare exact-base/candidate shared, browser, and
  full-suite phase timing under the existing optimization standard; do not
  accept lost Ubuntu/Windows coverage or a material critical-path regression
  hidden by moving work between phases. Verify that removing accidental macOS
  shared execution is consistent with the existing platform contract in
  `docs/ci-handbook.md` and `docs/testing.md`; do not edit those documents in
  this task.

- [ ] **Step 6: Reconcile the optimization ledger**

  Record the resulting node-level owner split, exact before/after collection,
  host-capable measurements, and retained/rejected ruling in the two reopened
  ledger rows. Rebaseline any later benchmark optimization against the new
  owner split.

- [ ] **Step 7: Commit only Task 2**

  ```bash
  git add src/officina/repository/checks/runner.py tests/test_browser_parallel_policy.py tests/test_repository_test_checks.py
  git commit -m "test: keep Chrome execution in serial owners"
  ```

  The controller updates the external Desktop ledger after this commit; it is
  evidence, not a repository path, and is never staged.

### Task 3: Prove Rutter worker completion

**Files:**

- Modify: `src/officina/rutter/tests/test_rutter_runtime.py`
- Modify: `src/officina/rutter/tests/test_rutter_storage.py`

**Interfaces:**

- Consumes: the two existing Rutter lock tests.
- Produces: worker-completion assertions that cannot silently outlive their tests.

- [ ] **Step 1: Strengthen the two existing Rutter tests**

  Wrap each worker target so it records any `BaseException`. After releasing
  the held lock, join with the existing bounded deadline in `finally`, then
  assert:

  ```python
  assert not thread.is_alive()
  assert worker_errors == []
  ```

  Keep the assertions that the second operation was blocked while the first
  lock was held. Do not create a static repository-wide join validator.

- [ ] **Step 2: Verify focused and shared execution**

  Run:

  ```bash
  python3 repo_checks.py --suite full --task tests:shared --selector src/officina/rutter/tests/test_rutter_runtime.py::test_registry_open_waits_for_the_reckoning_lock --selector src/officina/rutter/tests/test_rutter_storage.py::test_transactions_serialize_real_store_instances --jobs 1
  python3 repo_checks.py --suite precommit --jobs 8
  ```

  Expected: both commands pass without reruns.

- [ ] **Step 3: Commit only Task 3**

  ```bash
  git add src/officina/rutter/tests/test_rutter_runtime.py src/officina/rutter/tests/test_rutter_storage.py
  git commit -m "test: prove Rutter worker completion"
  ```

### Task 4: Remove unused assistant setup and cache only pip downloads

**Files:**

- Modify: `.github/workflows/python-tests.yml`
- Modify: `tests/test_repository_test_checks.py`
- Modify: `docs/testing.md`

**Interfaces:**

- Consumes: the existing workflow jobs, `requirements-ci.txt`, and workflow regression parser.
- Produces: ordinary matrix/probe jobs with Python dependency download caching and no unconditional assistant CLI setup.

- [ ] **Step 1: Add failing workflow assertions**

  Extend
  `test_ci_workflow_preserves_execution_dispatch_and_evidence_contracts` and
  `test_ci_workflow_dispatches_a_full_matrix_or_one_safe_probe` to assert both
  jobs use:

  ```python
  for job_name in ("test", "probe"):
      steps = parsed["jobs"][job_name]["steps"]
      setup_python = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
      assert setup_python["with"]["cache"] == "pip"
      assert setup_python["with"]["cache-dependency-path"] == "requirements-ci.txt"
      assert all("@anthropic-ai/claude-code" not in str(step) for step in steps)
      assert all("@openai/codex" not in str(step) for step in steps)
  matrix_node = next(step for step in parsed["jobs"]["test"]["steps"] if step.get("uses", "").startswith("actions/setup-node@"))
  assert matrix_node["if"] == "matrix.task == 'combined' || matrix.task == 'tests:shared'"
  probe_node = next(step for step in parsed["jobs"]["probe"]["steps"] if step.get("uses", "").startswith("actions/setup-node@"))
  assert probe_node["if"] == "inputs.task == 'combined' || inputs.task == 'tests:shared'"
  assert "LLM_WAKEUP_RUN_CLIENT_TESTS" not in workflow
  ```

  Retain exact-SHA verification, matrix membership, native sentinels, and
  artifact assertions.

- [ ] **Step 2: Observe the red workflow contract**

  Run the two workflow contract tests in
  `tests/test_repository_test_checks.py`; confirm only the new setup assertions
  fail.

- [ ] **Step 3: Simplify both workflow job setup blocks**

  Remove both assistant CLI install steps. Add the exact task conditions above
  to the two existing Node setup steps because only the shared
  `test_bundled_mathjax_is_emitted_as_valid_javascript` invokes `node --check`.
  Add `cache: pip` and `cache-dependency-path: requirements-ci.txt` to each
  existing `actions/setup-python` step. Keep the requirements install command
  unchanged; do not cache a virtualenv and do not change dependency pins.
  Update `docs/testing.md` to describe `requirements-ci.txt` as the CI
  dependency manifest rather than a lockfile and to remove the false statement
  that ordinary CI installs both assistant CLIs.

- [ ] **Step 4: Verify workflow contracts**

  Run:

  ```bash
  python3 -m pytest -q tests/test_repository_test_checks.py
  python3 repo_checks.py --suite validators --jobs 8
  ```

  Expected: both commands pass.

- [ ] **Step 5: Commit only Task 4**

  ```bash
  git add .github/workflows/python-tests.yml tests/test_repository_test_checks.py docs/testing.md
  git commit -m "perf(ci): remove unused setup and cache downloads"
  ```

### Task 5: Make deletion-only remote cleanup bypass local validation

**Files:**

- Modify: `.githooks/pre-push`
- Modify: `tests/test_repo_checks_entrypoint.py`
- Modify: `docs/contributors/git-hooks.md`

**Interfaces:**

- Consumes: Git's pre-push `remote-name remote-url` arguments and stdin records
  `<local-ref> <local-oid> <remote-ref> <remote-oid>` and the existing root
  `repo_checks.py --suite pre-push` delegation.
- Produces: a fail-closed deletion-only fast path.

- [ ] **Step 1: Add executable hook cases with a stub repository check**

  Reuse `test_support.git_repository.GitTestRepository` and
  `isolated_git_environment` to create a temporary repository. Copy the hook
  there and intercept only the delegated `python3` command. Cover:

  ```text
  (delete) 0000000000000000000000000000000000000000 refs/heads/tmp <remote-oid>
  refs/heads/main <local-oid> refs/heads/main <remote-oid>
  ```

  Assert one or more deletion-only records produce no repository-check call.
  Assert a normal update, mixed deletion/update, malformed nonempty record, and
  empty stdin each produce exactly one pre-push call. Record-driven cases pass
  mock remote-name and remote-URL arguments. Add a direct no-argument case with
  stdin deliberately left open and a short subprocess deadline, proving that
  it delegates immediately rather than blocking. Add a TTY-stdin case where
  supported. Invoke Bash explicitly so Windows does not depend on native
  shebang execution. Cover 40- and 64-hex records,
  leading/repeated/trailing spaces, tabs, carriage return, missing final
  newline, blank lines, and extra/missing fields. Include one real local `git
  push --delete` against a temporary bare remote and assert that its hook takes
  the deletion-only path.

- [ ] **Step 2: Observe the red hook contract**

  Run the new hook tests; confirm deletion-only currently delegates to the
  complete suite.

- [ ] **Step 3: Implement the narrow parser**

  Before reading stdin, require exactly Git's two standard hook arguments and
  require that stdin is not a TTY. Immediately execute the existing pre-push
  suite when either condition fails. Then use a Bash 3.2-compatible raw-line
  loop. Validate the exact lexical grammar
  `field SP field SP field SP field LF` before splitting; repeated, leading, or
  trailing whitespace, tabs, carriage returns, blank lines, and a missing final
  newline fail closed. A deletion requires `local_ref` equal to `(delete)`, a
  40- or 64-zero local OID, a nonempty `refs/` remote ref, and a same-length
  hexadecimal remote OID. Treat every mismatch, malformed line, mixed update,
  and empty stdin as non-deletion. Exit zero only when at least one valid record
  was seen and all records are deletions; otherwise execute the existing
  repository-check command unchanged.

  Update `docs/contributors/git-hooks.md` to document the one exception:
  deletion-only pushes skip repository validation, while ordinary, mixed,
  malformed, missing-argument, TTY, and direct/no-input invocation still run it
  without waiting for input.

- [ ] **Step 4: Verify hook and entrypoint contracts**

  Run:

  ```bash
  python3 -m pytest -q tests/test_repo_checks_entrypoint.py
  git diff --check
  ```

  Expected: both commands pass. Do not use `--no-verify` to test or consume the
  fast path.

- [ ] **Step 5: Commit only Task 5**

  ```bash
  git add .githooks/pre-push tests/test_repo_checks_entrypoint.py docs/contributors/git-hooks.md
  git commit -m "perf(git): skip checks for deletion-only pushes"
  ```

### Task 6: Measure the integrated first wave and stop

**Files:**

- Create: `first-wave.json` beside the Task 0 baseline in the recorded durable
  ci-debug context outside all worktrees (untracked evidence only)
- No production or test files may change in this task.

**Interfaces:**

- Consumes: accepted Task 1-5 commits and existing timing/report outputs.
- Produces: one comparison artifact and a decision on whether any deferred item merits a separate plan.

- [ ] **Step 1: Verify exact scope from a clean worktree**

  Record the baseline and candidate SHAs, list changed paths, and fail the
  checkpoint if any path lies outside Tasks 1-5.

- [ ] **Step 2: Run canonical local verification once**

  Run:

  ```bash
  python3 repo_checks.py --suite precommit --jobs 8 --timing-output "$CI_CONTEXT/candidate-precommit.json"
  python3 repo_checks.py --suite full --jobs 8 --timing-output "$CI_CONTEXT/candidate-full.json"
  git diff --check "$BASE_SHA..$CANDIDATE_SHA"
  ```

  Expected: both pass. Do not rerun to turn red evidence green.

- [ ] **Step 3: Qualify the exact candidate through ci-debug**

  Use the amended numbered algorithm. Record candidate SHA, each drift check,
  targeted batches, affected whole elements, complete matrices, repair rounds,
  and elapsed wall time. If CI is red, leave this measurement checkpoint, run
  the ordinary separately scoped repair loop, and restart Task 6 for the new
  SHA. A changed SHA never inherits qualification.

- [ ] **Step 4: Write the comparison artifact**

  Store baseline/candidate values for local precommit, shared, performance,
  browser, and full-suite wall time. From matching per-element job-step
  timestamps, record Node setup, setup-python/cache restoration, pip install,
  assistant CLI install/removal, and total setup envelopes. Record cache hit or
  miss; mark cache savings `not_measured` on a miss and wait for a naturally
  occurring warm-cache run rather than dispatching a matrix only for timing.
  Also record targeted request count, whole-element count,
  full-matrix count, and end-to-end elapsed time. Mark unavailable comparisons
  as `not_measured`; a normally green run does not measure red-matrix batching
  or drift avoidance, and the report must say so rather than estimate savings.

- [ ] **Step 5: Apply the stop rule**

  Stop after the first wave when exact-SHA CI is green. A deferred item may be
  proposed only when the artifact identifies its matching unresolved failure:
  adapter timeout/lost ownership for durable probes, SSH transport failure for
  keepalive, or duplicate required-check provenance for same-SHA reuse. Each
  proposal requires a separate user-approved design.

- [ ] **Step 6: Verify the committed scope without another commit**

  Verify that the Task 1-5 commits contain only their listed paths and that the
  worktree has no current-session tracked edits. Do not stage or commit the same
  paths again. Leave timing evidence untracked unless the user separately
  requests publication.

## Post-wave recommendation outside this plan

After Task 6, inspect GitHub's current rulesets and required check names. If
server-side `master` admission remains absent, present the exact native GitHub
rule as a separate operator proposal. Do not change repository settings, create
a disposable pull request, or add a bot/workflow without explicit user
authorization and a separate reviewed plan.

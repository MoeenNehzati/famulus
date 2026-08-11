# Native Pytest Repository Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the repository's custom runtime loader and outer check-task pool with native pytest discovery, one xdist process for validators and ordinary tests, and serial performance thresholds.

**Architecture:** `repo_checks.py` selects one repository view and asks pytest to
collect ordinary tests plus custom validator items into one xdist queue. Pytest
configuration owns discovery and imports; pytest-xdist is the only worker pool.
Full-only performance thresholds run afterward without xdist. The timing
schema, suite membership, browser serialization, cache isolation, and benchmark
contracts remain intact.

**Tech Stack:** Python 3.11, pytest, pytest-xdist, JUnit XML, Git staged-mirror validation.

## Completion Status

Implementation is complete. The first native-runner revision was measured on
Linux on 2026-08-10, but its serial validator gate did not satisfy the required
one-pool architecture. Task 6 superseded that phase design with one repository
view, one combined collection, one xdist pool, and no fail-fast.
`--sequential` remains a compatibility alias pending cross-platform CI
certification. Tasks 1-5 below retain the implementation history; their
unchecked boxes are not the current completion record.

## Global Constraints

- Work on named branch `master` in the existing dirty tree; preserve unrelated changes.
- Do not stage, commit, amend, push, restore, or stash any file.
- Preserve timing schema version 1 and task IDs `validators`, `tests:shared`, and `tests:performance`.
- Preserve benchmark schema version 3.
- Preserve the hidden tracked-root validator-child interface.
- Retain `--sequential` as a deprecated no-op alias until cross-platform CI certifies the new default.
- Retain the browser lock during this implementation.
- Exact node-ID equality, not only total count, is the collection acceptance criterion.
- Performance claims require matched comparable observations.

---

### Task 6: Put validator and ordinary items in one pytest pool

**Files:**
- Modify: `src/officina/_validator_snapshot.py`
- Modify: `src/officina/repository_checks.py`
- Modify: `conftest.py`
- Modify: `pytest.ini`
- Modify: `tests/test_repository_test_checks.py`
- Modify: `tests/test_repository_validator_checks.py`
- Modify: `TESTING.md`

**Interfaces:**
- Consumes: the existing `ValidatorPytestPlugin`, staged-index materializer,
  pytest default collector, and xdist scheduler.
- Produces: `--repository-view {auto,working,staged}`; one combined pytest
  command for suites selecting validators and ordinary tests; complete
  execution without `-x` or a validator gate.

- [x] **Step 1: Add RED runner tests**

  Assert that precommit resolves to the staged view, manual full resolves to
  the working view, and a combined suite launches exactly one parallel pytest
  command containing the validator-collector option. Assert that a pooled
  failure does not suppress the serial performance command.

- [x] **Step 2: Add RED collector integration coverage**

  Build a temporary repository containing one ordinary `test_*` module and one
  validator module. Run the real pytest command with two workers and assert both
  node IDs execute in the same session.

- [x] **Step 3: Expose one prepared repository-view lifecycle**

  Refactor the existing index snapshot and mirror materialization into a
  context manager returning the selected execution root and immutable staged
  path list. Keep cleanup and isolated Git metadata behavior unchanged.

- [x] **Step 4: Register the validator collector in ordinary pytest**

  Add repository-only pytest options in `conftest.py`. When enabled, construct
  `ValidatorPytestPlugin` from the selected view before collection; otherwise
  leave default pytest behavior untouched. Add `validators` to `testpaths` so
  the custom and default collectors contribute to one session.

- [x] **Step 5: Replace phased execution with combined selection**

  Make `run_suite()` choose the repository view once and launch a single xdist
  command for the pooled validator/test selection. Keep the hidden task selector
  capable of selecting validators, shared tests, or performance thresholds for
  benchmarking. Aggregate statuses and never pass `-x`.

- [x] **Step 6: Document the repository-view policy explicitly**

  State in `TESTING.md` that precommit runs the staged mirror, manual suites run
  the working tree, CI runs its clean checkout, and no pytest session mixes the
  two import views.

- [x] **Step 7: Verify and measure**

  Run focused collector/runner tests, exact collection comparison, one small
  two-worker combined smoke, and matched eight-worker precommit observations.
  Report whole-run and pooled active-phase core utilization separately.

---

### Task 1: Make native pytest discovery canonical

**Files:**
- Modify: `pytest.ini`
- Modify: `tests/test_unified_pytest_collection.py`
- Modify: `src/officina/common/discover_tests.py`
- Test: `tests/test_unified_pytest_collection.py`
- Test: `tests/test_discover_tests.py`

**Interfaces:**
- Consumes: pytest's `testpaths`, `python_files`, and `norecursedirs` configuration.
- Produces: native no-argument collection of the same full-suite node IDs; `is_test_module(path, repo_root=None) -> bool` remains available for classification only.

- [ ] **Step 1: Add a failing native-discovery regression**

  Change `test_unified_pytest_collection.py` to compare no-argument native
  pytest collection with collection over the existing canonical roots and to
  assert the three `skills/initialize-tdd/_rtx/tests` nodes are present.

  ```python
  assert native.returncode == explicit.returncode == 0
  assert collected_nodes(native.stdout) == collected_nodes(explicit.stdout)
  assert any("skills/initialize-tdd/_rtx/tests" in node for node in native_nodes)
  ```

- [ ] **Step 2: Run the regression and record RED**

  Run: `python3 -m pytest -q tests/test_unified_pytest_collection.py`

  Expected: FAIL because the current broad `skills/initialize-tdd` recursion exclusion omits three legitimate runtime tests.

- [ ] **Step 3: Narrow pytest discovery configuration**

  Set:

  ```ini
  norecursedirs = skills/initialize-tdd/assets/python/tests .git __pycache__
  ```

  Keep `testpaths = tests hooks/tests skills src/officina/wakeup/tests` and
  `addopts = --import-mode=importlib`.

- [ ] **Step 4: Separate path classification from execution discovery**

  Keep `is_test_module()` and the minimal constants it needs. Remove cached
  directory enumeration only after runner consumers are migrated in Task 3.

- [ ] **Step 5: Run focused discovery checks**

  Run: `python3 -m pytest -q tests/test_unified_pytest_collection.py tests/test_discover_tests.py`

  Expected: PASS with exact native/explicit node equality.

- [ ] **Step 6: Inspect checkpoint without committing**

  Run: `git diff --check -- pytest.ini src/officina/common/discover_tests.py tests/test_unified_pytest_collection.py tests/test_discover_tests.py`

---

### Task 2: Replace the synthetic runtime loader with ordinary imports

**Files:**
- Delete: `test_support/runtime_module.py`
- Delete: `tests/test_runtime_module_test_support.py`
- Modify: `hooks/tests/test_inject_dispatcher_context.py`
- Modify: loader consumers under `skills/daily-plan/_rtx/tests/`
- Modify: loader consumers under `skills/email-triage/_rtx/tests/`
- Modify: loader consumers under `skills/find-handoff-candidates/_rtx/tests/`
- Modify: loader consumers under `skills/g-calendar/_rtx/tests/`
- Modify: loader consumers under `skills/install-assistant-tools/_rtx/tests/`
- Modify: loader consumers under `skills/list-manager/_rtx/tests/`
- Modify: loader consumers under `skills/math-dependency-graph/_rtx/tests/`
- Modify: loader consumers under `skills/recurring-tasks/_rtx/tests/`
- Modify: loader consumers under `skills/skill-drift/_rtx/tests/`

**Interfaces:**
- Consumes: pytest importlib mode, package-relative imports, `monkeypatch`, and real subprocesses.
- Produces: zero imports or CLI invocations of `test_support.runtime_module`; explicit state-isolation fixtures where freshness matters.

- [ ] **Step 1: Freeze the reverse-consumer inventory**

  Run: `rg -n "load_runtime_module|test_support.runtime_module" hooks tests skills test_support`

  Record every call site and classify it as shared import, explicit reload,
  fresh subprocess, or obsolete loader coverage.

- [ ] **Step 2: Add RED state-isolation tests before changing imports**

  For each mutable module, first add a test that calls the real behavior twice
  with two distinct temporary configurations and asserts that the second call
  observes only its own configuration. For example:

  ```python
  @pytest.fixture
  def isolated_runtime(monkeypatch, tmp_path):
      from .. import _healthcheck as module
      monkeypatch.setattr(module, "LOG_DIR", tmp_path / "logs")
      monkeypatch.setattr(module, "JOBS_FILE", tmp_path / "jobs.yaml")
      return module
  ```

  Cover repeated calls in healthcheck, email-triage envelope filtering,
  list-manager commands, recurring-task enable/disable, and category cache.

- [ ] **Step 3: Run the new isolation tests and record RED where current cleanup is implicit**

  Run focused files containing the new assertions with `python3 -m pytest -q`.

  Expected: each new repeated-call test fails because a concrete module-level
  mutation survives into the second call. If a candidate passes before any
  fixture change, classify it as already isolated and do not add a fixture.

- [ ] **Step 4: Convert skill-local consumers to relative imports**

  Replace path loaders with forms such as:

  ```python
  from .. import _gcal_client as gcal
  from .. import _job_control as job_control
  from .. import _yaml_store as yaml_store
  ```

  Move imports into function-scoped fixtures only when a test needs controlled
  import-time state. Use `monkeypatch` for all mutable globals.

- [ ] **Step 5: Convert cross-tree consumers to repository-qualified imports**

  Use:

  ```python
  import importlib

  installer = importlib.import_module(
      "skills.install-assistant-tools._rtx._install_launcher"
  )
  ```

  Repository-qualified imports must not create physical `_rtx` entries in
  `sys.modules`.

- [ ] **Step 6: Replace loader CLI smokes with actual executable boundaries**

  Invoke the production module or `python_machine_interface_runner` in a child
  process with repository root and `src` on `PYTHONPATH`. Do not add a new
  loader wrapper.

- [ ] **Step 7: Delete loader-only coverage and implementation**

  Delete the helper self-tests and foreign-`_rtx` restoration test only after
  all product-behavior assertions have replacements. Delete
  `test_support/runtime_module.py` last.

- [ ] **Step 8: Verify all former consumers**

  Run every file returned by the Step 1 inventory in one serial pytest command,
  then repeat under `-n 8 --dist loadgroup`.

  Expected: all focused files pass; `rg` returns no loader reference.

- [ ] **Step 9: Inspect checkpoint without committing**

  Run: `git diff --check -- test_support tests hooks/tests skills`

---

### Task 3: Replace the outer pool with one phased runner

> Superseded by Task 6. This section records the measured intermediate design;
> its validator-first and fail-fast requirements are not current requirements.

**Files:**
- Modify: `src/officina/repository_checks.py`
- Modify: `tests/test_repository_test_checks.py`
- Modify: `.github/workflows/python-tests.yml` only after cross-platform certification; otherwise leave its compatibility invocation unchanged

**Interfaces:**
- Consumes: `_validator_snapshot.run_all()`, `_suite_pytest_args()`, `_pytest_args()`, stable suite policy constants, and `_terminate_task_process()` or a smaller equivalent.
- Produces: `run_suite(..., task_id: str | None = None, task_cache_dir: Path | None = None) -> int`; ordered stable phase IDs with no `CheckTask` or slot pool.

- [ ] **Step 1: Rewrite runner-policy tests to describe phases rather than tasks**

  Add assertions that:

  ```python
  assert runner.SUITE_PHASES["full"] == (
      "validators", "tests:shared", "tests:performance"
  )
  assert runner.SUITE_PHASES["precommit"] == (
      "validators", "tests:shared"
  )
  ```

  Add tests for fail-fast, direct validator execution, functional xdist
  arguments, serial performance arguments, task selection, invalid task/suite
  combinations, cache paths, Ctrl-C status 130, and deprecated sequential alias.

- [ ] **Step 2: Run focused runner tests and record RED**

  Run: `python3 -m pytest -q tests/test_repository_test_checks.py`

  Expected: FAIL because `CheckTask`, the scheduler, and the dual routes still
  own execution.

- [ ] **Step 3: Replace suite definitions with stable ordered phase IDs**

  Use one suite-to-phase mapping and one suite-to-test-profile mapping. Keep
  exact validator exclusions, deselections, portability nodes, distribution
  modes, and performance-node configuration.

- [ ] **Step 4: Implement one streaming subprocess boundary**

  Start one child with inherited stdout/stderr, wait for it, and terminate its
  process tree on `KeyboardInterrupt`. Return 130 after cleanup. Do not poll,
  buffer output, replay logs, or admit multiple phases.

- [ ] **Step 5: Implement direct ordered phase execution**

  Within one temporary directory:

  ```python
  for task_id in selected_phase_ids:
      status, wall_seconds, junit_path = run_phase(task_id)
      completed.append((task_id, status, wall_seconds, junit_path))
      if status:
          break
  ```

  Validators call `_validator_snapshot.run_all()` in the parent. Functional
  pytest uses no enumerated targets except portability's exact nodes. Full
  functional execution deselects performance nodes; performance uses jobs=1.

- [ ] **Step 6: Preserve timing schema without task objects**

  Adapt `_write_timing_report()` to accept completed phase records and emit the
  unchanged schema/version/task IDs. Write partial reports after any failure.

- [ ] **Step 7: Remove obsolete scheduler code**

  Delete `CheckTask`, `_execution_groups`, `_build_check_tasks`,
  `_run_check_tasks`, outer `_run_validator_task`, pooled branching, and outer
  `--internal-run-validators`. Retain the hidden tracked-root parser route.

- [ ] **Step 8: Preserve the sequential compatibility alias**

  Parse `--sequential`, emit no alternative behavior, and document it as a
  temporary deprecated alias. Do not update CI until all three OSes certify the
  default route.

- [ ] **Step 9: Run focused runner and validator tests**

  Run:

  ```text
  python3 -m pytest -q tests/test_repository_test_checks.py \
    tests/test_repository_validator_checks.py tests/test_fixture_probe.py
  ```

  Expected: PASS with no scheduler-only test remaining.

- [ ] **Step 10: Inspect checkpoint without committing**

  Run: `git diff --check -- src/officina/repository_checks.py tests/test_repository_test_checks.py .github/workflows/python-tests.yml`

---

### Task 4: Decouple benchmark selection from runner internals

**Files:**
- Modify: `scripts/benchmark-test-suite.py`
- Modify: `tests/test_benchmark_test_suite.py`
- Modify: `scripts/benchmark-precommit.py` only if its delegation arguments change

**Interfaces:**
- Consumes: hidden runner arguments `--task-id` and `--task-cache-dir`.
- Produces: selected-checkout benchmark commands without importing `_build_check_tasks`; unchanged schema version 3 artifacts.

- [ ] **Step 1: Replace benchmark tests with CLI-resolution expectations**

  Assert a task command has this shape:

  ```python
  [
      sys.executable,
      str(repo / "repo_checks.py"),
      "--suite", "full",
      "--jobs", "8",
      "--task-id", "tests:shared",
      "--task-cache-dir", str(cache_dir),
  ]
  ```

  Assert unknown task IDs fail in the selected checkout's runner, not during a
  private module import.

- [ ] **Step 2: Run benchmark tests and record RED**

  Run: `python3 -m pytest -q tests/test_benchmark_test_suite.py tests/test_benchmark_precommit.py`

  Expected: FAIL because the harness still loads `_build_check_tasks()`.

- [ ] **Step 3: Delete selected-root module loading**

  Remove `_load_repository_checks()` and all `sys.modules`/`sys.path` snapshot
  manipulation. Construct only runner CLI commands from the selected root.

- [ ] **Step 4: Preserve cache and artifact semantics**

  Keep selected-checkout paths, fingerprint checks, warm priming, requested
  runs, task IDs, task cache paths, return-code classification, resource
  sampling, and schema version 3. Reuse one stable task cache for a warm series;
  cold observations remove/recreate their benchmark-owned cache.

- [ ] **Step 5: Run benchmark-focused tests**

  Run: `python3 -m pytest -q tests/test_benchmark_test_suite.py tests/test_benchmark_precommit.py tests/test_benchmark_command.py`

  Expected: PASS with no selected-root import leakage test because no import occurs.

- [ ] **Step 6: Run a small live benchmark smoke**

  Run one warm observation of a small supported task or suite with resource
  measurement disabled. Expected: exit 0 and valid schema version 3 JSON.

- [ ] **Step 7: Inspect checkpoint without committing**

  Run: `git diff --check -- scripts/benchmark-test-suite.py scripts/benchmark-precommit.py tests/test_benchmark_test_suite.py`

---

### Task 5: Update documentation and certify behavior

**Files:**
- Modify: `TESTING.md`
- Modify: `docs/test-performance-audit.md`
- Modify: `docs/test-refactor-ledger/*.md` only where they claim the deleted loader or scheduler is current
- Modify: `docs/superpowers/specs/2026-08-10-native-pytest-runner-design.md` only for implementation-discovered corrections
- Modify: `docs/superpowers/plans/2026-08-10-native-pytest-runner.md` checkbox state

**Interfaces:**
- Consumes: final runner CLI, suite membership, timing output, benchmark commands, and measured artifacts.
- Produces: current operational documentation and matched performance evidence.

- [ ] **Step 1: Update current documentation**

  Document one xdist pool, explicit sequential phases, native discovery,
  pytest-only test modules, staged validators, serial performance tests,
  temporary cache isolation, benchmark task selection, and the temporary
  `--sequential` alias.

- [ ] **Step 2: Run exact collection comparisons**

  Compare node-ID sets for validators, tests, precommit, pre-push, portability,
  and full against a clean worktree at the current `HEAD`. Record additions or
  removals caused only by approved new regression tests separately.

- [ ] **Step 3: Run focused and one-core acceptance**

  Run loader-consumer tests, runner/benchmark tests, validator tests, browser
  policy tests, and canonical `--jobs 1` precommit. Expected: green except
  explicitly documented pre-existing dirty-tree failures outside scope.

- [ ] **Step 4: Run eight-core acceptance**

  Run canonical precommit and full with `--jobs 8`. Preserve exact exit and
  failure classification; do not classify unrelated known failures as runner
  regressions.

- [ ] **Step 5: Measure matched old/new performance**

  Use an isolated `HEAD` worktree and the same benchmark harness, host power
  state, worker count, cache condition, suite, and repetition count. Measure at
  least three warm observations each for precommit and full when green;
  otherwise label observations diagnostic and make no speedup claim.

- [ ] **Step 6: Report resource utilization**

  Report wall median, whole-run effective cores, functional-phase effective
  cores, peak cores, and final-quarter/10-second/5-second tails. Compare against
  the recorded current baseline without attributing noise to code changes.

- [ ] **Step 7: Run final verification**

  Run `git diff --check` on every implementation-owned path and `git status
  --short`. Confirm no files were staged or committed and unrelated dirty paths
  remain untouched.

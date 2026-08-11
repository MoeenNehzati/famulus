# Single-Pool Pytest Scheduling Implementation Plan

> **Historical stage:** This plan records the browser scheduling step that was
> implemented before the runner simplification. Its outer coordinator, worker
> lease, isolated-runtime, and fail-fast descriptions are superseded by
> [Native Pytest Repository Runner Design](../specs/2026-08-10-native-pytest-runner-design.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the uncommitted fixed browser/general pytest lanes with one six-worker xdist pool that schedules all serialized browser tests as one standard `loadgroup` work unit.

**Architecture:** The outer repository coordinator keeps its existing eight-slot process scheduler. Browser-containing pytest suites use `--dist=loadgroup`; all five Chrome modules declare `xdist_group("browser")`, so one worker executes the already-serialized browser work while the other workers remain eligible for ordinary tests. Browser-free parallel suites retain `worksteal`, and serial execution remains unchanged.

**Tech Stack:** Python 3.13, pytest 8.3, pytest-xdist 3.8, the existing repository check coordinator, and the existing benchmark harness.

## Global Constraints

- Preserve the exact test inventory, assertions, browser serialization fixture, skips, subprocess boundaries, validator staged-mirror lifecycle, isolated `_rtx` processes, fail-fast admission behavior, and platform policy.
- Use one shared pytest process for ordinary and browser tests; do not create a `tests:browser` task.
- At `--jobs 8`, `tests:shared` leases six xdist workers while the validator and one isolated task consume the remaining two slots.
- Keep the existing browser lock as a defensive guard for direct and noncanonical invocations.
- Use `loadgroup` only for parallel suite profiles that include browser tests; retain `worksteal` elsewhere.
- Do not split test files or add historical-timing input to scheduling.
- Treat wall time as the acceptance metric and effective-core usage as diagnostic evidence.
- Do not make whole-suite speed claims from dirty, failing, sandbox-restricted, or fail-fast-truncated runs.
- Preserve unrelated staged and unstaged worktree changes.
- Do not stage or commit any file until the user explicitly authorizes a commit.

---

## File map

- `pytest.ini` — registers the browser scheduling marker for warning-free serial collection.
- `tests/test_visualization_browser.py` — declares membership in the shared browser xdist group.
- `tests/test_visualization_containment_edges_browser.py` — declares membership in the shared browser xdist group.
- `tests/test_visualization_inspector_and_bezier_browser.py` — declares membership in the shared browser xdist group.
- `tests/test_visualization_projection_arrangements_browser.py` — declares membership in the shared browser xdist group.
- `tests/test_visualization_projection_browser.py` — declares membership in the shared browser xdist group.
- `tests/test_browser_parallel_policy.py` — verifies exact browser-module inventory and marker coverage without launching another pytest process.
- `src/officina/repository_checks.py` — selects the distribution mode and restores one shared pytest task.
- `tests/test_repository_test_checks.py` — verifies suite-specific distribution arguments and task leases.
- `tests/test_benchmark_test_suite.py` — verifies the benchmark harness resolves the restored shared task.
- `TESTING.md` — documents the single-pool worker and marker contract.
- `docs/test-performance-audit.md` — records diagnostic measurements without overstating a failing full run.

---

### Task 1: Declare and verify one browser xdist group

**Files:**
- Create: `tests/test_browser_parallel_policy.py`
- Modify: `pytest.ini`
- Modify: `tests/test_visualization_browser.py`
- Modify: `tests/test_visualization_containment_edges_browser.py`
- Modify: `tests/test_visualization_inspector_and_bezier_browser.py`
- Modify: `tests/test_visualization_projection_arrangements_browser.py`
- Modify: `tests/test_visualization_projection_browser.py`

**Interfaces:**
- Consumes: pytest's module-level `pytestmark` convention and pytest-xdist's built-in `xdist_group(name)` marker.
- Produces: `repository_checks.CHROME_TESTS` exactly matches every `tests/**/*_browser.py`, and each path exposes exactly one module-level `xdist_group("browser")` marker.

- [ ] **Step 1: Write the failing repository marker-policy test**

Create `tests/test_browser_parallel_policy.py` with this policy test:

```python
from pathlib import Path
import runpy

import pytest

from officina import repository_checks


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative_path", sorted(repository_checks.CHROME_TESTS))
def test_browser_modules_declare_one_shared_xdist_group(
    relative_path: str,
) -> None:
    namespace = runpy.run_path(str(REPO_ROOT / relative_path))
    marker = namespace.get("pytestmark")

    assert marker is not None
    assert marker.name == "xdist_group"
    assert marker.args == ("browser",)
    assert marker.kwargs == {}
```

- [ ] **Step 2: Run the policy test and verify RED**

Run:

```bash
pytest -o pythonpath=src -q tests/test_browser_parallel_policy.py::test_browser_modules_declare_one_shared_xdist_group
```

Expected: five failures because the browser modules do not yet define `pytestmark`.

- [ ] **Step 3: Register and apply the marker**

Add this configuration to `pytest.ini`:

```ini
markers =
    xdist_group(name): keep tests with the same resource group on one xdist worker
```

Immediately after `import pytest` in each of the five browser modules, add:

```python
pytestmark = pytest.mark.xdist_group("browser")
```

Do not alter the browser test functions or `tests/conftest.py::serialize_browser_tests`.

- [ ] **Step 4: Run the marker-policy test and verify GREEN**

Run:

```bash
pytest -o pythonpath=src -q tests/test_browser_parallel_policy.py::test_browser_modules_declare_one_shared_xdist_group
```

Expected: `5 passed` with no unknown-marker warnings.

- [ ] **Step 5: Add exact browser-inventory coverage**

Add this test before the parameterized marker-policy test:

```python
def test_browser_inventory_matches_all_discovered_browser_modules() -> None:
    discovered = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests").rglob("*_browser.py")
    }

    assert discovered == repository_checks.CHROME_TESTS
```

This closes the policy boundary without a nested xdist subprocess. The runner
command tests separately verify `loadgroup`, worker leases, shared-task shape,
and serial behavior.

- [ ] **Step 6: Run the complete browser-policy test module**

Run:

```bash
pytest -o pythonpath=src -q tests/test_browser_parallel_policy.py
```

Expected: `6 passed`: one exact-inventory case and five marker-policy cases.

- [ ] **Step 7: Review checkpoint without staging or committing**

Run:

```bash
git diff --check -- pytest.ini tests/test_browser_parallel_policy.py tests/test_visualization_browser.py tests/test_visualization_containment_edges_browser.py tests/test_visualization_inspector_and_bezier_browser.py tests/test_visualization_projection_arrangements_browser.py tests/test_visualization_projection_browser.py
```

Expected: exit 0. Do not stage or commit.

---

### Task 2: Restore one shared task and select loadgroup by suite

**Files:**
- Modify: `src/officina/repository_checks.py:923-952,1003-1036,1212-1366`
- Modify: `tests/test_repository_test_checks.py:44-110,261-340`
- Modify: `tests/test_benchmark_test_suite.py:82-122`

**Interfaces:**
- Consumes: module-level `xdist_group("browser")` markers from Task 1.
- Produces: `_pytest_args(*, verbose: bool, jobs: int = 1, distribution: str = "worksteal") -> list[str]`; `_suite_pytest_args(name: str, *, verbose: bool, jobs: int = 1) -> list[str]`; one `tests:shared` task leasing six slots at `jobs=8`.

- [ ] **Step 1: Replace the fixed-lane tests with failing single-pool tests**

Keep `test_runner_adds_exact_xdist_worker_count_for_parallel_jobs` as the default-worksteal contract. Add these distribution tests:

```python
@pytest.mark.parametrize("suite", ["full", "pre-push"])
def test_browser_suites_use_loadgroup(suite: str) -> None:
    args = runner._suite_pytest_args(suite, verbose=False, jobs=6)
    assert args[args.index("--dist") + 1] == "loadgroup"


@pytest.mark.parametrize("suite", ["precommit", "portability"])
def test_browser_free_suites_keep_worksteal(suite: str) -> None:
    args = runner._suite_pytest_args(suite, verbose=False, jobs=6)
    assert args[args.index("--dist") + 1] == "worksteal"


def test_serial_browser_suite_adds_no_distribution_mode() -> None:
    args = runner._suite_pytest_args("full", verbose=False, jobs=1)
    assert "--dist" not in args
```

Replace `test_full_suite_runs_browser_files_in_a_bounded_parallel_lane` with:

```python
def test_full_suite_keeps_browser_files_in_one_shared_loadgroup_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "_resolve_suite",
        lambda _name: ["tests", "skills/example/_rtx/tests"],
    )
    tasks = runner._build_check_tasks(
        tmp_path,
        "full",
        verbose=False,
        jobs=8,
        validator_ids=(),
        excluded_validator_ids=(),
    )

    assert [task.id for task in tasks] == [
        "validators",
        "tests:shared",
        "tests:skills/example/_rtx/tests",
        "tests:performance",
    ]
    assert [task.slots for task in tasks] == [1, 6, 1, 8]
    shared = tasks[1]
    assert shared.argv[shared.argv.index("-n") + 1] == "6"
    assert shared.argv[shared.argv.index("--dist") + 1] == "loadgroup"
    assert not any(argument.startswith("--ignore=") for argument in shared.argv)
```

Retain the serial full-suite test, but assert only the three task IDs,
`[1, 1, 1]` slots, and absence of `--dist`; remove its `CHROME_TESTS`
monkeypatch because browser files are no longer path-filtered in task construction.

- [ ] **Step 2: Update the benchmark task-resolution test for the desired contract**

In `test_task_resolution_loads_live_selected_root_without_officina_leakage`, assert:

```python
assert slots == 6
assert command[command.index("--dist") + 1] == "loadgroup"
assert not any(argument.startswith("--ignore=") for argument in command)
with pytest.raises(ValueError, match="tests:browser"):
    benchmark.resolve_benchmark_command(
        Path(__file__).resolve().parents[1],
        "full",
        8,
        "tests:browser",
        tmp_path / "browser-task-cache",
    )
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
env PYTHONPATH=<repo>:<repo>/src pytest -q tests/test_repository_test_checks.py tests/test_benchmark_test_suite.py
```

Expected: failures show `worksteal` instead of `loadgroup`, the extra
`tests:browser` task, and 4/2 slots instead of one six-slot shared task.

- [ ] **Step 4: Parameterize common pytest argument construction**

Change the `_pytest_args` signature to:

```python
def _pytest_args(
    *,
    verbose: bool,
    jobs: int = 1,
    distribution: str = "worksteal",
) -> list[str]:
```

When `jobs > 1`, append:

```python
args.extend(["-n", str(jobs), "--dist", distribution])
```

Update its structured docstring so the intent, rationale, and pseudocode state
that the suite policy supplies the distribution mode.

In `_suite_pytest_args`, construct the common arguments with:

```python
distribution = "loadgroup" if name in {"full", "pre-push"} else "worksteal"
args = _pytest_args(
    verbose=verbose,
    jobs=jobs,
    distribution=distribution,
)
```

Do not change suite deselections.

- [ ] **Step 5: Remove only the uncommitted fixed-lane implementation**

In `_build_check_tasks`:

- delete `split_browser_lane`, `browser_jobs`, and `general_jobs`;
- restore `group_jobs = 1 if isolated else shared_jobs`;
- remove all `--ignore=<browser path>` arguments;
- remove creation of `CheckTask("tests:browser", ...)`;
- restore the docstring to one shared ordinary-test task plus isolated groups.

Do not use `git checkout` or another broad restoration command because this
file contains both committed runner work and the current uncommitted experiment.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run:

```bash
env PYTHONPATH=<repo>:<repo>/src pytest -q tests/test_repository_test_checks.py tests/test_benchmark_test_suite.py tests/test_browser_parallel_policy.py tests/test_fixture_probe.py
```

Expected: all tests pass in the one serial pytest process; no nested xdist pool
is created.

- [ ] **Step 7: Review checkpoint without staging or committing**

Run:

```bash
git diff --check -- src/officina/repository_checks.py tests/test_repository_test_checks.py tests/test_benchmark_test_suite.py
```

Expected: exit 0. Do not stage or commit.

---

### Task 3: Document and benchmark the standard scheduler

**Files:**
- Modify: `TESTING.md:128-138`
- Modify: `docs/test-performance-audit.md`

**Interfaces:**
- Consumes: the canonical `tests:shared` task and `loadgroup` marker contract from Tasks 1–2.
- Produces: user-facing scheduling documentation and reproducible diagnostic artifacts for the single-pool candidate.

- [ ] **Step 1: Update the runner documentation**

Replace the fixed-lane paragraph in `TESTING.md` with:

```markdown
Omitting `--jobs` uses the live default. For the parallel full suite, an
eight-slot budget gives the single `tests:shared` process six xdist workers.
Chrome-backed modules share one `xdist_group("browser")`, so one worker runs the
already-serialized browser group while the remaining workers stay eligible for
ordinary tests. Serial execution ignores xdist grouping.
```

Add a direct browser-containing task example only through `tests:shared`; do not
document a `tests:browser` task.

- [ ] **Step 2: Run a small unrestricted loadgroup correctness sample**

Run outside sandbox restrictions because Chrome requires process/socket access:

```bash
scripts/benchmark-command.py --output /tmp/single-pool-loadgroup-small.json \
  --log /tmp/single-pool-loadgroup-small.log -- \
  pytest -o pythonpath=src -q -n 6 --dist loadgroup \
  tests/test_visualization_browser.py \
  tests/test_visualization_containment_edges_browser.py \
  tests/test_visualization_inspector_and_bezier_browser.py \
  tests/test_visualization_projection_arrangements_browser.py \
  tests/test_visualization_projection_browser.py \
  tests/test_repository_test_checks.py \
  tests/test_benchmark_test_suite.py
```

Expected: exit 0; 31 browser tests plus the focused ordinary tests execute once.
Record wall time, average effective cores, peak cores, and return code.

- [ ] **Step 3: Run the matched worksteal comparison**

Run the identical targets and worker count, changing only the distribution mode:

```bash
scripts/benchmark-command.py --output /tmp/single-pool-worksteal-small.json \
  --log /tmp/single-pool-worksteal-small.log -- \
  pytest -o pythonpath=src -q -n 6 --dist worksteal \
  tests/test_visualization_browser.py \
  tests/test_visualization_containment_edges_browser.py \
  tests/test_visualization_inspector_and_bezier_browser.py \
  tests/test_visualization_projection_arrangements_browser.py \
  tests/test_visualization_projection_browser.py \
  tests/test_repository_test_checks.py \
  tests/test_benchmark_test_suite.py
```

Expected: exit 0. Compare wall time first and effective-core use second. If
`loadgroup` is slower, stop and retain the uncommitted experiment for redesign;
do not proceed to the full diagnostic run.

- [ ] **Step 4: Run one canonical unrestricted diagnostic full sample**

Run:

```bash
scripts/benchmark-test-suite.py --repo <repo> --suite full \
  --output /tmp/full-single-pool-loadgroup.json --runs 1 --cache cold --jobs 8 \
  --no-prime --measure-resources
```

Expected: the log contains one `START task=tests:shared slots=6` and no
`tests:browser`. Record failures rather than treating a dirty-tree run as a
green timing. Calculate main-phase effective cores only over the interval in
which `tests:shared` is active, alongside the whole-run metric.

- [ ] **Step 5: Record evidence without overstating it**

Append a dated scheduler subsection to `docs/test-performance-audit.md` with:

- exact commands and artifact paths;
- loadgroup and worksteal small-sample return codes, item counts, wall times,
  average effective cores, and peak cores;
- full-run return code, wall time, whole-run cores, main-phase cores, and exact
  failures;
- the explicit statement that a dirty or failing full run is diagnostic, not a
  certified whole-suite improvement.

- [ ] **Step 6: Run final focused verification**

Run:

```bash
env PYTHONPATH=<repo>:<repo>/src pytest -q \
  tests/test_browser_parallel_policy.py \
  tests/test_repository_test_checks.py \
  tests/test_benchmark_test_suite.py \
  tests/test_fixture_probe.py
git diff --check
```

Expected: all focused tests pass and `git diff --check` exits 0.

- [ ] **Step 7: Final review checkpoint without staging or committing**

Inspect:

```bash
git diff -- pytest.ini TESTING.md src/officina/repository_checks.py \
  tests/test_browser_parallel_policy.py \
  tests/test_repository_test_checks.py tests/test_benchmark_test_suite.py \
  tests/test_visualization_browser.py \
  tests/test_visualization_containment_edges_browser.py \
  tests/test_visualization_inspector_and_bezier_browser.py \
  tests/test_visualization_projection_arrangements_browser.py \
  tests/test_visualization_projection_browser.py \
  docs/test-performance-audit.md
```

Confirm the diff contains the single-pool scheduler change and pre-existing
approved test-performance work only. Do not stage or commit.

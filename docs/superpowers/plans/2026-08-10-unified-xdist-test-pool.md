# Unified xdist Test Pool Implementation Plan

> **Historical stage:** This plan records the transition from per-runtime pytest
> processes to native unified collection. References to the runtime loader,
> separate validator lifecycle, and outer runner tasks are superseded by
> [Native Pytest Repository Runner Design](../specs/2026-08-10-native-pytest-runner-design.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every compatible functional test into one collision-free xdist pool using all requested pytest workers.

**Architecture:** Pytest importlib collection gives test modules repository-relative identities, empty runtime-test package markers are removed, and remaining ambiguous imports use ordinary repository-qualified helpers or the existing collision-free runtime loader. The runner emits one full-width functional pytest task while validators and performance measurements retain their current correctness boundaries.

**Tech Stack:** Python 3.13, pytest, pytest-xdist, existing `officina.repository_checks`, existing `test_support.runtime_module`.

## Global Constraints

- Preserve the exact test inventory, assertions, skips, subprocess behavior, and platform policy.
- Preserve the staged validator snapshot and uncontended performance-test lifecycle.
- Keep the existing browser `xdist_group("browser")` serialization policy.
- Add no custom collector, adaptive scheduler, or production runtime package redesign.
- Do not stage, commit, amend, or push any files.
- Preserve unrelated dirty-worktree changes, including existing test-performance refactors.

---

### Task 1: Prove and fix collision-free collection

**Files:**
- Create: `tests/test_unified_pytest_collection.py`
- Modify: `pytest.ini`
- Modify: `tests/conftest.py` or a focused `test_support` helper only where importlib collection exposes an ambiguous helper import
- Delete: each existing `skills/*/_rtx/tests/__init__.py`
- Modify: only runtime test modules that still fail collection after the package-marker change

**Interfaces:**
- Consumes: `test_support.runtime_module.load_runtime_module(path: Path) -> ModuleType`
- Produces: one canonical pytest configuration under which multiple runtime roots collect in the same process

- [ ] **Step 1: Add a focused subprocess test that invokes `python -m pytest --collect-only -q` for two formerly colliding runtime roots and asserts exit code zero and node IDs from both roots.**
- [ ] **Step 2: Run that test against the current layout and retain the expected `_rtx.tests` collision as RED evidence.**
- [ ] **Step 3: Set `addopts = --import-mode=importlib` in `pytest.ini` and remove the fifteen empty `_rtx/tests/__init__.py` markers.**
- [ ] **Step 4: Run the focused test; for each remaining collection error, replace unqualified test-helper imports with repository-qualified imports or load runtime targets through `load_runtime_module` without changing assertions.**
- [ ] **Step 5: Run repository-wide `pytest --collect-only -q`; record the collected count and require zero collection errors.**
- [ ] **Step 6: Run the formerly isolated runtime suites serially and fix only collection-state regressions revealed by the unified import context.**

### Task 2: Consolidate functional test tasks

**Files:**
- Modify: `tests/test_repository_test_checks.py`
- Modify: `tests/test_benchmark_test_suite.py`
- Modify: `src/officina/repository_checks.py`

**Interfaces:**
- Consumes: `_resolve_suite(name: str) -> list[str]` and `_suite_pytest_args(name: str, *, verbose: bool, jobs: int) -> list[str]`
- Produces: `_execution_groups(test_dirs: list[str]) -> list[list[str]]` containing one functional group and `_build_check_tasks(...)` containing one full-width `tests:shared` task

- [ ] **Step 1: Change runner-policy tests to require one functional group containing ordinary and `_rtx` targets, `tests:shared` with eight slots at `jobs=8`, no per-runtime task IDs, and unchanged validator/performance tasks.**
- [ ] **Step 2: Run the focused runner tests and retain the expected old-grouping failures as RED evidence.**
- [ ] **Step 3: Make `_execution_groups` return one nonempty group and remove the `_rtx` singleton branch and static `_shared_test_jobs` reservation from task construction.**
- [ ] **Step 4: Give `tests:shared` the requested job count and preserve suite-specific `worksteal`/`loadgroup`, deselections, cache isolation, deterministic reporting, and performance-task isolation.**
- [ ] **Step 5: Run repository-check and benchmark-harness tests until green.**

### Task 3: Preserve narrow resource serialization

**Files:**
- Modify: only test modules with a reproduced parallel shared-resource failure
- Modify: `tests/test_browser_parallel_policy.py` if the inventory assertion needs adaptation to importlib collection
- Modify: `pytest.ini` only for documented marker registration

**Interfaces:**
- Consumes: pytest `xdist_group(name)` and the existing browser inventory policy
- Produces: item-level or module-level grouping only for proven shared resources

- [ ] **Step 1: Run the unified functional targets with `-n 8 --dist loadgroup` and capture every failure.**
- [ ] **Step 2: Reproduce each suspected scheduling failure serially and in isolation; do not mark failures caused by product defects or dirty-tree expectations.**
- [ ] **Step 3: For each confirmed resource collision, add the narrowest shared `xdist_group` marker and a policy test that fails without it.**
- [ ] **Step 4: Re-run browser policy, skip hygiene, affected suites, and the unified eight-worker run.**

### Task 4: Inventory, correctness, and performance acceptance

**Files:**
- Modify: `TESTING.md`
- Modify: `docs/test-performance-audit.md`
- Create: benchmark artifacts only under `/tmp`

**Interfaces:**
- Consumes: `repo_checks.py --suite <suite> --jobs <n>`, `scripts/benchmark-test-suite.py`, and runner timing output
- Produces: documented canonical one-pool policy and fresh matched timing evidence

- [ ] **Step 1: Compare the unified collection node-id set with the union from the old grouped policy; require exact equality.**
- [ ] **Step 2: Run focused collection, runtime-loader, runner-policy, browser-policy, benchmark-harness, and fixture-probe tests.**
- [ ] **Step 3: Run precommit with one and eight jobs; report exact exit status, wall time, and test totals.**
- [ ] **Step 4: Run full with one and eight jobs; distinguish green acceptance observations from dirty-tree diagnostics.**
- [ ] **Step 5: Run matched cold and warm eight-job benchmarks and report wall time, average effective cores for the active phase and whole run, peak cores, and tail utilization.**
- [ ] **Step 6: Update `TESTING.md` and the performance audit with commands, boundaries, and measured results; make no claim unsupported by green matched evidence.**
- [ ] **Step 7: Run `git diff --check`, inspect the exact scoped diff, and request independent code review. Address every Critical or Important finding before completion.**

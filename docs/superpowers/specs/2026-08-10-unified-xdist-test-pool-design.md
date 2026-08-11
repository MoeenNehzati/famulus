# Unified xdist Test Pool Design

> **Historical design:** This document explains the native-collection
> migration. Its runtime-loader and separate-validator assumptions were later
> removed. The current architecture is
> [Native Pytest Repository Runner Design](2026-08-10-native-pytest-runner-design.md).

## Goal

Run every compatible functional pytest item through one eight-worker xdist
invocation so idle workers can take work from any repository or skill-runtime
test suite. Preserve the exact collected test inventory, assertions, skips,
subprocess boundaries inside tests, browser serialization, platform policy,
validator staged-snapshot lifecycle, and uncontended performance measurements.

## Current constraint

The runner currently gives the shared pytest task six workers and launches each
discovered `skills/*/_rtx/tests` root in a separate one-worker pytest process.
That boundary avoids a collection collision: the runtime roots expose fifteen
different physical packages under the same logical Python name, `_rtx`.

A direct repository-wide collection currently stops with 66 import errors.
Pytest's standard `--import-mode=importlib` reduces the failures to 33 but
cannot by itself disambiguate runtime test packages that still contain
`_rtx/tests/__init__.py` or tests that depend on unqualified helper imports.

## Architecture

### Collection

- Configure pytest to use `--import-mode=importlib` canonically.
- Remove the fifteen `skills/*/_rtx/tests/__init__.py` package markers so pytest
  derives collision-free module identities from repository-relative paths.
- Preserve runtime package-relative behavior with repository-qualified imports
  where safe and the existing
  `test_support.runtime_module.load_runtime_module()` helper where runtime
  modules need a synthetic package for relative imports.
- Move test helpers out of ambiguous `conftest` imports and address them through
  ordinary test-support modules.
- Do not add a custom collector or a second discovery system.

### Execution

- Resolve the same suite targets as today, but place all compatible functional
  targets into one `tests:shared` pytest task.
- Give that task the complete requested pytest worker count: `-n 8` for
  `--jobs 8`.
- Continue to use `loadgroup` for browser-containing suites and keep the five
  browser modules in their existing `xdist_group("browser")` group.
- Keep validators as their existing staged-snapshot task. Do not convert
  validators to pytest items in this change.
- Keep performance-threshold tests as an uncontended task because concurrent
  work would invalidate their measurements.

The outer coordinator remains responsible for deterministic reporting,
fail-fast admission, interruption cleanup, and the total task lifecycle. When
the eight-worker functional task is active under an eight-slot budget, other
tasks wait; this eliminates the static six-plus-two partition. Validators may
become a later critical path, but that is a separate measured optimization.

## State and resource policy

- Function-scoped `tmp_path` and `monkeypatch` isolation remains the default.
- Module-level `sys.path`, `sys.modules`, environment, or working-directory
  mutations must either be eliminated or restored by a fixture/helper.
- Only demonstrated shared external resources receive `xdist_group` markers.
- Browser serialization remains mandatory. No directory receives blanket
  serialization merely because it is an `_rtx` runtime.
- Real installer and scheduler subprocesses retain their current temporary
  homes, repositories, and platform skips.

## Verification

1. A focused two-runtime collection test fails on the old package layout and
   passes under the new canonical configuration.
2. Repository-wide collection completes without errors.
3. The one-pool node-id set equals the union of node IDs from the prior grouped
   execution policy.
4. Focused runtime suites pass serially.
5. Precommit and full functional tests pass with one and eight workers, subject
   only to already-documented dirty-tree failures unrelated to scheduling.
6. Browser policy, skip hygiene, runner policy, and benchmark harness tests
   remain green.
7. Cold and warm matched benchmarks report wall time, average effective cores,
   and the tail profile. Performance claims require green matched observations.

## Non-goals

- Rewriting production skill runtimes into a new package hierarchy.
- Changing test assertions, behavior coverage, skips, or platform policy.
- Parallelizing validators internally.
- Running performance thresholds under concurrent load.
- Adding adaptive scheduling or a custom pytest distribution plugin.
- Staging, committing, or pushing the dirty worktree in this phase.

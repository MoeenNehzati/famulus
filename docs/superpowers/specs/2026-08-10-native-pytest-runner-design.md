# Native Pytest Repository Runner Design

## Goal

Replace the custom repository-check loader and process pool with the smallest
runner that preserves repository policy:

1. every invocation chooses exactly one repository view;
2. the custom collector adds validator items to pytest's ordinary test items;
3. one pytest-xdist pool executes both item kinds without fail-fast;
4. performance thresholds alone may execute serially after the pooled run.

Pytest owns discovery, imports, fixtures, item scheduling, and reporting. The
repository runner owns only named-suite policy, repository-view selection,
subprocess lifecycle, and normalized timing output.

## Repository-view policy

One pytest session must never mix imports from the staged mirror and the working
tree. That creates two incompatible versions of the same Python packages under
the same module names and would require another import-isolation framework.

The canonical policy is therefore:

- `precommit` uses the staged Git mirror for validators and ordinary tests;
- manual `validators`, `tests`, `full`, `pre-push`, and `portability` runs use
  the working tree;
- CI uses its clean checked-out tree, which is already the commit under test;
- `--repository-view auto`, the default, applies the rules above;
- `--repository-view working` and `--repository-view staged` are explicit
  overrides for diagnosis and reproducible local checks.

The selected view controls collection, imports, fixtures, validator inputs, and
ordinary test execution together. Untracked or unstaged files are intentionally
absent from a staged precommit run.

## Preserved behavior

The rewrite preserves:

- the public `repo_checks.py` entry point and existing named suites;
- exact test and validator membership for every suite;
- explicit validator selection overriding tier exclusions;
- one internally consistent staged or working repository view per invocation;
- the hidden tracked-mirror child interface used by `_validator_snapshot`;
- complete execution of all selected pooled items after any individual failure;
- serial, uncontended performance thresholds;
- platform skips and the seven exact portability nodes;
- browser serialization;
- `--jobs` validation and the two-thirds-of-logical-CPUs default;
- timing schema version 1, including task IDs `validators`, `tests:shared`, and
  `tests:performance`, partial reports, wall time, pytest time, and per-file
  outcomes;
- benchmark cold/warm definitions, selected-checkout execution, repository
  fingerprint checks, and artifact schemas.

## Architecture

### Suite policy

`repo_checks.py` resolves each named suite to an item selection drawn from three
stable benchmark categories:

```text
validators + tests:shared -> tests:performance
```

There is no generic task graph, task class, slot cost, first-fit admission,
polling loop, runner-level pool, or validator gate. When a suite selects both
validators and ordinary tests, both are collected into one pytest session and
one xdist queue. A failure does not add `-x`, cancel queued work, or prevent the
remaining pooled items from running. `pytest-xdist` is the only worker
scheduler.

The phase meanings are:

- `validators`: enable the custom validator collector and select no ordinary
  test items when requested alone.
- `tests:shared`: use pytest's default collection and enable the validator
  collector in combined suites. Parallel runs receive `-n JOBS`;
  browser-containing suites use `loadgroup`, and browser-free suites use
  `worksteal`.
- `tests:performance`: run the configured performance nodes without xdist.

The `tests` and `full` suites retain isolated performance thresholds, but the
performance invocation still runs after pooled failures so the runner is not
fail-fast. Precommit and pre-push have one pooled invocation.

### Combined collection

The existing validator collector continues to create subclasses of standard
pytest modules and functions. It does not reimplement ordinary discovery.
`pytest.ini` includes the validator root, the custom collector claims canonical
validator files only when enabled, and pytest's default collector handles the
normal `test_*` prefixes. The resulting items are handed to the same xdist
scheduler.

### Discovery

Execution discovery comes only from pytest configuration. The canonical roots
remain:

```ini
testpaths = tests hooks/tests skills src/officina/wakeup/tests
```

The initialize-TDD exclusion narrows to its scaffold template rather than the
whole skill:

```ini
norecursedirs = skills/initialize-tdd/assets/python/tests .git __pycache__
```

This configuration must collect the same node IDs as the existing explicit
runner targets. The runner no longer enumerates test directories.

The docstring validator still needs to classify arbitrary source paths. Keep a
small path-classification helper for that purpose; it is not an execution
discovery system.

### Runtime imports

Delete `test_support.runtime_module` after migrating every reverse consumer.
Each call site is classified before conversion:

1. ordinary shared import: use a relative import from `_rtx/tests`;
2. cross-tree import: use repository-qualified `importlib.import_module()`;
3. fresh module state: use an explicit fixture, `monkeypatch`, and narrowly
   scoped reload only when import-time behavior is under test;
4. fresh interpreter state or executable behavior: use a real subprocess;
5. loader-isolation coverage: delete after the loader is gone.

Normal module caching is intentional. A test must declare and restore mutable
module state rather than receiving implicit partial freshness from a loader.
Production direct-script fallbacks remain supported. Test modules themselves
are pytest-owned and need no private loader CLI.

### Process lifecycle

The runner streams child output directly. It does not buffer and replay stdout
or stderr. A narrow process helper may retain process-group creation and
cross-platform descendant cleanup so an interrupted xdist or browser run does
not leave children behind. The helper does not schedule or coordinate phases.

Each invocation creates one temporary root with a distinct pytest cache and
JUnit path for each pytest phase. This preserves checkout cleanliness and
timing comparability without task machinery.

### Browser policy

The five Chrome-backed modules retain `xdist_group("browser")`. Full and
pre-push runs retain `loadgroup`. The invocation-local browser lock remains
during this refactor because direct parallel pytest can select another
distribution mode. Removing the lock requires a separate explicit policy that
either makes all parallel runs go through `repo_checks.py` or makes `loadgroup`
global.

### Timing

Timing remains JUnit-based. The runner records a small phase result containing
the stable task ID, exit status, wall time, JUnit path, and parsed file records.
It writes schema version 1 after success or failure, including only phases that
started. No `CheckTask` object is involved.

### Benchmark selection

The benchmark harness must not import another checkout's private runner module.
It invokes the selected checkout's `repo_checks.py` through a constrained hidden
task selector accepting only the stable task IDs valid for the chosen suite:

- `validators`;
- `tests:shared`;
- `tests:performance`.

The selector is an internal benchmarking interface, not a second public suite
model. A companion hidden `--task-cache-dir` is valid only with the selector
and overrides that phase's ordinary temporary cache. The harness supplies this
path, retains state fingerprints, and preserves its current artifact schema.
Ordinary runner cache isolation and benchmark warm/cold cache control remain
separate concerns.

## Removed mechanisms

The completed rewrite removes:

- `CheckTask` and slot accounting;
- `_execution_groups`, `_build_check_tasks`, and `_run_check_tasks`;
- runner-level pooling, admission, polling, and output replay;
- the duplicate pooled and sequential implementations;
- outer `--internal-run-validators`;
- dynamic test-directory execution discovery;
- `test_support.runtime_module` and its CLI;
- benchmark loading of `_build_check_tasks` from another checkout;
- tests and documentation that describe removed scheduler or loader behavior.

The hidden tracked-root validator-child arguments are not removed.

## Compatibility migration

CI currently passes `--sequential` as a cross-platform rollback control. During
the migration, the option becomes a deprecated no-op alias because the new
default is itself the simple phased path. Update CI to the default command only
after Linux, macOS, and Windows verify subprocess interruption and descendant
cleanup. Remove the alias after that matrix is green.

No compatibility alias is added for loader internals. Loader consumers migrate
before the helper is deleted.

## Error handling

- Validator findings retain status 1; validator infrastructure failures retain
  status 2.
- Pytest phase statuses pass through unchanged.
- Validator and functional failures are aggregated by the same pytest run.
- Pooled failures do not prevent selected performance thresholds from running.
- Timing output is still written for every completed phase.
- Invalid suite/task combinations fail before execution.
- Ctrl-C terminates the active process tree and returns status 130.

## Verification gates

The rewrite is complete only after all of the following hold:

1. New and old node-ID sets are exactly equal for validators, tests,
   precommit, pre-push, portability, and full; total count alone is insufficient.
2. Every migrated loader consumer passes focused tests, including repeated-call
   and mutable-global cases.
3. Staged validator and working-tree test integration remains exact.
4. Timing golden tests cover success, validator failure, functional failure,
   partial output, and all three stable task IDs.
5. Benchmark tests cover selected-checkout execution, task selection, cache
   injection, warm/cold behavior, fingerprints, and schema compatibility.
6. SIGINT cleanup covers xdist and browser descendants.
7. Focused one-core and eight-core runs pass.
8. Linux, macOS, and Windows CI pass before the `--sequential` alias is removed.
9. Matched warm measurements compare the old and new precommit and full suites;
   performance claims require comparable green observations.

## Non-goals

- Creating a second validator worker pool or a validator-first barrier.
- Introducing adaptive scheduling or a pytest distribution plugin.
- Moving skill runtime code into a new package hierarchy.
- Splitting test files solely to create scheduling units.
- Changing assertions, skips, platform support, validator findings, or
  performance thresholds.
- Committing or pushing the dirty working tree as part of this implementation.

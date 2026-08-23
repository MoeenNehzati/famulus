# Repository Testing

This is the canonical maintainer guide to the repository's Python tests,
validators, local hook, CI jobs, and benchmark interfaces.

For pipeline architecture, exact-SHA debugging, platform-specific pitfalls,
historical failure lessons, and performance baselines, see the
[Continuous Integration Handbook](./ci-handbook.md).

## Commands

Run the staged local gate used by the pre-commit hook:

```bash
python3 repo_checks.py --suite precommit
```

Run every validator and functional test, with Chrome-backed tests and
performance thresholds isolated from the pooled phase:

```bash
python3 repo_checks.py --suite full --verbose
```

Other public suites are:

```bash
python3 repo_checks.py --suite validators
python3 repo_checks.py --suite tests
python3 repo_checks.py --suite pre-push
python3 repo_checks.py --suite portability
```

Use `--jobs N` to choose the pytest-xdist worker count. The default is two
thirds of the machine's logical CPUs, with a minimum of one. Requests above one
require `pytest-xdist`.

## Collection

`repo_checks.py` is the only repository-check entry point.
`src/officina/repository/checks/runner.py` owns suite policy, repository views, pytest
arguments, and validator integration. `pytest.ini` owns ordinary discovery:

- roots: `tests/`, `hooks/tests/`, `skills/`, `src/officina/wakeup/tests/`, and
  `validators/`;
- file names: `test_*.py` and `validate_*.py`;
- import mode: pytest `importlib` mode;
- excluded template: `skills/initialize-tdd/assets/python/tests/`.

The custom plugin turns repository validators into ordinary pytest function
items. Pytest's default collector contributes the functional items. When a
suite includes both, validator and functional items enter the same xdist queue
and consume one worker budget. The runner does not maintain a second inventory
of test directories.

## Suites

| Suite | Repository view by default | Contents |
| --- | --- | --- |
| `validators` | working | All selected repository validators. |
| `tests` | working | Full functional selection, then performance thresholds serially. |
| `precommit` | staged | Validators and the fast functional selection in one pytest invocation. |
| `pre-push` | working | Validators and functional tests except docstring and performance tests. |
| `portability` | working | Seven cross-platform boundary sentinels. |
| `full` | working | Performance thresholds serially, then validators and browser-free functional tests together, then Chrome-backed tests serially. |

The precommit selection excludes installation tests, Chrome tests, docstring
tests, performance thresholds, the docstring validator, and the nested-module
inventory assertion that requires a clean committed checkout. The latter is
incompatible with a hook that necessarily runs while changes are staged.

The full suite runs `tests/test_dispatcher_performance.py` first and keeps
Chrome-backed modules in a later separate single-worker invocation. Prior
repository load invalidates the calibrated performance thresholds, while
Chrome's virtual-time completion is unreliable under pooled repository load.

No suite uses global pytest fail-fast. A failure does not cancel already queued
items or later declared phases. The isolated browser task uses `--maxfail=1`
because later Chrome cases are not useful evidence after its first failure.

## Repository views

Every pytest session uses one internally consistent source tree:

- `precommit` uses an exact temporary mirror of the Git index;
- all other suites use the working tree by default;
- `--repository-view staged` and `--repository-view working` override the
  default;
- CI's clean checkout already represents the commit under test.

Unstaged and untracked files are absent from the staged mirror. This means a
new implementation or test must be staged before the canonical precommit
command can exercise it. Manual working-view validators may report untracked
logs, build artifacts, or scratch files.

The runner places Python bytecode and pytest caches outside the execution tree.
This permits normal bytecode reuse without modifying the staged mirror.

## Parallel execution

Pytest-xdist is the only worker pool. The repository runner does not schedule a
second layer of test processes.

- Browser-free parallel phases use `--dist worksteal`.
- Chrome-backed tests run in their own one-worker phase; no second lock or
  xdist grouping layer is used.
- A one-worker run omits xdist arguments entirely.

The hidden `--sequential` option is a deprecated compatibility alias. It does
not select a different runner implementation. CI retains it temporarily while
the simplified route is certified on Linux, macOS, and Windows.

## Selection and timing interfaces

Maintainers may repeat `--validator ID` or `--exclude-validator ID` for suites
that contain validators. The private stable phase identifiers used by the
benchmark harness are `validators`, `tests:shared`, `tests:performance`, and
`tests:browser`. CI runs the complete browser behavior suite on Ubuntu and in
a dedicated one-worker Windows shard. macOS gates validators, shared tests,
performance invariants, portability, keyring, and scheduler behavior, but not
Chrome rendering: the hosted macOS Chrome CLI renders correctly and then fails
to terminate reliably, so treating its timeout as success is not an acceptable
browser gate.

`--timing-output PATH` writes schema-version-1 JSON containing task wall time
and pytest's per-file setup, call, and teardown totals. These totals do not
include collection, controller startup, or unattributed scheduler overhead.

## Local hook

`.githooks/pre-commit` performs these operations in order:

1. reject detached `HEAD`;
2. regenerate `PROFILES.md` when configuration changed;
3. regenerate and stage maintained documentation artifacts;
4. regenerate the local README preview;
5. scan staged content with `gitleaks`;
6. run `python3 repo_checks.py --suite precommit`.

The hook may update generated files in the index. Review the staged diff after
it completes.

## CI

`.github/workflows/python-tests.yml` runs on pushes and pull requests to
`master` and `main` using Linux, macOS, and Windows. Each matrix job installs
the exact Python test environment from `requirements-ci.txt` and both
supported assistant CLIs, then runs:

1. the full repository suite on Ubuntu;
2. explicit validator, shared, and performance shards on macOS and Windows;
3. the complete browser suite in a separate Windows shard;
4. the portability sentinel on every supported OS;
5. on macOS and Windows, the native keyring smoke;
6. on macOS and Windows, the native recurring-scheduler smoke.

The native smokes use `always()` so their platform evidence is still collected
after an unrelated full-suite failure.

`requirements-ci.txt` is the reproducible GitHub Actions lock for pytest,
pytest-xdist, and every imported test/validator dependency. Update it only
after the proposed versions pass the full repository suite; runtime dependency
declarations remain governed separately by the blueprint inventory.

## Platform skips

Skips are repository-level coverage decisions. Each `pytest.skip`,
`pytest.mark.skipif`, `unittest.SkipTest`, `unittest.skip`, or `self.skipTest`
under a test root needs a nearby `famulus-skip` comment with:

- `category`: an accepted skip category;
- `reason`: why the condition is part of the supported contract;
- `alternate`: where equivalent or nearest coverage exists.

Do not skip a product failure merely because it appears on one host. Use an
explicit platform contract and preserve alternate coverage.

## Adding tests

Place repository tests under `tests/` or `hooks/tests/`, and wakeup tests under
`src/officina/wakeup/tests/`. A skill has two test locations, and which one a
test belongs in follows from what it asserts about. Runtime behavior — the
Python a machine interface executes — goes under `skills/<skill>/_rtx/tests/`,
beside the code it covers. The module's own gateway contract goes under
`skills/<skill>/tests/`: instruction wording the skill promises, routing
between its interfaces, and the shape of its declared exports. Both are
collected, because `pytest.ini` lists bare `skills` in `testpaths`. Update `pytest.ini` only when a discovery boundary
changes. Update `src/officina/repository/checks/runner.py` only when suite policy
changes, and update this guide whenever either contract changes.

Prefer normal pytest fixtures at the narrowest correct scope for immutable or
resettable preparation. Keep real subprocess, filesystem, browser, and platform
boundaries when they are the behavior under test. A faster test is not an
improvement if it weakens the assertion or changes isolation semantics.

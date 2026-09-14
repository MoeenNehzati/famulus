# Repository Testing

This is the canonical maintainer guide to the repository's tests, validators,
suites, and benchmark interfaces.

For pipeline architecture, exact-SHA debugging, platform-specific pitfalls,
historical failure lessons, and performance baselines, see the
[Continuous Integration Handbook](./ci-handbook.md).

Binding test-code requirements, smells, and remedies live in the
[Code Test Design and Performance Standard](../references/node-standards/code-testing.standard.yaml).
For performance work, use the
[code-test optimization playbook](./contributors/optimizing-code-tests.md).
Historical campaign measurements and rejected approaches are preserved in the
[2026-08 cleanup retrospective](./history/2026-08-code-test-performance-cleanup.md).

## Tests and validators

Tests and validators are both evidence about nodes, but they answer different
questions. Any node may be covered by either or both. A test or validator's
location follows its ownership scope, not the node type.

- A **test** runs a scenario and asserts an observable result. Tests cover
  behavior, integrations, and the behavior of validators themselves.
- A **validator** deterministically checks a repository or node artifact for
  conformance with a rule and reports each finding. The rule belongs to its
  standard or contract; the validator enforces that rule but does not define it.

Validators also need focused tests. For example,
`validators/portable_dates.py` is a validator, while
`tests/validate_portable_dates.py` tests its accepted and rejected cases. The
`validate_*.py` test filename does not make a file a repository validator.

The current repository runner discovers validator implementations from two
central locations:

| Location | Scope | Canonical ID |
| --- | --- | --- |
| `validators/<name>.py` | Repository-wide conformance | `repo/<name>` |
| `validators/skill/<name>.py` | Shared skill-system conformance | `skill-maker/<name>` |

Tests live with the narrowest scope that owns the scenario:

| Location | Scope |
| --- | --- |
| `tests/` | Repository-wide, integration, and validator tests |
| `hooks/tests/` | Git and assistant lifecycle hooks |
| `skills/<skill>/tests/` | A skill's instruction gateway and declared interface contract |
| `skills/<skill>/_rtx/tests/` | A skill's private runtime behavior |
| `src/officina/rutter/tests/` | Officina Rutter behavior |

These paths describe current collection. They are not a claim that tests or
validators belong only to particular node types.

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

## Collection and execution

`repo_checks.py` is the only repository-check entry point.

Certification may pass `--validation-scope-file FILE`, containing JSON
`{"paths": [...], "node_ids": [...]}`. Paths identify repository-relative
whole-file subjects; IDs identify selected DFS nodes separately from the full
graph used as context. Validators select relevant subjects before scanning.
Owner context alone does not select sibling sources or module documents, and
aggregate documentation checks apply only to selected artifacts. Omission
keeps ordinary full validation; two empty lists select no conformance subjects.
`--validator-group graph|local` selects validators by their existing
`REQUIRES_BLUEPRINT_GRAPH` declaration. Omission runs both groups. Certification
shares the graph group across renewal nodes and runs the local group when each
stale node reaches its audit turn. Local checks skip graph preflight; mixed
graph/file validators remain entirely in the graph group.
These options do not change test selection or the default commit/CI gates. See
[certification scope](officina/certification_and_drift.md) for how subjects are
derived from nodes and prerequisites.

`src/officina/repository/checks/runner.py` owns suite policy, repository views, pytest
arguments, and validator integration. `pytest.ini` owns ordinary discovery:

- roots: `tests/`, `hooks/tests/`, `skills/`, `src/officina/rutter/tests/`, and
  `validators/`;
- file names: `test_*.py` and `validate_*.py`;
- import mode: pytest `importlib` mode;
- excluded template: `skills/initialize-tdd/assets/python/tests/`.

The custom plugin adapts repository validators into pytest function items for
scheduling, reporting, and fixture injection. This does not make them tests.
Native validator functions may request `graph` with `REQUIRES_BLUEPRINT_GRAPH`;
only validators using the legacy singleton entry point need `validate_with_graph`.
Pytest's default collector contributes the test items. When a suite includes
both, validator and test items enter the same xdist queue and consume one
worker budget. The runner does not maintain a second inventory of test
directories.

## Suites

| Suite | Repository view by default | Contents |
| --- | --- | --- |
| `validators` | working | Repository validators except docstrings, unless explicitly selected. |
| `tests` | working | Browser-free functional tests, performance thresholds serially, then Chrome-backed tests serially. |
| `precommit` | staged | Validators and the fast functional selection in one pytest invocation. |
| `pre-push` | working | Validators and functional tests except docstring and performance tests. |
| `portability` | working | Seven cross-platform boundary sentinels. |
| `full` | working | Performance thresholds serially, then validators and browser-free functional tests together, then Chrome-backed tests serially. |

The precommit selection excludes Chrome tests, docstring tests, performance
thresholds, the docstring validator, and reviewed expensive integration tests
that do not belong in the fast staged gate.

Run the docstring validator explicitly with
`repo_checks.py --suite validators --validator repo/docstrings`.
The `full` suite still includes it.

The full suite runs `tests/test_dispatcher_performance.py` first and keeps
Chrome-backed modules in a later separate single-worker invocation. Prior
repository load invalidates the calibrated performance thresholds, while
Chrome's virtual-time completion is unreliable under pooled repository load.

No suite uses global pytest fail-fast. A failure does not cancel already queued
items or later declared phases. The isolated browser task uses `--maxfail=1`
because later Chrome cases are not useful evidence after its first failure.

## Repository views

Every run containing validators uses one internally consistent source tree for
its validator and test items:

- `precommit` uses an exact temporary mirror of the Git index;
- other validator-bearing suites use the working tree by default;
- `--repository-view staged` and `--repository-view working` override that
  default for runs containing validators;
- test-only suites and tasks always execute from the working tree;
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

The pre-commit hook runs the staged `precommit` suite after its generation and
secret-scanning steps. The pre-push hook runs the working-tree `pre-push`
suite. See [Repository Git Hooks](./contributors/git-hooks.md) for activation,
exact ordering, generated-file side effects, targeted validator commands, and
failure handling.

## CI

`.github/workflows/python-tests.yml` runs on pushes and pull requests to
`master` and `main` using Linux, macOS, and Windows. Each matrix job installs
the Python test environment from `requirements-ci.txt`, then runs:

1. the full repository suite on Ubuntu;
2. explicit validator, shared, and performance shards on macOS and Windows;
3. the complete browser suite in a separate Windows shard;
4. the portability sentinel after a failed combined or shared check on every supported OS;
5. on macOS and Windows, the native keyring smoke;
6. on macOS and Windows, the native recurring-scheduler smoke.

The native smokes use `always()` so their platform evidence is still collected
after an unrelated full-suite failure.

`requirements-ci.txt` is the GitHub Actions dependency manifest for pytest,
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

Place repository-wide tests under `tests/`, hook tests under `hooks/tests/`, and
Rutter tests under `src/officina/rutter/tests/`. A skill has two test locations,
and which one a test belongs in follows from what it asserts about. Runtime
behavior—the Python code executed by a machine interface—goes under
`skills/<skill>/_rtx/tests/`, beside the code it covers. The module's own
gateway contract goes under `skills/<skill>/tests/`: instruction wording the
skill promises, routing between its interfaces, and the shape of its declared
exports. Both are collected, because `pytest.ini` lists bare `skills` in
`testpaths`. Update `pytest.ini` only when a discovery boundary changes. Update
`src/officina/repository/checks/runner.py` only when suite policy changes, and
update this guide whenever either contract changes.

Prefer normal pytest fixtures at the narrowest correct scope for immutable or
resettable preparation. Keep real subprocess, filesystem, browser, and platform
boundaries when they are the behavior under test. A faster test is not an
improvement if it weakens the assertion or changes isolation semantics.

### Preventing setup inflation

For every expensive test, record its entry point, initial state, action or
mutation, exact observable, retained evidence owner, and required physical
boundary. Setup should stop at the lowest stable layer owning that observable.
Share only immutable preparation, isolate mutating consumers, and remember that
pytest fixture scope is per xdist worker.

The binding details and remedies are in the
[code-testing standard](../references/node-standards/code-testing.standard.yaml).
Use the [optimization playbook](./contributors/optimizing-code-tests.md) when
auditing or reducing existing cost. Do not copy those rules into this guide.

## Adding validators

Place a repository-wide validator in `validators/` and a shared skill-system
validator in `validators/skill/`. Each exposes the validator protocol expected
by the repository runner and has focused tests of accepted and rejected cases
under `tests/`. Do not add a second test that merely invokes the validator and
expects an empty finding list: the validator suite already owns that live
conformance result.

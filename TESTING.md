# Testing, Hooks, and CI

This file is the canonical maintainer reference for Python test suites, the local pre-commit hook, and the GitHub Actions test workflow.

## Canonical Commands

Run the named local pre-commit suite:

```bash
python3 repo_checks.py --suite precommit
```

Run the full Python suite (validators + tests), including installation tests:

```bash
python3 repo_checks.py --suite full --verbose
```

Run the fast cross-platform boundary sentinel:

```bash
python3 repo_checks.py --suite portability --verbose
```

Run validators directly:

```bash
python3 repo_checks.py --suite validators
```

Regenerate generated documentation surfaces:

```bash
python3 scripts/generate-doc-artifacts.py
```

## Named Python Suites

`repo_checks.py` is the single entry point and `officina.repository_checks` is
the source of truth for suite policy. Pytest's canonical `pytest.ini` owns
execution discovery through `tests/`, `hooks/tests/`, `skills/`, and
`src/officina/wakeup/tests/`. The runner does not enumerate test directories.

Pytest uses importlib collection. Every functional test selected by a named
suite is collected and executed in one `tests:shared` pytest process. Skill
runtime tests use ordinary package-relative imports; there is no synthetic
runtime loader or separate runtime-directory task.

### `precommit`

This suite runs:

- `tests/`
- `hooks/tests/`
- `src/officina/wakeup/tests/`
- skill-owned tests under `skills/`, excluding the install-assistant-tools
  test roots

It intentionally omits
`tests/test_nested_module_migration.py::TestNestedModuleMigrationContract::test_repository_inventory_matches_reviewed_v6_cutover_surface`
because that assertion requires a clean committed tree and is therefore
incompatible with a pre-commit hook that necessarily runs while changes are
staged. Run it after the commit or through the ordinary full suite in a clean
checkout.

### `full`

This suite runs everything in `precommit`, plus:

- `skills/install-assistant-tools/_rtx/tests/`

### `portability`

This is an early-failure subset of `full`, not additional coverage. It checks
native atomic writes, the Windows atomic path, separated Python process
targets, hostile Git line-ending configuration, a foreign-platform scheduler
artifact, equivalent repository roots, and isolated index stages.

`skills/initialize-tdd/assets/python/tests/` is not part of this repo's own test
suite. It is a scaffold template for new projects. The skill's own
`skills/initialize-tdd/_rtx/tests/` remains included.

## Pre-commit Hook

[`.githooks/pre-commit`](.githooks/pre-commit) currently runs, in order:

1. Refuse commits from detached `HEAD`.
2. Regenerate `PROFILES.md` if config-backed tables changed.
3. Regenerate documentation artifacts and restage the generated docs.
4. Regenerate `_build/README-preview.html`.
5. Run `gitleaks protect --staged --redact`.
6. Run `python3 repo_checks.py --suite precommit`.

One repository-view rule governs the complete pytest invocation:

- `precommit` runs validators and ordinary tests together from the exact staged
  Git mirror. Unstaged and untracked files are intentionally absent.
- Manual `validators`, `tests`, `pre-push`, `portability`, and `full` runs use
  the working tree by default. Validators can therefore report untracked build,
  log, or scratch files during a manual working-view run.
- CI uses its clean checked-out tree, which is already the commit being tested.
- `--repository-view working` or `--repository-view staged` overrides the
  suite default; `--repository-view auto` applies the policy above.

A pytest session never mixes staged-mirror imports with working-tree imports.
The custom collector contributes validator `pytest.Function` items while
pytest's default collector contributes ordinary `test_*` items. Both enter one
xdist queue and use the same `--jobs` worker budget. Validator failures do not
cancel queued tests, and the runner never adds `-x`.

Full-only performance thresholds remain a separate serial invocation so their
limits are not distorted by concurrent load. They still run after pooled
failures; this is the sole intentional exception to the one-invocation rule.
The default worker count is two-thirds of the machine's logical CPUs.

## Performance Benchmarks

Measure any command and record process-tree resource use:

```bash
scripts/benchmark-command.py --output /tmp/checks.json --log /tmp/checks.log -- \
  python3 repo_checks.py --suite precommit --jobs 8
```

Run repeated, cache-controlled measurements of the centralized precommit suite:

```bash
scripts/benchmark-precommit.py --repo . --output /tmp/precommit.json \
  --runs 3 --cache warm --jobs 8
```

`benchmark-precommit.py` calls `benchmark-command.py`; it does not duplicate
suite discovery or execution policy from `officina.repository_checks`.

Benchmark any named complete suite with the canonical harness:

```bash
scripts/benchmark-test-suite.py --repo . --suite full --output /tmp/full.json \
  --runs 3 --cache warm --jobs 8
```

To measure the browser-containing shared phase directly, supply its stable
`tests:shared` task ID. The harness asks the selected checkout's runner to
execute that phase; it does not import runner internals. Warm observations in
one benchmark invocation share one benchmark-owned pytest cache, while cold
observations receive fresh caches:

```bash
scripts/benchmark-test-suite.py --repo . --suite full --task-id tests:shared \
  --output /tmp/shared.json --runs 3 --cache warm --jobs 8
```

Omitting `--jobs` uses the live default. For the parallel full suite, an
eight-job request gives the single `tests:shared` process eight xdist workers.
Chrome-backed modules share one `xdist_group("browser")`, so one worker runs the
already-serialized browser group while the remaining workers stay eligible for
ordinary tests. Serial execution ignores xdist grouping.

## GitHub Actions

[`.github/workflows/python-tests.yml`](.github/workflows/python-tests.yml) runs on `push` and `pull_request` for `master` and `main`.

It uses one matrix job across:

- `ubuntu-latest`
- `macos-latest`
- `windows-latest`

Each job runs, in order:

1. checkout
2. Node setup
3. Python setup
4. `pip install pytest pytest-xdist pyyaml jsonschema keyring`
5. install Claude and Codex CLIs
6. `python3 repo_checks.py --suite full --verbose --sequential`
7. `python3 repo_checks.py --suite portability --verbose`
8. macOS and Windows only: `FAMULUS_REQUIRE_NATIVE_KEYRING=1 python3 -m pytest -q tests/test_officina_secret_store.py::test_default_backend_native_roundtrip_when_available`
9. macOS and Windows only: `FAMULUS_RUN_SCHEDULER_SMOKE=1 python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py`

Validators, full tests, and portability checks intentionally share the same CI worker so setup happens once per operating system.

`--sequential` is currently a deprecated no-op compatibility alias. CI retains
it until the simplified default route is certified on Linux, macOS, and
Windows; it no longer selects a second implementation.

The native keyring smoke is optional in normal local runs and strict in CI on
macOS and Windows. Without `FAMULUS_REQUIRE_NATIVE_KEYRING=1`, the default
native roundtrip test may skip when the host has no usable keyring backend;
with that variable set, backend unavailability is a failure. Shared secret
store behavior is still covered in every ordinary test run through fake
usable, null, fail, and zero-priority keyring backends.

The native recurring scheduler smoke is opt-in outside CI. In CI it uses an
`always()` step condition on macOS and Windows so launchd/Task Scheduler
results are still collected when unrelated full-suite tests fail first. It
creates one unique temporary scheduler entry, triggers it through the native
scheduler, waits for a marker file, and then removes only that entry. Normal
local test runs skip it unless
`FAMULUS_RUN_SCHEDULER_SMOKE=1` is set.

## Skip Hygiene

Skips are repo-level coverage decisions. New test skips must be visible to
`validators/skip_hygiene.py`, which is run by `repo_checks.py` before
the Python suite in both hooks and CI.

Every `pytest.skip`, `pytest.mark.skipif`, `unittest.SkipTest`, `unittest.skip`
or `self.skipTest` in the repo's test tree must have a nearby comment:

```python
# famulus-skip: category=platform-contract; reason=Windows uses registry env vars; alternate=test_windows_registry_env
@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses registry")
def test_shell_rc_env_var():
    ...
```

The required fields are:

- `category`: one of `capability-unavailable`, `empty-contract`,
  `live-smoke-opt-in`, `native-backend-unavailable`, `platform-contract`, or
  `unsupported-platform`
- `reason`: what condition makes this skip correct
- `alternate`: where equivalent or nearest practical coverage exists

Use skip markers only when they describe the desired platform contract. Do not
skip a failing test merely because a host exposes a product bug. If a platform
uses a different supported mechanism, add or point to the alternate test for
that mechanism.

## Adding or Moving Tests

When you add, remove, or rename a repo-owned Python test directory:

1. Keep it under the canonical boundary for its owner: repo tests under
   `tests/` or `hooks/tests/`, wakeup module tests under
   `src/officina/wakeup/tests/`, and skill runtime tests under
   `skills/<skill>/_rtx/tests/`.
2. Update `pytest.ini` when a canonical discovery boundary changes, and update
   `officina.repository_checks` only when suite exclusion policy changes.
3. Update this file if the suite boundaries changed.

Pytest discovers concrete files inside those boundaries; the runner does not
maintain a second directory inventory.

## Known hazards

- (Resolved 2026-07-05) `test_codex_install.py` used to `pip install -e`
  `script_dispatcher` from a temp dir into the live Python environment,
  breaking `dispatcher` after cleanup. The installer no longer pip-installs
  first-party code: `dispatcher` is a generated launcher in the managed bin
  dir that runs from the repo (`$AI`), so test installs can no longer
  clobber it. A stale pip copy in an env can shadow nothing (the bin dir
  precedes it on PATH) and may be `pip uninstall`ed.
- (Resolved 2026-07-06) install/uninstall tests used to run against the REAL
  repo root, repeatedly deleting or overwriting live recurring-tasks runtime
  artifacts.
  Now: `test_uninstall.py` builds a fake repo and passes `--repo-root`;
  `setup_tools.run()` takes a `repo_root` parameter that in-process tests
  MUST pass (see its docstring). A regression test asserts the real generated
  agent environment file survives an uninstall run.
- Some list-manager/daily-plan integration paths touch real cloud lists if
  run without sandboxing; a stray "Test: valid entry with deadline" entry
  appeared on the live todo list on 2026-07-04.

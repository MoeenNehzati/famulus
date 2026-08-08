# Testing, Hooks, and CI

This file is the canonical maintainer reference for Python test suites, the local pre-commit hook, and the GitHub Actions test workflow.

## Canonical Commands

Run the named local pre-commit suite:

```bash
python3 repo_checks.py --suite precommit
```

Run the full Python suite, including installation tests:

```bash
python3 repo_checks.py --suite tests --verbose
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
the source of truth for suite
membership. The fixed boundaries are `tests/`, `hooks/tests/`, and skill-owned
runtime test directories. The runner discovers concrete `skills/*/_rtx/tests`
directories at execution time so migrated runtime modules cannot fall out of
the suite when a skill gains or loses a code module.

### `precommit`

This suite runs:

- `tests/`
- `hooks/tests/`
- discovered `skills/*/_rtx/tests/`, excluding the install-assistant-tools
  runtime tests named below

It intentionally omits
`tests/test_nested_module_migration.py::TestNestedModuleMigrationContract::test_repository_inventory_matches_reviewed_v5_cutover_surface`
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

`skills/initialize-tdd/assets/python/tests/` is not part of this repo's own test suite. It is a scaffold template for new projects.

## Pre-commit Hook

[`.githooks/pre-commit`](.githooks/pre-commit) currently runs, in order:

1. Refuse commits from detached `HEAD`.
2. Regenerate `PROFILES.md` if config-backed tables changed.
3. Regenerate documentation artifacts and restage the generated docs.
4. Regenerate `_build/README-preview.html`.
5. Run `gitleaks protect --staged --redact`.
6. Run `python3 repo_checks.py --suite precommit`.

Two execution details matter:

- `gitleaks` scans staged content.
- `officina._validator_snapshot` evaluates a git-tracked mirror, so validators see staged content without being confused by untracked scratch files.

The Python tests run from the working tree, not from a staged mirror.

The centralized check runner executes the selected validators and pytest groups.
Its `--jobs` option controls parallel pytest workers; the default is two-thirds
of the machine's logical CPUs.

## Performance Benchmarks

Measure any command and record process-tree resource use:

```bash
scripts/benchmark-command.py --output /tmp/checks.json -- \
  python3 repo_checks.py --suite precommit --jobs 8
```

Run repeated, cache-controlled measurements of the centralized precommit suite:

```bash
scripts/benchmark-precommit.py --repo . --output /tmp/precommit.json \
  --runs 3 --cache warm --jobs 8
```

`benchmark-precommit.py` calls `benchmark-command.py`; it does not duplicate
suite discovery or execution policy from `officina.repository_checks`.

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
4. `pip install pytest pyyaml jsonschema keyring`
5. install Claude and Codex CLIs
6. `python3 repo_checks.py --suite validators`
7. `python3 repo_checks.py --suite portability --verbose`
8. `python3 repo_checks.py --suite tests --verbose`
9. macOS and Windows only: `FAMULUS_REQUIRE_NATIVE_KEYRING=1 python3 -m pytest -q tests/test_officina_secret_store.py::test_default_backend_native_roundtrip_when_available`
10. macOS and Windows only: `FAMULUS_RUN_SCHEDULER_SMOKE=1 python3 -m pytest -q skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py`

Validators and tests intentionally share the same CI worker so setup happens once per operating system.

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
   `tests/` or `hooks/tests/`, and skill runtime tests under
   `skills/<skill>/_rtx/tests/`.
2. Update `officina.repository_checks` only if the boundary or exclusion
   policy changes.
3. Update this file if the suite boundaries changed.

The runner may discover concrete directories inside those boundaries, but the
boundaries themselves must remain explicit in the script and in this document.

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

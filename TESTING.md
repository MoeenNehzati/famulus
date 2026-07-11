# Testing, Hooks, and CI

This file is the canonical maintainer reference for Python test suites, the local pre-commit hook, and the GitHub Actions test workflow.

## Canonical Commands

Run the named local pre-commit suite:

```bash
python3 scripts/run-python-tests.py --suite precommit
```

Run the full Python suite, including installation tests:

```bash
python3 scripts/run-python-tests.py --suite full --verbose
```

Run validators directly:

```bash
python3 validators/runner.py
```

Regenerate generated documentation surfaces:

```bash
python3 scripts/generate-doc-artifacts.py
```

## Named Python Suites

`scripts/run-python-tests.py` is the single source of truth for suite membership.

### `precommit`

This suite runs:

- `tests/`
- `hooks/tests/`
- `skills/cloud-files/tests/`
- `skills/daily-plan/tests/`
- `skills/email-client/tests/`
- `skills/email-triage/tests/`
- `skills/find-handoff-candidates/tests/`
- `skills/g-calendar/tests/`
- `skills/list-manager/tests/`
- `skills/math-dependency-graph/tests/`
- `skills/recurring-tasks/tests/`
- `skills/skill-maker/tests/`

### `full`

This suite runs everything in `precommit`, plus:

- `skills/install-assistant-tools/tests/`

`skills/initialize-tdd/assets/python/tests/` is not part of this repo's own test suite. It is a scaffold template for new projects.

## Pre-commit Hook

[`.githooks/pre-commit`](.githooks/pre-commit) currently runs, in order:

1. Refuse commits from detached `HEAD`.
2. Regenerate `PROFILES.md` if config-backed tables changed.
3. Regenerate documentation artifacts and restage the generated docs.
4. Regenerate `_build/README-preview.html`.
5. Run `gitleaks protect --staged --redact`.
6. Run `python3 validators/runner.py`.
7. Run `python3 scripts/run-python-tests.py --suite precommit`.

Two execution details matter:

- `gitleaks` scans staged content.
- `validators/runner.py` evaluates a git-tracked mirror, so validators see staged content without being confused by untracked scratch files.

The Python tests run from the working tree, not from a staged mirror.

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
4. `pip install pytest pyyaml jsonschema`
5. install Claude and Codex CLIs
6. `python3 validators/runner.py`
7. `python3 scripts/run-python-tests.py --suite full --verbose`

Validators and tests intentionally share the same CI worker so setup happens once per operating system.

## Adding or Moving Tests

When you add, remove, or rename a repo-owned Python test directory:

1. Update the explicit suite membership in `scripts/run-python-tests.py`.
2. Decide whether the directory belongs in `precommit`, `full`, or both.
3. Update this file if the suite boundaries changed.

Do not rely on implicit glob expansion for the suite contract. The point of the runner script is to make the operational boundary explicit.

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

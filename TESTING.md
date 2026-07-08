# Testing

## Canonical commands

Run everything (from repo root):

```
python3 -m pytest
```

Collection is configured in `pytest.ini`:
- `testpaths`: `tests/`, `hooks/tests/`, `skills/*/tests/`
- `python_files = test_*.py validate_*.py` — the `tests/validate_*.py` smoke
  tests for repo validators and `skills/skill-maker/validators/` are collected too.
- `skills/initialize-tdd/` is excluded: its `assets/python/tests/` is a
  scaffold template for new projects, not this repo's tests.

Run a single suite:

```
python3 -m pytest skills/<name>/tests
python3 -m pytest hooks/tests
python3 -m pytest tests            # validator smoke tests
```

## Tiers

- **Unit/suite tests** (everything above): fast, sandboxed via tmp dirs.
- **Pre-commit validators**: run by `.githooks/pre-commit` via
  `validators/runner.py` — not pytest; the pytest files in `tests/` only
  smoke-test the validator modules. The same hook also regenerates the
  generated documentation artifacts, `_build/README-preview.html`, and
  `PROFILES.md` when needed.
- **Browser smoke test**: `skills/math-dependency-graph/tests` launches
  headless Chrome; slowest part of the suite.

## Known hazards

- (Resolved 2026-07-05) `test_codex_install.py` used to `pip install -e`
  `script_dispatcher` from a temp dir into the live Python environment,
  breaking `dispatcher` after cleanup. The installer no longer pip-installs
  first-party code: `dispatcher` is a generated launcher in the managed bin
  dir that runs from the repo (`$AI`), so test installs can no longer
  clobber it. A stale pip copy in an env can shadow nothing (the bin dir
  precedes it on PATH) and may be `pip uninstall`ed.
- (Resolved 2026-07-06) install/uninstall tests used to run against the REAL
  repo root, repeatedly deleting or overwriting the live generated
  `skills/recurring-tasks/scripts/env.sh` (breaking all recurring jobs).
  Now: `test_uninstall.py` builds a fake repo and passes `--repo-root`;
  `setup_tools.run()` takes a `repo_root` parameter that in-process tests
  MUST pass (see its docstring). A regression test asserts the real env.sh
  survives an uninstall run.
- Some list-manager/daily-plan integration paths touch real cloud lists if
  run without sandboxing; a stray "Test: valid entry with deadline" entry
  appeared on the live todo list on 2026-07-04.

## Documentation System

- `python3 scripts/generate-doc-artifacts.py` regenerates `docs/skills.md`
  plus the embedded coverage blocks in the user and contributor docs.
- Documentation conformance now lives in `validators/`, not in a prose-shape
  README unit test.

# Testing

## Canonical commands

Run everything (from repo root):

```
python3 -m pytest
```

Collection is configured in `pytest.ini`:
- `testpaths`: `tests/`, `hooks/tests/`, `skills/*/tests/`
- `python_files = test_*.py validate_*.py` — the `tests/validate_*.py` smoke
  tests for `skills/my-writing-skills/validators/` are collected too.
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
  smoke-test the validator modules.
- **Browser smoke test**: `skills/math-dependency-graph/tests` launches
  headless Chrome; slowest part of the suite.

## Known hazards

- `skills/install-assistant-tools/tests/test_codex_install.py` performs a
  real `pip install -e` of `script_dispatcher` from a temp dir into the
  active Python environment. After the temp dir is cleaned up, the installed
  `dispatcher` breaks (`ModuleNotFoundError`). Repair:

  ```
  python3 -m pip install -e /path/to/repo/script_dispatcher
  ```

  TODO: isolate this test (venv or mock pip) so it can't clobber the live
  environment.
- Some list-manager/daily-plan integration paths touch real cloud lists if
  run without sandboxing; a stray "Test: valid entry with deadline" entry
  appeared on the live todo list on 2026-07-04.

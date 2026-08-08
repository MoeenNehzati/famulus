# Repository Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace separate test and validator runners with one `repo_checks.py` command and bind all repository gates to it.

**Architecture:** `repo_checks.py` is the sole executable surface. A non-executable `officina.repository_checks` module owns suite discovery, staged validator isolation, pytest collection, and execution.

**Tech Stack:** Python 3, pytest, Git hooks, GitHub Actions

## Global Constraints

- Preserve staged-index semantics for validators.
- Preserve working-tree semantics and existing membership for ordinary tests.
- Do not leave compatibility executable wrappers.
- Do not touch unrelated dirty work.

---

### Task 1: Specify the single execution authority

**Files:**
- Modify: `tests/test_repo_tests_entrypoint.py`
- Modify: `tests/test_repository_test_checks.py`

**Interfaces:**
- Consumes: root CLI suite names and hook files.
- Produces: failing assertions for `repo_checks.py`, deleted legacy runners, and direct hook bindings.

- [ ] Rename the entry-point integration tests and assert all suites through `repo_checks.py`.
- [ ] Assert `repo_tests.py`, `validators/runner.py`, and `scripts/run-python-tests.py` do not exist.
- [ ] Assert pre-commit, pre-push, CI, and skill hooks call only `repo_checks.py`.
- [ ] Run the focused tests and verify they fail because the migration is absent.

### Task 2: Consolidate implementation

**Files:**
- Create: `repo_checks.py`
- Create: `src/officina/repository_checks.py`
- Delete: `repo_tests.py`
- Delete: `validators/runner.py`
- Delete: `scripts/run-python-tests.py`
- Modify: `tests/test_repository_validator_checks.py`
- Modify: `tests/test_repository_test_checks.py`

**Interfaces:**
- Consumes: `run_suite(repo_root, suite, verbose=False, validator_ids=()) -> int`.
- Produces: `repo_checks.py --suite NAME [--validator ID] [--verbose]`.

- [ ] Move suite discovery and staged validator execution into `officina.repository_checks`.
- [ ] Make the root command call that module directly for parent and staged-child operation.
- [ ] Repoint unit tests from legacy modules to the consolidated internal API.
- [ ] Run the focused tests and verify they pass.

### Task 3: Bind repository gates and documentation

**Files:**
- Modify: `.githooks/pre-commit`
- Create: `.githooks/pre-push`
- Modify: `.githooks/skill/check-blueprints`
- Modify: `.githooks/skill/check-dependencies`
- Modify: `.githooks/skill/check-names`
- Modify: `.githooks/skill/check-runtime-files`
- Modify: `.github/workflows/python-tests.yml`
- Modify: `README.md`
- Modify: `TESTING.md`
- Modify: `docs/contributors/documentation-system.md`

**Interfaces:**
- Consumes: named suites and repeatable `--validator` selector.
- Produces: direct bindings with no legacy runner reference.

- [ ] Bind pre-commit to `--suite precommit` and pre-push to `--suite pre-push`.
- [ ] Bind skill hooks to `--suite validators --validator ID`.
- [ ] Update CI and active contributor documentation.
- [ ] Run hook-contract and documentation tests.

### Task 4: Verify the migration

**Files:**
- Test: `tests/test_repo_checks_entrypoint.py`
- Test: `tests/test_repository_validator_checks.py`
- Test: `tests/test_repository_test_checks.py`

**Interfaces:**
- Consumes: complete consolidated command.
- Produces: fresh evidence for correctness and legacy-path removal.

- [ ] Run the focused regression suite.
- [ ] Run `git diff --check` and a zero-reference scan over active code and bindings.
- [ ] Run the staged validator suite in an isolated repository view.

# Portability Boundary Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate repository portability boundaries and enforce them through
the existing validator, test, CI, and certification paths.

**Architecture:** The existing validator runner becomes a tracked-index
two-phase executor. Existing repository-path and Python-runtime owners absorb
their duplicate semantics. Existing test, standards, cross-platform validator,
and certification owners are extended rather than paralleled.

**Tech Stack:** Python 3.11, pathlib, Git plumbing, pytest, PyYAML, JSON Schema,
GitHub Actions.

## Global constraints

- Work on the existing `master` checkout, as previously authorized.
- Do not touch `docs/plans/nested-module-behavior.md`.
- Use TDD for every production behavior change.
- Preserve provider-neutral blueprint schemas.
- Do not add a portability validator, policy file, standard family, or Python
  target module.
- Do not commit or push without separate authorization.

---

### Task 1: Execute validators from staged index bytes

**Files:**

- Modify: `validators/runner.py`
- Modify: `validators/skill_runtime_files.py`
- Modify: `skills/skill-maker/validators/blueprints.py`
- Modify: `.githooks/skill/check-blueprints`
- Modify: `.githooks/skill/check-dependencies`
- Modify: `.githooks/skill/check-names`
- Modify: `.githooks/skill/check-runtime-files`
- Test: `tests/test_validator_runner.py`
- Test: `tests/validate_skill_runtime_files.py`
- Test: `tests/validate_blueprints.py`

**Interfaces:**

- Produces:
  `run_all(repo_root: Path = REPO_ROOT, validator_ids: Sequence[str] | None = None) -> dict[str, list[str]]`
- Produces: canonical IDs `repo/<stem>` and `skill-maker/<stem>`
- Produces: `ValidatorRunnerError(RuntimeError)`

- [ ] Add failing tests proving that staged bytes win over worktree bytes, an
  untracked validator and live `docs_tooling` module do not execute, canonical
  selection works, and materialization failures do not fall back.

- [ ] Add failing mode tests for `100644`, `100755`, `120000`, and nonzero
  stages. Assert that symlinks and conflict-only paths have no mirror worktree
  entry while the isolated index retains their records.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_validator_runner.py
  ```

  Expected: the new tests fail against worktree-copy and live-import behavior.

- [ ] Refactor `runner.py` into bootstrap and tracked-child phases. Materialize
  regular stage-0 blobs from Git object IDs, launch the mirror's runner in a
  fresh interpreter, and exchange the result as JSON. Reject every setup,
  selection, load, and validator-execution error with
  `ValidatorRunnerError`.

- [ ] Make validator discovery return canonical IDs, implement repeatable
  `--validator`, sort execution, and preserve validator findings as result
  values.

- [ ] Remove ordinary validators' `git ls-files` calls. Move `_cx` executable
  mode checking from `skill_runtime_files.py` into the index-aware blueprint
  validator.

- [ ] Change all direct skill hooks to invoke selected runner IDs.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_validator_runner.py tests/validate_skill_runtime_files.py tests/validate_blueprints.py
  ```

  Expected: pass.

---

### Task 2: Extract repository-path conversion

**Files:**

- Create: `src/officina/common/repository_paths.py`
- Create: `src/officina/common/blueprints/repository-paths.yaml`
- Modify: `src/officina/common/blueprint.yaml`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/git_provenance.py`
- Modify: `src/officina/common/certification_view.py`
- Modify: `src/officina/common/certification_hashing.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/runtime/python_machine_interface_runner.py`
- Modify: `docs/architecture.md`
- Test: `tests/test_officina_repository_paths.py`
- Test: existing graph, Git-provenance, certification, dispatcher, and runner
  tests

**Interfaces:**

- Produces: `RepositoryPathError(ValueError)`
- Produces:
  `equivalent_root_relative_path(path: Path, root: Path) -> Path`
- Produces:
  `repository_relative_path(path: Path, repo_root: Path) -> Path`
- Produces:
  `repository_relative_posix(path: Path, repo_root: Path) -> str`

- [ ] Write failing tests for lexical containment, relative inputs rooted at
  `repo_root`, macOS-equivalent aliases, outside paths, nonexistent
  descendants, and descendant symlinks that must not be followed.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_officina_repository_paths.py
  ```

  Expected: fail because the shared module does not exist.

- [ ] Extract the existing ancestor-`samefile` algorithm into the new module.
  Make repository wrappers root relative inputs at `repo_root` and POSIX
  serialization explicit.

- [ ] Replace graph, Git-provenance, certification-view, hashing, dispatcher,
  and runner duplicates. Preserve each caller's current public error or
  readiness result.

- [ ] Add the behavioral-source blueprint and update the common module's
  content, sources, exports, dependencies, and architecture ownership.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_officina_repository_paths.py tests/test_officina_blueprint_graph.py tests/test_officina_git_provenance.py tests/test_officina_certification_view.py tests/test_officina_certification_hashing.py tests/test_officina_dispatcher.py tests/test_officina_python_machine_interface.py
  ```

  Expected: pass.

---

### Task 3: Carry Python process targets structurally

**Files:**

- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `src/officina/runtime/python_machine_interface_runner.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: live `skills/*/blueprint.yaml`
- Regenerate: live `skills/*/.pooled-blueprint-review.yaml`
- Test: `tests/test_officina_dispatcher.py`
- Test: `tests/test_dispatcher_route_smoke.py`
- Test: `tests/test_officina_python_machine_interface.py`
- Test: `skills/skill-certifier/tests/test_certifier.py`

**Interfaces:**

- Produces: `PythonProcessTargetError(ValueError)`
- Produces:
  `PythonProcessTarget(gateway_path: Path, process_entry: str)`
- Extends: `ResolvedInvocation.python_target`
- Extends: `ResolvedInvocationMetadata.python_target`

- [ ] Write failing tests requiring `_rtx/*.py`, a Python identifier, separate
  metadata payload fields, separate runner argv tokens, structured trace
  requests/responses, and prohibition on semantic parsing of `command`.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_officina_dispatcher.py::test_python_process_target_keeps_gateway_and_entry_separate tests/test_dispatcher_route_smoke.py tests/test_officina_python_machine_interface.py
  ```

  Expected: fail because the type and separate transport do not exist.

- [ ] Add the target type to the existing Python adapter. Carry it through both
  resolved invocation types and emit
  `{"python_target": {"gateway_path": "...", "process_entry": "..."}}`.

- [ ] Replace composite trace keys, child payloads, route-smoke inputs,
  certificate evidence, runner loading, and dependency inspection. Change the
  runner CLI to two target tokens.

- [ ] Migrate every live runner permission array. Keep composite parsing only
  in the exact migration-only function allowlist.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_officina_dispatcher.py tests/test_dispatcher_route_smoke.py tests/test_officina_python_machine_interface.py skills/skill-certifier/tests/test_certifier.py
  ```

  Expected: pass.

---

### Task 4: Consolidate deterministic Git test fixtures

**Files:**

- Create: `test_support/git_repository.py`
- Modify: `tests/test_blueprint_inventory.py`
- Modify: `tests/test_interface_injection_migration.py`
- Modify: `tests/test_node_certification_hashing.py`
- Modify: `tests/test_officina_blueprint_template.py`
- Modify: `tests/test_officina_certification_view.py`
- Modify: `tests/test_officina_git_provenance.py`
- Modify: `tests/test_validator_runner.py`
- Modify: `tests/v4_certification_fixtures.py`
- Modify: `skills/skill-certifier/tests/test_certifier.py`
- Modify: `skills/install-assistant-tools/tests/install_test_utils.py`
- Modify: `skills/install-assistant-tools/tests/test_dev_link.py`
- Modify: `skills/install-assistant-tools/tests/test_e2e_lifecycle.py`
- Modify: `skills/install-assistant-tools/tests/test_install_manifest.py`
- Modify: `skills/install-assistant-tools/tests/test_uninstall.py`
- Test: `tests/test_git_test_repository.py`

**Interfaces:**

- Produces: `GitTestRepository.create(root, branch="main", filemode=True)`
- Produces:
  `GitTestRepository.git(*args, check=True, input_bytes=None) -> CompletedProcess[bytes]`

- [ ] Write failing tests for exact target creation, fixed identity and branch,
  explicit `core.autocrlf` and `core.filemode`, bytes results, sanitized Git
  execution, and no implicit add or commit.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/test_git_test_repository.py
  ```

  Expected: fail because the helper does not exist.

- [ ] Implement the helper by delegating to
  `officina.common.git_provenance.run_git`.

- [ ] Replace ordinary local helpers and repository setup. Annotate only calls
  whose subject requires raw Git using the closed categories
  `ambient-config`, `hooks`, `object-format`, `index-stages`,
  `validator-isolation`, or `run-git-contract`.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests skills/skill-certifier/tests
  ```

  Expected: pass.

---

### Task 5: Integrate enforcement, certification, standards, and CI

**Files:**

- Modify: `validators/cross_platform.py`
- Modify: `tests/validate_cross_platform.py`
- Modify: `references/skill-standards/skill-guidelines.standard.yaml`
- Regenerate: `references/skill-standards/skill-guidelines.md`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: skill-certifier blueprints and generated contract block
- Modify: `skills/skill-drift/references/certification-basis-roots.json`
- Modify: `scripts/run-python-tests.py`
- Modify: `tests/test_run_python_tests.py`
- Modify: `.github/workflows/python-tests.yml`
- Modify: `docs/testing.md`

**Interfaces:**

- Produces:
  `run_v4_mechanical_checks(repo_root: Path = REPO_ROOT) -> CommandResult`
- Preserves: certifier schema-v1 evidence as a list containing one runner
  result
- Produces: `--suite portability`

- [ ] Write failing validator tests for raw-Git annotations, composite Python
  targets in Python and live permission arrays, historical exact exclusions,
  and migration-only function exclusion.

- [ ] Write failing certifier tests proving there is no mechanical bypass,
  runner failure precedes signing, and schema-v1 evidence contains one result.

- [ ] Write failing suite tests for the exact portability node tuple and CI
  ordering.

- [ ] Extend `cross_platform.py`; do not add another validator. Update existing
  standard families: `cross-platform-tools`, `test-file-conventions`, and
  `validator-test-conventions`.

- [ ] Remove the duplicate blueprint-sync dispatch, dispatcher injection,
  `skip_mechanical`, hidden CLI flag, and corresponding certifier blueprint
  dependency and permission. Preserve the evidence-list shape.

- [ ] Add only missing basis sources:
  standard-v6 schema/validator/renderer, `repository_paths.py`, and
  `docs_tooling/**/*.py`. Add a test that validator repository imports are
  basis-covered.

- [ ] Add the explicit portability suite and CI step between validators and the
  full suite. Update `docs/testing.md`.

- [ ] Run:

  ```bash
  python3 -m pytest -q tests/validate_cross_platform.py tests/test_run_python_tests.py skills/skill-certifier/tests/test_certifier.py tests/test_officina_certification_hashing.py
  ```

  Expected: pass.

---

### Task 6: Regenerate and verify the complete repository

**Files:**

- Regenerate through existing owners: live standard Markdown, blueprint
  contract blocks, permission projections, and tracked generated docs
- After exact-source certification: ignored certificate logs and pooled reviews

- [ ] Run the standard renderer and blueprint synchronization owner; do not
  hand-edit generated artifacts.

- [ ] Run:

  ```bash
  python3 validators/runner.py
  python3 scripts/run-python-tests.py --suite portability --verbose
  python3 scripts/run-python-tests.py --suite full --verbose
  ```

  Expected: all pass locally.

- [ ] Run the native Linux, macOS, and Windows CI matrix and correct only
  evidence-backed platform failures.

- [ ] Once the exact source state is committed with user authorization,
  recertify dependency-first and verify the generated certificate logs and
  pooled reviews against that commit.

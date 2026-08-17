# Officina Source Relocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move shared Officina code into coherent top-level packages while preserving implementation behavior and eliminating old import addresses.

**Architecture:** This is a relocation-only refactor. Existing implementation files move intact; callers use concrete owning modules; imports, resource paths, tests, documentation, and ownership metadata follow the files. Each affected package `__init__.py` contains only the package summary and complete `Includes` inventory.

**Tech Stack:** Python 3.11+, pytest, YAML/JSON package data, Officina blueprint metadata.

## Execution Status

- [x] Controller, configuration, docstring, blueprints, certification, credentials, and Git sources relocated.
- [x] Standards, visualization, repository checks, and validator snapshot encoded in the reusable manifest.
- [x] Reusable engine and manifest audited in a temporary copy: 409 focused tests passed.
- [x] Initial relocation application and one audited generated-block closure application completed.
- [x] Final verification: idempotent zero-change preflight, 410 focused tests, empty retired-address search, clean diff check, and 32/32 validators in a tracked-equivalent audit copy.
- [x] Reusable within-module trial: relocated the live standards extractor and its sidecar in a disposable copy, passed 15 focused tests, and reached a zero-change second preflight.
- [x] Rename-aware docstring regression gate: preserve strict checks for new findings while preventing unchanged legacy findings from blocking mechanical relocations.
- [ ] Current-source installer bootstrap: rebuild the managed runtime without importing Officina APIs from the active release.
- [ ] Certifier runtime closure: install the repository validator dependencies required before signing.

## Global Constraints

- Do not decompose or redesign implementation bodies.
- Do not retain compatibility facades or old import aliases.
- Preserve callable names, signatures, return values, exceptions, effects, CLI behavior, and serialized formats.
- Move or retarget blueprint metadata only as required to keep file ownership and existing interfaces valid; do not introduce new authority unrelated to the move.
- Update every active Python, resource, command, test, documentation, and metadata reference to its canonical new address.
- Every affected `__init__.py` must summarize the package and list every directly owned tracked file or child package under `Includes`.
- Do not modify, stage, stash, commit, or revert unrelated dirty files.
- Do not commit without explicit user approval.

---

### Task 1: Controller package

**Files:**
- Move: `src/officina/common/controller.py` to `src/officina/controller/model.py`
- Move: `src/officina/common/controller_protocol.py` to `src/officina/controller/protocol.py`
- Create: `src/officina/controller/__init__.py`
- Modify: `tests/test_controller_protocol.py`

**Interfaces:**
- Produces: concrete `officina.controller.model` and `officina.controller.protocol` module addresses.

- [x] Move both implementation files without changing their bodies.
- [x] Update the protocol's relative import and both module-docstring addresses.
- [x] Add the README-only package initializer.
- [x] Update the focused test to import the concrete owning modules.
- [x] Run `pytest -q tests/test_controller_protocol.py` — observed `7 passed`.
- [x] Verify no active old controller address and run `git diff --check`.

### Task 2: Configuration and docstring packages

**Files:**
- Move: `src/officina/common/configured_schema.py` to `src/officina/configuration/configured_schema.py`
- Move: `src/officina/common/configuration.schema.json` to `src/officina/configuration/schema.json`
- Move: `src/officina/common/repository_configuration.py` to `src/officina/configuration/repository.py`
- Move: `src/officina/common/blueprints/repository-configuration.yaml` to `src/officina/configuration/blueprints/repository.yaml`
- Create: `src/officina/configuration/__init__.py`
- Move: `src/officina/common/docstring/{docstring_parser.py,docstring_policy.py,docstring_validation.py,config.yaml}` to `src/officina/docstring/{parser.py,policy.py,validation.py,config.yaml}`
- Create: `src/officina/docstring/__init__.py`
- Remove after callers move: `src/officina/common/docstring/docstring_schema.py`, `src/officina/common/docstring_parser.py`, `src/officina/common/docstring_schema.py`, and `src/officina/common/docstring_validation.py`.
- Modify: every configuration/docstring consumer found by the old-address search.

**Interfaces:**
- Produces: canonical concrete modules under `officina.configuration` and `officina.docstring`.

- [x] Record baseline results for the configured-schema, repository-configuration, docstring parser/schema, and docstring-validator tests — observed `140 passed`.
- [x] Move configuration files, update the schema resource filename, and rewrite relative imports.
- [x] Add the configuration package README and update every active caller.
- [x] Move docstring files, rewrite internal imports, and remove only the proven facade files.
- [x] Add the docstring package README and update visualization, validator, docs-tooling, test, and documentation callers.
- [x] Run the focused configuration/docstring tests, old-address searches, import smoke tests, package-docstring validation, and `git diff --check` — observed `140 passed`, zero package-docstring issues, and no old active address outside the planned historical mapping.

### Task 3: Blueprint, certification, credentials, and Git packages

**Files:**
- Move: `src/officina/common/{blueprint_authorization.py,blueprint_graph.py,blueprint_inventory.py,blueprint_template.py,pooled_blueprint.py,process_binding_compiler.py,interface_projection.py}` to `src/officina/blueprints/{authorization.py,graph.py,inventory.py,template.py,pooled.py,process_binding.py,projection.py}`.
- Move: `src/officina/blueprint_search.py` to `src/officina/blueprints/search.py`.
- Move: `src/officina/common/blueprints/{blueprint-graph.yaml,blueprint-inventory.yaml,blueprint-template.yaml,pooled-blueprint.yaml,process-binding-compiler.yaml}` to `src/officina/blueprints/blueprints/{graph.yaml,inventory.yaml,template.yaml,pooled.yaml,process-binding.yaml}`.
- Move: `src/officina/common/{certification_hashing.py,certification_view.py,certificate_records.py}` to `src/officina/certification/{hashing.py,view.py,records.py}` and move their three existing sidecars to `src/officina/certification/blueprints/{hashing.yaml,view.yaml,records.yaml}`.
- Move: `src/officina/common/{google_credentials.py,oauth_json.py,secret_store.py}` to `src/officina/credentials/{google.py,oauth.py,secret_store.py}` and move their three existing sidecars to `src/officina/credentials/blueprints/{google.yaml,oauth.yaml,secret-store.yaml}`.
- Move: `src/officina/common/git_provenance.py` to `src/officina/git/provenance.py` and its sidecar to `src/officina/git/blueprints/provenance.yaml`.
- Create: each domain's README-only `__init__.py`.
- Modify: `src/officina/common/blueprint.yaml` and moved sidecars only to reflect final ownership and addresses.
- Modify: dispatcher, runtime, installer, skill runtime, tests, documentation, and certification-basis references found by the caller manifest.

**Interfaces:**
- Produces: canonical packages `officina.blueprints`, `officina.certification`, `officina.credentials`, and `officina.git` with direct implementation-module imports.
- Preserves: existing source contracts and caller behavior under their relocated identities.

- [x] Record focused baseline test results for blueprint, certification, credentials, OAuth, secret-store, and Git provenance.
- [x] Move blueprint implementation files and their existing sidecars; update only imports, paths, and identity-bearing metadata.
- [x] Move certification files and sidecars; update imports and certification-basis paths without changing hash semantics beyond path identity.
- [x] Move credentials files and sidecars; preserve secret-store and OAuth behavior.
- [x] Move Git provenance and its sidecar; preserve subprocess, pinning, and tree-materialization behavior.
- [x] Add the four package READMEs and update all active callers.
- [x] Run focused tests, graph validation, old-address searches, import smoke tests, and `git diff --check`.

### Task 4: Standards and visualization packages

**Files:**
- Move: `src/officina/common/standard_extractor.py` to `src/officina/standards/extractor.py`
- Move: `src/officina/common/standard_query.py` to `src/officina/standards/query.py`
- Move: their existing sidecars to `src/officina/standards/blueprints/`
- Move: `src/officina/common/visualization/` to `src/officina/visualization/` without decomposing it.
- Create/update: README-only package initializers for `standards`, `visualization`, `from_blueprint`, `from_docstring`, and `html_renderer`.
- Modify: all active consumers and path-bearing documentation.

**Interfaces:**
- Produces: canonical concrete modules under `officina.standards` and `officina.visualization`.

- [x] Record focused standard and visualization baseline results.
- [x] Move standards sources/sidecars and rewrite query/extractor callers.
- [x] Move the visualization tree intact, preserving the user's concurrent runtime/CSS edits.
- [x] Rewrite blueprint, docstring, resource, docs-tooling, and math-dependency-graph imports.
- [x] Merge package-boundary README material into the affected `__init__.py` docstrings without deleting long-form documentation.
- [x] Run the focused standards and visualization suite, old-address searches, and `git diff --check`.

### Task 5: Repository checks and common contraction

**Files:**
- Move: `src/officina/common/discover_tests.py` to `src/officina/repository/checks/discovery.py`.
- Move: `src/officina/common/repository_checks.py` to `src/officina/repository/checks/runner.py`.
- Move: `src/officina/common/repo_checks/{remote.py,remote_macos_windows.py,__init__.py}` to `src/officina/repository/checks/{remote.py,remote_macos_windows.py,__init__.py}`.
- Move: `src/officina/_validator_snapshot.py` to `src/officina/validators/snapshot.py`.
- Create/update: `src/officina/repository/__init__.py`, `src/officina/repository/checks/__init__.py`, `src/officina/validators/__init__.py`, and `src/officina/common/__init__.py`.
- Modify: repository-root `repo_checks.py` and all active repository-check callers.
- Remove: duplicate package-overview README files after their package-boundary content is represented in `__init__.py`.

**Interfaces:**
- Produces: `officina.repository.checks.runner` with `main`; retains only true primitives in `officina.common`.

- [x] Record repository-check and validator baseline results.
- [x] Move discovery, runner, remote-check, and snapshot files and update all imports/entry points.
- [x] Add repository package READMEs and update the root bootstrap.
- [x] Contract `common/__init__.py` to retained primitives and remove moved lazy exports.
- [x] Update every retained or moved package README docstring and remove duplicate package-overview README files.
- [x] Run repository-check/validator tests, concrete-module imports, old-address searches, and `git diff --check`.

### Task 6: Repository-wide closure

**Files:**
- Modify only active references discovered by the final search; preserve explicitly historical fixtures.

**Interfaces:**
- Verifies: every canonical concrete module and every supported command/resource address.

- [x] Search all tracked text for every removed dotted address, filesystem path, module/source/interface ID, command target, and package-resource name.
- [x] Classify any remaining occurrence as active, generated, stale documentation, or an exact historical fixture.
- [x] Run canonical concrete-module import smoke tests and package README docstring validation.
- [x] Run focused domain suites followed by the repository-supported broader check.
- [x] Run `git diff --check`, inspect the exact scoped diff, and report unrelated dirty state separately.
- [x] Present the completed relocation for user review and explicit commit authorization.

### Task 7: Rename-aware staged docstring regressions

**Files:**
- Modify: `src/officina/validators/snapshot.py`
- Modify: `validators/docstrings.py`
- Modify: `tests/test_repository_validator_checks.py`
- Modify: `tests/test_docstrings_validator.py`

**Interfaces:**
- Produces: an immutable `.git/officina-validator-baseline/` tree inside each staged validator mirror, keyed by the staged destination path and populated from the captured HEAD file or its rename source.
- Preserves: `validate_staged(repo_root, staged_paths) -> list[str]` and every existing validator-runner interface.
- Enforces: only staged docstring findings absent from the corresponding captured-HEAD baseline; new files have an empty baseline and therefore remain fully strict.

- [x] **Step 1: Add failing snapshot and adapter tests**

  Cover same-path modification baselines, rename-source baselines, unchanged legacy-finding suppression, new-finding rejection, and full enforcement for new files.

- [x] **Step 2: Run the focused tests and confirm the missing-baseline behavior fails**

  Run `pytest -q tests/test_repository_validator_checks.py tests/test_docstrings_validator.py` and retain the failure as the red phase.

- [x] **Step 3: Materialize captured-HEAD baselines inside the isolated staged mirror**

  Resolve same-path predecessors from the captured HEAD tree, resolve renamed predecessors from the captured index diff, copy only regular-file blobs, and fail closed on malformed Git output or unreadable objects.

- [x] **Step 4: Subtract matching baseline issue fingerprints in the staged docstring adapter**

  Compare findings by code, severity, node id, and message while deliberately excluding path and line number. Use multiset subtraction so duplicate diagnostics cannot be hidden accidentally. A missing, undecodable, or unparsable baseline suppresses nothing.

- [x] **Step 5: Run focused and staged validation**

  Run the two focused test files, `repo/docstrings`, `repo/skip_hygiene`, `git diff --check`, and the relocation idempotence check.

- [ ] **Step 6: Commit the validator fix, then certify the exact final commit**

  Stage only the four implementation/test files and this plan, inspect the staged diff, commit, verify the worktree is clean, and invoke `skill-certifier._rtx.interface.certify` through `dispatcher` with the full reviewed commit hash.

### Task 8: Current-source managed-runtime bootstrap

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_install.py`

**Interfaces:**
- Preserves: `install-assistant-tools._rtx.interface.scripts-install` and its interactive/non-interactive arguments.
- Produces: a fresh child process that executes the current `_phase_entry.py` with the current checkout's `src/` first on `PYTHONPATH` whenever the dispatcher loaded Officina from another runtime tree.
- Excludes: version detection, old-signature fallbacks, mutation of the active release, and direct calls into an older Officina API.

- [x] **Step 1: Add failing restart-boundary tests**

  Prove that a foreign `managed_runtime.py` location causes one current-source child invocation with unchanged arguments and exit status, while an already-current source runs in-process without recursion.

- [x] **Step 2: Implement the current-source restart boundary**

  Compare the loaded managed-runtime module with `REPO_SRC`; when they differ, run the same phase entry under `sys.executable` with `REPO_SRC` prepended to `PYTHONPATH` and inherited terminal streams.

- [x] **Step 3: Run focused installer and managed-runtime tests**

  Run the installer orchestration tests, managed-runtime tests, staged docstring validation, and `git diff --check`.

- [ ] **Step 4: Commit, refresh, verify, and certify**

  Commit the bootstrap fix, run the public installer interface in approved development mode, verify the active release and dispatcher help, then certify the exact final commit and commit only generated certificate records if required.

### Task 9: Certifier validator runtime dependencies

**Files:**
- Modify: `skills/skill-certifier/_rtx/blueprints/rtx-certifier.yaml`
- Regenerate: `references/blueprint/runtime_dependencies.json`
- Regenerate: `references/runtime/requirements-core.in`
- Regenerate: `references/runtime/requirements-core.lock`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_scaffold.py`
- Modify: `tests/test_officina_managed_runtime.py`

**Interfaces:**
- Preserves: the certifier interface and mechanical gate behavior.
- Adds: exact managed-runtime dependencies `pytest==8.3.4`, `pytest-xdist==3.8.0`, and `pyflakes==3.2.0`, which the certifier's existing repository-validator subprocess requires.
- Excludes: ambient-Python fallback, validator bypass, and optional dependency installation.

- [x] **Step 1: Add a failing generated-manifest dependency test**
- [x] **Step 2: Declare the dependencies and regenerate blueprint/runtime artifacts**
- [x] **Step 3: Regenerate and verify the hash-checked runtime lock**
- [ ] **Step 4: Commit, refresh the runtime, and rerun certification**

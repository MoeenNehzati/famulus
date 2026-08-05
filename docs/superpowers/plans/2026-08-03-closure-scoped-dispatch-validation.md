# Closure-Scoped Dispatch and Immediate-Caller Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement immediate-module authorization, validate declared caller ownership repository-wide, and allow valid dispatch closures to run despite unrelated blueprint or certification defects.

**Architecture:** The canonical resolver evaluates target-owned access against the immediate caller's registered ancestry. Runtime `DispatchCall` declarations identify each call independently; source context is tracing-only. A dispatcher-only scoped graph loader reuses the canonical builder and reports proven-unrelated defects as warnings.

**Tech Stack:** Python 3.11+, existing Officina graph/authorization/runtime modules, AST validators, pytest.

**Status:** Implemented. Focused suites and the live list-manager dispatch pass.
The repository-wide precommit and validator runs still report unrelated dirty
worktree failures recorded under Verification below.

## Global Constraints

- No upstream caller chain or source identity participates in authorization.
- A namespace or facade owner that accepts a call becomes the immediate caller
  of the next hop.
- Access is granted only by self, public access, or an allowlisted immediate-caller ancestor.
- Strict repository validators remain repository-wide.
- Existing unrelated worktree changes remain untouched.
- Do not commit without separate user authorization.

---

### Task 1: Immediate-caller module authorization

**Files:**
- Modify: `src/officina/common/blueprint_authorization.py`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Test: `tests/test_officina_blueprint_authorization.py`
- Test: `tests/test_officina_python_machine_interface.py`

**Interfaces:**
- Produces: module-only `resolve_interface_authorization(...)` decisions.
- Preserves: optional `caller_source_id` result metadata.

- [x] Write failing tests for self, public, exact caller, ancestor caller,
  descendant asymmetry, sibling calls, missing source, mismatched source, and
  undeclared `uses_interfaces`.
- [x] Run focused tests and confirm failures identify the current exact-only and
  source-required branches.
- [x] Change `_evaluate_access(...)` so a policy admits when its resolved caller
  set intersects `graph.module_ancestry[caller_module_id]`.
- [x] Remove source-required, source-owner, and declared-use denial branches.
  Do not add caller source to required certificate subjects.
- [x] Change `PythonMachineInterface.dispatch()` to resolve every declared call
  directly with `caller_source_id=None` and `host_caller=False`; runtime context
  may check a module mismatch but must not be required or propagated.
- [x] Run authorization and runtime tests to green.

### Task 2: Repository-wide declared-caller ownership validation

**Files:**
- Modify: `validators/skill/dispatch_caller_module.py`
- Test: `tests/validate_dispatch_caller_module.py`

**Interfaces:**
- Consumes: `RepositoryBlueprintGraph.direct_file_owners`, `source_modules`, and
  registered module roots.
- Produces: validation errors for missing, dynamic, or wrong immediate callers.

- [x] Write failing tests for valid and invalid `DispatchCall` declarations in
  non-skill registered modules and behavioral-source-owned Python files.
- [x] Run focused validator tests and confirm the current skills-only scan misses
  those files.
- [x] Derive production Python files from the loaded graph, map source ownership
  to its containing module, retain existing skill fallback for graph-optional
  operation, and exclude test directories.
- [x] Validate both `DispatchCall(caller_module_id=...)` and direct
  `dispatch(caller_skill=...)` against the deepest owning module.
- [x] Run validator tests to green.

### Task 3: Closure-scoped graph and advisory diagnostics

**Files:**
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/dispatcher/cli.py`
- Test: `tests/test_officina_blueprint_graph.py`
- Test: `tests/test_officina_dispatcher.py`
- Test: `tests/test_dispatcher_route_smoke.py`

**Interfaces:**
- Produces: `load_dispatch_blueprint_graph(...)`, structured invocation warnings,
  and CLI stderr warnings.

- [x] Retain the existing red/green tests for unrelated invalid modules,
  absolute caller references, and behavioral-source dependency closure.
- [x] Add dispatch-resolution coverage proving warnings and fatal relevant errors.
- [x] Confirm all certification rejection codes become warnings after bootstrap
  is attempted.
- [x] Run focused graph, dispatcher, and runtime tests to green; record the two
  unrelated live-repository route-smoke failures.

### Task 4: Documentation and live verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/scaffolding/README.md`
- Modify: `references/blueprint/README.md`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `docs/superpowers/specs/2026-08-03-closure-scoped-dispatch-validation-design.md`

- [x] Document the immediate-caller predicate, self/private behavior, ancestor
  grants, sibling example, and non-role of source identity and upstream chains.
- [x] Document `uses_interfaces` as static dependency/certification metadata.
- [x] Run focused suites for authorization, graph, dispatcher, runtime, caller
  validator, and route smoke.
- [x] Run `python3 scripts/run-python-tests.py --suite precommit` and
  `python3 validators/runner.py`; distinguish new failures from unrelated dirty
  work.
- [x] Run the live list-manager cloud read and confirm warnings do not block it.
- [x] Mark the design `Implemented`, run `git diff --check`, and inspect exact
  final scope without staging or committing.

## Verification

- Focused authorization, graph, dispatcher, runtime, caller-validator,
  relationship, and metadata suites: 219 passed.
- Full precommit suite: 1217 passed, 10 skipped, 8 failed. The failures arise
  from pre-existing invalid live blueprints, deleted certification-basis input,
  stale skill graph data, and an unrelated browser projection test.
- Validator runner: pre-existing stale generated docs and the ungenerated
  `llm-wakeup` blueprint contract remain.
- Live `list-manager._rtx.interface.cloud-read-beautify` cloud read: exit 0 with the
  intended unrelated-blueprint and certification warnings.
- Scoped `git diff --check` and `schema-meta.json` parsing: pass.

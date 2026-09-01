# Platform Semantic Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically replay only functional tests that touch registered platform semantics under Linux-hosted Windows and macOS models.

**Architecture:** A context-local boundary registry records exact pytest node IDs during the normal pass. The runner merges worker traces and launches fresh, serial replay subprocesses for the applicable alternate models; native-only behavior stays in native CI.

**Tech Stack:** Python 3.11+, pytest/xdist hooks, contextvars, JSON trace files, Officina repository checks and standards.

**Spec:** `docs/plans/2026-09-01-platform-semantic-replay-design.md`

## Global Constraints

- New or modified functional tests for semantic boundaries contain no replay annotations, platform branches, or injected-fault setup.
- Replay selects exact passed node IDs observed at registered semantic boundaries; untouched tests are never replayed.
- Explicit platform arguments are conformance inputs and never trigger automatic replay.
- Alternate models are pure and perform no host OS calls.
- Model selection is a union, never a Cartesian product; replay is serial initially.
- Only contractually transparent faults compose automatically with functional assertions.
- Physical filesystem, process, pipe, socket, browser, keyring, and performance behavior retains native evidence.
- Preserve unrelated work and keep all commits on `codex/ci-integration-17aba16e`.

---

### Task 1: Canonical standard and platform models

**Files:**
- Create: `src/officina/platforms/__init__.py`
- Create: `src/officina/platforms/models.py`
- Create: `src/officina/platforms/registry.py`
- Create: `tests/test_platform_models.py`
- Modify: `references/node-standards/code-testing.standard.yaml`

**Interfaces:**
- Produces: `PlatformModel`, `BoundaryContract`, `boundary_model()`, `active_fault()`, `observe_boundary()`, and `platform_replay()`.

- [ ] Write failing model and registry tests for Linux/macOS/Windows path flavor, PATH separator, executable suffix, newline, environment-key normalization, implicit observer calls, explicit-input suppression, and context restoration.
- [ ] Run `pytest -q tests/test_platform_models.py` and verify the missing-interface failure.
- [ ] Implement immutable models and a contextvar-backed registry. The public shape is:

```python
def boundary_model(boundary_id: str, *, explicit: str | None = None) -> PlatformModel: ...
def active_fault(boundary_id: str) -> str | None: ...
@contextmanager
def platform_replay(model: str, *, fault: tuple[str, str] | None = None, observer=None): ...
```

- [ ] Add required standard assertions for semantic replay fidelity, boundary ownership, selective execution, transparent faults, and retained native evidence; bump the revision and update verified dependent digests only.
- [ ] Run the focused model tests and standard validator, then commit.

### Task 2: Exact-node discovery and replay plugin

**Files:**
- Create: `src/officina/repository/checks/platform_replay.py`
- Create: `tests/test_platform_replay.py`

**Interfaces:**
- Consumes: Task 1 registry observer and replay context.
- Produces: pytest options for discovery trace directory, replay model, and replay fault; worker-local JSON records keyed by exact node ID.

- [ ] Write pytester failures proving an unannotated test that touches a boundary is recorded, an untouched test is absent, parametrized IDs stay exact, failed baseline tests are excluded, worker traces merge deterministically, and replay context is restored.
- [ ] Run `pytest -q tests/test_platform_replay.py` and verify failures.
- [ ] Implement hooks around each test protocol: install the current node-ID observer, retain boundary IDs only after a passing call report, and atomically publish one JSON file per worker. In replay mode, install the requested model/fault and disable discovery.
- [ ] Run the focused plugin tests and commit.

### Task 3: Selective repository-runner integration

**Files:**
- Modify: `src/officina/repository/checks/runner.py`
- Modify: `tests/test_repository_test_checks.py`
- Modify: `docs/ci-handbook.md`

**Interfaces:**
- Consumes: Task 2 trace records and Task 1 boundary contracts.
- Produces: fresh serial pytest commands labeled `tests:platform-replay:<model>[:<boundary>/<fault>]`.

- [ ] Write failing runner tests for plugin loading, run-private trace paths, Linux-only follow-up, exact selector preservation, union grouping, one-fault-at-a-time commands, timing labels, replay failure propagation, and no follow-up after a red baseline.
- [ ] Run the exact new runner selectors and verify failures.
- [ ] Add discovery arguments to ordinary `combined` and `tests:shared` commands. After a green Linux baseline, merge traces and invoke only observed node IDs for each applicable model/fault; do not recurse into discovery.
- [ ] Document the semantic/native evidence boundary and run focused runner tests plus `tests:portability`; commit.

### Task 4: Real pilot and no-new-debt enforcement

**Files:**
- Modify: `src/officina/common/famulus_paths/__init__.py`
- Modify: `tests/test_officina_famulus_paths.py`
- Create: `references/platform-boundary-debt.yaml`
- Modify: `validators/cross_platform.py`
- Modify: `tests/validate_cross_platform.py`

**Interfaces:**
- Consumes: `boundary_model("famulus-paths", explicit=platform)`.
- Produces: one real semantic boundary reached by ordinary unannotated functional tests and a frozen inventory of unclassified ambient host access.

- [ ] Write a failing platform-agnostic functional path test that omits explicit platform input and asserts only stable `FamulusPaths` meaning; prove discovery selects it for both alternate models.
- [ ] Make `resolve_famulus_paths` accept `platform: str | None = None` and resolve through the registered boundary while preserving every explicit caller.
- [ ] Add validator failures for ambient host primitives inside registered semantic modules, malformed native declarations, and any new unclassified host-dependent module beyond the checked debt inventory.
- [ ] Generate the initial debt inventory from inspected live code, add passing native-declaration and semantic-model cases, run affected tests and validators, then commit.

### Task 5: Verification and CI closure

**Files:**
- Modify only files required by failures attributable to this plan.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: reviewed branch with green focused, portability, precommit, and native matrix evidence.

- [ ] Run platform model, replay plugin, runner, pilot, and validator selectors.
- [ ] Run `python3 repo_checks.py --suite portability --jobs 1`, then host-capable `python3 repo_checks.py --suite precommit --jobs 8`.
- [ ] Independently review the complete branch and repair every Critical/Important finding through the subagent fix loop.
- [ ] Run targeted Windows/macOS probes for affected shared tests, each whole affected element, then the complete matrix. Diagnose and repair attributable failures until green.

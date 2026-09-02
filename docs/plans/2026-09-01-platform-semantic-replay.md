# Platform Semantic Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task by task.

**Goal:** Turn native macOS/Windows CI failures into a small committed registry of exact pytest nodes, then replay those known semantic regressions on Linux during pre-push and full CI.

**Architecture:** Native CI is the discovery mechanism. `ci-debug` registers a test only after isolating an exact native failure and proving that a production semantic boundary reproduces it. One strict registry loader and one pytest replay plugin serve targeted diagnosis, pre-push, and full Linux CI. The first and only boundary is `famulus-paths`; physical OS behavior remains native-only.

**Spec:** `docs/plans/2026-09-01-platform-semantic-replay-design.md`

## Scope and budget

Implementation may change only these files. The hard limit is **1,200 gross changed lines** (additions plus deletions) against the implementation baseline. This replaces the old 900-line limit because the registry design adds two real blueprint records, remote probe transport, and their tests. An unlisted required file, generated change, second boundary, or total above 1,200 stops implementation for plan revision.

| File | Change |
|---|---|
| `src/officina/platforms/__init__.py` | Create public exports. |
| `src/officina/platforms/model.py` | Create fixed models, host normalization, boundary lookup, observer, and replay context. |
| `src/officina/platforms/blueprint.yaml` | Create module ownership and export record. |
| `src/officina/platforms/blueprints/model.yaml` | Create source and Python-interface record. |
| `src/officina/dispatcher/platforms/__init__.py` | Delegate existing normalization to the model module. |
| `src/officina/repository/checks/platform_replay.py` | Create registry loader and replay-only pytest plugin. |
| `tests/platform-semantic-replay.json` | Create committed registry. |
| `tests/test_platform_replay.py` | Create model, loader, and plugin tests. |
| `src/officina/repository/checks/runner.py` | Add replay task and suite scheduling. |
| `tests/test_repository_test_checks.py` | Add runner and suite-policy tests. |
| `src/officina/repository/checks/remote.py` | Permit targeted remote replay probes. |
| `tests/test_repo_checks_remote.py` | Test remote replay-task acceptance. |
| `src/officina/common/famulus_paths/__init__.py` | Route implicit platform choice through the boundary model. |
| `src/officina/common/famulus_paths/_get_interface.py` | Stop forwarding `sys.platform`. |
| `src/officina/common/blueprints/famulus-paths.yaml` | Record optional platform and model dependency. |
| `tests/test_officina_famulus_paths.py` | Add boundary contracts and the seed implicit-path test. |
| `skills/ci-debug/SKILL.md` | Add registry decision and verification route. |
| `skills/ci-debug/instructions/repair-element.md` | Permit evidence-backed registry edits in repair scope. |
| `skills/ci-debug/prevention.md` | Distinguish registration from speculative prevention. |
| `skills/ci-debug/tests/test_ci_debug_instructions.py` | Test the registration instructions. |
| `.github/workflows/python-tests.yml` | Add the replay task to manual-dispatch choices. |
| `docs/testing.md` | Document local and targeted use. |
| `docs/ci-handbook.md` | Document the CI learning loop. |

Do not modify `.githooks/pre-push`: it already delegates to `repo_checks.py --suite pre-push`. Do not add a CI matrix element or change the ci-debug machine-report schema. Do not replace the existing portability tests; they remain an independent sentinel.

After every task:

```bash
git diff --name-only <implementation-baseline>
git diff --numstat <implementation-baseline>
```

Verify that every path is listed above and the gross total is at most 1,200.

## Fixed contracts

### Production boundary

`src/officina/platforms/model.py` exposes:

```python
PlatformModel = Literal["linux", "macos", "windows"]

def current_platform_name(token: str | None = None) -> str: ...

def boundary_model(boundary_id: str, *, explicit: str | None = None) -> PlatformModel: ...

@contextmanager
def platform_replay(
    model_id: PlatformModel,
    *,
    observer: Callable[[str], None] | None = None,
) -> Iterator[None]: ...
```

Only `famulus-paths` is valid. `current_platform_name` maps Darwin, Windows, and Linux host tokens as the dispatcher does today and returns an unsupported token unchanged. Separately, to preserve the Famulus API, explicit `darwin` maps to `macos`, explicit `win32` maps to `windows`, and every other explicit token maps to POSIX policy; explicit lookup does not notify the observer. Omitted lookup uses the scoped replay model or native host policy and notifies the observer. Context reset occurs in `finally`.

The module blueprint is `platforms@1`. It registers `platforms.source.model@1` from `blueprints/model.yaml` and exports `platforms.interface.model@1` from `platforms.source.model.interface.python-api@1` with `allow_all_modules: true`, an empty caller list, and no process binding. The source blueprint owns `model.py`, declares no dependencies, and exposes only that Python interface.

### Registry

`tests/platform-semantic-replay.json` has this closed schema:

```json
{
  "schema_version": 1,
  "entries": [
    {
      "nodeid": "tests/test_officina_famulus_paths.py::test_implicit_paths_keep_feature_roots_derived",
      "boundary": "famulus-paths",
      "models": ["macos", "windows"],
      "provenance": {
        "kind": "seed",
        "reference": "docs/plans/2026-09-01-platform-semantic-replay-design.md"
      },
      "reason": "Exercises implicit Famulus path policy through stable derived-root assertions."
    }
  ]
}
```

Native discoveries instead use:

```json
{
  "kind": "native-ci",
  "run_id": "123456789",
  "sha": "0123456789abcdef0123456789abcdef01234567",
  "os": "windows-latest"
}
```

The loader requires a nonempty `::` node suffix and a nonempty duplicate-free `models` subset of `macos` and `windows`. It rejects unknown keys, versions, boundaries, absolute/option-like/traversing/missing/non-test paths, duplicate `(nodeid, boundary)` pairs, empty reasons, and malformed provenance. Seed provenance has exactly `kind` and `reference`; native provenance has exactly `kind`, numeric `run_id`, 40-hex `sha`, and `os` equal to `macos-latest` or `windows-latest`. It also rejects entries not sorted by `(nodeid, boundary)` or models not ordered `macos`, `windows`. The runner rejects entries whose test file is in `PREPUSH_EXCLUDED_TESTS`. Grouping de-duplicates a node/model pair and unions its expected boundaries, so pytest executes that node once per model. Replay collection proves the suffix exists.

### Replay task

`tests:semantic-replay` accepts only exact selectors already present in the registry. On Linux it groups selected nodes by model and runs non-empty groups serially in canonical order `macos`, `windows`. Each command passes node IDs as separate arguments and sets:

```text
-p officina.repository.checks.platform_replay
--officina-platform-replay-model=<model>
```

The plugin loads the fixed registry from the tested repository root. For each selected node it activates the model, observes implicit boundary lookup, and requires collection plus passing setup/call/teardown with no skip, xfail, or XPASS. It fails the session if the node misses any boundary declared for that node/model. Separate task labels (`tests:semantic-replay:macos` and `tests:semantic-replay:windows`), pytest caches, and timing output identify each model.

On non-Linux hosts, the task prints an explicit skip and succeeds without starting model subprocesses.

### Suite order

- `precommit`: unchanged; no replay.
- `pre-push` on Linux: existing combined/shared baseline; replay if that baseline passed; existing browser phase regardless of either result.
- `full` on Linux: existing performance phase; combined; replay if combined passed; browser.
- Other hosts: the same suite order, with replay explicitly skipped.
- `workflow_dispatch`: permits the selectable parent task `tests:semantic-replay` for ci-debug probes.

---

### Task 1: Add the platform model

**Files:**

- Create `src/officina/platforms/__init__.py`
- Create `src/officina/platforms/model.py`
- Create `src/officina/platforms/blueprint.yaml`
- Create `src/officina/platforms/blueprints/model.yaml`
- Modify `src/officina/dispatcher/platforms/__init__.py`
- Create `tests/test_platform_replay.py`

- [ ] Write tests for native and explicit normalization, unknown boundary/model rejection, implicit observer notification, explicit observer suppression, nested replay, and exception restoration.
- [ ] Run `pytest -q tests/test_platform_replay.py`; confirm failure because the model module is absent.
- [ ] Implement the fixed mapping and one `ContextVar` holding model plus observer. Add no registration API or native system-call emulation.
- [ ] Make the dispatcher helper delegate while preserving its existing public import and unsupported-token behavior.
- [ ] Add ownership and interface blueprints for the new package.
- [ ] Run `pytest -q tests/test_platform_replay.py` and focused repository blueprint validators.
- [ ] Commit the Task 1 files.

### Task 2: Add the registry loader and replay plugin

**Files:**

- Create `src/officina/repository/checks/platform_replay.py`
- Create `tests/platform-semantic-replay.json`
- Modify `tests/test_platform_replay.py`

The module exposes:

```python
@dataclass(frozen=True)
class ReplayEntry:
    nodeid: str
    boundary: str
    models: tuple[PlatformModel, ...]
    provenance: Mapping[str, str]
    reason: str

def load_replay_registry(repo_root: Path) -> tuple[ReplayEntry, ...]: ...
def replay_nodes_by_model(entries: Iterable[ReplayEntry]) -> dict[PlatformModel, tuple[str, ...]]: ...
```

- [ ] Add loader tests for a valid seed/native entry and every rejection listed in the registry contract. Use temporary repositories; keep the committed registry empty until Task 4 adds its real seed.
- [ ] Add grouping tests proving two boundary entries for one node/model produce one pytest node with the union of both expected boundaries.
- [ ] Add pytester tests showing an exact registered node passes under a model only when it reaches all declared boundaries.
- [ ] Add failing cases for missing collection, setup/call/teardown failure, skip, xfail, XPASS, absent boundary use, and a selected node not registered for the requested model.
- [ ] Run `pytest -q tests/test_platform_replay.py`; confirm the new cases fail.
- [ ] Implement strict JSON parsing and deterministic grouping. Keep the registry path fixed; accept no arbitrary registry-path option.
- [ ] Implement the replay-only pytest option and hooks. Install/reset `platform_replay` around each selected item and aggregate its three reports before validating it. Do not implement baseline observation, manifest publication, or xdist transport.
- [ ] Run `pytest -q tests/test_platform_replay.py`.
- [ ] Commit the Task 2 files.

### Task 3: Wire targeted, pre-push, and CI replay

**Files:**

- Modify `src/officina/repository/checks/runner.py`
- Modify `tests/test_repository_test_checks.py`
- Modify `src/officina/repository/checks/remote.py`
- Modify `tests/test_repo_checks_remote.py`
- Modify `.github/workflows/python-tests.yml`

- [ ] Add runner tests for deterministic model commands, separate node arguments/cache/timing/task labels, selector intersection, invalid selector rejection, empty groups, non-Linux skip, and failure propagation.
- [ ] Add suite tests proving `precommit` is unchanged; `pre-push` schedules combined, replay, browser; a red combined phase suppresses replay but not browser; and `full` preserves performance before combined, replay, browser.
- [ ] Run `pytest -q tests/test_repository_test_checks.py`; confirm the new cases fail.
- [ ] Register the selectable parent task `tests:semantic-replay`. In `normalize_test_selectors`, special-case this task by loading the registry and accepting only exact registered node IDs; do not use the ordinary `None` task allowlist. Group only that subset when selectors are supplied.
- [ ] Insert the parent replay phase after shared tests in `SUITE_PHASES["pre-push"]` and `SUITE_PHASES["full"]`. `_suite_runs` then yields it after the pooled `combined` run. In `run_suite`, expand that parent phase into its serial model commands; if the preceding combined result is red, print a replay skip and continue to browser. Preserve the runner's existing non-failfast browser behavior.
- [ ] Add `tests:semantic-replay` to `remote.py`'s closed `SUPPORTED_TASKS`, its remote acceptance tests, and `workflow_dispatch` task choices. Do not alter the remote report schema or add matrix jobs: the Ubuntu `full` job reaches replay through suite policy.
- [ ] Run `pytest -q tests/test_repository_test_checks.py tests/test_repo_checks_remote.py` and inspect the workflow diff.
- [ ] Run `python3 repo_checks.py --suite pre-push --task tests:semantic-replay` on Linux with an empty registry; confirm a successful no-op before Task 4.
- [ ] Commit the Task 3 files.

### Task 4: Route Famulus paths and seed replay

**Files:**

- Modify `src/officina/common/famulus_paths/__init__.py`
- Modify `src/officina/common/famulus_paths/_get_interface.py`
- Modify `src/officina/common/blueprints/famulus-paths.yaml`
- Modify `tests/test_officina_famulus_paths.py`
- Modify `tests/platform-semantic-replay.json`

- [ ] Add `test_implicit_paths_keep_feature_roots_derived`. Call `resolve_famulus_paths` without `platform`; assert stable invariants such as absolute roots and existing derived-field relationships, not a model-specific literal layout.
- [ ] Add focused tests that omitted platform observes `famulus-paths` and that explicit platform remains compatible without observation.
- [ ] Add the seed entry shown above for both models.
- [ ] Run the new exact test under ordinary Linux pytest and under direct macOS/Windows replay; confirm replay fails before boundary routing.
- [ ] Change `resolve_famulus_paths(*, platform: str | None = None, home, environ)` and `FamulusPaths.get(..., platform: str | None = None)` to assign `model = boundary_model("famulus-paths", explicit=platform)`, branch on `model == "macos"` and `model == "windows"`, and use the existing POSIX branch otherwise.
- [ ] Add direct contract tests proving omitted platform under macOS/Windows replay selects the corresponding concrete path layout, while explicit inputs retain current layout and suppress observation. Keep the annotation-free seed focused on stable derived-root invariants.
- [ ] Remove `_get_interface.py`'s explicit `sys.platform` argument so the production gateway uses implicit policy. In `famulus-paths.yaml`, add dependency `{source: platforms.source.model, version: 1, blueprint: {base: repository-root, path: src/officina/platforms/blueprints/model.yaml}}`; add `{interface: platforms.interface.model, version: 1}` to both source-level and `common.source.famulus-paths.interface.python-api` `uses_interfaces`; and make the platform argument optional. `famulus-paths-get.yaml` remains unchanged.
- [ ] Run the native exact test, both exact replay groups, all Famulus-path tests, and focused blueprint validators.
- [ ] Commit the Task 4 files.

### Task 5: Make ci-debug maintain the registry

**Files:**

- Modify `skills/ci-debug/SKILL.md`
- Modify `skills/ci-debug/instructions/repair-element.md`
- Modify `skills/ci-debug/prevention.md`
- Modify `skills/ci-debug/tests/test_ci_debug_instructions.py`

Add this literal decision procedure to the skill:

```text
1. Isolate the exact native failing pytest node from logs or a targeted file probe.
2. Diagnose whether its cause is policy owned by a declared semantic boundary.
   If the cause requires physical host behavior, do not register it.
3. Choose the contract-owning replay node. Try the native node only if it is a
   shared functional test that invokes the affected entry point through the
   implicit boundary; otherwise trace that entry point, augment its existing
   test, or create one only if no test owns that contract.
4. Existing boundary: write a provisional registry entry, then run targeted
   Linux replay against the bad candidate. Retain it only if replay observes
   that boundary and reproduces the failure. If the first candidate does not,
   remove it and return once to step 3; classify native-only/unresolved only if
   no contract-owning candidate reproduces.
5. Missing boundary: add the smallest reusable production boundary only when the
   native failure and a red/green boundary contract test justify it; then register.
6. Record native-ci run_id, sha, os, and a specific reason. Add only evidenced models.
7. Make retained replay pass, then verify the native exact node, affected matrix
   element, and full matrix.
```

For every macOS/Windows pytest repair assignment, the coordinator includes `tests/platform-semantic-replay.json` in the allowed path scope. The repair agent changes it only after step 2 classifies the failure as modeled semantic policy. If a new boundary or an unassigned production path is required, the agent returns a concrete scope-expansion request instead of editing outside its envelope.

For an existing boundary, the red proof is a working-tree command run after adding the provisional entry and before changing production behavior:

```bash
python3 repo_checks.py --suite pre-push --task tests:semantic-replay --selector <contract-owning-nodeid>
```

The entry's `sha` remains the original native failing SHA. Keep it only when this command fails for the diagnosed behavior after observing the boundary. After the repair, require the same local command green, commit the entry and repair, then use `ci-debug._rtx.interface.run-targeted-tests` for an Ubuntu replay probe and the exact node on the original native OS.

- [ ] Add instruction tests requiring the seven decisions, exact provenance fields, the exact local provisional-entry command, post-commit Ubuntu/native probes, physical-host exclusion, and the rule that CI reports never mutate Git.
- [ ] Run `pytest -q skills/ci-debug/tests/test_ci_debug_instructions.py`; confirm failure.
- [ ] Update the repair instruction so a repair agent may edit the registry within its assigned branch/path scope. Registration is part of repairing a proven regression, not a post-green speculative prevention proposal.
- [ ] Update coordinator instructions to include the registry path in macOS/Windows pytest repair scopes. Preserve approval for unrelated suite expansion. Prohibit automatic removal after green CI; rename/removal requires explicit collection and replay verification.
- [ ] Run `pytest -q skills/ci-debug/tests/test_ci_debug_instructions.py`.
- [ ] Commit the Task 5 files.

### Task 6: Document and verify the complete path

**Files:**

- Modify `docs/testing.md`
- Modify `docs/ci-handbook.md`

- [ ] Document these commands:

```bash
# All known semantic regressions, Linux only
python3 repo_checks.py --suite pre-push --task tests:semantic-replay

# One registered node during diagnosis
python3 repo_checks.py --suite pre-push --task tests:semantic-replay --selector tests/test_officina_famulus_paths.py::test_implicit_paths_keep_feature_roots_derived

# Normal developer gate; replay is automatically scheduled on Linux
python3 repo_checks.py --suite pre-push
```

- [ ] State that native CI discovers candidates, ci-debug commits evidence-backed entries, and physical behavior remains native-only. State that portability tests do not choose replay tests.
- [ ] Run focused tests:

```bash
pytest -q tests/test_platform_replay.py tests/test_repository_test_checks.py tests/test_officina_famulus_paths.py skills/ci-debug/tests/test_ci_debug_instructions.py
```

- [ ] Run repository validation and the real local gate:

```bash
python3 repo_checks.py --suite validators
python3 repo_checks.py --suite pre-push --jobs 8
```

- [ ] Verify the seed node runs in both replay groups, browser still runs after an earlier failure in a runner test, pre-commit contains no replay phase, the workflow has no new matrix element, and the final diff stays within the scope ledger and 1,200-line ceiling.
- [ ] Commit the Task 6 files.

## Completion conditions

- Native CI plus ci-debug is the only mechanism that adds unforeseen tests.
- Pre-push and Ubuntu full CI consume the same committed registry and plugin.
- Every selected test is exact, reproducible under a declared boundary, and stale entries fail loudly.
- No claim is made about unmodeled or physical OS behavior.

# Officina relocation closure implementation plan

> **Execution:** Use `superpowers:subagent-driven-development` for delegated execution, or `superpowers:executing-plans` for inline/separate-session execution. Complete tasks in order and review after every commit.

**Goal:** Make one schema-v2 relocation invocation derive, validate, report, and atomically publish every deterministic blueprint, dependency, certification-basis, and generated-artifact change implied by an approved source move.

**Architecture:** Keep `relocation.py` as the manifest and transaction facade. Materialize its declared projection in an isolated shadow tree, run closure producers there, reconcile only whitelisted outputs into the original `ChangeSet`, and publish once. Reuse the canonical graph, route tracer, blueprint synchronizer, and validators; extend them only where the approved design requires a reusable bootstrap trace or Git-independent tracked-file input.

**Tech stack:** Python 3.13, dataclasses, pathlib, PyYAML, jsonschema, pytest, canonical Officina blueprint graph and route-smoke runtime.

## Global constraints

- Manifest schema version 2 is the only supported execution path; do not retain a version-1 compatibility branch.
- Automate only deterministic closure. Module registration, package disposition, export creation, and caller authorization remain explicit manifest policy.
- Preflight may mutate only an isolated shadow tree. The real worktree receives one already-validated `ChangeSet` through the existing concurrent-change guard and atomic publisher.
- Preserve unrelated dirty files byte-for-byte and preserve executable modes.
- Exclude copied Git metadata, caches, environments, dependency directories, build outputs, certificate histories, and pooled reviews from the shadow tree.
- Do not duplicate graph loading, process tracing, blueprint generation, or validator rules.
- Do not run full pytest, certification, signing, installation, activation, commit, or push from the relocation command.
- Keep visualization implementation changes out of every commit; it is under concurrent development.
- Do not stage the existing `skills/skill-drift/_rtx/tests/test_drift_check.py` edit; that is a separate paused bug fix.

---

### Task 1: Advance the manifest to schema v2 and make package policy explicit

**Files:**

- Modify: `src/officina/refactor/relocation.schema.json`
- Modify: `src/officina/refactor/relocation.py`
- Modify: `tests/test_officina_relocation.py`
- Modify: `tests/test_officina_source_relocation_manifest.py`
- Modify: `docs/superpowers/plans/2026-08-17-officina-relocation-closure.md`

**Step 1: Write failing schema and parser tests**

Add focused tests proving that version 1 is rejected, every boundary record is typed, and disposition-specific fields are enforced:

```python
def test_manifest_v2_requires_explicit_package_boundary_dispositions(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "move.yaml", {
        "schema_version": 2,
        "package_boundaries": [
            {"path": "src/officina/tools", "disposition": "registered-module",
             "module_id": "tools", "blueprint": "src/officina/tools/blueprint.yaml"},
            {"path": "src/officina/helpers", "disposition": "unregistered-package"},
        ],
    })
    assert manifest.package_boundaries[0].module_id == "tools"
    assert manifest.package_boundaries[1].module_id is None


@pytest.mark.parametrize("value", [
    {"schema_version": 1},
    {"schema_version": 2, "package_boundaries": [
        {"path": "src/officina/tools", "disposition": "registered-module"}
    ]},
    {"schema_version": 2, "package_boundaries": [
        {"path": "src/officina/tools", "disposition": "unregistered-package",
         "module_id": "tools"}
    ]},
])
def test_manifest_rejects_legacy_or_incoherent_boundary_policy(...): ...
```

Run:

```bash
pytest -q tests/test_officina_relocation.py -k 'manifest_v2 or boundary_policy'
```

Expected: FAIL because schema version 2 and `package_boundaries` are unknown.

**Step 2: Add the v2 model and strict JSON schema**

Add:

```python
PackageDisposition = Literal[
    "existing-module", "registered-module", "unregistered-package"
]

@dataclass(frozen=True)
class PackageBoundary:
    path: str
    disposition: PackageDisposition
    module_id: str | None = None
    blueprint: str | None = None

@dataclass(frozen=True)
class RelocationManifest:
    package_boundaries: tuple[PackageBoundary, ...] = ()
    # existing fields remain unchanged
```

Set the schema constant to `2`. Use conditional JSON-schema branches so only `registered-module` accepts and requires `module_id` plus `blueprint`; the other dispositions accept neither. Reject duplicate paths in `load_manifest`.

Add `_validate_package_boundary_declarations(changes, manifest)` to the declared projection. It must identify package boundaries newly created by moves/catalogs, require one manifest record for each, and verify that:

- `existing-module` resolves to an already registered module in the pre-move tree;
- `registered-module` points to the declared module blueprint and matching module id in the projection;
- `unregistered-package` has no module blueprint in the projection.

Return exact paths in every failure.

**Step 3: Run the focused tests**

```bash
pytest -q tests/test_officina_relocation.py -k 'manifest or boundary'
```

Expected: PASS.

**Step 4: Migrate the repository acceptance manifest**

Set its schema version to 2 and declare these package dispositions:

```python
{
    ("src/officina/standards", "registered-module"),
    ("src/officina/visualization", "unregistered-package"),
    ("src/officina/repository", "unregistered-package"),
    ("src/officina/repository/checks", "unregistered-package"),
    ("src/officina/validators", "unregistered-package"),
}
```

The registered `standards` boundary must declare module id `standards` and
blueprint `src/officina/standards/blueprint.yaml`. Update the acceptance test
to require the schema version and these exact declarations. This migration is
Task 1 work because version-1 manifests are intentionally unsupported.

**Step 5: Commit Task 1 and its manifest migration**

```bash
git add src/officina/refactor/relocation.schema.json src/officina/refactor/relocation.py tests/test_officina_relocation.py refactors/officina-source-relocation.yaml tests/test_officina_source_relocation_manifest.py docs/superpowers/plans/2026-08-17-officina-relocation-closure.md
git commit -m "Require relocation package dispositions"
```

---

### Task 2: Add a mode-preserving isolated shadow repository

**Files:**

- Create: `src/officina/refactor/shadow.py`
- Create: `tests/test_officina_relocation_shadow.py`
- Modify: `src/officina/refactor/__init__.py`

**Step 1: Write failing shadow isolation tests**

Cover current dirty bytes, projected writes/deletes, executable modes, exclusions, symlink rejection, deterministic snapshots, and cleanup:

```python
def test_shadow_materializes_current_tree_plus_projection(tmp_path: Path) -> None:
    changes = ChangeSet(tmp_path, inventory_exclusions=(".git", ".venv"))
    changes.write_text("src/pkg/new.py", "VALUE = 2\n")
    changes.deletes.add("src/pkg/old.py")
    with materialize_shadow(changes) as shadow:
        assert (shadow.root / "src/pkg/new.py").read_text() == "VALUE = 2\n"
        assert not (shadow.root / "src/pkg/old.py").exists()
        assert shadow.snapshot() == shadow.snapshot()
    assert not shadow.root.exists()


def test_shadow_preserves_executable_mode_and_rejects_symlinks(...): ...
def test_shadow_excludes_git_caches_certificates_and_pooled_reviews(...): ...
```

Run:

```bash
pytest -q tests/test_officina_relocation_shadow.py
```

Expected: FAIL because `officina.refactor.shadow` does not exist.

**Step 2: Implement the shadow boundary**

Use these interfaces:

```python
@dataclass(frozen=True)
class ShadowSnapshot:
    files: Mapping[str, tuple[str, int]]  # sha256 and permission mode

@dataclass
class ShadowRepository:
    root: Path
    original: ChangeSet

    def snapshot(self) -> ShadowSnapshot: ...
    def changed_paths(self, before: ShadowSnapshot) -> tuple[str, ...]: ...

@contextmanager
def materialize_shadow(changes: ChangeSet) -> Iterator[ShadowRepository]: ...
```

Create the root with `tempfile.TemporaryDirectory`. Copy the `ChangeSet.projected_files()` inventory, obtaining bytes through `ChangeSet.read_bytes()` and modes through the projected/disk mode helpers. Never copy `.git`, nested worktree metadata, symlinks, or excluded path classes. Raise `RelocationError` for a symlink encountered in the included inventory. Do not use shell copy commands.

Update the package README docstring to list `shadow.py` and its exact role; do not add imports or facades to `__init__.py`.

**Step 3: Run tests**

```bash
pytest -q tests/test_officina_relocation_shadow.py tests/test_officina_relocation.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add src/officina/refactor/shadow.py src/officina/refactor/__init__.py tests/test_officina_relocation_shadow.py
git commit -m "Add isolated relocation shadow tree"
```

---

### Task 3: Expose canonical route targets and bootstrap tracing

**Files:**

- Modify: `src/officina/certification/hashing.py`
- Modify: `src/officina/certification/blueprints/hashing.yaml`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: `skills/skill-certifier/_rtx/tests/test_certifier.py`
- Modify: `tests/test_node_certification_hashing.py`
- Modify: `tests/test_officina_python_machine_interface.py`

**Step 1: Write failing ownership and tracer tests**

Move the existing `_python_route_smoke_trace_specs` behavior tests to the canonical hashing suite and add a bootstrap-only trace test:

```python
def test_python_route_smoke_trace_specs_select_process_bound_python_sources(...):
    specs = python_route_smoke_trace_specs(graph, (source_id,))
    assert specs == ((source_id, interface_id, expected_target),)


def test_route_smoke_bootstrap_trace_runs_without_an_interface_target(...):
    paths = trace_python_route_smoke_bootstrap_dependencies(tmp_path)
    assert tmp_path / "src/officina/blueprints/__init__.py" in paths
    assert gateway_path not in paths
```

Run:

```bash
pytest -q tests/test_node_certification_hashing.py tests/test_officina_python_machine_interface.py skills/skill-certifier/_rtx/tests/test_certifier.py -k 'trace_specs or bootstrap_trace or route_audit'
```

Expected: FAIL because both public helpers are absent.

**Step 2: Extract route-target selection without changing behavior**

Move the selector from `_node_certifier.py` into `officina.certification.hashing` as:

```python
def python_route_smoke_trace_specs(
    graph: RepositoryBlueprintGraph,
    certification_node_ids: Sequence[str],
) -> tuple[tuple[str, str, PythonProcessTarget], ...]: ...
```

Keep the same validation, deduplication, ordering, logical package, and logical entrypoint behavior. Replace the certifier’s private implementation with the canonical import and adjust monkeypatch sites. Add the exact `blueprints.source.process-binding` dependency to `src/officina/certification/blueprints/hashing.yaml`; the runtime tracer remains certification-basis software, as it is today.

Update that sidecar's description, Python-API operation enum, and result description to cover route-target selection. Do not add a second export: this remains part of `certification.interface.hashing`.

**Step 3: Refactor the route tracer to support a bootstrap-only observation**

Add:

```python
def trace_python_route_smoke_bootstrap_dependencies(
    repo_root: Path,
    *,
    expected_schema_version: int = 6,
    schema_root: Path | None = None,
) -> tuple[Path, ...]: ...
```

Refactor the existing child-runner construction so one child process can return its imports after graph/resolver/harness initialization without loading or executing a target interface. Preserve `trace_python_route_smoke_dependencies_batch` output and error semantics. The new helper must use the same source-root and schema-root resolution; do not emulate bootstrap imports in the relocation package.

**Step 4: Run focused and regression tests**

```bash
pytest -q tests/test_node_certification_hashing.py tests/test_officina_python_machine_interface.py skills/skill-certifier/_rtx/tests/test_certifier.py
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/officina/certification/hashing.py src/officina/certification/blueprints/hashing.yaml src/officina/runtime/python_machine_interface.py skills/skill-certifier/_rtx/_node_certifier.py skills/skill-certifier/_rtx/tests/test_certifier.py tests/test_node_certification_hashing.py tests/test_officina_python_machine_interface.py
git commit -m "Expose canonical relocation route traces"
```

---

### Task 4: Derive source dependencies and certification-basis closure

**Files:**

- Create: `src/officina/refactor/closure.py`
- Create: `tests/test_officina_relocation_closure.py`
- Modify: `src/officina/refactor/__init__.py`

**Step 1: Build a minimal schema-v6 repository fixture**

The fixture must contain two registered behavioral sources, one process-bound affected gateway, a uniquely owned imported source, a README-only Officina package initializer absent from the certification basis, and a substantive bootstrap file. Use real canonical graph loading and monkeypatch only child-process trace results.

**Step 2: Write failing dependency closure tests**

```python
def test_unique_route_owner_adds_one_direct_source_dependency(...):
    result = close_deterministic_references(shadow, manifest, affected_paths)
    sidecar = yaml.safe_load(provider_sidecar.read_text())
    assert sidecar["dependencies"] == [{
        "blueprint": {"base": "repository-root", "path": owner_sidecar_path},
        "reason": f"Route smoke loads uniquely owned source {loaded_path}.",
        "source": owner_source_id,
        "version": owner_version,
    }]
    assert result.dependency_additions == ((consumer_source_id, owner_source_id),)


def test_ambiguous_or_unowned_route_path_is_reported_not_guessed(...): ...
def test_required_caller_authority_is_reported_not_granted(...): ...
def test_unmapped_path_survives_into_report_with_exact_path(...): ...
```

**Step 3: Write failing certification-basis tests**

```python
def test_bootstrap_readme_initializer_is_added_to_certification_basis(...): ...
def test_bootstrap_substantive_python_file_remains_unresolved(...): ...
def test_moved_basis_path_is_rewritten_through_typed_path_mapping(...): ...
```

Run:

```bash
pytest -q tests/test_officina_relocation_closure.py -k 'dependency or authority or basis'
```

Expected: FAIL because `officina.refactor.closure` does not exist.

**Step 4: Implement deterministic closure records and graph helpers**

Use immutable result records:

```python
@dataclass(frozen=True)
class ClosureResult:
    dependency_additions: tuple[DependencyAddition, ...] = ()
    certification_basis_changes: tuple[str, ...] = ()
    generated_artifact_changes: tuple[str, ...] = ()
    validation_results: tuple[str, ...] = ()
    required_architectural_decisions: tuple[str, ...] = ()
    unresolved_references: tuple[str, ...] = ()

def affected_behavioral_source_ids(
    graph: RepositoryBlueprintGraph,
    changed_paths: Iterable[str],
) -> tuple[str, ...]: ...

def close_deterministic_references(
    shadow: ShadowRepository,
    manifest: RelocationManifest,
    changed_paths: Iterable[str],
) -> ClosureResult: ...
```

Load `references/blueprint` explicitly with expected schema version 6. Select affected process targets with `python_route_smoke_trace_specs` and run one `trace_python_route_smoke_dependencies_batch`. Use `RepositoryBlueprintGraph.direct_file_owners`, authored node input paths, declared source dependencies, and `source_modules` to classify observed paths. Add a dependency only for one behavioral-source owner and serialize its exact repository-root sidecar locator/version. Use one stable reason string.

Detect but do not add missing export/caller authority. Put the exact caller/interface requirement in `required_architectural_decisions`.

Run `trace_python_route_smoke_bootstrap_dependencies` once. Rewrite existing basis entries through typed path renames, auto-add only AST-proven README-only `__init__.py` files, keep entries sorted and unique, and report every substantive or unowned bootstrap path in `unresolved_references`.

Update `src/officina/refactor/__init__.py` to document `closure.py`; do not export its names.

**Step 5: Run tests**

```bash
pytest -q tests/test_officina_relocation_closure.py -k 'dependency or authority or basis'
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/officina/refactor/closure.py src/officina/refactor/__init__.py tests/test_officina_relocation_closure.py
git commit -m "Derive relocation dependency closure"
```

---

### Task 5: Reuse generators and validators with a fixed-point write boundary

**Files:**

- Modify: `src/officina/refactor/closure.py`
- Modify: `tests/test_officina_relocation_closure.py`
- Modify: `validators/skill/blueprints.py`
- Modify: `tests/test_repository_validator_checks.py`

**Step 1: Write failing generator-boundary and validator tests**

```python
def test_canonical_sync_changes_only_allowed_generated_artifacts(...): ...
def test_unexpected_generator_or_validator_write_is_rejected(...): ...
def test_second_closure_pass_is_a_no_change_fixed_point(...): ...
def test_blueprint_validator_accepts_explicit_shadow_tracked_files(...): ...
```

The generated-output allowlist is exact:

- generated contract/interface/used-interface blocks in affected `SKILL.md` files;
- `references/blueprint/runtime_dependencies.json`;
- blueprint/source sidecars changed by deterministic closure;
- `references/certification/certification-basis-roots.json`.

Run:

```bash
pytest -q tests/test_officina_relocation_closure.py tests/test_repository_validator_checks.py -k 'generator or validator or fixed_point or shadow_tracked'
```

Expected: FAIL.

**Step 2: Give the canonical validator an explicit tracked-file view**

Change only the reusable API boundary:

```python
def validate_with_graph(
    repo_root: Path,
    graph: RepositoryBlueprintGraph,
    *,
    tracked_files: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
) -> list[str]: ...
```

When `tracked_files` is omitted, retain the current Git query and behavior. When supplied, validate that mapping instead. The relocation shadow supplies one stage-0 `100644` or `100755` record per included file. Do not initialize a Git repository in the shadow tree and do not weaken authored-source/mode checks.

**Step 3: Run the canonical synchronizer and validators in the shadow**

Invoke the copied `skills/skill-maker/_rtx/_blueprint_syncer.py` using `sys.executable`, `cwd=shadow.root`, explicit UTF-8 capture, and schema version 6. Snapshot before and after every subprocess/API validator. Reject changes outside the allowlist. Then run synchronizer `--check`, blueprint preflight/validation with the synthetic tracked-file view, and relationship validation against the already loaded graph.

**Step 4: Enforce convergence**

Split closure into one private pass and one coordinator:

```python
def _close_once(...) -> ClosureResult: ...

def close_relocation(...) -> ClosureResult:
    first = _close_once(...)
    second_before = shadow.snapshot()
    second = _close_once(...)
    if shadow.snapshot() != second_before or second.has_changes:
        raise RelocationError("relocation closure did not converge")
    return first.with_validation("closure-fixed-point")
```

Refresh standard digests before final validation and require a second refresh to be unchanged. Ensure validators are read-only by comparing snapshots.

**Step 5: Run tests**

```bash
pytest -q tests/test_officina_relocation_closure.py tests/test_repository_validator_checks.py
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/officina/refactor/closure.py tests/test_officina_relocation_closure.py validators/skill/blueprints.py tests/test_repository_validator_checks.py
git commit -m "Validate relocation closure at a fixed point"
```

---

### Task 6: Reconcile and publish one complete transaction

**Files:**

- Modify: `src/officina/refactor/relocation.py`
- Modify: `src/officina/refactor/closure.py`
- Modify: `scripts/relocate_officina_sources.py`
- Modify: `tests/test_officina_relocation.py`
- Modify: `tests/test_officina_relocation_closure.py`

**Step 1: Write failing end-to-end transaction tests**

Cover report truthfulness, reconcile allowlists, no-write failure, concurrent-change detection, dirty-file preservation, and idempotence:

```python
def test_report_contains_calculated_closure_results(...):
    report = plan_relocation(root, manifest).report()
    assert report["derived_dependency_additions"]
    assert report["certification_basis_changes"]
    assert report["generated_artifact_changes"]
    assert report["unresolved_references"] == []
    assert report["required_architectural_decisions"] == []


def test_apply_rejects_unresolved_or_required_decisions_without_writing(...): ...
def test_late_closure_failure_preserves_every_real_byte_and_mode(...): ...
def test_apply_preserves_unrelated_dirty_files(...): ...
def test_second_public_preflight_reports_no_changes(...): ...
def test_one_plan_updates_moved_gateway_sidecar_dependency_and_generated_manifest(...): ...
```

Run:

```bash
pytest -q tests/test_officina_relocation.py tests/test_officina_relocation_closure.py -k 'report or apply or late_closure or dirty or second_public'
```

Expected: FAIL because closure results are not part of `ChangeSet` or its report.

**Step 2: Extend `ChangeSet` with calculated result categories**

Add stable collections for `derived_dependency_additions`, `certification_basis_changes`, `generated_artifact_changes`, `validation_results`, `required_architectural_decisions`, and `unresolved_references`. Remove the unconditional placeholder from `report()`.

Add reconciliation:

```python
def reconcile_shadow_changes(
    changes: ChangeSet,
    shadow: ShadowRepository,
    before_closure: ShadowSnapshot,
    result: ClosureResult,
) -> None: ...
```

For every changed shadow path, verify it belongs to the approved output classes, then absorb exact bytes and modes through `ChangeSet.write_bytes`. Reject deletes or writes outside that allowlist. Preserve the initial expected-byte snapshot so publication still detects concurrent real-worktree edits.

**Step 3: Wire the public plan in one direction**

Refactor the old mechanics into `_project_manifest(root, manifest)`. Then:

```python
def plan_relocation(root: Path, manifest: RelocationManifest) -> ChangeSet:
    changes = _project_manifest(root, manifest)
    with materialize_shadow(changes) as shadow:
        before_closure = shadow.snapshot()
        result = close_relocation(shadow, manifest, changes.changed_paths())
        reconcile_shadow_changes(changes, shadow, before_closure, result)
    _validate_projected_tree(changes, manifest)
    return changes
```

Use the private declared-projection function for narrow mechanical unit tests; reserve `plan_relocation` and the CLI tests for complete schema-v6 fixtures. `apply_change_set` must reject nonempty decisions/references before staging any temporary file. The CLI needs no new mode: preflight and `--apply` already share the same plan.

The CLI must still emit the complete JSON report when either calculated blocker collection is nonempty, then exit 2. With `--apply`, it must reject before `apply_change_set`; without `--apply`, this is a read-only unsuccessful preflight rather than a false ready result.

**Step 4: Run transaction tests**

```bash
pytest -q tests/test_officina_relocation.py tests/test_officina_relocation_shadow.py tests/test_officina_relocation_closure.py
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/officina/refactor/relocation.py src/officina/refactor/closure.py scripts/relocate_officina_sources.py tests/test_officina_relocation.py tests/test_officina_relocation_closure.py
git commit -m "Close relocations before atomic publication"
```

---

### Task 7: Prove the Officina relocation acceptance case end to end

**Files:**

- Modify: `refactors/officina-source-relocation.yaml`
- Modify: `tests/test_officina_source_relocation_manifest.py`
- Modify: `docs/superpowers/specs/2026-08-17-officina-relocation-closure-design.md`
- Modify: `docs/superpowers/plans/2026-08-17-officina-relocation-closure.md`

**Step 1: Add the end-to-end acceptance proof**

Add a clean fixture reconstructed from the pre-relocation paths and assert one
invocation includes the previously missed README-only package initializers,
blueprint dependency closure, and generated runtime manifest in the same
`ChangeSet`, with no hand-authored post-plan patch. The schema-v2 manifest
migration and its direct acceptance assertions are Task 1 work.

**Step 2: Run focused verification**

```bash
pytest -q tests/test_officina_relocation.py tests/test_officina_relocation_shadow.py tests/test_officina_relocation_closure.py tests/test_officina_source_relocation_manifest.py tests/test_node_certification_hashing.py tests/test_officina_python_machine_interface.py skills/skill-certifier/_rtx/tests/test_certifier.py tests/test_repository_validator_checks.py
```

Expected: PASS.

Run the real manifest in read-only mode:

```bash
scripts/relocate_officina_sources.py --root . --manifest refactors/officina-source-relocation.yaml
```

Expected on the already-relocated repository: exit 0; zero moves, writes, deletes, derived additions, unresolved references, or required decisions; validation includes graph, generated-artifact, validator, and fixed-point success.

Run repository validators only:

```bash
python3 repo_checks.py --suite validators
```

Expected: PASS. This is post-implementation verification, not a command embedded in relocation.

**Step 3: Review the diff and update document status**

Check only the implementation paths from Tasks 1-7, so concurrent visualization and skill-drift work cannot contaminate the review:

```bash
git diff --check
git status --short
```

Verify that no visualization-development file and no paused skill-drift test is staged. Change the design spec status from `Draft for review` to `Implemented` only after all acceptance checks pass. Mark this plan’s tasks complete with the verified commands and results; do not claim certification.

**Step 4: Commit the end-to-end proof and documentation**

```bash
git add tests/test_officina_source_relocation_manifest.py docs/superpowers/specs/2026-08-17-officina-relocation-closure-design.md docs/superpowers/plans/2026-08-17-officina-relocation-closure.md
git commit -m "Prove atomic Officina relocation closure"
```

---

## Final review checklist

- Trace every acceptance test in the design spec to a named test above.
- Confirm every new public function has a complete repository-standard docstring and every non-obvious invariant has an explanatory comment.
- Confirm package `__init__.py` remains documentation-only and names `relocation.py`, `shadow.py`, `closure.py`, and `relocation.schema.json`.
- Confirm the second public preflight is empty on both the fixture and the live already-relocated repository.
- Confirm reports are deterministic under repeated JSON rendering.
- Confirm every generator/validator write is snapshot-checked and allowlisted.
- Confirm `apply_change_set` has no write path when decisions, references, validation, or convergence fail.
- Confirm certification remains a separate explicit post-commit action.

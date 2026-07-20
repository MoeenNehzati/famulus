# Post-Migration Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After live blueprints have migrated to version 3, remove the version-2 branches from the existing common and dispatcher paths, make certification advisory during generated-interface injection, and retain the current module and caller structure wherever it still works.

**Architecture:** Simplify in place. Keep `src/officina/dispatcher/core.py`, the existing `DispatchCall(caller_skill, target_skill, interface, ...)` contract, and the current health/audit APIs. Remove legacy parsing and fallback branches; do not introduce a registry object, a request object, a new dispatcher package layout, new health record schemas, or a repository-wide caller migration.

**Tech Stack:** Python 3, PyYAML, JSON Schema draft-07, pytest, existing Officina descriptor-safe runtime helpers.

## Global Constraints

- Execute only after every live blueprint consumed by this path has `schema_version: 3` and `node_type`.
- Accept only canonical public dispatcher targets of the form `<skill>.machine.<export>` at the CLI boundary.
- Keep `DispatchCall.target_skill` and `DispatchCall.interface`; compose their canonical target internally.
- Keep `SkillBlueprintGraph` as a v3-derived skill view so health, pooled-review, audit, and drift code do not need redesign in this change.
- Certification is advisory. It never changes lookup, authorization, projection membership, compilation, or execution.
- Every projected direct or helper machine interface contains `is_certified: true|false`.
- Automatic injection means generated used-interface blocks written into declared LLM gateway Markdown by the existing blueprint sync command. It does not mean SessionStart injection.
- Keep the current confined `_rtx` Python gateway. `_cx` remains reserved for later work.
- Preserve descriptor-safe loading, deterministic argv, dry-run without execution or stdin reads, and binding cleanup.
- Use repository-relative paths in documentation and diagnostics.

## Explicit non-goals

- No `registry.py`, `compiler.py`, or `runtime.py` split.
- No `DispatchRequest` or separate error hierarchy.
- No change to `DispatchCall` declarations in skills.
- No rewrite of artifact-health, pooled-review, certificate, or audit record formats.
- No rewrite of historical execution/review logs.
- No regeneration of existing skill blueprints or sidecars merely to exercise compatibility.

---

### Task 1: Make the existing common blueprint path v3-only

**Files:**

- Modify: `src/officina/common/blueprint_inventory.py`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/blueprint_template.py`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `tests/test_blueprint_inventory.py`
- Modify: `tests/test_officina_blueprint_graph.py`
- Modify: `tests/test_officina_blueprint_template.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`

**Interfaces:**

- Preserves: `SkillBlueprintGraph`, `load_skill_blueprint_graph()`, `RepositoryBlueprintGraph`, and `load_repository_blueprint_graph()`.
- Removes: version-2 parsing, `blueprint_type` as an authored field, and `expanded_legacy_blueprint()`.

- [ ] **Step 1: Add focused failing tests**

Add tests proving that inventory rejects a discovered document when:

```yaml
schema_version: 2
blueprint_type: skill
id: demo
```

and accepts a version-3 document using `node_type`. Add a graph test proving that `load_skill_blueprint_graph(skill_root)` still returns the existing skill-view shape when its source documents are version 3. Add syncer/template tests proving no `schema_version == 2`, `blueprint_type`, or legacy expansion path is used.

- [ ] **Step 2: Run the focused tests and confirm the legacy behavior fails them**

```bash
pytest tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py tests/test_officina_blueprint_template.py skills/skill-maker/tests/test_blueprint_tools.py -q
```

Expected: FAIL because inventory and graph/template/sync code still accept or expand version-2 declarations.

- [ ] **Step 3: Remove version-2 parsing without changing downstream graph shapes**

In `blueprint_inventory.py`, require top-level `schema_version == 3`, require a non-empty string `node_type`, and reject the authored field `blueprint_type`. Preserve strict YAML parsing, no-follow reads, issue aggregation, and repository-relative diagnostics.

In `blueprint_graph.py`:

- remove version-2 schema selection, `_legacy_graph()`, virtual legacy interface construction, and `expanded_legacy_blueprint()`;
- keep `RepositoryBlueprintGraph` as the repository loader's result;
- keep `SkillBlueprintGraph` and `BlueprintNode.blueprint_type` as compatibility views for the existing health/audit consumers, but populate them only from version-3 documents;
- make `load_skill_blueprint_graph(skill_root)` validate the version-3 documents owned by that skill and return the existing view shape: one synthetic owner root plus the actual version-3 nodes and their edges. Do not synthesize legacy machine/LLM interface nodes from old root mappings.

This removes the v2 data dependency without forcing health, pooled-review, audit, and drift rewrites.

- [ ] **Step 4: Remove authoring and sync fallbacks**

In `blueprint_template.py`, select the version-3 schema from `node_type` only. Preserve the existing renderer and reference handling.

In `_blueprint_syncer.py`, remove imports and calls for `expanded_legacy_blueprint()` and version-2 loading. Build generated contract, owner-interface, and dependency blocks from version-3 declarations already loaded by the repository graph.

- [ ] **Step 5: Verify the common path**

```bash
pytest tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py tests/test_officina_blueprint_template.py tests/test_officina_artifact_health.py tests/test_officina_pooled_blueprint.py skills/skill-maker/tests/test_blueprint_tools.py skills/skill-audit/tests skills/skill-drift/tests -q
```

Expected: PASS with unchanged health/audit public interfaces.

- [ ] **Step 6: Commit the v3-only loader simplification**

```bash
git add src/officina/common/blueprint_inventory.py src/officina/common/blueprint_graph.py src/officina/common/blueprint_template.py skills/skill-maker/_rtx/_blueprint_syncer.py tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py tests/test_officina_blueprint_template.py skills/skill-maker/tests/test_blueprint_tools.py
git commit -m "refactor: make blueprint loading v3 only"
```

---

### Task 2: Make certification advisory in projection and automatic injection

**Files:**

- Rename: `src/officina/common/certification_view.py` to `src/officina/common/certification_status.py`
- Rename after Phase 4 creates it: `src/officina/common/certificate_certification_view.py` to `src/officina/common/certificate_status.py`
- Modify: `src/officina/common/interface_projection.py`
- Modify: `src/officina/common/__init__.py`
- Modify: `references/blueprint/interface-projection.schema.json`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `tests/test_interface_projection.py`
- Modify: `tests/test_officina_certificate_certification_view.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`

**Interfaces:**

- Produces: `CertificationStatus.is_certified(module_id, interface_id, interface_version) -> bool`.
- Preserves: `project_consumer_interfaces()` and the existing generated used-interface block format, adding one boolean per machine interface.

- [ ] **Step 1: Replace gate tests with metadata tests**

Use a test provider with this behavior:

```python
class SelectedCertificationStatus:
    def __init__(self, certified: set[str]) -> None:
        self.certified = certified

    def is_certified(self, module_id: str, interface_id: str, interface_version: int) -> bool:
        return interface_id in self.certified
```

Assert that a certified direct interface, an uncertified direct interface, and an uncertified helper are all present. Assert their `is_certified` values are `True`, `False`, and `False`. Remove the test that expects uncertified projection to raise.

- [ ] **Step 2: Run the projection test and confirm it fails**

```bash
pytest tests/test_interface_projection.py -q
```

Expected: FAIL because projection still calls `check_export()` and rejects false certification.

- [ ] **Step 3: Replace the decision gate with a boolean status**

Define:

```python
class CertificationStatus(Protocol):
    def is_certified(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> bool: ...


class AlwaysUncertified:
    def is_certified(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> bool:
        return False
```

Rename the certificate-backed implementation to `CertificateStatus`. Missing, stale, failed, mismatched, unreadable, or unverifiable certificates return `False`; they do not raise through this projection-facing method.

- [ ] **Step 4: Annotate projected machine interfaces**

Delete `_certify()` and every certification-dependent rejection. In `_project_export()`, add:

```python
"is_certified": certification.is_certified(
    module.node_id,
    export.interface_id,
    export.version,
),
```

Require this boolean in `interface-projection.schema.json`. Keep structural graph, helper-safety, reference, confinement, and projection-size errors unchanged.

- [ ] **Step 5: Connect the existing projection and writer helpers to normal sync**

The current `plan_consumer_interface_updates()` and `apply_consumer_interface_updates()` helpers have no production caller. In `run_sync()`:

1. load the repository graph once;
2. project each `llm-interface` consumer with the certificate-backed status provider, or `AlwaysUncertified` when no certificate store exists;
3. call `plan_consumer_interface_updates()` for the complete projection map before any write;
4. in `--check` mode report stale generated blocks without writing;
5. otherwise call `apply_consumer_interface_updates()` after all projections succeed.

Add an integration test showing that normal sync writes both certified and uncertified interfaces and that a second check-only run reports no drift.

- [ ] **Step 6: Verify projection and automatic Markdown injection**

```bash
pytest tests/test_interface_projection.py tests/test_officina_certificate_status.py tests/test_typed_blueprint_schemas.py skills/skill-maker/tests/test_blueprint_tools.py -q
```

Expected: PASS; `is_certified: false` never removes or blocks an interface.

- [ ] **Step 7: Commit advisory injection behavior**

```bash
git add src/officina/common/certification_view.py src/officina/common/certification_status.py src/officina/common/certificate_certification_view.py src/officina/common/certificate_status.py src/officina/common/interface_projection.py src/officina/common/__init__.py references/blueprint/interface-projection.schema.json skills/skill-maker/_rtx/_blueprint_syncer.py tests/test_interface_projection.py tests/test_officina_certificate_certification_view.py tests/test_officina_certificate_status.py skills/skill-maker/tests/test_blueprint_tools.py
git commit -m "refactor: make certification injection metadata"
```

---

### Task 3: Simplify the dispatcher in place

**Files:**

- Modify: `src/officina/dispatcher/core.py`
- Modify: `src/officina/dispatcher/cli.py`
- Modify: `src/officina/dispatcher/__init__.py`
- Modify: `src/officina/common/machine_interface_binding.py`
- Modify: `src/officina/common/__init__.py`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `tests/test_machine_module_dispatcher.py`
- Modify: `tests/test_officina_dispatcher.py`
- Modify: `tests/test_machine_interface_binding.py`
- Modify: `tests/test_officina_python_machine_interface.py`

**Interfaces:**

- Produces in `core.py`: `DispatchPlan`, `DispatchPlanMetadata`, `is_authorized()`, `compile_dispatch_plan()`, and `run_dispatch_plan()`.
- Preserves: `InvocationError`, `PythonMachineInterface.dispatch()`, and the existing `DispatchCall` field shape.

- [ ] **Step 1: Rewrite existing dispatcher tests around the three-stage pipeline**

Keep the existing test files. Cover:

- canonical export lookup and module-ID rejection;
- same-skill authorization;
- cross-skill authorization requiring both callee admission and the caller's exact target/version dependency;
- authorization failure before argument parsing;
- caller argument syntax/type/arity rejection during compilation;
- no certification parameter or certification failure path;
- dry-run compiling without execution or stdin reads;
- execution using an already compiled plan and always closing bindings.

- [ ] **Step 2: Run the focused tests and confirm they fail on the mixed pipeline**

```bash
pytest tests/test_machine_module_dispatcher.py tests/test_officina_dispatcher.py tests/test_machine_interface_binding.py tests/test_officina_python_machine_interface.py -q
```

Expected: FAIL because `core.py` still falls back to legacy resolution, gates on certification, combines compilation with execution, and accepts split CLI syntax.

- [ ] **Step 3: Keep the existing plan object but give it behavioral names**

Rename `ResolvedInvocation` to `DispatchPlan` and `ResolvedInvocationMetadata` to `DispatchPlanMetadata` in `core.py`. Do not move them to new modules. Preserve descriptor ownership, `metadata()`, `as_payload()`, context-manager cleanup, logical dry-run command rendering, cwd, environment, and `pass_fds`.

Rename the pure binding helpers in place:

```text
parse_caller_invocation()    -> parse_caller_arguments()
compile_gateway_invocation() -> compile_gateway_argv()
```

- [ ] **Step 4: Replace mixed resolution with one compiler**

Delete `_LoadedBlueprint`, portable-legacy loading, `_resolve_machine_module_dispatch()`, pattern/sidecar fallback resolution, and certification arguments from dispatcher code.

Implement `compile_dispatch_plan()` in `core.py` with this order:

```python
graph = load_repository_blueprint_graph(root)
module, export = resolve_machine_export(graph, target, target_version)
if not is_authorized(graph, caller_skill, module, export):
    raise InvocationError(f"caller skill `{caller_skill}` is not authorized for `{target}`")
parsed = parse_caller_arguments(export, args, stdin_requested=stdin_requested)
compiled = compile_gateway_argv(module, export, parsed)
return build_v3_python_plan(module, export, compiled, caller_skill, root)
```

`is_authorized()` returns a boolean. Same-skill calls return `True`. Cross-skill calls return `True` only when the export admits the caller and a caller-owned v3 node/export declares the exact target and version. Compilation accepts only the existing confined `_rtx` Python gateway and rejects `_cx` as unsupported.

- [ ] **Step 5: Extract execution from the existing `dispatch()` body**

Implement `run_dispatch_plan(plan, *, stdin, timeout, capture_output, check, text)` in `core.py` by moving the current `subprocess.run()` block into it. It validates the compiled stdin contract, runs the plan, translates launch failures to `InvocationError`, and closes the plan in `finally`. It does not load a graph, authorize, parse, or compile.

Update `PythonMachineInterface.dispatch()` to preserve its public arguments and `DispatchCall` declarations while internally composing:

```python
target = f"{call.target_skill}.machine.{call.interface}"
plan = compile_dispatch_plan(..., target=target, ...)
return run_dispatch_plan(plan, ...)
```

- [ ] **Step 6: Reduce the CLI to parse, compile, run**

Accept one canonical target positional. Remove `<target-skill> <interface>` shorthand. The control flow is:

```python
plan = compile_dispatch_plan(...)
if args.dry_run:
    with plan:
        print(json.dumps(plan.as_payload(), indent=2, sort_keys=True))
    return 0
stdin = sys.stdin.buffer.read() if args.stdin else None
completed = run_dispatch_plan(plan, stdin=stdin, capture_output=True)
```

Export only `InvocationError`, both plan types, `compile_dispatch_plan()`, and `run_dispatch_plan()` from `officina.dispatcher`.

- [ ] **Step 7: Verify the in-place dispatcher simplification**

```bash
pytest tests/test_machine_module_dispatcher.py tests/test_officina_dispatcher.py tests/test_machine_interface_binding.py tests/test_officina_python_machine_interface.py hooks/tests/test_inject_dispatcher_context.py -q
```

Expected: PASS with no new dispatcher modules and no `DispatchCall` declaration migration.

- [ ] **Step 8: Commit the dispatcher simplification**

```bash
git add src/officina/dispatcher/core.py src/officina/dispatcher/cli.py src/officina/dispatcher/__init__.py src/officina/common/machine_interface_binding.py src/officina/common/__init__.py src/officina/runtime/python_machine_interface.py tests/test_machine_module_dispatcher.py tests/test_officina_dispatcher.py tests/test_machine_interface_binding.py tests/test_officina_python_machine_interface.py
git commit -m "refactor: simplify dispatcher after v3 migration"
```

---

### Task 4: Remove dead migration code and align only active contracts

**Files:**

- Remove: `src/officina/common/interface_injection_migration.py`
- Remove: `tests/test_interface_injection_migration.py`
- Modify: `src/officina/common/__init__.py`
- Modify: `references/skill-standards/skill-guidelines.standard.yaml`
- Regenerate: `references/skill-standards/skill-guidelines.md`
- Modify: `tests/fixtures/standards/skill-guidelines.md`
- Modify: `tests/fixtures/standards/skill-guidelines-source-map.yaml`
- Modify: `tests/fixtures/standards/skill-guidelines-enforcement-ledger.yaml`
- Modify: `tests/test_migrated_standards_fidelity.py`
- Modify: `docs/plans/machine-module-contract/README.md`

**Interfaces:**

- Removes: migration disposition reports and superseded dispatcher/projection gate statements.
- Preserves: the detailed machine-module plan as historical design evidence, historical execution logs, record formats, skill blueprints, and unrelated standards.

- [ ] **Step 1: Delete the migration-only helper**

Delete `interface_injection_migration.py`, its test, and its exports. Task 2's automatic injection is now the only live injection path.

- [ ] **Step 2: Update the two active standard rules that conflict**

In the canonical standard:

- replace the statement that public dispatch and injection require a current certificate with the advisory rule and `is_certified` tag;
- replace the seven legacy dispatcher-role steps with `parse canonical target -> load v3 graph and resolve export -> check authorization -> parse arguments -> compile gateway argv -> build plan -> dry-run or execute`;
- leave the existing `DispatchCall(target_skill=..., interface=...)` example unchanged.

Regenerate only the standard view, source-map entries, digests, and enforcement-ledger entries affected by those blocks. Do not revise unrelated guideline sections or hook rules.

No `.githooks/skill` implementation change is expected: the current blueprint hook does not enforce certification or the legacy seven-step dispatcher sequence. Running the hook remains required verification.

- [ ] **Step 3: Mark the old machine-module design as superseded without rewriting it**

Add one prominent note to `docs/plans/machine-module-contract/README.md` that the post-migration simplification plan supersedes its dispatcher compatibility and certification-gate decisions. Do not edit its detailed phase documents, `execution-log.md`, or `review-log.md`.

Confirm the remaining terms are confined to that explicitly superseded plan:

Search:

```bash
rg -n "certificate.*required|require.*certificate|certification gate|CertificationView|RejectingCertificationView" docs/plans/machine-module-contract references/skill-standards
```

Expected: the active canonical standard contains no gate statement; remaining machine-module-plan hits are historical under the supersession note.

- [ ] **Step 4: Prove the removed runtime paths are gone**

```bash
rg -n "portable_legacy|legacy_compatibility|expanded_legacy_blueprint|interface_injection_migration|RejectingCertificationView|CertificationView|resolve_dispatch_metadata|resolve_dispatch\(|ResolvedInvocation" src skills tests hooks
```

Expected: no live implementation or test reference. Historical documentation may retain clearly marked superseded terms.

- [ ] **Step 5: Run focused and repository checks**

```bash
pytest tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py tests/test_interface_projection.py tests/test_machine_module_dispatcher.py tests/test_officina_dispatcher.py tests/test_officina_python_machine_interface.py tests/validate_standard_documents.py tests/test_migrated_standards_fidelity.py -q
.githooks/skill/check-blueprints
.githooks/pre-commit
git diff --check
git status --short
```

Expected: all checks pass and status contains only the intentional post-migration simplification files.

- [ ] **Step 6: Commit the final cleanup**

```bash
git add src/officina/common/interface_injection_migration.py src/officina/common/__init__.py tests/test_interface_injection_migration.py references/skill-standards tests/fixtures/standards tests/test_migrated_standards_fidelity.py docs/plans/machine-module-contract/README.md
git commit -m "chore: remove post-migration compatibility"
```

---

## Completion evidence

Report:

- proof that live blueprint inputs are version 3 before compatibility removal;
- exact legacy branches and migration helpers removed;
- proof that uncertified interfaces remain injected with `is_certified: false`;
- the final in-place `parse -> compile -> run` dispatcher flow;
- confirmation that `DispatchCall` declarations and health/audit record formats did not change;
- focused test and repository validation results;
- exact final worktree scope.

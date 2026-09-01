# Direct Setup Preflight Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove repository-wide graph construction and unnecessary setup-manager subprocesses from ordinary Famulus MCP calls while preserving authorization and managed-setup semantics.

**Architecture:** Split existing direct invocation authorization from process compilation so MCP can inspect one authorized, invocation-local blueprint snapshot. Build a sparse `RepositoryBlueprintGraph` from that same `DirectBlueprintRepository` and the canonical setup parsers, then reuse it both in MCP classification and in setup-manager `status`/`authorize`; all other manager operations retain full-graph loading.

**Tech Stack:** Python 3.12, pytest, PyYAML, existing Officina Dispatcher and setup-interface-manager runtime.

**Spec:** `docs/plans/2026-09-01-direct-setup-preflight-performance-design.md`

## Global Constraints

- Derive paths only through `DirectBlueprintRepository`; do not enumerate roots or add a catalog, index, cache, daemon, or second path resolver.
- Reuse canonical setup requirement, managed metadata, dependency-order, evaluation, ledger, and atomic authorization machinery.
- Preserve public Dispatcher payloads, manager signatures, error redaction, dry-run behavior, and exactly-once target launch.
- Observe relevant blueprint edits on each invocation and fail closed on every relevant malformed or inconsistent declaration.
- Optimize manager `status` and `authorize` only; keep full-graph loading for `begin`, run/settle/recover, teardown, and `invalidate`.
- Do not commit without explicit user authorization. Preserve unrelated changes in `hooks/hooks.json`, `hooks/tests/test_inject_dispatcher_context.py`, and `plugin.json`.

## File map

- `src/officina/dispatcher/direct_authorization.py`: retain one authorized direct snapshot and compile it through the existing process-binding path.
- `src/officina/dispatcher/direct_runtime.py`: materialize already-authorized metadata with the existing confined runner command and environment.
- `src/officina/blueprints/graph.py`: expose package-private per-export setup validators used by both full and sparse graph assembly.
- `src/officina/dispatcher/direct_setup.py`: load only target ancestry and explicit setup prerequisites, then return the existing sparse graph type plus exact lifecycle classification.
- `src/officina/dispatcher/__init__.py`: export only the MCP/runtime functions that must cross the package boundary; keep the authorization context private.
- `mcp_server.py`: authorize once, bypass setup manager for proven-unmanaged calls, and intercept exact managed lifecycle calls before compilation.
- `skills/setup-interface-manager/_rtx/_setup_manager.py`: select route-local graph loading only after `status`/`authorize` arguments are parsed.
- Existing focused tests: verify equivalence, fail-closed behavior, no inventory/full graph/manager/ledger work, and preserved setup state semantics.
- `docs/officina/dispatcher.md` and `docs/setup.md`: document the direct hot path and retained full-graph operations.

---

### Task 1: Split Direct Authorization from Compilation

**Files:**
- Modify: `src/officina/dispatcher/direct_authorization.py`
- Modify: `src/officina/dispatcher/direct_runtime.py`
- Modify: `tests/test_dispatcher_direct_authorization.py`

**Interfaces:**
- Produces: package-private `AuthorizedDirectInvocation` with `repository`, `caller_modules`, `target_modules`, `source`, `export`, `authorization`, and `diagnostics`.
- Produces: `authorize_direct_invocation(...) -> AuthorizedDirectInvocation` and `compile_direct_invocation(authorized, *, argv, stdin_requested) -> ResolvedInvocationMetadata`.
- Produces: package-private `resolve_direct_export_from_module(module, interface_id, interface_version) -> tuple[DirectBlueprintNode, DirectInterfaceExport]` containing the current source/export/version validation.
- Produces: `materialize_authorized_invocation(authorized, *, argv, stdin_requested) -> ResolvedInvocation` in `direct_runtime.py`.
- Preserves: `resolve_direct_invocation(...) -> ResolvedInvocationMetadata` as authorize-then-compile composition.

- [ ] **Step 1: Add a failing composition-equivalence test**

```python
def test_authorize_then_compile_matches_public_direct_resolution(tmp_path: Path) -> None:
    configuration = _repository(tmp_path)
    expected = resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="caller",
        interface_id="target.interface.run",
        interface_version=1,
        argv=["value"],
        stdin_requested=False,
    )
    authorized = direct_authorization.authorize_direct_invocation(
        configuration=configuration,
        caller_module_id="caller",
        interface_id="target.interface.run",
        interface_version=1,
    )
    actual = direct_authorization.compile_direct_invocation(
        authorized, argv=["value"], stdin_requested=False
    )
    assert actual == expected
```

- [ ] **Step 2: Run the focused test and verify it fails because the two functions do not exist**

Run: `python3 -m pytest tests/test_dispatcher_direct_authorization.py::test_authorize_then_compile_matches_public_direct_resolution -q`

- [ ] **Step 3: Move, without rewriting, the existing resolution prelude into authorization**

```python
@dataclass(frozen=True)
class AuthorizedDirectInvocation:
    repository: DirectBlueprintRepository
    caller_modules: tuple[DirectModule, ...]
    target_modules: tuple[DirectModule, ...]
    source: DirectBlueprintNode
    export: DirectInterfaceExport
    authorization: AuthorizationResult
    diagnostics: tuple[InvocationDiagnostic, ...]


def authorize_direct_invocation(
    *,
    configuration: RepositoryConfiguration,
    caller_module_id: str,
    interface_id: str,
    interface_version: int | None,
    certification_status: Mapping[str, object] | None = None,
    host_caller: bool = False,
) -> AuthorizedDirectInvocation:
    repository = DirectBlueprintRepository(configuration)
    # Retain the existing statements from target parsing through construction
    # of `authorization` and `diagnostics`, but stop before argument parsing.
    return AuthorizedDirectInvocation(
        repository=repository,
        caller_modules=caller_modules,
        target_modules=target_modules,
        source=source,
        export=export,
        authorization=authorization,
        diagnostics=diagnostics,
    )
```

Keep this dataclass out of `__all__` and out of serialized metadata.

- [ ] **Step 4: Move only argument/process compilation into the compiler and preserve the wrapper**

```python
def compile_direct_invocation(
    authorized: AuthorizedDirectInvocation,
    *,
    argv: list[str],
    stdin_requested: bool,
) -> ResolvedInvocationMetadata:
    source = authorized.source
    export = authorized.export
    # Retain the existing route-smoke/argument parsing and process-target block.
    # Replace former locals with fields on `authorized` when constructing the
    # unchanged `ResolvedInvocationMetadata` value.
    return ResolvedInvocationMetadata(
        caller_module_id=authorized.authorization.caller_module_id,
        target_module_id=authorized.authorization.requested_owner_module_id,
        script_interface=export.source_interface_id or "",
        target=export.interface_id,
        pattern=plan.pattern_name or "",
        cwd=authorized.target_modules[-1].blueprint_path.parent,
        command=list(plan.argv),
        stdin=plan.stdin_argument_id is not None,
        python_target=python_target,
        caller_source_id=None,
        terminal_module_id=authorized.authorization.terminal_module_id,
        implementing_source_id=export.source_node_id,
        authorization=authorized.authorization,
        schema_version=6,
        diagnostics=authorized.diagnostics,
    )


def resolve_direct_invocation(
    *,
    configuration: RepositoryConfiguration,
    caller_module_id: str,
    interface_id: str,
    interface_version: int | None,
    argv: list[str],
    stdin_requested: bool,
    certification_status: Mapping[str, object] | None = None,
    host_caller: bool = False,
) -> ResolvedInvocationMetadata:
    return compile_direct_invocation(
        authorize_direct_invocation(
            configuration=configuration,
            caller_module_id=caller_module_id,
            interface_id=interface_id,
            interface_version=interface_version,
            certification_status=certification_status,
            host_caller=host_caller,
        ),
        argv=argv,
        stdin_requested=stdin_requested,
    )
```

- [ ] **Step 5: Factor runtime materialization after compilation**

```python
def _materialize_metadata(
    configuration: RepositoryConfiguration,
    metadata: ResolvedInvocationMetadata,
) -> ResolvedInvocation:
    # Move the existing Python-target validation, runner command, and confined
    # environment construction here without changing any token or field.
    return ResolvedInvocation(
        metadata_value=metadata,
        command=command,
        env=_confined_environment(configuration, metadata.cwd),
    )


def materialize_authorized_invocation(
    authorized: AuthorizedDirectInvocation,
    *,
    argv: list[str],
    stdin_requested: bool,
) -> ResolvedInvocation:
    metadata = compile_direct_invocation(
        authorized, argv=argv, stdin_requested=stdin_requested
    )
    return _materialize_metadata(authorized.repository.configuration, metadata)
```

Make existing `_materialize()` call `authorize_direct_invocation()` followed by `materialize_authorized_invocation()` so both old and MCP paths share the runner construction.

- [ ] **Step 6: Run direct authorization and Dispatcher regression tests**

Run: `python3 -m pytest tests/test_dispatcher_direct_authorization.py tests/test_dispatcher_performance.py -q`

Expected: all pass; existing metadata payload tests remain byte-for-byte equivalent.

---

### Task 2: Share Canonical Per-Export Setup Validation

**Files:**
- Modify: `src/officina/blueprints/graph.py`
- Modify: `tests/test_officina_setup_requirements.py`

**Interfaces:**
- Produces: `_setup_requirement(export_id: str, export: InterfaceExport) -> tuple[tuple[str, int], ...] | None`.
- Produces: `_managed_setup_for_export(export_id: str, export: InterfaceExport, exports: Mapping[str, InterfaceExport]) -> ManagedSetup | None`.
- Preserves: `_setup_requirements()` and `_managed_setup_metadata()` as sorted full-graph aggregators.

- [ ] **Step 1: Add failing invariant tests**

Add fixtures proving that graph loading raises `BlueprintGraphError` when a managed setup references a teardown or verifier in another module, and when two setup exports in one module both declare `setup_management`.

```python
with pytest.raises(BlueprintGraphError, match="same module"):
    load_repository_blueprint_graph(repo)

with pytest.raises(BlueprintGraphError, match="at most one managed setup"):
    load_repository_blueprint_graph(repo)
```

- [ ] **Step 2: Run only the new tests and verify both fail**

Run: `python3 -m pytest tests/test_officina_setup_requirements.py -k 'same_module or one_managed_setup' -q`

- [ ] **Step 3: Extract the existing loop bodies into canonical helpers**

```python
def _setup_requirement(
    export_id: str, export: InterfaceExport
) -> tuple[tuple[str, int], ...] | None:
    declaration = export.export_declaration or {}
    raw = declaration.get("setup_requires_setup_of")
    is_setup = export_id.endswith(".interface.setup")
    # Move the current setup/non-setup, list, entry shape, duplicate,
    # self-reference, existence, and pinned-version checks here unchanged.
    return tuple(parsed) if is_setup else None


def _setup_requirements(exports):
    return {
        export_id: requirement
        for export_id, export in sorted(exports.items())
        if (requirement := _setup_requirement(export_id, export)) is not None
    }
```

Extract `_managed_setup_for_export` analogously, including the existing executable/read-only/no-arguments verifier checks.

- [ ] **Step 4: Tighten lifecycle ownership and owner uniqueness in the shared validator**

Use exact module equality for all lifecycle references:

```python
if target.module_node_id != owner_module_id:
    raise BlueprintGraphError(
        f"{owner_id}: setup_management.{field} target {interface_id!r} must belong to the same module"
    )
```

In `_managed_setup_metadata`, reject a second `ManagedSetup` whose setup export has the same `module_node_id` as an existing owner before validating dependency order.

- [ ] **Step 5: Run canonical setup tests**

Run: `python3 -m pytest tests/test_officina_setup_requirements.py -q`

Expected: all pass, including existing prerequisite version and cycle cases.

---

### Task 3: Build the Route-Local Sparse Setup Graph

**Files:**
- Create: `src/officina/dispatcher/direct_setup.py`
- Create: `tests/test_dispatcher_direct_setup.py`

**Interfaces:**
- Consumes: `DirectBlueprintRepository`, `DirectModule`, `_setup_requirement`, `_managed_setup_for_export`, `RepositoryBlueprintGraph`, and `managed_setup_order`.
- Produces: `DirectSetupProjection(graph: RepositoryBlueprintGraph, lifecycle: tuple[str, Literal["setup", "teardown"]] | None)`.
- Produces: `load_direct_setup_projection(repository, target_modules, target_export) -> DirectSetupProjection`.
- Produces: `load_direct_setup_graph(configuration, target_interface) -> RepositoryBlueprintGraph` for manager subprocesses.

- [ ] **Step 1: Add focused failing tests for the loader boundary**

Tests must build synthetic v6 repositories and assert:

```python
projection = load_direct_setup_projection(
    authorized.repository,
    authorized.target_modules,
    authorized.export,
)
assert projection.graph.setup_requirements == canonical.setup_requirements
assert projection.graph.managed_setups == canonical.managed_setups
assert managed_setup_order(projection.graph, "root.interface.setup") == (
    *managed_setup_order(canonical, "root.interface.setup"),
)
```

Also spy on `Path.iterdir`, `os.walk`, subprocess launch, and write methods so unrelated modules, enumeration, subprocesses, or writes fail the test. Add malformed relevant metadata, missing prerequisite, version mismatch, cycle, symlink, nearest-owner, and unrelated-module read-count cases.

- [ ] **Step 2: Run the new module tests and verify import failure**

Run: `python3 -m pytest tests/test_dispatcher_direct_setup.py -q`

- [ ] **Step 3: Convert only loaded module exports to existing `InterfaceExport` values**

```python
def _module_exports(module: DirectModule) -> dict[str, InterfaceExport]:
    return {
        export_id: resolve_direct_export_from_module(module, export_id)
        for export_id in sorted(module.declaration["exports"])
    }
```

Do not derive filesystem paths here. All module loads go through the supplied invocation-local repository.

- [ ] **Step 4: Load ancestry owners and recursively load explicit prerequisites**

```python
def load_direct_setup_projection(repository, target_modules, target_export):
    # 1. Project exports for target ancestry modules.
    # 2. Find the nearest ancestry module with one managed owner.
    # 3. Recursively parse each setup_requires_setup_of reference, loading the
    #    referenced module by exact ID through repository.load_module().
    # 4. Build module_parents from each loaded ancestry.
    # 5. Construct RepositoryBlueprintGraph with empty non-setup fields and
    #    populated exports/module_parents/setup_requirements/managed_setups.
    # 6. Run managed_setup_order for the selected root and classify exact
    #    setup/teardown IDs by same-module export scan.
    return DirectSetupProjection(graph=graph, lifecycle=lifecycle)
```

If no ancestry declaration contains `setup_management`, return a sparse graph containing the target export and `lifecycle=None` without following unrelated references.

- [ ] **Step 5: Implement the manager convenience loader using the same repository**

```python
def load_direct_setup_graph(
    configuration: RepositoryConfiguration,
    target_interface: str,
) -> RepositoryBlueprintGraph:
    repository = DirectBlueprintRepository(configuration)
    target_module_id, _ = parse_interface_id(target_interface)
    target_modules = repository.load_ancestry(target_module_id)
    target_export = resolve_direct_export(repository, target_modules, target_interface)
    return load_direct_setup_projection(
        repository, target_modules, target_export
    ).graph
```

The `resolve_direct_export` helper must be factored from Task 1's existing direct source/export validation; it must not become another path resolver.

- [ ] **Step 6: Run sparse-loader and canonical equivalence tests**

Run: `python3 -m pytest tests/test_dispatcher_direct_setup.py tests/test_officina_setup_requirements.py -q`

---

### Task 4: Make MCP Preflight Use the Authorized Sparse Projection

**Files:**
- Modify: `src/officina/dispatcher/__init__.py`
- Modify: `mcp_server.py`
- Modify: `tests/test_mcp_setup_preflight.py`

**Interfaces:**
- Consumes: `authorize_direct_invocation`, `materialize_authorized_invocation`, and `load_direct_setup_projection`.
- Preserves: `invoke(...)` result contracts and `_manager_call(...)` isolation/redaction.

- [ ] **Step 1: Rewrite MCP tests around one authorized context**

Add assertions that an unmanaged ordinary call produces exactly `authorize`, `compile`, `launch`; never calls `_manager_call`, full graph loading, or ledger APIs. Managed-ready produces `authorize`, `status`, `manager-authorize`, `compile`, `launch`. Exact managed lifecycle produces `authorize`, `setup-managed` and never compiles.

- [ ] **Step 2: Run the MCP test file and verify the new structural assertions fail**

Run: `python3 -m pytest tests/test_mcp_setup_preflight.py -q`

- [ ] **Step 3: Replace graph-first invocation with authorize/classify/compile**

```python
authorized = authorize_direct_invocation(
    configuration=load_repository_configuration(ROOT / "officina.toml"),
    caller_module_id=caller,
    interface_id=interface,
    interface_version=version,
    host_caller=True,
)
projection = load_direct_setup_projection(
    authorized.repository,
    authorized.target_modules,
    authorized.export,
)
if projection.lifecycle is not None:
    root, operation = projection.lifecycle
    return _setup_managed(operation, root, caller, interface, version)
if projection.graph.managed_setups:
    refusal = _ordinary_preflight(caller, interface, version)
    if refusal is not None:
        return refusal
resolved = materialize_authorized_invocation(
    authorized,
    argv=caller_argv(arguments),
    stdin_requested=arguments.stdin is not None,
)
```

Keep dry-run and direct manager targets on their current path. Compile immediately before launch after atomic manager authorization.

- [ ] **Step 4: Delete obsolete MCP graph helpers and imports**

Remove `_repository_graph`, `_managed_lifecycle`, `_authorize_managed_lifecycle`, `lru_cache`, canonical graph loading, graph export resolution, and graph authorization imports from `mcp_server.py`.

- [ ] **Step 5: Run MCP and Dispatcher tests**

Run: `python3 -m pytest tests/test_mcp_setup_preflight.py tests/test_famulus_mcp.py tests/test_dispatcher_direct_authorization.py -q`

Expected: unmanaged calls prove no manager/full graph/ledger access; existing redaction, required/busy, ready authorization, dry-run, and exactly-once launch cases pass.

---

### Task 5: Use the Sparse Graph in Manager Status and Authorize

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Modify: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`
- Modify: `tests/test_setup_interface_manager_integration.py`

**Interfaces:**
- Consumes: `load_direct_setup_graph(configuration, target_interface)`.
- Preserves: `SetupManager`, `StatusInterface`, `AuthorizeInterface`, public CLI signatures, `evaluate_target`, and `authorize_ready_root`.

- [ ] **Step 1: Add tests that distinguish hot-path and full-graph loading**

Inject spies so `StatusInterface` and `AuthorizeInterface` fail if `_graph_loader` is called, while `BeginInterface` and `InvalidateInterface` still call it. Assert each hot-path call independently invokes the direct loader with the runtime repository configuration and parsed `target_interface`.

- [ ] **Step 2: Run focused manager construction tests and verify failure**

Run: `python3 -m pytest skills/setup-interface-manager/_rtx/tests/test_setup_manager.py -k 'graph_loader or status or authorize' -q`

- [ ] **Step 3: Make manager construction argument-aware without changing public parsers**

```python
def build_graph(self, args: argparse.Namespace):
    context = runtime_dispatch_context(self)
    repo_root = Path(context.repo_root or REPO_ROOT)
    return self._graph_loader(repo_root)

def build_manager(self, args: argparse.Namespace) -> SetupManager:
    # Retain the existing getter, LedgerStore, dispatch closure, and bindings.
    graph = self.build_graph(args)
    return SetupManager(
        graph=graph,
        store=store,
        dispatch=dispatch,
        bindings=self._bindings,
    )

def run(self, args: argparse.Namespace) -> int:
    message = getattr(args, "_manager_usage_error", None)
    if isinstance(message, str):
        return self._malformed(message)
    return self._emit(self.invoke(self.build_manager(args), args))
```

- [ ] **Step 4: Override only the two hot-path graph selections**

```python
class _DirectPreflightInterface(_ManagerInterface):
    def build_graph(self, args: argparse.Namespace):
        context = runtime_dispatch_context(self)
        configuration = load_repository_configuration(
            Path(context.repository_config)
        )
        return load_direct_setup_graph(configuration, args.target_interface)

class StatusInterface(_DirectPreflightInterface):
    operation = "status"

class AuthorizeInterface(_DirectPreflightInterface):
    operation = "authorize"
```

Use the verified `RuntimeDispatchContext.repository_config` field and fail with `ManagerBootstrapError("repository configuration is unavailable")` when it is `None`. Convert `DirectBlueprintError`, `BlueprintGraphError`, and `OSError` to the existing redacted `ManagerBootstrapError` response.

- [ ] **Step 5: Run manager unit and integration tests**

Run: `python3 -m pytest skills/setup-interface-manager/_rtx/tests/test_setup_manager.py skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py tests/test_setup_interface_manager_integration.py -q`

Expected: status remains read-only; authorize remains atomic; required/ready/busy and claims are unchanged.

---

### Task 6: Documentation, Structural Gate, and Performance Evidence

**Files:**
- Modify: `docs/officina/dispatcher.md`
- Modify: `docs/setup.md`
- Modify only if needed for repeatability: `tests/test_dispatcher_performance.py`

**Interfaces:**
- Documents: one authorized snapshot, proven-unmanaged bypass, route-local manager status/authorize, and retained full-graph manager operations.

- [ ] **Step 1: Update canonical documentation concisely**

State that path derivation remains `module_id -> configured root/module segments/blueprint.yaml`; MCP does not catalogue modules; setup-manager remains the sole ledger authority; and only status/authorize use the sparse live graph.

- [ ] **Step 2: Run structural searches**

Run: `rg -n "_repository_graph|_managed_lifecycle|_authorize_managed_lifecycle" mcp_server.py tests`

Expected: no production definitions or calls remain.

Run: `rg -n "load_repository_blueprint_graph" mcp_server.py skills/setup-interface-manager/_rtx/_setup_manager.py`

Expected: none in MCP; one retained default loader in manager for non-hot-path operations.

- [ ] **Step 3: Run focused affected suites**

Run: `python3 -m pytest tests/test_dispatcher_direct_authorization.py tests/test_dispatcher_direct_setup.py tests/test_dispatcher_performance.py tests/test_mcp_setup_preflight.py tests/test_famulus_mcp.py tests/test_officina_setup_requirements.py skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py skills/setup-interface-manager/_rtx/tests/test_setup_manager.py tests/test_setup_interface_manager_integration.py -q`

- [ ] **Step 4: Run repository checks for the changed element**

Run: `python3 scripts/repo_checks.py --help`

Select the existing focused selector for Dispatcher/setup-interface-manager from the displayed live CLI, then run it. Do not guess selector names.

- [ ] **Step 5: Measure controlled latency distributions**

Use the same commands and environment as the baseline design measurements. Warm each route, collect at least 21 samples, and report median and p95 for MCP initialization, first non-dry invocation, warm unmanaged invocation, manager status, and managed-ready status/authorize/target. Do not introduce hard timing assertions until the post-change distribution is observed.

- [ ] **Step 6: Inspect owned diffs and leave the work uncommitted**

Run: `git status --short --branch`

Run: `git diff -- src/officina/dispatcher/direct_authorization.py src/officina/dispatcher/direct_runtime.py src/officina/dispatcher/direct_setup.py src/officina/dispatcher/__init__.py src/officina/blueprints/graph.py mcp_server.py skills/setup-interface-manager/_rtx/_setup_manager.py tests/test_dispatcher_direct_authorization.py tests/test_dispatcher_direct_setup.py tests/test_mcp_setup_preflight.py tests/test_officina_setup_requirements.py skills/setup-interface-manager/_rtx/tests/test_setup_manager.py tests/test_setup_interface_manager_integration.py docs/officina/dispatcher.md docs/setup.md`

Expected: only task-owned changes appear in this diff; unrelated dirty paths remain untouched. Request explicit authorization before any commit.

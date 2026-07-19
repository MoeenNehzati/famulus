# Inventory, Graph, and Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

**Goal:** Discover target v3 blueprint documents deterministically, normalize nested exports,
validate graph/ownership/tool boundaries, and dispatch public export IDs through
their owning module.

**Architecture:** A strict inventory module owns YAML parsing. The graph owns
nodes plus nested export records. A pure binding compiler produces an invocation
plan consumed by dispatcher; graph and dispatcher share validators rather than
reimplementing contract rules.

**Tech Stack:** Python, PyYAML, jsonschema, pytest, dispatcher.

**Primary requirements:** `MOD-007`, `MOD-008`, `BND-003` through `BND-007`,
`ARG-004`, `ARG-009`, `DEP-001` through `DEP-003`, `IO-002`, `IO-004`,
`IO-005`, `INV-001` through `INV-003`, `ADM-003`, `ADM-004`, `ADM-009`,
and the dispatcher portion of `MIG-002`.

## Preconditions and required reading

- Phase 1's completion report is accepted and its schema/standard gate passes.
  This establishes prerequisites; it does not authorize Phase 2.
- Read `../IMPLEMENT.md` and the requirement entries above in
  `../01-decision-ledger.md`.
- Read `Strict blueprint inventory`, `Normalized repository model`, and
  `Dispatcher resolution` in `../03-inventory-graph-and-injection.md`.
- Read `Interface partial application`, `Direct I/O and ownership`, and `Tools
  and helpers` in `../02-machine-module-contract.md`.
- Read only the matching rows in `../05-verification-matrix.md`.

## Phase stop conditions

Stop if Phase 1's normalized schema API is unstable, if graph normalization
would merge runtime and certification authority, if dispatcher resolution would
make an uncertified module export executable, or if strict inventory cannot
fail before yielding. A migration-only validation failure is not an accepted
way to pass this phase. Stale existing declarations are outside this phase gate.

Plan 2 introduces the dispatcher code path before certificate mechanics are
available. Create `src/officina/common/certification_view.py` with the narrow
read-only protocol used by dispatcher and projection. The production default in
this plan rejects every `machine-module` export as `certification-unavailable`;
focused tests inject a deterministic passing/failing view. Existing declarations
are not rewritten or used as target-behavior evidence. Plan 4 replaces the rejecting view
with the certificate-backed implementation; therefore Plan 2 proves resolution
and binding but does not make uncertified module exports publicly usable.

```python
@dataclass(frozen=True)
class CertificationDecision:
    certified: bool
    code: str
    message: str


class CertificationView(Protocol):
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
    ) -> CertificationDecision: ...
```

`code` is a closed stable diagnostic key; `message` is nonempty display text.
The rejecting implementation returns
`CertificationDecision(False, "certification-unavailable", ...)`. Dispatcher
and projection consume the decision unchanged and never infer a reason from a
bare boolean.

## Task 1: Strict inventory API

**Files:**

- Create: `src/officina/common/blueprint_inventory.py`
- Modify: `src/officina/common/__init__.py`
- Test: `tests/test_blueprint_inventory.py`

**Produces:** `BlueprintDocument`, `BlueprintInventoryError`, and
`iter_blueprints(repo_root, *, skip_parse_errors=False)`.

- [ ] Write fixtures/tests for root and hidden-sidecar discovery independent of
  reachability, repository-relative lexical ordering, and path/owner-root data.
- [ ] Write negative tests for duplicate keys, custom tags, non-string keys,
  nonmapping roots, non-JSON YAML values, unreadable files, and multiple errors.
- [ ] Assert strict mode yields nothing before raising the aggregate error;
  diagnostic mode yields valid documents and reports only parse failures.
- [ ] Implement a `yaml.SafeLoader` subclass that rejects duplicate keys and
  normalize recursively to the documented `JsonValue` union.
- [ ] Run `pytest tests/test_blueprint_inventory.py -q` and verify all inventory
  cases pass without graph construction.

## Task 2: Normalize modules and exports in the graph

**Files:**

- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/blueprint_search.py`
- Test: `tests/test_officina_blueprint_graph.py`
- Test: repository relationship fixtures used by `tests/validate_blueprints.py`

**Produces:**

```python
@dataclass(frozen=True)
class MachineInterfaceExport:
    interface_id: str
    version: int
    local_name: str
    module_node_id: str
    declaration: Mapping[str, JsonValue]


@dataclass(frozen=True)
class ExportDependencyEdge:
    source_export_id: str
    target_interface_id: str
    target_version: int


@dataclass(frozen=True)
class HelperEdge:
    source_export_id: str
    local_helper_id: str
    target_interface_id: str
    binding: Mapping[str, JsonValue]


@dataclass(frozen=True)
class ModuleCertificationEdge:
    source_module_id: str
    target_node_id: str


@dataclass(frozen=True)
class RepositoryBlueprintGraph:
    nodes: Mapping[str, BlueprintNode]
    node_edges: tuple[BlueprintEdge, ...]
    machine_exports: Mapping[str, MachineInterfaceExport]
    export_edges: tuple[ExportDependencyEdge, ...]
    helper_edges: tuple[HelperEdge, ...]
    certification_edges: tuple[ModuleCertificationEdge, ...]


def resolve_machine_export(
    graph: RepositoryBlueprintGraph,
    interface_id: str,
    version: int | None = None,
) -> tuple[BlueprintNode, MachineInterfaceExport]: ...
```

- [ ] Refactor target v3 repository graph loaders to consume the strict inventory;
  add `load_repository_blueprint_graph(repo_root) -> RepositoryBlueprintGraph`
  without treating legacy declarations as normalized v3 modules.
- [ ] Add module nodes and nested export indices. Reject module IDs in the
  callable namespace and reject duplicate public export IDs across modules.
- [ ] Preserve every ordinary authored node edge in `node_edges`, including
  skill declarations, LLM dependencies, and behavior-source dependencies.
  `node_edges` remains the authority for node closure and ordinary
  certification order; it is not replaced by export-specific records.
- [ ] Compute certification node dependencies as the union of all authored
  direct module/export edges while retaining interface-scoped edges separately.
  Resolve export versions before projecting targets to owner modules; omit
  same-module certificate self-edges while retaining and cycle-checking the
  runtime export edge.
- [ ] Use `export_edges` only for selected-export runtime authority and cycle
  checks; use `helper_edges` only for bounded helper validation/projection; and
  derive `certification_edges` from `node_edges` plus resolved export edges for
  module-level hash/certificate ordering. Test every traversal independently.
- [ ] Resolve runtime authority as exactly module plus selected-interface direct
  tools. Add tests proving siblings and ordinary transitive edges do not leak.
- [ ] Validate module-shared and export-private ownership, content ownership,
  write authorization, external reads, overlaps, helper target membership,
  helper cycles, versions, platform compatibility, and caller authorization.
- [ ] Keep any raw legacy discovery needed by Phase 5 separate from target v3
  normalization. Do not project modules back into singular interface nodes.
- [ ] Run `pytest tests/test_officina_blueprint_graph.py -q`.

## Task 3: Compile invocation bindings

**Files:**

- Create: `src/officina/common/machine_interface_binding.py`
- Test: `tests/test_machine_interface_binding.py`

**Produces:**

```python
@dataclass(frozen=True)
class ParsedCallerInvocation:
    values: Mapping[str, object]
    stdin_requested: bool


@dataclass(frozen=True)
class CompiledInvocationPlan:
    argv: tuple[str, ...]
    stdin_argument_id: str | None


def parse_caller_invocation(
    export: MachineInterfaceExport,
    argv: Sequence[str],
    *,
    stdin_requested: bool,
) -> ParsedCallerInvocation: ...


def compile_gateway_invocation(
    module: BlueprintNode,
    export: MachineInterfaceExport,
    parsed: ParsedCallerInvocation,
) -> CompiledInvocationPlan: ...
```

- [ ] Add red tests for required/default handling, scalar/list encoding, arity,
  flag/switch behavior, stdin presence, fixed positional/options/switches, and
  deterministic positional-first parsing plus fixed-value insertion into
  implementation positions.
- [ ] Parse the live dispatcher tail: public positionals occupy increasing
  declared implementation positions; an unbounded positional list consumes
  values until a recognized declared option/switch; named options consume their
  declared arity; switches consume no value; unknown names and trailing values
  fail. Apply defaults after parsing and reject `stdin_requested` unless exactly
  one stdin argument is declared.
- [ ] Add negative tests for missing/extra values, type/format failures,
  option/position collisions, fixed/caller collisions, dispatcher-option names,
  ambiguous arity, secret argv/fixed values, and attempts to override fixed
  parameters.
- [ ] Implement type validation and a two-bucket compiler: merge fixed and
  caller positionals by declared implementation position, then emit named
  entries in canonical argument-ID order.
  Named ordering is deterministic for tests but not part of the public contract.
- [ ] Keep stdin bytes outside both functions. The compiled plan names the stdin
  argument only; dispatcher attaches actual bytes at execution. Dry-run needs
  only `stdin_requested` and never reads stdin. Never synthesize shell strings.
- [ ] Run `pytest tests/test_machine_interface_binding.py -q`.

## Task 4: Resolve and dispatch nested exports

**Files:**

- Modify: `src/officina/dispatcher/core.py`
- Create: `src/officina/common/certification_view.py`
- Modify: `src/officina/runtime/python_machine_interface.py`
- Modify: `src/officina/runtime/python_machine_interface_runner.py`
- Test: `tests/test_officina_dispatcher.py`
- Test: `skills/list-manager/tests/test_python_machine_interfaces.py`

**Consumes:** `resolve_machine_export()`, `parse_caller_invocation()`, and
`compile_gateway_invocation()`.

- [ ] Add failing tests proving a public export resolves to its module gateway,
  a module ID is rejected, access control is export-local, caller cwd is
  irrelevant, fixed values cannot be supplied, and global dispatcher arguments
  remain dispatcher-owned.
- [ ] Add index-construction tests proving schema/security/binding/reference/ownership
  failures prevent all public dispatch. Use a private test runner for malformed
  gateway unit tests rather than a dispatcher bypass.
- [ ] Require an injected `CertificationView` for machine-module dispatch.
  Assert the production placeholder rejects all such public calls and passing
  and failing target-fixture views exercise the gate.
- [ ] Update declared Python `DispatchCall` resolution to pin nested export IDs
  and versions while preserving the no-raw-dispatcher runtime rule.
- [ ] Support the Python gateway path only and preserve descriptor-safe loading.
  Reject command gateways in v3; their future design is reserved for tracked
  `_cx/` executables.
- [ ] Wire the exact call chain: dispatcher tail ->
  `parse_caller_invocation()` -> `compile_gateway_invocation()` -> prepend the
  gateway runner and dispatcher-owned `args_prefix` -> attach stdin bytes only
  for execution -> execute. Dry-run stops before reading stdin or running the
  gateway.
- [ ] Run `pytest tests/test_officina_dispatcher.py
  skills/list-manager/tests/test_python_machine_interfaces.py -q`.

## Task 5: Update template, hashing, and health projections

**Files:**

- Modify: `src/officina/common/blueprint_template.py`
- Modify: `src/officina/common/artifact_health.py`
- Test: `tests/test_officina_blueprint_template.py`
- Test: `tests/test_officina_artifact_health.py`

- [ ] Render module templates with a nonempty export example and caller-contract
  documentation; stop selecting the v3 machine-interface schema.
- [ ] Hash the entire module document/content once. Associate nested export IDs
  with the module subject and compute module dependency hashes from the node
  dependency union without changing export runtime authority.
- [ ] Resolve and hash the conformance manifest plus the transitive closure of
  every contract/schema/format reference into a canonical locator/digest map.
  Include that map and referenced bytes in module currentness; add changed,
  moved, missing, symlinked, and restored-reference tests.
- [ ] Add tests proving any export contract or shared content change invalidates
  module state and all exports, while interface versions change only when
  authored.
- [ ] Run `pytest tests/test_officina_blueprint_template.py
  tests/test_officina_artifact_health.py -q`.

## Task 6: Phase gate

- [ ] Run `pytest tests/test_blueprint_inventory.py
  tests/test_typed_blueprint_schemas.py tests/test_officina_blueprint_graph.py
  tests/test_machine_interface_binding.py tests/test_officina_dispatcher.py
  tests/test_officina_blueprint_template.py
  tests/test_officina_artifact_health.py
  skills/list-manager/tests/test_python_machine_interfaces.py -q`.
- [ ] Validate the target v3 fixtures and graph. Repository-wide failures caused
  only by stale existing declarations are recorded separately and do not fail
  this phase.
- [ ] Run `git diff --check` before Plan 3.

## Phase completion evidence

Report inventory/graph/binding/dispatcher APIs produced, requirement IDs,
focused and combined test commands with counts, certification-view behavior,
repository validation output, and exact worktree scope. Stop for review before
Plan 3.

# Direct Setup Preflight Performance Design

Status: Reviewed; ready for implementation planning

## Goal

Remove repository-wide graph loading from ordinary Famulus MCP invocation and
from setup-manager `status` and `authorize`. Runtime work must depend only on
the requested module ancestry and, when relevant, its explicit setup
prerequisite closure.

Current measurements attribute roughly 4.2--6.4 seconds per ordinary call to
the setup-manager status subprocess rebuilding the complete graph. The first
non-dry MCP call also builds that graph. Direct route resolution itself costs
roughly 6--16 ms, and the measured target costs roughly 0.2--0.3 seconds.

## Runtime constraints

- Derive blueprint paths from module IDs through the existing
  `DirectBlueprintRepository`; do not inventory module directories.
- Preserve current host, namespace, terminal-export, version, argument, and
  process-binding checks.
- Resolve and authorize the target once per MCP invocation. Setup
  classification must use that same loaded ancestry.
- Keep ledger reads, locks, claims, recovery, and settlement inside
  setup-interface-manager.
- Keep public `status` read-only and `authorize` atomic.
- Observe relevant blueprint edits on the next invocation.
- Fail closed on malformed, missing, ambiguous, symlinked, unauthorized, or
  version-inconsistent relevant declarations.
- Add no catalog, persisted/generated index, graph cache, daemon, MCP-side
  ledger access, or second path resolver.

## Existing machinery to reuse

### Direct route resolution

Extract the existing target-resolution prelude in `direct_authorization.py`
without changing its behavior. It already loads:

- caller and target ancestry through `DirectBlueprintRepository`;
- the terminal export and selected behavioral source;
- the source interface and requested version;
- every namespace and terminal authorization filter.

Add one private `AuthorizedDirectInvocation` value in that module. It retains
the invocation-local repository, loaded caller and target modules, selected
source/export values, and existing `AuthorizationResult`. It is not added to
public payloads or `ResolvedInvocationMetadata`.

Keep `resolve_direct_invocation()` as the public composition:

```text
authorize exact direct invocation -> compile arguments/process binding
```

This provides one authorized snapshot for setup classification and permits an
authorized Markdown lifecycle interface to be intercepted before process
binding is compiled.

### Setup metadata and state

Factor the per-export parsing currently used by `_setup_requirements()` and
`_managed_setup_metadata()` so canonical full-graph construction and direct
setup loading call the same validators. Do not implement another prerequisite
parser, cycle algorithm, lifecycle validator, or setup classifier.

The direct loader recursively resolves only referenced setup exports through
the same direct export-resolution helper. It constructs a sparse existing
`RepositoryBlueprintGraph` containing only the fields already consumed by
setup evaluation:

- `exports`;
- `module_parents`;
- `setup_requirements`;
- `managed_setups`.

Continue using `ManagedSetup`, `managed_setup_order()`, `evaluate_target()`,
`authorize_ready_root()`, and the current manager/state-transition functions.
Do not add a parallel setup projection model.

## Canonical lifecycle invariants

Direct reverse lifecycle lookup is impossible when teardown or verifier
exports may live in an arbitrary sibling module. Enforce two narrow canonical
invariants:

1. a managed setup, its teardown, and both verifiers are exports of the same
   module;
2. a module declares at most one managed setup owner.

The direct loader scans only the already-loaded module's `exports` mapping to
identify exact setup and teardown lifecycle calls. Canonical graph validation
and the direct loader both enforce the invariants; JSON Schema alone cannot
enforce cross-reference locality. The current repository has no production
`setup_management` declarations, so only synthetic fixtures require migration.

## MCP flow

For a non-manager invocation:

1. build the authorized direct invocation once;
2. inspect its loaded target ancestry for the nearest managed owner;
3. if no ancestry module contains managed setup metadata, compile and launch
   immediately without invoking the manager or touching its ledger;
4. if the exact target is managed setup or teardown, return the existing
   redacted `setup_managed` route without compiling or launching it;
5. otherwise call manager `status`; return the existing required/busy response,
   or call atomic `authorize` immediately before compiling and launching the
   original target.

Dry runs and direct manager targets retain their existing exemptions. Remove
MCP's `_repository_graph()`, `_managed_lifecycle()`, and
`_authorize_managed_lifecycle()` graph-based path.

## Setup-manager scope

Optimize only `status` and `authorize` in this change. They are the ordinary
invocation hot path and the measured source of multi-second latency.

After arguments are parsed, each manager subprocess independently loads a live
route-local sparse graph from the exact repository configuration and target
interface. It does not accept an MCP-computed projection. `SetupManager`,
`evaluate_target()`, `authorize_ready_root()`, and public manager signatures
remain unchanged.

Other manager operations, including `begin`, run/settle/recover, teardown, and
`invalidate`, retain canonical full-graph loading in this change. Invalidation
currently performs global reverse-dependent discovery; replacing that behavior
with ledger claims would be a separate semantic change and is not needed to
make ordinary calls fast.

Do not initially combine `status` and `authorize`. Measure managed-ready calls
after graph loading is removed, then consider one atomic preflight only if the
remaining subprocess cost is material.

## Verification

Reuse existing dispatcher, setup-evaluation, setup-manager, MCP preflight, and
canonical setup-validation tests. Add only focused coverage for the new seams:

- direct authorization followed by compilation remains payload-equivalent;
- setup loading uses `DirectBlueprintRepository` and performs no enumeration,
  writes, or subprocess launch;
- one unrelated module does not change relevant read/probe counts;
- a proven-unmanaged MCP call performs no full-graph load, manager subprocess,
  or setup-ledger access;
- manager `status` and `authorize` perform no full-graph load;
- nearest owner and a synthetic prerequisite closure match one canonical
  full-graph result;
- same-module lifecycle and one-owner-per-module invariants fail closed;
- missing prerequisites, version mismatch, cycle, malformed relevant metadata,
  and symlink paths fail closed;
- existing required/ready/busy, atomic claim, redaction, lifecycle interception,
  and exactly-once launch tests continue to pass.

Run controlled before/after benchmarks for:

- MCP initialization and first non-dry invocation;
- warm unmanaged invocation;
- manager status;
- managed-ready status/authorize/target flow.

Record median and p95 over at least 21 measured samples after warm-up. Promote
latency values to hard policy only after observing the optimized distribution;
structural no-inventory/no-manager guarantees remain normal-CI requirements.

## Scope

Expected changes are limited to direct Dispatcher authorization/blueprint
helpers, the MCP adapter, route-local setup metadata loading, narrow lifecycle
validation, setup-manager status/authorize construction, focused tests, and the
canonical setup documentation. No other manager operation, persisted format,
installer, hook, certification, or plugin-persistence behavior is included.

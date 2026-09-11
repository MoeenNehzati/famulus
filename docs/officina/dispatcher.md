# Dispatcher

## Operating model

This document is the operational reference for the version-6 dispatcher. The
dispatcher is a bounded router and authorization checker: it resolves one
declared interface, checks the relevant blueprint policies, compiles the
declared process binding, and launches the gateway. It does not repair or
synchronize repository state.

For the end-to-end role of dispatch, see [Getting Started](getting-started.md).
[Blueprints](blueprints.md) explains the declarations Dispatcher consumes.

For each invocation, Dispatcher follows one bounded path:

```text
resolve -> authorize -> bind -> execute
```

It resolves the canonical caller and target, authorizes every crossed module
boundary, binds supplied arguments and stdin to the declared process grammar,
then executes the selected gateway. It mechanically enforces that invocation
grammar. It does not generally verify gateway output against a declared output
schema; the producer, consumer, or owning adapter must perform that validation
where the contract requires it. See [Schemas](schema.md).

MCP execution and every actual `PythonMachineInterface.dispatch()` insert the
dynamic setup check described below between authorization and binding.

## Invocation

The shared `famulus_dispatcher` MCP server resolves the current plugin package and invokes
Dispatcher internally. Its `invoke` tool accepts the generated projection:

```json
{"caller":"<top-level-skill>","interface":"<module>.interface.<name>","version":1,"arguments":{"positionals":[],"options":{},"stdin":null},"dry_run":false}
```

Set `dry_run` to `true` to authorize and compile without launching the gateway. The
result is JSON containing the canonical caller and target IDs, selected source
interface, compiled argv, working directory, Python entrypoint, stdin decision,
and warnings. Failures use the tool's structured dispatcher result.

The host caller must be a discoverable top-level skill. Runtime code may make
nested calls using its immediate canonical module ID through the programmatic
dispatcher API. A host cannot claim a private child such as `daily-plan._rtx`
as its identity.

## Repository configuration

The MCP runtime supplies one exact absolute
`officina.toml` path from the selected plugin or checkout. The
configuration file's directory is the repository root and its ordered
`modules.roots` entries are the only blueprint lookup roots:

```toml
schema_version = 1

[modules]
roots = ["skills", "src/officina"]
```

Dispatcher does not consult `AI`, the current directory, parent directories,
or `PYTHONPATH` to discover a repository. Missing, duplicated, escaping,
symlinked, or malformed configured roots fail closed.

## Direct lookup

Module identity comes from its path beneath a configured root. For example:

```text
skills/list-manager/blueprint.yaml      -> list-manager
skills/list-manager/_rtx/blueprint.yaml -> list-manager._rtx
```

A dotted child is reachable only when every parent registers the next segment
in `children`. External traversal additionally requires an explicit
`namespace_exports` route whose nonempty `surface.only` mapping contains the
exact descendant interface and version. Version 6 has no facade aliases and
no `surface.all` form.

For one request, dispatcher reads only:

1. `officina.toml`;
2. the caller and target ancestry blueprints;
3. the terminal target module blueprint; and
4. the selected behavioral-source blueprint.

It does not list module directories or read unrelated blueprints. Repository
size therefore does not determine route-resolution work.

The lookup rule remains direct and catalog-free:
`module_id -> configured root/module segments/blueprint.yaml`. The MCP server
does not catalogue modules, generate an index, or add another path resolver.

## Managed setup preflight

An ordinary non-dry MCP call checks setup after authorizing its exact route.
Every actual `PythonMachineInterface.dispatch()` does the same for its own
target, including calls in standalone chains. Setup classification reuses the
invocation-local repository and already loaded target ancestry; it does not
authorize the route again. A public `.interface.setup` export is managed
automatically. An unmanaged target trivially proceeds without a manager call
or ledger access. Metadata inspection, dry-run, and offline resolution do not
perform these live checks.

When a managed `.interface.setup` is required, the direct setup loader follows
only explicit `setup_requires_setup_of` references and builds the sparse fields
consumed by the existing setup evaluator. Exact managed setup and teardown
interfaces are intercepted before process-binding compilation. An ordinary
managed target still requires manager `status`, followed by atomic `authorize`
when ready, before the original target is compiled and launched.

This is a per-call check, not static traversal of `uses_interfaces`. A declared
dependency that is never called does not trigger setup. Calls whose caller or
terminal target module is exactly `setup-interface-manager._rtx` bypass this
gate so manager control routes, actions, and verifiers cannot recurse into it;
prefix lookalikes do not bypass it. MCP calls carrying `setup_flow_id` retain
their separate exact-flow authorization path.

A nested setup refusal prevents that target from launching and travels through
the existing private process diagnostic channel. Each process boundary
prepends its canonical interface ID to an outer-to-inner `call_path`. At the
MCP root, the existing validation checks the signal and produces the existing
setup continuation or error result. `setup_required` preserves the outer MCP
caller/interface/version as the continuation; `setup_busy` exposes only the
active flow identity and authorizes no action. Ordinary dispatcher and
application errors keep their existing behavior. Standalone chains still gate actual dispatches,
but only MCP renders setup continuations.

The `manager` object returned for `setup_required` or `setup_managed` is a
complete `famulus_dispatcher.invoke` request and must be used unchanged.

The outer process may already have done work before reaching a blocked nested
call. Retrying the outer invocation can repeat that work; this is not a promise
of transitive preflight or exactly-once execution.

Setup-interface-manager remains the sole authority for ledger reads, locks,
claims, recovery, and settlement. Its `status` and `authorize` routes load the
sparse setup closure for the requested target. A `begin setup` call with
no active flow loads the closure rooted at its requested setup interface.
During an active setup flow, `run-markdown`, `run-python`, `settle`, `recover`,
`recover-busy`, and `authorize-markdown-call` load the closure rooted at the
flow's ledger-recorded root setup interface. Begin calls while a flow is active,
teardown operations (including `teardown-all`), invalidation, and flow-bound
lifecycle calls without an active setup flow retain canonical repository-wide
graph loading. Teardown and invalidation need broader lifecycle topology; the
other fallbacks fail closed without assuming setup-flow state.

## Authorization

For a policy owned by module `y`, caller `x` is admitted when `x == y`, the
policy sets `allow_all_modules: true`, or the ancestry of `x` intersects the
resolved `allowed_callers`. Naming a module admits that module and its
registered descendants; it does not admit its parent or siblings.

Only target-side namespace boundaries below the caller/target lowest common
ancestor are crossed. At every crossed boundary, dispatcher evaluates the
namespace policy and any interface-specific restriction. After a gate admits
the call, that namespace owner becomes the immediate caller for the next hop.
The terminal child export is always an authority ceiling.

Source `uses_interfaces` declarations are static validation and certification
facts. They do not independently grant runtime authority.

## Execution boundary

Dispatcher accepts only a process-bindable exported interface. It compiles
arguments and stdin against the selected source declaration before launch.
Python gateways run through the selected interpreter and a confined importer
rooted at the selected module. Ambient import paths that expose configured
repository modules are removed.

Ordinary gateway stdout, stderr, and exit status pass through unchanged;
private setup refusals follow the diagnostic path described above. `--dry-run`
does not read stdin or launch the gateway.

## Failures and warnings

Relevant malformed or unsafe state is an error, including an invalid canonical
ID, missing registration, missing namespace route, ambiguous top-level module,
access denial, interface/version mismatch, unsafe source path, invalid process
binding, or repository-configuration error. Dispatcher reports the authored
condition and does not repair it.

Unrelated malformed modules are neither errors nor warnings because they are
not read. Repository-wide validators own complete topology and schema checks.

Certification currentness is advisory during dispatch. Missing, stale,
expired, malformed, or unavailable status produces a warning but cannot grant
or deny permission. Certification and drift tools remain authoritative for
reviewing and signing repository state outside the live route.

## Process and performance

Fresh-process measurements include interpreter and module startup. They are
not measurements of authorization alone. The repository performance gates
require warm in-process resolution below 50 ms median, and fresh CLI resolution
below a median that `_fresh_cli_budget_ms` sets per OS family: 125 ms on Linux,
150 ms on macOS, 175 ms on Windows. The three differ because hosted process
creation does, and treating that difference as a dispatcher regression would
only teach the gate to be ignored. Every gate is a median; no percentile is
enforced, since a single contended sample is what a percentile would catch and
host contention is not what these tests measure. They are regression budgets
rather than portable timing guarantees; ordinary operating-system file cache
and host-load variation is expected.

## What dispatcher never does

The live routing path does not build repository-wide graphs, inventories,
snapshots, catalogs, caches, or manifests; inspect Git; derive or repair
certificates; synchronize blueprints; contact a network; acquire routing locks;
or write routing state. The sparse setup projection described above is limited
to the setup closure selected by one requested target or ledger-recorded
setup-flow root; it is not a repository inventory. Selecting that graph grants
no runtime authority. Repository-wide operations belong to explicit validators
or the setup-manager fallback cases described above.

## Related documentation

- [Overview](README.md)
- [Getting Started](getting-started.md)
- [Architectural principles](architectural-principles.md)
- [Blueprints](blueprints.md)
- [Schemas](schema.md)
- [Certification and drift](certification_and_drift.md)
- [Blueprint schemas](../../references/blueprint-schema/README.md)

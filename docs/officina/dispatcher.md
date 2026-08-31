# Dispatcher

This document is the operational reference for the version-6 dispatcher. The
dispatcher is a bounded router and authorization checker: it resolves one
declared interface, checks the relevant blueprint policies, compiles the
declared process binding, and launches the gateway. It does not repair or
synchronize repository state.

## Invocation

The shared `famulus` MCP server resolves the current plugin package and invokes
Dispatcher internally. Developers may also run the module form directly from
an explicitly configured checkout. Neither route discovers a checkout from the
current directory.

The module-form developer/debug surface is:

```bash
python -m officina.dispatcher.cli --caller-skill <top-level-skill> \
  <module>.interface.<name> [arguments...]
```

Use `--dry-run` to authorize and compile without launching the gateway. The
result is JSON containing the canonical caller and target IDs, selected source
interface, compiled argv, working directory, Python entrypoint, stdin decision,
and warnings. `--error-format json` produces a stable structured failure.

The host caller must be a discoverable top-level skill. Runtime code may make
nested calls using its immediate canonical module ID through the programmatic
dispatcher API. A host cannot claim a private child such as `daily-plan._rtx`
as its identity.

## Repository configuration

The MCP runtime and development activation supply one exact absolute
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

Gateway stdout, stderr, and exit status pass through unchanged. `--dry-run`
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

The live path does not build repository graphs, inventories, snapshots,
catalogs, caches, or manifests; inspect Git; derive or repair certificates;
synchronize blueprints; contact a network; acquire routing locks; or write
routing state. Those operations belong to explicit offline tools.

## Related documentation

- [Architecture](architecture.md)
- [Skill blueprints](skill-blueprints.md)
- [Certification and drift](certification_and_drift.md)
- [Blueprint schemas](../../references/blueprint-schema/README.md)

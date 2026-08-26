# Setup Interface Dependency Implementation Plan

**Goal:** Add explicit, dependency-ordered setup interfaces without changing
existing skill behavior.

**Design:** `docs/plans/2026-08-21-skill-setup-lifecycle-design.md`

## Task 1: Extend blueprint validation

- Add `setup_requires_setup_of` to the v6 module export schema.
- Require it on `.interface.setup` exports and forbid it elsewhere.
- Validate public setup targets, exact versions, duplicates, and cycles.
- Store the validated relation separately from `uses_interfaces`.

## Task 2: Provide setup ordering

- Add a small `setup_order` graph helper.
- Return dependencies first and the requested setup interface last.
- Deduplicate diamonds and handle long chains iteratively.
- Reject unknown roots and cycles with useful errors.

## Task 3: Declare existing setup behavior

- Export `install-assistant-tools.interface.setup` as an alias of its default
  source interface with no prerequisite.
- Export `connect-google.interface.setup` as an alias of its default source
  interface with no setup prerequisite.
- Export setup aliases for `cloud-files`, `online-calendar`, and `list-manager`, each
  requiring Google setup.
- Do not edit the functional instruction bodies.

## Task 4: Generate the setup contract

- Render `Setup Requires Setup Of` separately from `Uses Interfaces`.
- Render the complete dependency-first order through `setup_order`, including
  transitive prerequisites once and the requested setup interface last.
- Regenerate only canonical blueprint-derived artifacts.

## Task 5: Verify

- Test schema acceptance and semantic rejection cases.
- Test real repository chains, diamond deduplication, cycles, unknown roots,
  repeated calls, and a long chain.
- Verify every setup export aliases its existing default source interface.
- Run focused graph, schema, generator, dispatcher, and repository checks.
- Confirm generated blueprints are in sync.

## Completion criteria

- Setup prerequisites are explicit and versioned.
- Setup order is deterministic and dependency-first.
- Setup dependencies remain distinct from runtime interface dependencies.
- Existing setup behavior is unchanged.
- No receipt, teardown, recovery, lifecycle coordinator, or hook machinery is
  introduced.

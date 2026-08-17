# Officina relocation closure transaction

**Status:** Accepted for implementation

**Date:** 2026-08-17

**Supersedes:** The closure and validation portions of
`2026-08-16-blueprint-aware-relocation-tool-design.md`

## Objective

Make one relocation invocation produce the complete deterministic repository
change implied by an approved manifest. The command must move sources, update
their addresses and blueprint facts, synchronize derived artifacts, and prove
that the projected repository is closed before it writes to the real worktree.

Certification remains a separate post-commit operation. It should confirm the
result, not discover missing mechanical relocation work.

## Problem

The existing engine projects moves, textual renames, declared ownership
transfers, package catalogs, and standard digests. Its blueprint behavior is
limited to changes explicitly encoded in the manifest. Its projected-tree
validator does not load the resulting blueprint graph, derive source
dependencies, inspect route-smoke loads, update the certification basis, or
synchronize generated blueprint artifacts. The report also emits
`unresolved_references: []` without calculating unresolved references.

The Officina source relocation consequently required a later manual update for
README-only package initializers loaded by certification bootstrap. Other
certification findings exposed unrelated pre-existing metadata and certifier
defects; the relocation engine must not claim ownership of those.

## Design rule

The engine automates deterministic closure and rejects architectural ambiguity.

A fact is deterministic when the projected repository admits one valid result,
such as a moved gateway path, a uniquely owned imported source, or a generated
manifest produced by an existing canonical generator. Registration, authority,
and trust decisions remain explicit manifest policy.

## One invocation, one real mutation

The command retains read-only preflight by default and `--apply` for mutation.
Both modes execute the same transaction:

1. Load and validate the manifest.
2. Build the current in-memory relocation projection.
3. Materialize that projection in an isolated shadow repository.
4. Derive and apply permitted closure changes in the shadow repository.
5. Validate the closed shadow repository.
6. Reconcile permitted shadow changes into the in-memory `ChangeSet`.
7. Emit one complete report.
8. On `--apply`, publish that exact `ChangeSet` once to the real worktree.

No closure command runs against the real worktree before publication. A failed
preflight therefore leaves it unchanged.

## Manifest authority

The manifest continues to declare moves, typed identity renames, ownership
transfers, exact rewrites, package catalogs, and caller additions. It also
declares the disposition of every new package boundary created by a move:

- `existing-module`: ownership moves into an already registered module;
- `registered-module`: the manifest supplies the approved new module identity
  and boundary;
- `unregistered-package`: the package is intentionally not registered.

The engine must reject a new package boundary with no disposition. It must not
infer whether a package should become a module.

Caller additions remain explicit authorization. The engine may detect that an
authorization is required, but it may not grant it unless the manifest already
authorizes the exact caller and interface.

The manifest schema advances to version 2. The repository acceptance manifest
is migrated to version 2; the engine does not maintain a parallel legacy
execution path.

## Shadow repository

The shadow repository represents the actual current worktree plus the projected
change, including relevant unrelated dirty files. It excludes Git metadata,
worktrees, virtual environments, caches, dependency directories, build output,
certificates, and pooled reviews. Regular-file bytes and executable modes are
preserved.

The shadow root exists only for preflight. Canonical Officina loaders and
generators run against that root so the relocation engine does not duplicate
their graph, schema, routing, or generated-artifact logic.

Before each generator or validator runs, the engine snapshots the shadow file
inventory and hashes. Only declared closure outputs may change. An unexpected
write is a preflight failure.

## Deterministic closure

Closure producers run in this order.

### 1. Blueprint graph

Load the complete projected schema-v6 repository graph. This validates module
and behavioral-source identities, ownership, sidecar locators, interfaces,
exports, callers, and declared edges. Graph failure is reported without trying
later producers.

### 2. Affected source dependencies

Affected behavioral sources are those whose content, gateway, blueprint,
imports, or declared identities changed in the projection. Their Python process
interfaces are traced in one route-smoke batch using the existing isolated,
no-normal-arguments runner. The trace must not invoke ordinary interface effects.

For each loaded repository path:

- a direct input needs no change;
- an already reachable declared dependency needs no change;
- a path with one registered behavioral-source owner produces one direct source
  dependency with the owner’s exact version and sidecar locator;
- a package initializer owned by an already reachable module is accepted by the
  existing module-package rule;
- zero or multiple eligible owners produce an unresolved reference.

Generated dependency reasons use one stable factual template naming the loaded
source path. The engine does not generate exports or caller authority.

### 3. Certification bootstrap basis

Run an empty-target baseline trace for the certification route-smoke harness.
Any repository path loaded before target isolation must already be owned by the
certification basis. A missing path may be added automatically only when it is a
README-only package initializer containing one module docstring and no
executable statements. Missing substantive code is a trust decision and is
reported as unresolved.

Moved existing basis paths are rewritten through the typed path mapping.

### 4. Canonical generated artifacts

Run the existing blueprint synchronizer inside the shadow repository. Its
permitted outputs are:

- generated contract and interface blocks in affected `SKILL.md` files;
- generated used-interface blocks;
- `references/blueprint/runtime_dependencies.json`.

The relocation engine absorbs the exact generated bytes. It does not reproduce
the generator’s logic.

### 5. Fixed-point confirmation

Reload the graph and repeat the closure sequence once. The second sequence must
produce no further changes. Any additional change or repeated state is a
non-convergent-closure failure. Every closure producer must therefore be
idempotent.

## Validation

After closure converges, preflight requires:

- every declared target exists and every retired active address is absent;
- Python files parse and declared package initializers remain README-only;
- the complete schema-v6 blueprint graph loads;
- every affected route-smoke path maps to direct input, declared dependency,
  reachable package initializer, or certification basis;
- generated blueprint artifacts are in sync in check-only mode;
- standard digest closure is stable;
- repository blueprint validators pass against the shadow root;
- no validator changes the shadow repository;
- the second full relocation preflight reports zero changes.

The relocation command does not run the full repository test suite or issue
certificates. Focused behavioral tests and certification run after the source
change is committed.

## Reconciliation and publication

Only these shadow differences may enter the final `ChangeSet`:

- manifest-declared moves and rewrites;
- blueprint module and behavioral-source documents;
- the certification-basis root manifest;
- canonical generated blueprint artifacts;
- declared package initializer catalogs;
- refreshed standard digests.

Any other shadow difference fails preflight. Before publication, every real
input is compared with the byte snapshot used to create the projection. A
concurrent change aborts the operation. Successful application uses the existing
mode-preserving temporary-file replacement and ordered deletion mechanism.

## Report contract

The stable report contains:

- declared moves;
- direct writes and deletes;
- derived blueprint changes;
- derived dependency additions;
- certification-basis changes;
- generated-artifact changes;
- refreshed digests;
- validation results;
- required architectural decisions;
- unresolved references.

`required_architectural_decisions` and `unresolved_references` are calculated
collections. `--apply` is rejected unless both are empty. The report never uses
an unconditional empty placeholder.

## Performance boundary

The graph is repository-wide, but dynamic tracing is limited to affected
process interfaces and runs in one child batch. Canonical generators run once
plus one no-change confirmation. No full pytest run, signing operation,
installation, or activation occurs inside relocation preflight.

The expected user-facing workflow is therefore:

1. one relocation preflight;
2. one relocation application;
3. focused tests and one commit;
4. one certification run for the committed affected nodes.

## Failure behavior

Preflight fails with exact paths and owners when:

- a new package boundary has no declared disposition;
- a loaded path has no unique owner;
- an import requires undeclared authority;
- substantive code would need certification-basis trust;
- a canonical generator changes an undeclared file;
- closure does not converge;
- validation fails;
- the real worktree changes between projection and publication.

No failure path writes to the real worktree.

## Acceptance tests

The implementation must prove:

1. A moved source and sidecar produce correct gateway, content, source,
   dependency, and generated-manifest addresses in one `ChangeSet`.
2. A uniquely owned route-smoke import creates its required dependency without
   a hand-authored YAML patch.
3. Missing README-only certification bootstrap initializers are added to the
   basis, while substantive files are rejected.
4. A new package with no boundary disposition fails before writing.
5. Required caller authority is reported but never granted implicitly.
6. Unexpected generator or validator writes fail preflight.
7. A late graph, route, or validation failure leaves every real byte and mode
   unchanged.
8. Applying the accepted change preserves unrelated dirty files.
9. A second preflight is a zero-change fixed point.
10. `unresolved_references` reports a real unmapped-path fixture.
11. The Officina source-relocation acceptance manifest requires no manual
    certification-basis or generated-blueprint adjustment.

## Non-goals

- Certifying, signing, committing, installing, activating, or pushing.
- Registering modules or granting authority without manifest approval.
- Refactoring implementation bodies or creating compatibility facades.
- Repairing unrelated pre-existing blueprint or certifier defects.
- Treating all imported implementation files as trusted certification basis.
- Replacing the canonical blueprint graph, route tracer, synchronizer, or
  repository validators.

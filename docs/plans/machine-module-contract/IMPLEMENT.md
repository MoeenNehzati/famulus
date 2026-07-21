# Machine-Module Contract Implementation Guide

> **Superseded — history only.** Do not execute these phases. The current
> implementation authority is
> [`docs/plans/unified-architecture-migration.md`](../unified-architecture-migration.md).
> This guide records the former version-3 sequence and its stop gates; it no
> longer authorizes further work.

This is the single entrypoint for implementing this package. The root
`README.md` is a human overview; files `01` through `05` are normative; the
`implementation/` directory contains the ordered execution phases.

## Authority

Use this precedence when documents appear to disagree:

1. `01-decision-ledger.md` for settled cross-cutting decisions and names;
2. normative designs `02` through `04` for detailed behavior;
3. `05-verification-matrix.md` for enforcement ownership and test families;
4. the active implementation phase for file scope and execution order;
5. examples, the legacy crosswalk, and the review log as non-normative aids.

An implementation phase may reference or operationalize a requirement, but it
must not redefine it. Stop when a conflict cannot be resolved by this order.

## Starting procedure

1. Verify the repository is on a named branch and inspect the complete worktree.
2. Preserve unrelated changes; never stage, restore, or rewrite them.
3. Record the current baseline for the focused tests named by the active phase.
4. Read `01-decision-ledger.md` once for the global vocabulary.
5. Open `implementation/README.md` and select only a phase explicitly named by
   the user. If no phase is named, stop without editing. Read only the authorized
   phase's required normative sections.
6. Review the phase for conflicts with the live repository before editing.

Do not begin by loading every design, crosswalk, example, and review artifact.
Use the phase's required-reading section to keep context bounded.

## Phase workflow

Execute one phase at a time and keep its checkboxes current:

1. satisfy its prerequisites;
2. run its baseline checks;
3. perform each task test-first in the documented order;
4. run the focused check after each task;
5. run the complete phase gate;
6. inspect the exact diff and confirm no unrelated path changed;
7. emit the completion report below and stop for review.

Do not continue past a failed gate, an unresolved normative conflict, missing
required evidence, or a change that materially expands the phase's file scope.
Do not weaken a validator, fixture, contract, or certificate requirement merely
to make a gate pass.

## Required phase completion report

```text
Phase: <number and name>
Requirements implemented: <IDs>
Files changed: <exact paths>
Tests: <exact commands and pass/fail counts>
Generated artifacts checked: <paths or none>
Known failures: <none, or exact pre-existing failure and evidence>
Worktree scope: <clean except listed paths>
Next-phase prerequisites satisfied: yes|no, with reason
Next phase authorized: no unless explicitly named by the user
```

Satisfying next-phase prerequisites does not authorize execution. A later phase
starts only when the user explicitly names it after accepting the current phase
boundary.

## Implementation order

1. [Schema, standards, and fixtures](implementation/01-schema-standards-and-fixtures.md)
2. [Inventory, graph, and dispatcher](implementation/02-inventory-graph-and-dispatcher.md)
3. [Consumer-local injection](implementation/03-consumer-local-injection.md)
4. [Admissibility and certification](implementation/04-admissibility-and-certification.md)
5. [Migration, documentation, and release](implementation/05-migration-docs-and-release.md)

Plans 2 and 3 consume Plan 1's schema API. Plan 4 consumes the graph and
projection APIs from Plans 2 and 3. Plan 5 is the only phase that creates or
replaces live blueprint declarations. It derives target v3 modules from live
content and verified behavior; earlier blueprints are non-authoritative hints.

## Prompt for an implementation agent

```text
Implement docs/plans/machine-module-contract by following IMPLEMENT.md.

Execute only phase <explicitly authorized phase number>. Do not infer authority
for any later phase. Read the decision ledger, then only the normative sections
named by that phase. Review the phase against the live
repository before editing. Work test-first, preserve unrelated changes, and do
not reinterpret normative requirements. Stop on specification conflicts,
missing evidence, expanded scope, or failed gates. At the phase boundary, emit
the required completion report and wait for explicit authorization before
continuing.
```

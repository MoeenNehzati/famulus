---
name: refactor-node
description: >-
  Use when the user asks for a behavior-preserving audit or refactor of a registered Officina node or one of its owned sources. Do not use for feature work, bug fixes, generic code review, or files outside registered node ownership.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 6

Uses Interfaces:
- `refactor-node.source.gateway -> refactor-node.source.instruction-refactoring.interface.refactor-instructions@1`
- `refactor-node.source.gateway -> refactor-node.source.python-refactoring.interface.refactor-python@1`
- `refactor-node.source.gateway -> standards.interface.query-standard@1`

Public Interfaces:
- `refactor-node.interface.default`
- `refactor-node.interface.refactor-instructions`
- `refactor-node.interface.refactor-python`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `refactor-node.interface.default` — Resolve node ownership and gateway language, then invoke the supported refactoring route without crossing scope boundaries.
- `refactor-node.interface.refactor-instructions` — Diagnose and repair an owned instruction source from its applicable standards.
- `refactor-node.interface.refactor-python` — Diagnose and, after approval, apply one verified behavior-preserving Python OOD refactoring move at a time.
<!-- END BLUEPRINT INTERFACES -->
# Refactor Node

Use `standards.interface.query-standard` as the sole repository-policy query.
Select the root from the scope already established from the request and current
artifact; the query validates and returns its complete pinned import closure.
Never ask the query to infer ownership or reconstruct a blueprint graph.

## Preflight

Before querying policy, identify the selected component and affected implementation
children. Retain the dry-run and verify caller, target `standards.interface.query-standard`, repository root,
selected standard path, task facts, view, and refs. Reject mismatches and inferred targets.

## Standards retrieval

Classify each selected scope by its known node role and gateway family, then
query the corresponding canonical root with `task.kind=refactor` and
`--view requirements`:

| Selected scope | Root standard |
|---|---|
| Python module | `references/node-standards/python-module.standard.yaml` |
| Python behavioral source | `references/node-standards/python-behavioral-source.standard.yaml` |
| Instruction module | `references/node-standards/instruction-module.standard.yaml` |
| Instruction behavioral source | `references/node-standards/instruction-behavioral-source.standard.yaml` |

Select by affected role, not filename. For a typical registered `SKILL.md`,
module identity, discovery, gateway, or export work uses the instruction-module
root; authored instruction work uses the instruction-behavioral-source root.
Query both only when both roles change; a narrow source-owned section uses only
the source root. For other mixed work, query each applicable root separately
and combine the returned requirements. A whole-skill audit queries both
instruction roots plus every declared Python module and source root.

If selected work touches test files or their fixtures or helpers, query
`references/node-standards/code-testing.standard.yaml` as an additional
independent root with `task.kind=refactor`. Set
`task.optimizes-test-performance` true for performance work and false otherwise.
Test artifacts are collected or executed by configured test or validation runner.
Markdown-only means no executable test file, fixture, or helper changes. Test
code that validates Markdown remains test code.

Imported documents arrive in the complete pinned import closure; never query
them separately. Apply `requirements.true`, resolve material
`requirements.unknown` and missing facts. Never silently discard a material
unknown. Then use exact returned document/ref
pairs: `--view context --refs-json JSON` for `context_index`; `--view
evidence` for checks, tests, and assurances, `semantic_reviews`, artifacts,
and limitations; and `--view remedies` for returned `remedied-by`
procedures. Use `--view full` or `--query-json` only through `--help`.

Retain a selected class, function, method, or instruction section as the
caller-owned sub-scope. Read scoped repository instructions and the current
diff, then characterize observable behavior before proposing changes.

## Evidence and preservation

Map affected behavior, ownership, dependencies, authorization, reverse
consumers, and verification. Classify canonical evidence,
supplemental change-relevant evidence with owner and limitations, and affected
refs with no mapped evidence. Perform semantic review and build a preservation
map for observables, route outcomes, fallbacks, approval boundaries, generated
invocations, and removed directives.

## Route

- Invoke `refactor-node.interface.refactor-python` for Python scopes.
- Invoke `refactor-node.interface.refactor-instructions` for Markdown gateway
  partitions.
- For mixed work, invoke both routes as needed and combine their proposals
  without crossing the established scope boundaries.

## Shared change contract

Preserve behavior; separate features, bug fixes, and public-API redesign. Before
mutation, report scope, preservation map, requirements, unresolved facts,
evidence, and remedy. Require approval, apply one move, inspect the exact diff
against the preservation map, and verify. An unvalidated move is non-final:
fix and rerun within scope or revert and stop.

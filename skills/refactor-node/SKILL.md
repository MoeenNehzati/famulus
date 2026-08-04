---
name: refactor-node
description: Use when auditing or refactoring a whole registered skill-system node or an owned file, class, function, method, or instruction section
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 5

Uses Interfaces:
- `refactor-node.source.gateway -> refactor-node.source.instruction-refactoring.interface.refactor-instructions@1`
- `refactor-node.source.gateway -> refactor-node.source.python-refactoring.interface.refactor-python@1`

Public Interfaces:
- `refactor-node.interface.default`
- `refactor-node.interface.query-standards`
- `refactor-node.interface.refactor-instructions`
- `refactor-node.interface.refactor-python`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `refactor-node.interface.query-standards` — Query effective node standards for a registered node or owned sub-scope.
  - `dispatcher --caller-skill refactor-node refactor-node.interface.query-standards <target> [--repo-root PATH] [--facts-json JSON] [--view requirements|context|evidence|remedies|full] [--refs-json JSON] [--query-json JSON]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `refactor-node.interface.default` — Resolve node ownership and gateway language, then invoke the supported refactoring route without crossing scope boundaries.
- `refactor-node.interface.refactor-instructions` — Diagnose and repair an owned instruction source from its applicable standards.
- `refactor-node.interface.refactor-python` — Diagnose and, after approval, apply one verified behavior-preserving Python OOD refactoring move at a time.
<!-- END BLUEPRINT INTERFACES -->
# Refactor Node

Use `refactor-node.interface.query-standards` as the sole repository-policy
source. It resolves ownership and validates every pinned import; never read one
standard file as the effective policy.

## Standards retrieval

Query the target with `task.kind=refactor` and `--view requirements`. Preserve
each returned owner, selected scope, exclusions, gateway family, and
`standard_ref`; report unsupported partitions.

For normal views, dereference each partition overlay index through the top-level
shared catalog. Use the catalog entry's exact `document` and `ref` in follow-up
queries; applicability and missing facts belong to the overlay.

| Current decision | Request | Use |
|---|---|---|
| Diagnose | `--view requirements` | Apply `requirements.true`. Inspect the missing facts in `requirements.unknown` and rerun. Never silently discard a material unknown. |
| Interpret or apply indexed context | `--view context --refs-json JSON` | Request a relevant `context_index` entry or requirement; read only returned families, definitions, guidance, and examples. |
| Plan or assess verification | `--view evidence --refs-json JSON` | Map returned checks, tests, and assurances to the selected refs; perform `semantic_reviews` and open only returned artifacts. Preserve limitations. |
| Repair a proven violation | `--view remedies --refs-json JSON` | Follow only returned `remedied-by` links and procedures, including preconditions, order, invariants, completion conditions, and risk. |
| Unusual extraction | `--query-json JSON` | Apply the generic record filter/projection described by `--help`. |
| Debug extraction | `--view full` | Inspect the complete projection only. |

`--refs-json` is a list of exact pairs such as
`[{"document":"node-standards.python-ood","ref":"python-ood.behavioral-contract#preserve-observables"}]`.
Request follow-up information only for refs that affect the current decision.

Retain a selected class, function, method, or instruction section as a sub-scope
of its returned owner. Read scoped repository instructions and the current diff,
then characterize observable behavior before proposing changes.

## Route

- Invoke `refactor-node.interface.refactor-python` for Python partitions.
- Invoke `refactor-node.interface.refactor-instructions` for Markdown gateway
  partitions.
- For a mixed whole module, invoke both routes as needed and combine their
  proposals without crossing ownership boundaries.

## Shared change contract

Preserve behavior; separate features, bug fixes, and public-API redesign. Before
mutation, report scope, relevant requirements, unresolved facts, evidence, and
the selected remedy. Require approval, apply one move at a time, inspect the
exact diff, and stop on failed verification or an ownership boundary.

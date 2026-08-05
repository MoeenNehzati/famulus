---
name: refactor-node
description: Use when auditing or refactoring a whole registered skill-system node or an owned file, class, function, method, or instruction section
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 6

Uses Interfaces:
- `refactor-node.source.gateway -> common.interface.query-standard@1`
- `refactor-node.source.gateway -> refactor-node.source.instruction-refactoring.interface.refactor-instructions@1`
- `refactor-node.source.gateway -> refactor-node.source.python-refactoring.interface.refactor-python@1`

Public Interfaces:
- `refactor-node.interface.default`
- `refactor-node.interface.refactor-instructions`
- `refactor-node.interface.refactor-python`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `refactor-node.interface.default` — Classify the selected scope, query its explicit standard root, then invoke the supported refactoring route without crossing scope boundaries.
- `refactor-node.interface.refactor-instructions` — Diagnose and repair an owned instruction source from its applicable standards.
- `refactor-node.interface.refactor-python` — Diagnose and, after approval, apply one verified behavior-preserving Python OOD refactoring move at a time.
<!-- END BLUEPRINT INTERFACES -->
# Refactor Node

Use `common.interface.query-standard` as the sole repository-policy query.
Select the root from the scope already established from the request and current
artifact; the query validates and returns its complete pinned import closure.
Never ask the query to infer ownership or reconstruct a blueprint graph.

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

Imported documents already arrive in the closure; never query them separately.
Follow up under the original root with exact returned `document` and `ref`
pairs. Establish `task.affects-executable-behavior` from the operation, not its
filename or language. Enrich and rerun requirements for each established
missing fact; report unresolved facts. Freeze that root and fact set for
context, evidence, and remedy follow-ups.

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

Retain a selected class, function, method, or instruction section as the
caller-owned sub-scope. Read scoped repository instructions and the current
diff, then characterize observable behavior before proposing changes.

## Route

- Invoke `refactor-node.interface.refactor-python` for Python scopes.
- Invoke `refactor-node.interface.refactor-instructions` for Markdown gateway
  partitions.
- For mixed work, invoke both routes as needed and combine their proposals
  without crossing the established scope boundaries.

## Shared change contract

Preserve behavior; separate features, bug fixes, and public-API redesign. Before
mutation, report scope, relevant requirements, unresolved facts, evidence, and
the selected remedy. Require approval, apply one move at a time, inspect the
exact diff, and stop on failed verification or an ownership boundary.

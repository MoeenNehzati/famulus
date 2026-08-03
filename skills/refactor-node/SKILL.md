---
name: refactor-node
description: Use when auditing or refactoring a whole registered skill-system node or an owned file, class, function, method, or instruction section
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 3

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
  - `dispatcher --caller-skill refactor-node refactor-node.interface.query-standards <target> [--repo-root PATH] [--facts-json JSON] [--view requirements|evidence|remedies|full]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `refactor-node.interface.default` — Resolve node ownership and gateway language, then invoke the supported refactoring route without crossing scope boundaries.
- `refactor-node.interface.refactor-instructions` — Diagnose and repair an owned instruction source from its applicable standards.
- `refactor-node.interface.refactor-python` — Diagnose and, after approval, apply one verified behavior-preserving Python OOD refactoring move at a time.
<!-- END BLUEPRINT INTERFACES -->
# Refactor Node

Use the deterministic standards query as the sole repository-specific
refactoring knowledge source. The LLM supplies semantic comparison and applies
approved moves; it does not reconstruct ownership, routing, applicability, or
remedies from memory.

The query is backed by the common standard extractor. It validates the selected
leaf and every pinned import before returning records; never treat a leaf file
read in isolation as the applicable policy.

## Query

1. Invoke `refactor-node.interface.query-standards` through its generated public
   interface contract, with the target positional first, the repository root,
   `task.kind=refactor`, and the `requirements` view.

   The target may be a requested node ID or path. Add inspected target facts to
   the JSON object when they are known.
2. Treat each partition's owner, direct content, declaration files, exclusions,
   gateway family, and `standard_ref` into the shared standards catalog as the routing
   authority. Report an `unsupported` partition; do not drop it or invent a
   language route.
3. For every `unknown` item whose outcome matters, inspect the named fact and
   rerun the query. Never silently discard an unknown item. Do not apply `false`
   items.
4. Rerun the same target and facts with `--view evidence` when assessing
   verification or semantic-review coverage, and with `--view remedies` only
   after diagnosing a violation. Use `--view full` only to debug the projection
   itself. Keep `--view requirements` as the normal first pass.
5. Retain an explicitly selected class, function, method, or instruction section
   as the sub-scope inside the returned owner. Read the current diff and scoped
   repository instructions, then characterize observable behavior before
   proposing changes.

## Refactoring brief

For every supported partition, resolve its `standard_ref` and build this working
set before diagnosis:

- Conformance: collect every applicable rule and its rule assertions from
  `items.true`, plus true guidance. Families, definitions, and examples supply
  interpretation, not additional requirements.
- Unresolved: inspect the missing facts named by `items.unknown` and rerun the
  query. Do not proceed past a material unknown.
- Excluded: do not apply `items.false`.
- Evidence: query `--view evidence`, associate returned checks, tests, and
  assurances with the assertions they cover, and preserve all limitations.
- Judgment: use the evidence view to perform returned `semantic_reviews`; distinguish their findings from
  mechanically proven violations.
- Resources: open artifacts returned by the evidence view only when an applicable item, evidence
  mechanism, or review references them. Artifacts are not independent policy.
- Repair: after proving a violation, query `--view remedies`, resolve its exact entry, and
  use the referenced procedure, preconditions, ordering, invariants, and
  completion conditions.

Treat `true` normative items as desired behavior. Use returned checks, tests,
assurances, semantic reviews, and limitations as evidence boundaries. For a
violation, form its assertion reference as `rule-id#assertion-id`. Match the
returned `remedied-by` relation by `(source.document, source.ref)`: try the assertion, then its
rule, then walk the rule's returned `ancestors` from nearest to farthest. Resolve
the target by `(target.document, target.ref)` and use only that procedure. If
multiple procedures remain applicable, choose the lowest-risk one whose stated
preconditions fit; otherwise present the alternatives and stop for direction.
If none is declared, report the missing remedy instead of inventing repository
policy.

## Route

- Invoke `refactor-node.interface.refactor-python` for Python partitions.
- Invoke `refactor-node.interface.refactor-instructions` for Markdown gateway
  partitions.
- For a mixed whole module, invoke both routes as needed and combine their
  proposals without crossing ownership boundaries.

## Shared change contract

Refactoring preserves behavior. Separate feature work, bug fixes, and public API
redesign. Report owner, scope and exclusions, applicable and unresolved items,
behavioral contract, evidence, ordered declared remedies, invariants, and
verification before mutation. Require explicit approval, apply one move at a
time, inspect the exact diff, and stop on failed verification. Never cross an
ownership boundary silently.

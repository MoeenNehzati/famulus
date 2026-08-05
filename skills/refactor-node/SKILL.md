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

`refactor-node.interface.query-standards` alone supplies repository policy: it
resolves ownership and validates pinned imports; no file alone is effective.

## Standards retrieval

### Preflight

Before every standards query, run and retain its exact dispatcher `--dry-run`.
Resolve `cwd/python_target.gateway_path` against the reviewed root's registered
implementation child and gateway. A match selects the gateway, not the full
imported runtime closure. On mismatch, reject it, select the
checkout with the installed wrapper's `AI=<reviewed-root>`, and repeat. Execute
only a match after verifying the rendered command's target, repository root,
facts, view, and refs; retain it for exact replay.

### Resolve

Query target with `task.kind=refactor` and `--view requirements`. Whole-node
audits query the target module and every registered implementation child.
Resolve every material `requirements.unknown` for target module and each child.
Preserve returned owners/scopes/exclusions/gateways/`standard_ref`; report
unsupported partitions. Never silently discard unknowns.

### Characterize

After these queries, read every returned supported behavioral source;
declarations/tests/contracts never replace owned implementation. If current
runtime policy requires private-source approval, obtain it before reading;
denied/unavailable means partial, never exclusion/completion. Explicitly owned
sub-scope: only selected source and directly affected declarations/consumers.
Read instructions/diff. Map affected observables/outcomes, fact ownership,
dependency/authorization/reverse-consumer edges, and evidence owner/limitations.
Mark unresolved edges. Separate refactoring from features, bug fixes, public-API
changes, and ownership expansion.

### Select and retrieve

Dereference partition overlays through the shared catalog;
use exact `document`/`ref`. Apply `requirements.true`; select `context_index`
entries; evidence maps checks, tests, and assurances. Perform every returned
`semantic_reviews` and open only returned artifacts; preserve limitations. The
overlay owns applicability/missing facts.

| Current decision | Request | Use |
|---|---|---|
| Resolve applicability | `--view requirements` | Resolve material unknowns and rerun. |
| Interpret indexed context | `--view context --refs-json JSON` | Read only returned context. |
| Assess verification | `--view evidence --refs-json JSON` | Preserve returned limitations. |
| Repair a violation | `--view remedies --refs-json JSON` | Follow only returned `remedied-by` procedures and their conditions/order/invariants/risk. |

Request only decision-relevant follow-ups. Use `--view full`/`--query-json` only
via `--help`. For verification, query every affected normative ref per owner
partition, plus refs used to diagnose and remedy. Report disjointly:
canonical evidence returned by the query; supplemental change-relevant checks,
naming their actual owner and limitations, including directly affected consumer
checks; and requested normative refs with no mapped evidence.

### Route

Characterize every supported partition before choosing the smallest justified
move; mutate one at a time; no-churn is valid.

- Route Python through `refactor-node.interface.refactor-python`; Markdown
  through `refactor-node.interface.refactor-instructions`.

### Propose and change

Before mutation, report scope, preservation map, requirements, unresolved facts,
classified evidence, and remedy; require approval. A behavior repair requires
genuine RED evidence. A behavior-preserving structural move requires
standards-backed design pressure and green characterization before and after.
If diagnosis reveals a behavioral defect, report and stop: its fix needs
separately approved scope. Apply one move, inspect its exact diff against the
preservation map, and run every relevant returned validator. Fix failures within
the approved move and rerun;
otherwise revert and stop. Never consume the result until validation passes.

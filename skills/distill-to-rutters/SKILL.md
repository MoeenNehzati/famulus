---
name: distill-to-rutters
description: Use when an existing Markdown skill instruction should be transformed into transparent Rutters and an operable Voyage dispenser. Do not use for ordinary summarization, general Rutter development, or operating an existing compass.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 1

Uses Interfaces:
- `distill-to-rutters.source.design-implementation -> using-compass.interface.default@5`
- `distill-to-rutters.source.gateway -> distill-to-rutters._rtx.interface.validate-and-route@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.assign-rutters.interface.assign-rutters@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.breakdown.interface.breakdown@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.design-implementation.interface.design-implementation@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.extract-evolutions.interface.extract-evolutions@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.finalize.interface.finalize@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.implement.interface.implement@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.validate-logic.interface.validate-logic@1`
- `distill-to-rutters.source.gateway -> distill-to-rutters.source.verify.interface.verify@1`

Public Interfaces:
- `distill-to-rutters.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `distill-to-rutters.interface.default` — Route exactly one approval-authorized stage only after exact workflow identity, recursive freshness, typed predecessor success, and a final consulted-path rehash; then stop for validation.
<!-- END BLUEPRINT INTERFACES -->
# Distill to Rutters

Turn one Markdown instruction into a review-gated Rutter implementation. The
gateway routes one stage at a time; stage interfaces own the actual analysis,
authoring, and verification.

## Preflight

Resolve one readable Markdown input, its owning registered skill module, and
that module's declared implementation boundary. Preserve the original file and
unrelated dirty work. The workspace is a sibling directory named from the input
stem with `_distillation` appended. The input, owning module, implementation
boundary, and workspace must remain inside the repository; refuse paths that
would escape it.

Every stage artifact must live in that exact `<source-stem>_distillation`
workspace and preserve one common root `source` prerequisite. Never splice an
artifact, context row, or deliverable from another source or distillation run.

Approval is bound to exact bytes. File existence or conversational assent
without a path and digest is not approval, and there is no status ledger. In a
new conversation, require the last artifact path, its gateway-reported SHA-256,
its typed outcome, and the user's explicit `approve` or `reject` decision.

## Route exactly one stage

With no prior artifact, invoke
`distill-to-rutters._rtx.interface.validate-and-route@1` in
`source-preflight` mode on the source Markdown, with an empty approved digest
and decision `approve`. Only its `breakdown` route may bootstrap the workflow;
preflight cannot authorize a later stage.

Before every post-bootstrap route, invoke that injected interface with the
artifact path, expected stage, user-approved digest, and explicit decision.
Consume only its JSON `status`, `artifact_digest`, `outcome`,
`authorized_route`, and `earliest_stale_prerequisite`. Do not invoke a private
filesystem implementation path.

The artifact basename must match the accepted stage exactly:

| Order | Accepted stage | Required exact filename |
|---:|---|---|
| 1 | `breakdown` | `01_breakdown.md` |
| 2 | `assign-rutters` | `02_rutter_assignment.md` |
| 3 | `extract-evolutions` | `03_evolutions_and_transitions.md` |
| 4 | `validate-logic` | `04_logic_validation.md` |
| 5 | `design-implementation` | `05_implementation_design.md` |
| 6 | `implement` | `06_implementation_report.md` |
| 7 | `finalize` | `07_entrypoint.md` |
| 8 | `verify` | `08_verification.md` |

Unknown or old filenames cannot authorize routing. Treat a basename mismatch
as failed approval authority even when the file's envelope, outcome, or digest
would otherwise validate. A correction must use the fixed filename owned by
its stage; it cannot skip or reorder a stage.

Only `accepted` may advance, and only in this fixed order:

| Accepted stage | Authorized next stage |
|---|---|
| `breakdown` | `assign-rutters` |
| `assign-rutters` | `extract-evolutions` |
| `extract-evolutions` | `validate-logic` |
| `validate-logic` | `design-implementation` |
| `design-implementation` | `implement` |
| `implement` | `finalize` |
| `finalize` | `verify` |
| `verify` | stop complete |

For `gap` or `rejected`, invoke only the owning stage returned as the
non-advancing repair route. For `stale`, preserve every file and invoke only
the earliest owning stage returned by the interface. For `partial`, `failed`,
or `blocked`, stop until the condition changes or the user explicitly starts
the owning stage again. A bootstrap or repair route never skips a stage and
never counts as advancement.

Pass the input path, owning module, approved prerequisite artifacts, current
user feedback, and live repository state to the selected stage interface.
After it writes its assigned artifact, compute SHA-256 over the complete stored
bytes without normalization. Report the exact `(path, digest, outcome)` tuple,
ask the user to validate it, and stop. The next invocation must revalidate that
tuple, every transitive prerequisite, and every present context-closure path
before routing. Validation reads each consulted file's bytes once, compares a
recorded digest before parsing a changed artifact, and rehashes all consulted
paths before accepting; concurrent mutation produces `stale`, never acceptance.

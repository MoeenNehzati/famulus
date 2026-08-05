---
name: skill-maker
description: Use when creating or editing a personal skill in the shared skills directory
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 5

Uses Interfaces:
- `skill-maker.source.gateway -> refactor-node._rtx.interface.query-standards@4`
- `skill-maker.source.gateway -> skill-maker._rtx.interface.sync-blueprints@1`

Public Interfaces:
- `skill-maker.interface.default`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `skill-maker.interface.default` — Create or edit a personal skill under the repository's canonical module, interface, validation, and Git-safety standards.
<!-- END BLUEPRINT INTERFACES -->
## Research option when creating a skill

Before creating a skill, ask whether to research current documentation and
comparables unless the user already stated a preference. If yes, research
first; if no, use only the conversation and repository context.

## Git Safety

Before editing any skill file, verify the repo containing that file is on a
named branch (`git symbolic-ref HEAD` from the repo root). If it fails, check
out a named branch first. The pre-commit hook will block the eventual commit,
but catching this before editing avoids doing work that can't land.

## Standards retrieval

Before authoring, query `refactor-node._rtx.interface.query-standards` with the target,
`task.kind=author-skill`, and `--view requirements`. For a new skill, create only
the schema-minimum registration first. Treat each owner and `standard_ref` as a
scope boundary; never read one standard file as the complete policy.

For normal views, dereference each partition overlay index through the top-level
shared catalog. Copy exact `document` and `ref` values from catalog entries into
follow-up queries; applicability and missing facts belong to the overlay.

| Current decision | Request | Use |
|---|---|---|
| Author behavior | `--view requirements` | Apply `requirements.true`; inspect the missing facts in `requirements.unknown` and rerun. |
| Interpret or apply indexed context | `--view context --refs-json JSON` | Request a relevant `context_index` entry or requirement; read only returned context. |
| Choose verification | `--view evidence --refs-json JSON` | Map checks, tests, and assurances to selected refs; perform `semantic_reviews`, open only returned artifacts, and preserve limitations. |
| Repair a violation | `--view remedies --refs-json JSON` | Follow only returned remedies and procedures. |
| Unusual extraction | `--query-json JSON` | Use the generic filter/projection described by `--help`. |
| Debug extraction | `--view full` | Inspect the complete projection only. |

`--refs-json` contains exact `document` and `ref` pairs copied from the
requirements result. Request only information needed for the current decision,
and cite consequential requirement IDs.

When authoring a repository validator, rerun requirements with
`node.is-repository-validator=true`. If the validator requirement is relevant,
request its remedy to obtain the current creation and verification procedure;
do not reproduce that procedure in this wrapper.

## Referencing other skills

When this skill needs to mention another skill in documentation:

- use the skill name only, with an explicit requirement marker such as
  `**REQUIRED SUB-SKILL:** Use ...` or `**REQUIRED BACKGROUND:** Use ...`
- do not use `@.../SKILL.md` links to another skill file, because they force
  file loading instead of naming the dependency cleanly

**REQUIRED BACKGROUND:** Use `refactor-node` for standards-backed ownership and
scope resolution before changing skill content.

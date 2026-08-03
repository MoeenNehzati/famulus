---
name: skill-maker
description: Use when creating or editing a personal skill in the shared skills directory
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture, assistant-assurance, repository-workflow; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `skill-maker.source.gateway -> refactor-node.interface.query-standards@2`

Public Interfaces:
- `skill-maker.interface.default`
- `skill-maker.interface.sync-blueprints`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `skill-maker.interface.sync-blueprints` — Validate every skill blueprint and either check or refresh generated SKILL.md contract blocks and the runtime-dependency manifest.
  - `dispatcher --caller-skill skill-maker skill-maker.interface.sync-blueprints [--check]`
  - sync: Refresh generated files from blueprint.yaml.
  - check: Validate blueprints and fail if generated files are out of sync.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `skill-maker.interface.default` — Create or edit a personal skill under the repository's canonical module, interface, validation, and Git-safety standards.
<!-- END BLUEPRINT INTERFACES -->
## Research option when creating a skill

When creating a new skill, before writing it, ask the user whether they want
you to pull up online resources (documentation, comparable tools, domain
references, best practices) to guide writing the most comprehensive skill —
or to work from the conversation and repo context alone. Respect the answer:
if yes, research first and fold what you learn into the skill's instructions
and edge cases; if no, do not browse. Skip the question only when the user
has already stated a preference in the current conversation.

## Git Safety

Before editing any skill file, verify the repo containing that file is on a
named branch (`git symbolic-ref HEAD` from the repo root). If it fails, check
out a named branch first. The pre-commit hook will block the eventual commit,
but catching this before editing avoids doing work that can't land.

## Standards query

Use `refactor-node.interface.query-standards` as the canonical standards source
for the registered target or owned path before authoring. The query is backed by the common standard
extractor: it validates the selected leaf and every pinned import, so never read
one standard file as though it were the complete policy.

Invoke it through the generated public interface contract, with the target
positional first, the repository root, `task.kind=author-skill`, and the
`requirements` view.

For an existing skill, query its node or selected owned path. For a new skill,
create only the schema-minimum registration, then query that node. Supply known
facts, including the authoring task kind, and use each partition's owner and
`standard_ref` as the scope boundary.

Use `--view requirements` for the authoring brief. Query `--view evidence`
when selecting verification or semantic review, and `--view remedies` only
after diagnosing a violation. Reserve `--view full` for projection debugging.

### Authoring brief

Build one brief per partition before writing behavior:

- Requirements: collect every applicable rule and its rule assertions from
  `items.true`; apply true guidance as the repository default. Families,
  definitions, and examples explain those requirements but do not create new
  ones.
- Unresolved: inspect the missing facts named by `items.unknown`, then rerun the
  query. Do not author past a material unknown.
- Excluded: do not apply `items.false`.
- Verification: query the evidence view, then map returned checks, tests, and assurances to the affected
  requirements and retain their stated limitations.
- Judgment: schedule returned `semantic_reviews` from the evidence view; automated evidence does not
  replace them.
- Resources: open artifacts returned by the evidence view only when referenced by an applicable
  item, evidence mechanism, or review. An artifact is not independent policy.
- Repair: query the remedies view only for a diagnosed violation, following its exact
  source and target references rather than inventing a procedure.

Author and verify against this brief, citing standard item IDs for consequential
choices.

## Skill-system subdirectories

This skill owns mechanical authoring validation for the skill system:

- **`validators/`** — Python validator modules (names, metadata, blueprints, boundaries, dependencies, blueprint relationships). Each exports `validate(repo_root: Path) -> list[str]` and is auto-discovered by `validators/runner.py` on every commit. Query the target's node-standard closure for the applicable validator contract and conventions.
- **`tests/`** — behavior tests for the blueprint dispatcher and sync scripts (`test_blueprint_tools.py`).
- **runtime syncer** — refreshes generated blueprint artifacts.

To add a mechanical check, add a `.py` file to `validators/` with a `validate(repo_root)` function and a matching `tests/validate_<name>.py`. No registration is needed.

## Referencing other skills

When this skill needs to mention another skill in documentation:

- use the skill name only, with an explicit requirement marker such as
  `**REQUIRED SUB-SKILL:** Use ...` or `**REQUIRED BACKGROUND:** Use ...`
- do not use `@.../SKILL.md` links to another skill file, because they force
  file loading instead of naming the dependency cleanly

**REQUIRED BACKGROUND:** Use `refactor-node` for standards-backed ownership and
scope resolution before changing skill content.

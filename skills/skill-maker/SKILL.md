---
name: skill-maker
description: Use when creating or editing a personal skill in the shared skills directory
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Dependencies: none

Interface Version: 1

Exported Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `sync-blueprints` — Validate skill blueprints and optionally refresh generated compatibility artifacts.
  - `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints [--check]`
  - sync: Refresh generated files from blueprint.yaml.
  - check: Validate blueprints and fail if generated files are out of sync.

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
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

## Skill-system subdirectories

This skill owns the conformance infrastructure for the skill system:

- **`validators/`** — Python validator modules (names, metadata, blueprints, boundaries, dependencies, blueprint relationships). Each exports `validate(repo_root: Path) -> list[str]` and is auto-discovered by `validators/runner.py` on every commit. See `../../references/skill-guidelines.md` for the full validator contract and conventions.
- **`tests/`** — behavior tests for the blueprint dispatcher and sync scripts (`test_blueprint_tools.py`).
- **runtime syncer** — refreshes generated blueprint compatibility artifacts.

To add a new conformance check: add a `.py` file to `validators/` with a `validate(repo_root)` function and a matching `tests/validate_<name>.py`. No registration needed.

## Referencing other skills

When this skill needs to mention another skill in documentation:

- use the skill name only, with an explicit requirement marker such as
  `**REQUIRED SUB-SKILL:** Use ...` or `**REQUIRED BACKGROUND:** Use ...`
- do not use `@.../SKILL.md` links to another skill file, because they force
  file loading instead of naming the dependency cleanly

@./../../references/skill-guidelines.md

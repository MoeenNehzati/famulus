---
name: my-writing-skills
description: Use when creating or editing a personal skill in the shared skills directory
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

## Git Safety

Before editing any skill file, verify the repo containing that file is on a
named branch (`git symbolic-ref HEAD` from the repo root). If it fails, check
out a named branch first. The pre-commit hook will block the eventual commit,
but catching this before editing avoids doing work that can't land.

## Skill-system subdirectories

This skill owns the conformance infrastructure for the skill system:

- **`validators/`** — Python validator modules (names, metadata, blueprints, boundaries, dependencies, blueprint relationships). Each exports `validate(repo_root: Path) -> list[str]` and is auto-discovered by `validators/runner.py` on every commit. See `../../references/skill-guidelines.md` for the full validator contract and conventions.
- **`tests/`** — behavior tests for the blueprint dispatcher and sync scripts (`test_blueprint_tools.py`).
- **`scripts/`** — blueprint sync script (`sync_skill_blueprints.py`).

To add a new conformance check: add a `.py` file to `validators/` with a `validate(repo_root)` function and a matching `tests/validate_<name>.py`. No registration needed.

## Referencing other skills

When this skill needs to mention another skill in documentation:

- use the skill name only, with an explicit requirement marker such as
  `**REQUIRED SUB-SKILL:** Use ...` or `**REQUIRED BACKGROUND:** Use ...`
- do not use `@.../SKILL.md` links to another skill file, because they force
  file loading instead of naming the dependency cleanly

@./../../references/skill-guidelines.md

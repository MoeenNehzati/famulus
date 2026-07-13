---
name: regenerate-blueprints
description: Use when the user wants a refreshed blueprint.yaml for an existing skill generated under /tmp without modifying the skill.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `regenerate-blueprints.machine.regenerate-blueprint`
- `regenerate-blueprints.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `regenerate-blueprint` — Generate a refreshed blueprint YAML for one existing skill under /tmp.
  - `dispatcher --caller-skill regenerate-blueprints regenerate-blueprints.machine.regenerate-blueprint <skill-name>`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
## Purpose

Generate a schema-documented replacement blueprint for one existing skill.

## Rules

- Input is the exact skill directory name.
- Write only `/tmp/<skill-name>_blueprint.yaml`.
- Do not edit the skill's existing `blueprint.yaml`.
- Report the generated path and any validation failure.

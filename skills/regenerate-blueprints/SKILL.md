---
name: regenerate-blueprints
description: Use when the user wants a refreshed blueprint.yaml for an existing skill generated under /tmp without modifying the skill.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-development; topics: assistant-authoring, assistant-architecture; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces: none

Public Interfaces:
- `regenerate-blueprints.interface.default`
- `regenerate-blueprints.interface.regenerate-blueprint`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `regenerate-blueprints.interface.regenerate-blueprint` — Generate a refreshed blueprint YAML for one existing skill under /tmp.
  - `dispatcher --caller-skill regenerate-blueprints regenerate-blueprints.interface.regenerate-blueprint <skill-name>`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `regenerate-blueprints.interface.default` — Regenerate one existing skill blueprint into /tmp, report its path or validation failure, and never modify the source blueprint.
<!-- END BLUEPRINT INTERFACES -->
## Purpose

Generate a schema-documented replacement blueprint for one existing skill.

## Rules

- Input is the exact skill directory name.
- Write only `/tmp/<skill-name>_blueprint.yaml`.
- Do not edit the skill's existing `blueprint.yaml`.
- Report the generated path and any validation failure.

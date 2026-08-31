---
name: regenerate-blueprints
description: >-
  Use when an existing skill blueprint needs regeneration, whether requested directly or required by another skill. Do not use for ordinary blueprint editing or synchronization.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `regenerate-blueprints._rtx.interface.regenerate-blueprint@1` — Generate a refreshed blueprint YAML for one existing skill under /tmp.
  - `dispatcher --caller-skill regenerate-blueprints regenerate-blueprints._rtx.interface.regenerate-blueprint <skill-name>`

<!-- END BLUEPRINT INTERFACES -->
## Purpose

Generate a schema-documented replacement blueprint for one existing skill.

## Rules

- Input is the exact skill directory name.
- Write only `/tmp/<skill-name>_blueprint.yaml`.
- Do not edit the skill's existing `blueprint.yaml`.
- Report the generated path and any validation failure.

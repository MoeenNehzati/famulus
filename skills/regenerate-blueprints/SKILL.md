---
name: regenerate-blueprints
description: >-
  Use when an existing skill blueprint needs regeneration, whether requested directly or required by another skill. Do not use for ordinary blueprint editing or synchronization.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `regenerate-blueprints._rtx.interface.regenerate-blueprint` — Generate a refreshed blueprint YAML for one existing skill under /tmp.
  - Caller: `regenerate-blueprints`
  - Version: 1
  - Alternative: `skill-name`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["skill-name"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
## Purpose

Generate a schema-documented replacement blueprint for one existing skill.

## Rules

- Input is the exact skill directory name.
- Write only `/tmp/<skill-name>_blueprint.yaml`.
- Do not edit the skill's existing `blueprint.yaml`.
- Report the generated path and any validation failure.

---
name: skill-health
description: Use when reading, invalidating, migrating, or recording the local health state of Famulus skills.
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

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
This skill is being built in stages. The current implementation provides the
tested first-pass hashing core that future health-record commands will use.
This version hashes declared file roots directly and expands declared directory
roots recursively; it does not yet follow Python imports, Markdown links, or
other transitive file references.

Do not certify skill health from this skill until the exported health-record
interfaces exist and are documented in the generated blueprint interface block.

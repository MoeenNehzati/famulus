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
This version hashes declared file roots directly, expands declared directory
roots recursively, and includes Python dependency-explorer results for
PythonMachineInterface runtimes. The dependency explorer follows same-skill
modules loaded during route-smoke, declared `DispatchCall` targets recursively,
resolved command-runtime files, imported `officina` modules, and existing
address-like Markdown file references.

For same-skill Python imports that happen only inside normal runtime branches,
the owning interface's route-smoke hook is the declaration surface: it must
import behavior-relevant modules cheaply and without real side effects.

Do not certify skill health from this skill until the exported health-record
interfaces exist and are documented in the generated blueprint interface block.

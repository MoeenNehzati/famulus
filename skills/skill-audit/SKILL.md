---
name: skill-audit
description: Use when certifying local skill audit state after mechanical checks and blueprint exactness checks should write fresh audit records.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Skill Version: 1

Uses Interfaces:
- `skill-audit.machine.certify -> skill-drift.machine.compute-hashes@1`
- `skill-audit.machine.certify -> skill-drift.machine.drift-status@1`
- `skill-audit.machine.certify -> skill-maker.machine.sync-blueprints@1`

Public Interfaces:
- `skill-audit.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `certify` — Run audit gates and write fresh audit records for selected installed skills or all observed blueprint-backed installed skills.
  - `dispatcher --caller-skill skill-audit skill-audit.machine.certify certify [target ...] [--json]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
## Audit Rules

Use this skill only after the target skill's blueprint has been reviewed against
its actual behavior. The blueprint must be exact: every declared file root,
runtime dependency, permission, state path, interface call, and callable surface
must correspond to real behavior, and no behavior-relevant dependency may be
omitted.

Mechanical certification uses skill-maker for blueprint sync checks and
skill-drift for current hash computation and post-write drift verification.

`SKILL.md` may describe user interaction, decision flow, and interface
orchestration. It must not contain direct execution logic. Executable behavior,
whether public or private, belongs behind a declared interface in
`blueprint.yaml`.

Implicit references count. If instructions, docs, docstrings, runtime code, or
tests say to inspect a directory, script family, helper module, generated
artifact, state file, config file, external command surface, or similar
behavioral source without naming a direct path, treat that as a dependency that
must be represented in the blueprint.

If mechanical checks fail, semantic exactness fails, hash computation fails, or
post-write drift verification fails, report the failure and do not treat the
skill as certified.

Targets may be omitted, named by installed skill name, or given as an exact
skill root path. With no targets, certify every observed blueprint-backed
installed skill reported by the drift hash interface.

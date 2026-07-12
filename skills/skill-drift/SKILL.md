---
name: skill-drift
description: Use when reading or checking the local audit state of Famulus skills.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Dependencies: none

Interface Version: 1

Exported Interfaces:
- `skill-drift.machine.compute-hashes`
- `skill-drift.machine.drift-status`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `compute-hashes` — Compute current audit hashes for selected installed skill names, exact skill root paths, or all observed blueprint-backed skills.
  - `dispatcher --caller-skill skill-drift skill-drift.machine.compute-hashes compute-hashes [target ...] [--json]`
- `drift-status` — Read derived audit status for selected installed skill names, exact skill root paths, or all observed installed skills.
  - `dispatcher --caller-skill skill-drift skill-drift.machine.drift-status status [target ...] [--json]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
Use the exported status machine interface to read installed skill drift state.
Use the exported hash-computation machine interface when another skill needs the
current audit hashes without reading or comparing audit records.

This skill compares the hashes recorded in an installed skill's local audit
record with hashes computed from the currently installed skill files. With no
target skill names, the checker scans every supported assistant host's
installed skill roots and reports every discovered skill. The default status
output is a Markdown table and is saved under `_build/<date-time>.md`; `--json`
keeps the machine-readable output on stdout. Report the generated result
directly. Do not rewrite records, certify skills, or reinterpret the checker
output in the skill body.

Missing records, corrupt records, schema mismatches, skill mismatches, and hash
drift are all reported as audit-stale with specific concerns. A skill is reported
audit-current only when the recorded hash state exactly matches the current hash
state.

Hash computation is stricter than status reporting: it requires the target skill
to have a blueprint and fails if `blueprint.yaml` is missing.

Writing or refreshing audit records belongs to a separate certifier skill, not
this skill. The `_build/` report artifact is only a local rendered status
report.

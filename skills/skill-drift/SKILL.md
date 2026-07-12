---
name: skill-drift
description: Use when reading or checking the local audit state of Famulus skills.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `skill-drift.machine.compute-hashes`
- `skill-drift.machine.drift-status`
- `skill-drift.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `compute-hashes` — Compute current audit hashes for selected installed skill names, exact skill root paths, or all observed blueprint-backed skills.
  - `dispatcher --caller-skill skill-drift skill-drift.machine.compute-hashes compute-hashes [target ...] [--json]`
- `drift-status` — Read derived audit status for selected installed skill names, exact skill root paths, or all observed installed skills.
  - `dispatcher --caller-skill skill-drift skill-drift.machine.drift-status status [target ...] [--json] [--with-test-validate]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
Use the exported status machine interface to read installed skill drift state.
Use the exported hash-computation machine interface when another skill needs the
current audit hashes without reading or comparing audit records.

This skill compares the hashes recorded in an installed skill's local audit
record with hashes computed from the currently installed skill files. This is
the audit signal: it answers whether the existing certification record still
matches the certified artifact and audit standards.

The optional `--with-test-validate` flag adds a separate health signal by
running repo validators and each target skill's tests when those check surfaces
are available. Health failures are not audit drift: they mean the current repo
does not pass checks now. When health checks are requested, `overall_status`
uses the combined attention rule:

```text
needs-attention = audit-stale OR health-failed
```

Keep these signals separate when reporting results. Do not say a skill is
audit-stale merely because tests or validators failed.

With no target skill names, the checker scans every supported assistant host's
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

Interface hashes follow file-backed LLM bindings, declared `behavior_sources`,
machine `invocation.behavior_sources`, Python invocation entrypoints, traced
Python dependencies, and recursively declared `uses_interfaces`. Runtime
`direct_io` resources such as inboxes, calendars, user files, remote files, and
API responses are declarations only; their live contents are not hash inputs.

Writing or refreshing audit records belongs to a separate certifier skill, not
this skill. The `_build/` report artifact is only a local rendered status
report.

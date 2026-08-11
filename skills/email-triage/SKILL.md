---
name: email-triage
description: >-
  Use when the user asks for inbox-level email triage or processing. Do not use for ordinary email access, sending, or analysis of a single message.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: communications, personal-organization; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `email-triage.source.gateway -> email-triage._rtx.interface.fetch-filtered-envelopes@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-clear-failure@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-filter-envelopes@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-finalize-triage@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-get-cutoff@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-log-decision@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-mark-failure@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-prune-log@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-update-watermark@1`
- `email-triage.source.gateway -> email-triage._rtx.interface.scripts-write-metrics@1`
- `email-triage.source.gateway -> email-triage.source.instructions-triage.interface.triage@2`
- `email-triage.source.instructions-triage -> email-client.interface.default@3`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.fetch-filtered-envelopes@1`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.scripts-clear-failure@1`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.scripts-finalize-triage@1`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.scripts-get-cutoff@1`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.scripts-log-decision@1`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.scripts-mark-failure@1`
- `email-triage.source.instructions-triage -> email-triage._rtx.interface.scripts-prune-log@1`
- `email-triage.source.instructions-triage -> list-manager.interface.default@1`

Public Interfaces:
- `email-triage.interface.default`
- `email-triage.interface.triage`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `email-triage.interface.default` — Primary LLM-facing skill instructions.
- `email-triage.interface.triage` — Scans emails received since the last triage run and routes extracted action items to the right list.
<!-- END BLUEPRINT INTERFACES -->
# Email Triage

Use `email-triage.interface.triage` for every request within this skill's trigger
scope. Load that interface's detailed instructions and begin triage directly.

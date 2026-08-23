---
name: milestone-logging
description: Use when starting or completing substantive agent work that needs durable, role-labelled progress records and optional run recovery.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: task-automation, assistant-assurance; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 1

Uses Interfaces:
- `milestone-logging.source.gateway -> milestone-logging._rtx.interface.record@1`
- `milestone-logging.source.gateway -> milestone-logging._rtx.interface.timeline@1`

Public Interfaces:
- `milestone-logging.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `milestone-logging.interface.default` — Record a role-labelled progress event or inspect existing milestone records.
<!-- END BLUEPRINT INTERFACES -->
Skill: milestone-logging

Record progress before the first substantive action, before each distinct work item, every few tool calls, and at completion. For each ordinary record, name the work starting now and how the preceding piece ended. At completion, invoke `record` with `--done` and describe the final outcome. Always supply `--role` on every invocation.

Invoke `record` to create a record. If one invocation fails, report that exact failure once and continue the task; do not invent a record or retry blindly.

Use `--run` only for work that must outlive the current session. Put recovery-relevant state in its typed fields.

For a diagnostic read, invoke `timeline`; use its explicit list or run route to inspect existing records.

---
name: llm-wakeup
description: Use when the user wants to schedule a supported assistant session after a usage reset, infer a wakeup from a timeout, or manage per-session automatic wakeups.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-interaction; topics: session-management, task-automation; visibility: listed
Activation: user-request, skill-workflow, scheduled-job; persistent modifier: no

Skill Version: 1

Uses Interfaces:
- `llm-wakeup.source.gateway -> wakeup.interface.auto-policy@1`
- `llm-wakeup.source.gateway -> wakeup.interface.explicit-schedule@1`
- `llm-wakeup.source.gateway -> wakeup.interface.infer-schedule@1`

Public Interfaces:
- `llm-wakeup.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `llm-wakeup.interface.default` — Primary LLM-facing wakeup routing instructions.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: llm-wakeup

## Route by intent

- Enable, disable, or inspect automatic wakeups: invoke `auto-policy`.
- Schedule after a timeout when provider, session, or reset may need discovery:
  invoke `infer-schedule`.
- Schedule when provider, session, and reset are all explicit: invoke
  `explicit-schedule`.

Pass `message` and `delay` only when the user supplies them. Otherwise preserve
the interface defaults.

Do not guess a provider, session, alias, or reset time after an interface reports
ambiguity. For `auto-policy`, ask for the missing provider or canonical session
and retry `auto-policy`. For `infer-schedule`, ask for the missing explicit value;
when provider, session, and reset are all explicit, route to `explicit-schedule`.

For `auto-policy`, report the resolved provider, canonical session, and policy
state. For `infer-schedule` and `explicit-schedule`, report the resolved
provider, canonical session, and local scheduled time. Report failures plainly
without claiming a wakeup exists.

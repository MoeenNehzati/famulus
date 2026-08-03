---
name: llm-wakeup
description: Use when the user wants to schedule a Claude or Codex session after a usage reset, infer a wakeup from a timeout, or manage per-session automatic wakeups.
---

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
ambiguity. Ask for the missing explicit value or route to `explicit-schedule`.

Report the resolved provider, canonical session, and local scheduled time from
the interface result. Report failures plainly without claiming a wakeup exists.


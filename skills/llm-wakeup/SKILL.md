---
name: llm-wakeup
description: >-
  Use when the user asks to schedule or manage an automatic assistant-session wakeup after a usage reset or timeout.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `wakeup.interface.auto-policy` — Manage opt-in automatic near-limit wakeup scheduling for one provider session.
  - Caller: `llm-wakeup`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["action", "provider", "session-id"], "stdin": null}
    Required options: []; positional arity: 1..3; stdin: forbidden
- `wakeup.interface.explicit-schedule` — Persist a guarded wakeup for an explicitly identified provider session and reset time.
  - Caller: `llm-wakeup`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--delay": "duration", "--message": "message"}, "positionals": ["provider", "session-id", "reset-time"], "stdin": null}
    Required options: []; positional arity: 3..3; stdin: forbidden
- `wakeup.interface.infer-schedule` — Infer provider, canonical session, and reset time before persisting a guarded wakeup.
  - Caller: `llm-wakeup`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--delay": "duration", "--message": "message", "--text": "timeout-or-resume-text"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `wakeup.interface.setup` — Reconcile or remove the feature-owned wakeup integration.
  - Caller: `llm-wakeup`
  - Version: 1
  - Alternative: `setup`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--bin-dir": "DIR", "--canonical-python": "FILE", "--native-root": "DIR", "--plugin-root": "DIR"}, "positionals": ["setup"], "stdin": null}
    Required options: ["--bin-dir", "--canonical-python", "--native-root", "--plugin-root"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `teardown`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--bin-dir": "DIR", "--native-root": "DIR"}, "positionals": ["teardown"], "stdin": null}
    Required options: ["--bin-dir", "--native-root"]; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: llm-wakeup

## Route by intent

- Enable, disable, or inspect automatic wakeups: invoke `auto-policy`.
  The level is the action argument. `on` wakes the session only when the
  provider refused a turn for lack of quota and the session stopped there.
  `force` wakes it at reset whenever usage neared the limit, refused or not;
  pass it only when the user asks to be woken either way. `off` removes the
  policy, and `status` reports the level in effect.
- Schedule after a timeout when provider, session, or reset may need discovery:
  invoke `infer-schedule`.
- Schedule when provider, session, and reset are all explicit: invoke
  `explicit-schedule`.
- Set up, refresh, or tear down the optional wakeup commands and dedicated
  due-delivery registration: invoke `setup`. For setup, first obtain the exact
  selected Python fingerprint and current plugin root; do not discover Python.

Pass `message` and `delay` only when the user supplies them. Otherwise preserve
the interface defaults.

Do not guess a provider, session, alias, or reset time after an interface reports
ambiguity. For `auto-policy`, ask for the missing provider or canonical session
and retry `auto-policy`. For `infer-schedule`, ask for the missing explicit value;
when provider, session, and reset are all explicit, route to `explicit-schedule`.

For `auto-policy`, report the resolved provider, canonical session, and policy
state, including which level is in effect. For `infer-schedule` and
`explicit-schedule`, report the resolved provider, canonical session, and local
scheduled time. Report failures plainly
without claiming a wakeup exists.

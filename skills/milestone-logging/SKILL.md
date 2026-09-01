---
name: milestone-logging
description: Use when starting or completing substantive agent work that needs durable, role-labelled progress records and optional run recovery.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `milestone-logging._rtx.interface.record` — Append a role-labelled progress milestone or completion record. Progress supplies doing and optional prev; completion supplies the closing result as the done value instead of positional messages. Typed recovery state can also be mirrored into a durable run journal.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `milestone`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--attempt": "ATTEMPT", "--done": "PREV", "--event": "EVENT", "--evidence": "PATH", "--path": true, "--role": "ROLE", "--run": "ID", "--state": "STATE", "--step": "STEP", "--task": "TASK"}, "positionals": ["DOING", "PREV"], "stdin": null}
    Required options: []; positional arity: 0..2; stdin: forbidden
- `milestone-logging._rtx.interface.timeline` — List milestone sessions, render one session with its transcript events, or reconstruct one durable run as text or JSON.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `timeline`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--json": true, "--list": true, "--run": "ID", "--slow": "SECONDS", "-l": true}, "positionals": ["SESSION"], "stdin": null}
    Required options: []; positional arity: 0..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
Skill: milestone-logging

Use `record` before the first substantive action, before each distinct work item, every few tool calls, and at completion. Every record is role-labelled. A progress record names the work starting now and how the preceding piece ended; a completion record closes the final work item with its outcome.

If one `record` invocation fails, report that exact failure once and continue the task; do not invent a record or retry blindly.

To tell the user where this session's milestones are being written, invoke `milestone-logging._rtx.interface.record` with only `--path` and report the complete returned path verbatim.

Use the durable-run mode only for work that must outlive the current session. Put recovery-relevant state in its typed fields.

For a diagnostic read, invoke `timeline`; use its explicit list or run route to inspect existing records.

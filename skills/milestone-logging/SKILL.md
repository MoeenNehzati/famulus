---
name: milestone-logging
description: Use when starting or completing substantive agent work that needs durable, role-labelled progress records and optional run recovery.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `milestone-logging._rtx.interface.list-sessions` — List known milestone sessions and label counts as log files.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `list-sessions`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `milestone-logging._rtx.interface.read-run-json` — Return one durable run as structured JSON with all retained typed metadata.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `read-run-json`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["RUN"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `milestone-logging._rtx.interface.record-completion` — Append completion with session-retained typed metadata and an optional additive run mirror.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `record-completion`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--attempt": "ATTEMPT", "--event": "EVENT", "--evidence": "PATH", "--role": "ROLE", "--run": "ID", "--state": "STATE", "--step": "STEP", "--task": "TASK"}, "positionals": ["RESULT"], "stdin": null}
    Required options: ["--role"]; positional arity: 1..1; stdin: forbidden
- `milestone-logging._rtx.interface.record-progress` — Append progress with typed metadata retained in the session; run only adds identity fields and an identical journal mirror.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `record-progress`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--attempt": "ATTEMPT", "--event": "EVENT", "--evidence": "PATH", "--role": "ROLE", "--run": "ID", "--state": "STATE", "--step": "STEP", "--task": "TASK"}, "positionals": ["DOING", "PREV"], "stdin": null}
    Required options: ["--role"]; positional arity: 1..2; stdin: forbidden
- `milestone-logging._rtx.interface.run-path` — Validate a run identifier and print its journal path without appending.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `run-path`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["RUN"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `milestone-logging._rtx.interface.session-path` — Print the selected session path without appending.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `session-path`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `milestone-logging._rtx.interface.show-latest-session` — Render the latest session and all retained typed metadata; optional slow adds annotations only.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `show-latest-session`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--slow": "SECONDS"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `milestone-logging._rtx.interface.show-run` — Render one durable run as text with all retained typed metadata.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `show-run`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["RUN"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `milestone-logging._rtx.interface.show-session` — Render one exact session and retained typed metadata; optional slow adds annotations only.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `show-session`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--slow": "SECONDS"}, "positionals": ["SESSION"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
Skill: milestone-logging

Use `record-progress` before the first substantive action, before each distinct work item, and every few tool calls; use `record-completion` at completion. Both require `--role`. Progress names the work starting now and optionally how the preceding piece ended; completion takes one closing result.

If one recording invocation fails, report that exact failure once and continue the task; do not invent a record or retry blindly.

To report the current session path, invoke `session-path`; for a durable run path, invoke `run-path` with its ID. Neither path interface appends.

Typed fields are always retained in the session record. Add `--run ID` only when work must outlive the session; it adds run/session/agent identity and an identical run-journal mirror without changing other field effects.

For diagnostics, select exactly one explicit interface: `list-sessions`, `show-latest-session`, `show-session`, `show-run`, or `read-run-json`. Omit `--slow` for no annotations; a positive finite threshold adds annotations only.

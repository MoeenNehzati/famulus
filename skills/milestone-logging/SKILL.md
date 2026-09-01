---
name: milestone-logging
description: Use when starting or completing substantive agent work that needs durable, role-labelled progress records and optional run recovery.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

### Managed setup gate

Activate this gate only for an invocation of this skill's interfaces or an exact managed lifecycle entry below. Generic setup prose does not activate this gate.
Keep the original caller, interface, version, arguments, and stdin outside the ledger; the manager receives only its public continuation identity.

Managed lifecycle entries:
- Setup `milestone-logging.interface.setup@1` routes to `begin(setup, milestone-logging.interface.setup, ORIGINAL_CALLER, ORIGINAL_INTERFACE, ORIGINAL_VERSION)`; teardown `milestone-logging.interface.teardown@1` routes to `begin(teardown, milestone-logging.interface.setup, ORIGINAL_CALLER, ORIGINAL_INTERFACE, ORIGINAL_VERSION)`.

For an ordinary invocation, use this exact sequence:

1. Call `setup-interface-manager._rtx.interface.status@1` for the original target interface. If it is `unmanaged`, run the original request normally. If it is `setup_busy`, follow only its recovery result.
2. If it is `setup_required`, obtain permission, then call `setup-interface-manager._rtx.interface.begin@1` as `begin(setup, ROOT_SETUP_INTERFACE, ORIGINAL_CALLER, ORIGINAL_INTERFACE, ORIGINAL_VERSION)`, where `ROOT_SETUP_INTERFACE` is the returned root setup interface.
3. Follow only the returned exact structured current step: call `setup-interface-manager._rtx.interface.run-markdown@1` for a Markdown step, follow its returned instructions, then call `setup-interface-manager._rtx.interface.settle@1`; call `setup-interface-manager._rtx.interface.run-python@1` for a Python step. Repeat until the flow is ready.
4. Perform the ready recheck with `setup-interface-manager._rtx.interface.status@1` for the original target and require `ready`; then call `setup-interface-manager._rtx.interface.authorize@1` with the original target plus caller, interface, and version.
5. Retry the original request exactly once, with its original arguments and stdin, only when `authorize` returns `resume_original: true`.

For an exact managed setup or teardown invocation, do not launch it directly; use its listed `setup-interface-manager._rtx.interface.begin@1` route. A manager result that names an exact structured current step is the only bypass of this gate.

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
- `setup-interface-manager._rtx.interface.authorize` — Resume only unmanaged or ready targets and atomically claim every ready managed receipt.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["TARGET_INTERFACE", "ORIGINAL_CALLER", "ORIGINAL_INTERFACE", "ORIGINAL_VERSION"], "stdin": null}
    Required options: []; positional arity: 4..4; stdin: forbidden
- `setup-interface-manager._rtx.interface.begin` — Begin exactly one setup or teardown flow for a managed root and redacted continuation.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["OPERATION", "ROOT_SETUP", "ORIGINAL_CALLER", "ORIGINAL_INTERFACE", "ORIGINAL_VERSION"], "stdin": null}
    Required options: []; positional arity: 5..5; stdin: forbidden
- `setup-interface-manager._rtx.interface.run-markdown` — Return only the finite map's exact Markdown instructions and await independent settlement.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "INTERFACE"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
- `setup-interface-manager._rtx.interface.run-python` — Run the exact current Python action, its verifier, and the receipt transition in one call.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "INTERFACE"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: permitted
- `setup-interface-manager._rtx.interface.settle` — Independently verify and settle only the exact current Markdown action.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["FLOW_ID", "INTERFACE"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: forbidden
- `setup-interface-manager._rtx.interface.status` — Return unmanaged, ready, setup-required, or setup-busy without mutating claims.
  - Caller: `milestone-logging`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["TARGET_INTERFACE"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
Skill: milestone-logging

Use `record` before the first substantive action, before each distinct work item, every few tool calls, and at completion. Every record is role-labelled. A progress record names the work starting now and how the preceding piece ended; a completion record closes the final work item with its outcome.

If one `record` invocation fails, report that exact failure once and continue the task; do not invent a record or retry blindly.

To tell the user where this session's milestones are being written, invoke `milestone-logging._rtx.interface.record` with only `--path` and report the complete returned path verbatim.

Use the durable-run mode only for work that must outlive the current session. Put recovery-relevant state in its typed fields.

For a diagnostic read, invoke `timeline`; use its explicit list or run route to inspect existing records.

---
name: email-triage
description: >-
  Use when the user asks for inbox-level email triage or processing. Do not use for ordinary email access, sending, or analysis of a single message.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `email-triage._rtx.interface.fetch-filtered-envelopes` — Fetch email envelopes for one account through email-client and emit only envelopes strictly after the triage watermark.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `short-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--after": "YYYY-MM-DD", "--dedup-against": "todo|triage", "--rescan-after": "ISO_CUTOFF", "-a": "account"}, "positionals": [], "stdin": null}
    Required options: ["--after", "-a"]; positional arity: 0..0; stdin: forbidden
  - Alternative: `long-account`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--account": "account", "--after": "YYYY-MM-DD", "--dedup-against": "todo|triage", "--rescan-after": "ISO_CUTOFF"}, "positionals": [], "stdin": null}
    Required options: ["--account", "--after"]; positional arity: 0..0; stdin: forbidden
- `email-triage._rtx.interface.scripts-clear-failure` — Clear a latched triage failure after its cause is fixed, without advancing the watermark.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["reason"], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden
- `email-triage._rtx.interface.scripts-filter-envelopes` — Filter JSON envelopes (from email-client's mail-list, piped via stdin) to those strictly after the triage watermark.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"-a": "account"}, "positionals": [], "stdin": null}
    Required options: ["-a"]; positional arity: 0..0; stdin: permitted
- `email-triage._rtx.interface.scripts-finalize-triage` — Ordered, idempotent finalization of one triage run — writes metrics, then (only on success and only if no failure is latched) advances the watermark, recording the run id so a replayed call is a safe no-op.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--accounts": "a,b", "--added-todo": "N", "--added-triage": "N", "--deduped": "N", "--run-id": "id", "--skipped": "N", "--total-scanned": "N"}, "positionals": [], "stdin": null}
    Required options: ["--added-todo", "--added-triage", "--run-id", "--skipped", "--total-scanned"]; positional arity: 0..0; stdin: forbidden
- `email-triage._rtx.interface.scripts-get-cutoff` — Return the cutoff date for the current triage run, with a fallback if no watermark exists.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden
- `email-triage._rtx.interface.scripts-log-decision` — Append a triage classification decision for one email to triage.log.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["account", "id", "from", "subject", "DECISION", "reason"], "stdin": null}
    Required options: []; positional arity: 6..6; stdin: forbidden
- `email-triage._rtx.interface.scripts-mark-failure` — Record that this triage run failed, so update-watermark refuses to advance and the scheduled health check reports it.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["reason"], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden
- `email-triage._rtx.interface.scripts-prune-log` — Drop triage.log entries older than 30 days and print a one-line summary.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden
- `email-triage._rtx.interface.scripts-update-watermark` — Advance the triage watermark to the current timestamp. Refuses if scripts-mark-failure was called earlier in this run.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: forbidden
- `email-triage._rtx.interface.scripts-write-metrics` — Write metrics from a triage run (emails scanned, added to lists, skipped, deduped) to status.json for visibility and debugging.
  - Caller: `email-triage`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--accounts": "TEXT", "--added-todo": "N", "--added-triage": "N", "--deduped": "N", "--skipped": "N", "--total-scanned": "N"}, "positionals": [], "stdin": null}
    Required options: ["--added-todo", "--added-triage", "--skipped", "--total-scanned"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `email-triage.source.instructions-triage.interface.triage@2` — Scans emails received since the last triage run and routes extracted action items to the right list.
- `setup-python-environment.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact selected Python environment without MCP.
<!-- END BLUEPRINT INTERFACES -->
# Email Triage

Before loading or invoking the detailed triage workflow, follow
`setup-python-environment.interface.repair-selected-packages` for this owner's exact
declaration `["keyring"]`. Complete the full Task 2 fingerprint procedure; on any
failure, stop before fetching email or invoking a triage interface.

Use `email-triage.interface.triage` for every request within this skill's trigger
scope. Load that interface's detailed instructions and begin triage directly.

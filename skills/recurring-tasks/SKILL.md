---
name: recurring-tasks
description: >-
  Use when the user asks to set up or manage a recurring AI job. Do not use for one-off commands or generic scheduler questions.
---


<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `recurring-tasks._rtx.interface.scripts-disable` — Disable a job by setting enabled: false in jobs.yaml and syncing native scheduler entries.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["name"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-enable` — Enable a job by setting enabled: true in jobs.yaml and syncing native scheduler entries.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["name"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-healthcheck` — Run pre-flight and per-job health checks for all enabled recurring tasks and return nonzero when any check fails.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-remove-context` — Remove the shared native set only when the selected recurring config root is its current owner.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-setup` — Capture the selected Python and plugin root, initialize recurring-owned state without default jobs, and reconcile the shared scheduler set.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--canonical-python": "FILE", "--plugin-root": "DIR"}, "positionals": [], "stdin": null}
    Required options: ["--canonical-python", "--plugin-root"]; positional arity: 0..0; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-status` — List active recurring scheduler entries, next fire times, and service status.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-sync` — Regenerate native scheduler entries from jobs.yaml.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-test` — Trigger a job immediately through the native scheduler, then wait (bounded) for its run record and report whether the job actually succeeded.
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["name"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden
- `recurring-tasks._rtx.interface.scripts-view-logs` — Tail the run log for a job (default 50 lines).
  - Caller: `recurring-tasks`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--lines": "N"}, "positionals": ["job-name"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `setup-dispatcher-runtime.interface.repair-selected-packages@1` — Repair the core or one caller-owned package declaration in the exact dispatcher runtime without MCP.
<!-- END BLUEPRINT INTERFACES -->

# Recurring Tasks

Manage explicitly authorized recurring AI jobs through recurring-owned durable
state and the native per-user scheduler. Making this skill available does not
create, enable, or run a job.

## Context and ownership

Use the host-loaded
`setup-dispatcher-runtime.interface.repair-selected-packages` procedure for
feature `recurring-tasks` and the exact deduplicated declaration `["PyYAML"]`.
Run its complete initial literal-`python` fingerprint, pip/installability and
target-writability checks, repair and dry-run steps, and final fingerprint.
Require the final fingerprint to be byte-equal to the initial fingerprint.
Retain its canonical absolute executable; do not select or repair another
Python.

Resolve the current selected plugin root from the host-loaded location of this
skill. Invoke `scripts-setup` with that root and the retained canonical Python.
Setup captures both values in the validated recurring-owned descriptor. A
plugin-cache path change is repaired by rerunning setup; there is no generic
updater or installer-runtime indirection.

All feature contexts share one native scheduler set for the host account.
Setup, sync, enable, or disable replaces that complete set from the selected
durable jobs configuration. The canonical absolute durable recurring config
root is the `owner_id`; the last successful reconciliation owns the set. A
failed reconciliation cannot claim ownership, and non-owner removal leaves the
shared set unchanged.

## Operations

Use the generated interfaces as follows:

- `scripts-setup` creates or refreshes recurring-owned integration and replaces
  the shared scheduler set and healthcheck sentinel where supported. First
  setup creates an empty jobs file unless validated user or legacy jobs exist.
- `scripts-sync` replaces the shared scheduler set from enabled definitions.
- `scripts-enable` and `scripts-disable` change authorization for one job and
  reconcile its registration.
- `scripts-status` reports configured state and the shared native set.
- `scripts-test` triggers one job and waits, bounded, for a fresh outcome
  record; never treat scheduler trigger acceptance as job success.
- `scripts-view-logs` reads the selected durable context's bounded job log.
- `scripts-healthcheck` checks descriptor, source, scheduler, registration,
  activity, and outcome health. On platforms without the independent sentinel,
  invoke it on demand.
- `scripts-remove-context` removes shared registrations only when the selected
  durable config root is their owner, while preserving definitions and history.

Before invoking an interface, read the selected job and current status needed
for its arguments. Report a failed operation without claiming scheduler state
changed.

## Job definitions

Each job has a unique name, description, command, restricted cron schedule,
enabled flag, and optional success contract. The platform-neutral job schema
does not promise one identical Cartesian cron grammar across managed hosts.

Linux accepts exactly five fields. Minute and hour each accept `*`, `*/N` for
positive `N`, or one bounded bare integer (minute `0` through `59`, hour `0`
through `23`); day-of-month and month must each be `*`; weekday accepts `*` or
one bare integer from `0` through `7` (`0` and `7` are Sunday). Ranges and
comma lists are rejected.

macOS currently supports that same component-wise five-field subset, including
the Cartesian combinations of accepted minute and hour values. Windows supports
all-wildcard schedules; `*/N * * * *` for positive `N`; `M * * * *` for a
bounded minute `M`; and `M H * * D` for bounded `M`, bounded hour `H`, and an
optional weekday `D` (`*` or `0` through `7`). It does not support stepped or
wildcard minutes combined with a fixed hour or weekday, nor a stepped hour.

A missing success contract means exit-code-only success. If the job writes its
own status, require the expected inner status. Tolerated transient failures
must match both an explicitly allowed exit code and the current run's output
pattern.

Review the command, working directory, connected accounts, and intended writes
before enabling a job. Enabled jobs run without an interactive approval prompt
and may run outside the host sandbox; disabling a job ends that continuing
authorization.

Do not adopt presets merely because recurring setup was selected. Install a
preset only after explicit user selection. Preserve validated user-authored and
migrated legacy jobs, including a pre-existing legacy due-wakeup entry; do not
replace them from `default_jobs.yaml`. Recurring setup does not install
interactive launchers, wakeup integration, or unrelated optional packages.

## Execution and health invariants

The scheduler invokes the recurring executor with ordered shell-free argv using
the captured canonical Python, selected plugin root, descriptor, job, and log
roots. Paths containing spaces remain one argument. Do not introduce
the former runtime pointer, resolver coupling, legacy resolver entry point,
Python discovery, or a generic runtime wrapper. `invoke-skill` remains the
intentional virtual job token and enters the existing agent-command path.

The executor alone decides success from the process exit code, current-run
status, and declared success contract. It records one outcome with a unique run
ID. Test and healthcheck read that outcome; neither invents a second success
definition or substitutes log modification time.

Sync and healthcheck use the same renderer. If a run is active, healthcheck
applies the job timeout; otherwise it verifies registration activity and a
fresh successful outcome. Logs rotate after the configured cap with one prior
generation retained.

If the captured source disappears, report the exact source failure. Restore the
selected plugin or rerun recurring setup, then run `scripts-sync`. Do not
rewrite native registrations or the schedule descriptor by hand.

## Removal

The general installer does not own recurring setup, diagnosis, repair, or
teardown. Use `scripts-remove-context` for owner-checked native teardown.
Removal preserves recurring configuration and history; delete those only in a
separately authorized data-retention operation.

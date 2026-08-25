---
name: recurring-tasks
description: >-
  Use when the user asks to set up or manage a recurring AI job. Do not use for one-off commands or generic scheduler questions.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: task-automation, system-maintenance; visibility: featured
Activation: user-request, skill-workflow, scheduled-job; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-disable@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-enable@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-healthcheck@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-remove-context@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-setup@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-status@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-sync@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-test@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-view-logs@1`

Public Interfaces:
- `recurring-tasks.interface.default`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `recurring-tasks.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->

# Recurring Tasks

Manage explicitly authorized recurring AI jobs through the active installation
context and its native per-user scheduler. Installation only makes this skill
available; it does not create, enable, or run a job.

## Context and ownership

Load the active installation before every operation. If no valid active
context exists, complete or repair installation before recurring setup.

Each context owns an independent namespace keyed by `installation_id`:

- native scheduler registrations and healthcheck registration;
- mutable job definitions;
- run logs, outcome records, and in-flight state; and
- the recorded scheduler owner.

Standard and multiple development contexts may coexist. Never infer ownership
from the current checkout or process environment. Render registrations only
from the validated schedule descriptor and context-owned job configuration.

On the first standard operation, migrate only recognized legacy standard jobs,
logs, owner records, registrations, and healthcheck markers. Refuse ambiguous
or foreign state rather than adopting it.

## Operations

Use the generated interfaces as follows:

- `scripts-setup` initializes the selected context, migrates recognized legacy
  standard state when needed, synchronizes enabled jobs, and installs an
  independent healthcheck sentinel only where the platform supports it.
- `scripts-sync` reconciles enabled definitions with native registrations.
- `scripts-enable` and `scripts-disable` change authorization for one job and
  reconcile its registration.
- `scripts-status` reports configured and native state for this context.
- `scripts-test` triggers one job and waits, bounded, for a fresh outcome
  record; never treat scheduler trigger acceptance as job success.
- `scripts-view-logs` reads this context's bounded job log.
- `scripts-healthcheck` checks descriptor, source, scheduler, registration,
  activity, and outcome health. On platforms without the independent sentinel,
  invoke it on demand.
- `scripts-remove-context` removes only this context's registrations, sentinel,
  and owner record while preserving job definitions, logs, and history.

Before invoking an interface, read the selected job and current status needed
for its arguments. Report a failed operation without claiming scheduler state
changed.

## Job definitions

Each job has a unique name, description, command, restricted cron schedule,
enabled flag, and optional success contract. The accepted cron subset is `*`,
`*/N`, or one bare integer per field; ranges and comma lists are rejected.

A missing success contract means exit-code-only success. If the job writes its
own status, require the expected inner status. Tolerated transient failures
must match both an explicitly allowed exit code and the current run's output
pattern.

Review the command, working directory, connected accounts, and intended writes
before enabling a job. Enabled jobs run without an interactive approval prompt
and may run outside the host sandbox; disabling a job ends that continuing
authorization.

## Execution and health invariants

The scheduler invokes the immutable managed executor through the active
resolver, with explicit context-owned job and log roots. It does not execute
mutable runner files from a checkout or package cache and does not capture
ambient secrets.

The executor alone decides success from the process exit code, current-run
status, and declared success contract. It records one outcome with a unique run
ID. Test and healthcheck read that outcome; neither invents a second success
definition or substitutes log modification time.

Sync and healthcheck use the same renderer. If a run is active, healthcheck
applies the job timeout; otherwise it verifies registration activity and a
fresh successful outcome. Logs rotate after the configured cap with one prior
generation retained.

If the active source disappears, report the exact source failure. Restore that
package or development checkout, repair the same installation context, then
run `scripts-sync`. Do not rewrite native registrations or
the schedule descriptor by hand.

## Removal

Installer uninstall or purge automatically delegates this context's native
registration and sentinel teardown here before removing installer artifacts.
Direct `scripts-remove-context` remains available for standalone teardown and
recovery. Both paths fail closed unless native removal can be verified.

Context removal preserves recurring configuration and history. Delete those
only in a separately authorized data-retention operation.

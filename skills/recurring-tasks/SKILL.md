---
name: recurring-tasks
description: Manage recurring AI job automation via the host's native per-user scheduler (systemd on Linux, launchd on macOS, Task Scheduler on Windows). Define jobs in jobs.yaml, enable/disable/test them, and monitor health.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: task-automation, system-maintenance; visibility: featured
Activation: user-request, skill-workflow, scheduled-job; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `recurring-tasks.source.gateway -> install-assistant-tools.interface.default@2`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-disable@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-enable@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-ensure-agent-env@1`
- `recurring-tasks.source.gateway -> recurring-tasks._rtx.interface.scripts-healthcheck@1`
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

Manages AI-driven recurring job automation through the host's **native
per-user scheduler**. `jobs.yaml` is the single source of truth; a
platform-specific implementation translates it into that host's own scheduler
entries. Command parsing, logging, and success evaluation are shared across
hosts — only how a job gets triggered and launched differs.

## Process

This is the whole skill. Everything else is per-platform rendering, a CLI,
and an installer. Read this before changing anything here; if a change does
not fit this shape, the shape is probably right and the change is probably
wrong.

```
DATA
  jobs.yaml       one entry per job: name, command, schedule, enabled, success?
  registration    the host scheduler's own entry for a job
  outcome record  logs/<job>/latest.json — what the last run actually did
  output log      logs/<job>/run.log — that run's output

RENDER(job) -> registration
    derived from jobs.yaml and the install layout ONLY.
    never from the calling process's environment.

SYNC()                                    # user-invoked
    for each enabled job:      write RENDER(job)
    for each registration with no enabled job:   remove it
    reload the scheduler; activate the enabled registrations

RUN(job)                                  # scheduler-invoked
    mark in-flight
    execute job.command, bounded by a timeout, output -> output log
    outcome := EVALUATE(exit code, status written during THIS run, job.success)
    write outcome record (outcome, started_at, finished_at)
    clear in-flight

EVALUATE(exit_code, inner_status, contract) -> ok | failed(reason)
    the single definition of success. RUN owns it; CHECK never re-derives it.

CHECK()                            # invoked outside the scheduler it inspects
    fail unless the scheduler is reachable
    fail unless the scheduler can resolve the agent command
    for each enabled job:
        fail unless installed registration == RENDER(job)
        if a run is in flight:
            fail if it started longer ago than the job timeout
        else:
            fail unless an outcome record exists
            fail unless the record is newer than 2 x the schedule interval
            fail unless the record says ok
        fail unless the registration is active
    exit nonzero if anything failed    # the caller turns that into a notification
```

**Invariants.** Each one exists because violating it caused a real outage:

1. **One definition of success.** `EVALUATE` decides; `CHECK` only reads what
   was recorded. A second, looser notion of success in `CHECK` is how a job
   that produced nothing stayed green for days.
2. **One source of truth per question.** "Did it run recently?" is answered by
   the outcome record's timestamps — never by a log file's modification time.
   A killed run refreshes that timestamp without ever completing.
3. **One renderer.** `SYNC` and `CHECK` both call `RENDER`. When `CHECK`
   re-derived the expected registration from ambient `PATH` instead, every
   externally invoked run reported drift that did not exist — 12 consecutive false
   alarms.
4. **`CHECK` runs outside the scheduler it inspects**, driven by an
   independent timer, so it still reports when the scheduler itself is what
   has failed.

A job may declare that certain failures are transient — an exit code plus
patterns that must appear in that run's own output. `EVALUATE` applies this
when it decides the outcome, so a tolerated failure is recorded as a success
with its reason; `CHECK` still only reads the record.

**Current deviations** (the code does not yet fully match the above):

- Two `SYNC` implementations exist in the runtime rather than one.

**Key simplifications:**
- No per-job shell wrapper scripts
- No invoke-agent.sh/run-skill.sh layers (invoke-skill is on PATH)
- Job output is captured directly, without shell redirection
- Every job is launched through the same fixed launch-resolver path rather
  than whatever interpreter happened to run the sync — this keeps scheduled
  jobs working across runtime upgrades
- Environment inherited from the host scheduler's own per-user session
  (`AI_AGENT_COMMAND_TEMPLATE` already set there)

## Configuration

### jobs.yaml

```yaml
jobs:
  - name: example-job
    description: "Example: what this job does"
    command: "invoke-skill example-job"  # Can include env vars: VAR=value invoke-skill ...
    schedule: "0 * * * *"                # 5-field cron expression
    enabled: true
    success:                             # optional; omit entirely if the job has no self-reported status
      require_inner_status: ok
```

**Fields:**
- `name` — unique identifier (used for native scheduler entry names, logs)
- `description` — human-readable purpose
- `command` — shell command to execute (can include environment variables)
- `schedule` — cron expression (minute hour * * day-of-week; only the subset each platform's translator supports is accepted — see below)
- `enabled` — whether the job is scheduled
- `success` — optional success contract, see **Did the job actually succeed?** below

## Operations

Use the interfaces listed in the **Dispatcher Interfaces** block above. Key operations:

- **Setup (first time):** `scripts-sync` generates native scheduler entries from jobs.yaml and enables all enabled jobs.
- **Enable/Disable:** `scripts-enable` and `scripts-disable` modify jobs.yaml and resync native scheduler entries.
- **Test:** `scripts-test` runs a job immediately and reports whether it actually succeeded (see below — this is more than "did the scheduler accept the trigger").
- **View logs:** `scripts-view-logs` tails job logs (default 50 lines).
- **Check health:** `recurring-tasks._rtx.interface.scripts-healthcheck` verifies scheduler registration, job activity, and run freshness, and exits nonzero when any check fails. Where an independent sentinel is supported, setup registers it separately and the sentinel shows a desktop popup after every failed check.

## Scheduling

`jobs.yaml` is translated into whichever native scheduler the host provides.
Entry creation, triggering, and status inspection are host-specific; command
parsing, logging, and success evaluation are shared. Registration details are
documented with each host implementation.

Two facts affect how you author a job:

- **Schedule syntax is a restricted cron subset.** Each field accepts `*`, a
  step (`*/N`), or a single bare integer. Ranges (`9-17`) and comma lists
  (`9,12,17`) are rejected by every host translator.
- **Triggering semantics differ by host.** On some hosts a trigger blocks
  until the job finishes; on others it returns as soon as the trigger is
  accepted. Never read "the trigger succeeded" as "the job succeeded" — see
  below.

Scheduler entries are generated from `jobs.yaml` and regenerated on every
sync. Never edit them by hand.

## Did the job actually succeed?

Triggering a job only confirms the scheduler *accepted the trigger*. On some
hosts that call blocks until the job is done; on others it returns
immediately, so a bare "did the trigger succeed" check cannot tell you whether
the job's work succeeded, or whether it even started.

Testing a job accounts for this: after triggering it waits, bounded, for a
fresh outcome record identified by a new per-run id rather than a timestamp
(two runs can finish within the same second), and reports pass or fail from
that record instead of trusting the trigger.

Whether a run counts as successful is decided by combining two signals:

1. The literal process exit code. A non-zero exit (or a process that never
   spawned at all — missing executable, permission denied, etc.) always
   fails the run.
   `success.ignore_exit_codes` can list non-zero codes to treat as non-blocking
   in healthcheck only.
2. An optional self-reported inner status. Some jobs write their own
   `state/status.json` (`{"result": "ok" | "error" | "warning", ...}`) as
   part of an existing status-tracking mechanism of their own; jobs.yaml's
   `success.require_inner_status` can require that value to match. A job
   with no `success:` block declared passes on exit code alone — this
   matters because most jobs have no such self-reported status file, and
   requiring one they never write would make every run of theirs report
   failure.
   `success.ignore_exit_log_patterns` can list regex patterns that must appear
   in the recent run log to classify one of those exit-code failures as a
   tolerated transient condition.

Declare `success: {require_inner_status: ok}` for a job whose own code
writes a status file, and omit `success:` entirely for one that does not —
requiring a status a job never writes would fail every run of it.

Prefer making a job report its own outcome over leaving it on exit code
alone. An agent-driven job commonly exits 0 while accomplishing nothing, so
exit-code-only jobs can record success indefinitely while producing no
result. Where a job persists something, have that step record the status.

## Healthcheck

The healthcheck interface runs pre-flight checks (scheduler manager
reachable, the agent command configured and resolvable by the scheduler) plus
per-job registration, freshness, and activity checks. Its process exit
code reflects the outcome truthfully: `0` when every check passed, `1` when at
least one failed. Setup also installs an independent four-hour sentinel where
supported. The sentinel owns the desktop popup fallback, so launch failures
that occur before the checker starts are still reported.

## Logs and outcomes

Each run appends its output to the job's output log, bracketed by run-start
and run-end markers, and writes a structured outcome record: start and finish
times, the process exit code, the job's self-reported status if it has one,
the overall success verdict, a reason when it failed, and a per-run id.

Read the outcome record rather than re-deriving success from the output log —
testing a job and checking health both read that record, and a second opinion
about what "succeeded" means is how a failing job stayed green (invariant 1).

Output logs are appended and never rotated; manage their size externally.

## Common Tasks

### Add a new job

1. Add entry to `jobs.yaml`
2. Run `scripts-sync`
3. Test with `scripts-test <name>`

### Modify a job's schedule

1. Edit `jobs.yaml`
2. Run `scripts-sync`

### Investigate a job failure

1. Check logs: `scripts-view-logs <name>`
2. Check the structured outcome: `logs/<name>/latest.json`
3. Test manually: `scripts-test <name>`
4. Check native scheduler status — see the **Scheduling per platform** section above for the platform-specific command

## Files

- `jobs.yaml` is the source of truth for job definitions.
- Each job has an output log and an outcome record under its own log
  directory; the healthcheck keeps its own log alongside them.
- Scheduler entries live wherever the host scheduler keeps them and are
  regenerated from `jobs.yaml` on every sync — never edit them by hand.

---
name: recurring-tasks
description: Manage recurring AI job automation via the host's native per-user scheduler (systemd on Linux, launchd on macOS, Task Scheduler on Windows). Define jobs in jobs.yaml, enable/disable/test them, and monitor health.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: system-assistant

Skill Version: 1

Uses Interfaces:
- `recurring-tasks.source.gateway -> install-assistant-tools.interface.default@2`
- `recurring-tasks.source.rtx-run-record -> common.interface.atomic-files@1`
- `recurring-tasks.source.rtx-schedule-backend-init -> common.interface.famulus-paths@1`

Public Interfaces:
- `recurring-tasks.interface.default`
- `recurring-tasks.interface.scripts-disable`
- `recurring-tasks.interface.scripts-enable`
- `recurring-tasks.interface.scripts-ensure-agent-env`
- `recurring-tasks.interface.scripts-healthcheck`
- `recurring-tasks.interface.scripts-job-utils`
- `recurring-tasks.interface.scripts-setup`
- `recurring-tasks.interface.scripts-status`
- `recurring-tasks.interface.scripts-sync`
- `recurring-tasks.interface.scripts-test`
- `recurring-tasks.interface.scripts-view-logs`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `recurring-tasks.interface.scripts-disable` — Disable a job by setting enabled: false in jobs.yaml and syncing native scheduler entries.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-disable <name>`
- `recurring-tasks.interface.scripts-enable` — Enable a job by setting enabled: true in jobs.yaml and syncing native scheduler entries.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-enable <name>`
- `recurring-tasks.interface.scripts-ensure-agent-env` — Idempotently ensure recurring-tasks' systemd AI_AGENT_COMMAND_TEMPLATE is in place. Also run automatically by scripts-setup.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-ensure-agent-env --repo-root DIR --home DIR --bin-dir DIR [--dry-run]`
- `recurring-tasks.interface.scripts-healthcheck` — Run pre-flight and per-job health checks for all enabled recurring tasks; sends a desktop notification on failure.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-healthcheck`
- `recurring-tasks.interface.scripts-job-utils` — Validate the legacy no-argument compatibility surface without changing job state.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-job-utils ...`
- `recurring-tasks.interface.scripts-setup` — Verify prerequisites, sync native scheduler entries from jobs.yaml, install recurring health checks, and list active timers/tasks.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-setup [--migrate-cron]`
- `recurring-tasks.interface.scripts-status` — List active recurring scheduler entries, next fire times, and service status.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-status`
- `recurring-tasks.interface.scripts-sync` — Regenerate native scheduler entries from jobs.yaml.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-sync`
- `recurring-tasks.interface.scripts-test` — Trigger a job immediately through the native scheduler, then wait (bounded) for its run record and report whether the job actually succeeded.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-test <name>`
- `recurring-tasks.interface.scripts-view-logs` — Tail the run log for a job (default 50 lines).
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.interface.scripts-view-logs <job-name> [--lines N]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `recurring-tasks.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->

# Recurring Tasks

Manages AI-driven recurring job automation using each host's **native per-user
scheduler**: systemd user timers on Linux, launchd LaunchAgents on macOS, and
Task Scheduler on Windows. Jobs are defined in `jobs.yaml`, which is the
single source of truth; a platform-specific implementation translates it into
that host's native scheduler entries. Every platform shares the same job
command parsing, logging format, and success-evaluation logic — only how a
job gets triggered and how its process is launched differs.

## Quick Start

The skill provides dispatcher interfaces for all operations:

- Enable/disable jobs
- Test jobs immediately
- View job logs
- Check status and health
- Sync native scheduler entries

See the **Dispatcher Interfaces** block above for the exact commands.

## Architecture (Simplified)

```
jobs.yaml (source of truth)
    ↓
scripts-sync generates native scheduler entries for the current platform
    ↓
the native scheduler fires the job on schedule (or is triggered by scripts-test)
    ↓
a fixed, release-independent launch resolver starts the job runner
    ↓
the job runner parses the command from jobs.yaml (typically: invoke-skill <job-name>)
    ↓
runs the job, capturing output to logs/<job-name>/run.log and writing a
structured per-run outcome to logs/<job-name>/latest.json
```

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
- **Check health:** `scripts-healthcheck` verifies all jobs are running and logs are fresh, and exits non-zero when any check fails. Sends a desktop notification on failure.

## Scheduling per platform

`jobs.yaml` is translated into whichever native scheduler the current host
uses. All three translations share the same job runner, log format, and
success evaluation described further down — only entry creation, triggering,
and status inspection differ.

### Linux — systemd user timers

- Each job becomes a `.timer` + `.service` pair named `ai-<name>.timer` /
  `ai-<name>.service` under `~/.config/systemd/user/`.
- The service's `PATH` is built explicitly (launcher directory, the launch
  resolver's own directory, `~/.npm-global/bin`, `~/.local/bin`, and the
  standard system directories) rather than relying on shell inheritance.
- `DBUS_SESSION_BUS_ADDRESS` is set to `unix:path=%t/bus`. systemd expands
  `%t` to the runtime directory root (`$XDG_RUNTIME_DIR`, i.e.
  `/run/user/<uid>`) for whichever UID the `systemd --user` instance actually
  runs as, so this stays correct without a UID hardcoded into configuration.
- Inspect status: `systemctl --user list-timers 'ai-*.timer'`,
  `systemctl --user status ai-<name>.service`, or
  `journalctl --user -u ai-<name>.service -n 50 --no-pager`.
- `scripts-test` triggers a run via `systemctl --user start --wait`, which
  blocks until the unit finishes.

### macOS — launchd LaunchAgents

- Each job becomes a plist at
  `~/Library/LaunchAgents/ai-<name>.plist`, labeled `com.famulus.ai.<name>`.
- `ProgramArguments` invokes the same fixed launch resolver used on Linux —
  directly, as `argv[0]`, with no separate interpreter in front of it. That
  works because the resolver script carries its own `#!/usr/bin/env python3`
  shebang and launchd execs it the same way any Unix program is exec'd; this
  is the same convention the dispatcher/invoke-skill launcher shims use.
- `StartCalendarInterval` is computed from the cron expression (one entry, or
  a list of entries for combinations like `*/15 9 * * *`, which expands to
  four calendar intervals — one per quarter-hour within that hour). Only `*`,
  a step (`*/N`), or a single bare integer are accepted per field — hyphenated
  ranges like `9-17` and comma lists like `9,12,17` are not supported by any
  of the three platform translators.
- `StandardOutPath`/`StandardErrorPath` both point at
  `logs/<name>/run.log`, alongside the job runner's own writes to that file.
- Load/reload: `launchctl bootout gui/<uid> <plist>` then
  `launchctl bootstrap gui/<uid> <plist>` (sync does the bootout defensively
  before every bootstrap, so re-syncing an already-loaded job is safe).
- `scripts-test` triggers a run via
  `launchctl kickstart -k gui/<uid>/com.famulus.ai.<name>`, which — unlike
  systemd's `start --wait` — returns as soon as the trigger is accepted, not
  when the job finishes.
- Inspect status: `launchctl print gui/<uid>/com.famulus.ai.<name>`.

### Windows — Task Scheduler

- Each job becomes a scheduled task named `Famulus-AI-ai-<name>`.
- Windows has no shebang-based exec, so unlike the two Unix platforms above,
  the task's command line hands the launch resolver to an explicit `python`
  interpreter: `python <resolver-path> <job-runner-path> --jobs-file ... --job <name>`
  — the same convention the installer's generated Windows launcher shims use.
- The cron expression is translated to the nearest `schtasks` schedule
  (`/SC MINUTE`, `HOURLY`, `DAILY`, or `WEEKLY` with `/D <weekday>`, as
  supported by the cron subset this skill accepts).
- `scripts-test` triggers a run via `schtasks /Run /TN Famulus-AI-ai-<name>`,
  which — like launchd — returns once the trigger is accepted, not once the
  job finishes.
- Inspect status: `schtasks /Query /TN Famulus-AI-ai-<name>` (or
  `/FO LIST /V` for all jobs' details).

### Did the job actually succeed?

Triggering a job through the native scheduler only confirms the scheduler
*accepted the trigger*. On systemd that call blocks until the job is done, but
on launchd and Task Scheduler it returns immediately, so a bare "did the
trigger succeed" check can't tell you whether the job's own work succeeded —
or whether it has even started.

`scripts-test` accounts for this: after triggering, it waits (bounded, ~60s)
for a fresh structured outcome file to appear at `logs/<name>/latest.json`,
identified by a fresh per-run id rather than by timestamp (two runs can
finish within the same second), and reports pass/fail from that file's
`success` field instead of trusting the trigger call alone.

Whether a run counts as successful is decided by combining two signals:

1. The literal process exit code. A non-zero exit (or a process that never
   spawned at all — missing executable, permission denied, etc.) always
   fails the run.
2. An optional self-reported inner status. Some jobs write their own
   `state/status.json` (`{"result": "ok" | "error" | "warning", ...}`) as
   part of an existing status-tracking mechanism of their own; jobs.yaml's
   `success.require_inner_status` can require that value to match. A job
   with no `success:` block declared passes on exit code alone — this
   matters because most jobs have no such self-reported status file, and
   requiring one they never write would make every run of theirs report
   failure.

In the shipped `jobs.yaml`, one job (the inbox-triage job, run at 3am)
declares `success: {require_inner_status: ok}` because it already maintains
a `state/status.json` of its own; another (the daily-planning job, run at
7am) has no such mechanism, so it has no `success:` block and its outcome is
exit-code-only. This asymmetry is intentional, not an oversight — add a
`success:` block only for jobs that actually write a status file.

### Healthcheck

`scripts-healthcheck` runs pre-flight checks (native scheduler manager
reachable, `AI_AGENT_COMMAND_TEMPLATE` set and resolvable) plus a per-job
freshness/activity check, and sends a desktop notification when anything
fails. Its process exit code reflects the outcome truthfully: `0` when every
check passed, `1` when at least one failed — callers (cron, monitoring) can
rely on the exit code alone without parsing its log.

## Design Principles

1. **No hardcoded paths** — Commands use `invoke-skill` which is on PATH (managed by install-assistant-tools)
2. **jobs.yaml is source of truth** — All state comes from here, nothing else
3. **Minimal layers** — Direct process execution, no intermediate shell scripts
4. **Environment-based** — Uses the host scheduler's per-user environment for AI_AGENT_COMMAND_TEMPLATE
5. **Cross-platform** — Shared Python job-running and success-evaluation logic; only entry creation/triggering is platform-specific

## Logs

All logs go to `logs/<name>/run.log`, appended (never rotated — manage
manually or with logrotate) on every platform, since a single shared
component owns log writing regardless of which native scheduler fired the
job. Each run appends a `--- RUN START ---` marker, the job's captured
output, and a `--- RUN END (success=True|False) ---` marker (the Python
`bool`'s capitalized string form, since it's written via an f-string).

Alongside the log, `logs/<name>/latest.json` holds a structured record of the
most recent run: start/finish timestamps, the process exit code, the inner
status if the job reported one, the overall `success` verdict, a human
`reason` when it failed, and a per-run id. `scripts-test` and the healthcheck
both read this file rather than re-deriving success on their own.

### Healthcheck log

```
logs/healthcheck/run.log
```

One entry per check run (typically every 4 hours via cron).

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
- `logs/<name>/run.log` stores per-job output with RUN START/END markers.
- `logs/<name>/latest.json` stores the structured outcome of the most recent run.
- `logs/healthcheck/run.log` stores healthcheck output.
- Setup writes machine-local launcher environment state during recurring-tasks setup.

Note: native scheduler entries are generated per platform —
`~/.config/systemd/user/ai-<name>.{service,timer}` on Linux,
`~/Library/LaunchAgents/ai-<name>.plist` on macOS, and the
`Famulus-AI-ai-<name>` scheduled task on Windows. Do not edit these
manually — they're regenerated from jobs.yaml.

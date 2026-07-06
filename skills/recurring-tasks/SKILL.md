---
name: recurring-tasks
description: Manage recurring AI job automation via systemd timers. Define jobs in jobs.yaml, enable/disable/test them, and monitor health.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: system-assistant

Dependencies:
- install-assistant-tools

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `scripts-disable` — Disable a job by setting enabled: false in jobs.yaml and syncing unit files.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-disable <name>`
- `scripts-enable` — Enable a job by setting enabled: true in jobs.yaml and syncing unit files.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-enable <name>`
- `scripts-healthcheck` — Run pre-flight and per-job health checks for all enabled recurring tasks; sends a desktop notification on failure.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-healthcheck`
- `scripts-setup` — Verify prerequisites, sync systemd unit files from jobs.yaml, install the healthcheck cron entry, and list active timers.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-setup`
- `scripts-status` — List all active ai-* timers, next fire times, and service status.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-status`
- `scripts-sync` — Regenerate systemd unit files from jobs.yaml.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-sync`
- `scripts-test` — Trigger a job immediately via systemd, show output and status.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test <name>`
- `scripts-view-logs` — Tail the run log for a job (default 50 lines).
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-view-logs <job-name> [--lines N]`
<!-- END BLUEPRINT INTERFACES -->

# Recurring Tasks

Manages AI-driven recurring job automation using **systemd user timers**. 
Jobs are defined in `jobs.yaml`, which is the single source of truth.

## Quick Start

The skill provides dispatcher interfaces for all operations:

- Enable/disable jobs
- Test jobs immediately
- View job logs
- Check status and health
- Sync systemd units

See the **Owner-Facing Script Interfaces** block above for the exact dispatcher commands.

## Architecture (Simplified)

```
jobs.yaml (source of truth)
    ↓
sync-units.py (generates systemd units)
    ↓
systemd timer fires on schedule
    ↓
systemd service runs: bash -c "<command from jobs.yaml>"
    ↓
Command typically: invoke-skill <job-name>
    ↓
Logs to: logs/<job-name>/run.log
```

**Key simplifications:**
- ✓ No per-job runner scripts (command runs directly via bash -c)
- ✓ No invoke-agent.sh/run-skill.sh layers (invoke-skill is on PATH)
- ✓ Direct command invocation from systemd unit
- ✓ Environment inherited from systemd user session (AI_AGENT_COMMAND_TEMPLATE already set)

## Configuration

### jobs.yaml

```yaml
jobs:
  - name: example-job
    description: "Example: what this job does"
    command: "invoke-skill example-job"  # Can include env vars: VAR=value invoke-skill ...
    schedule: "0 * * * *"                # 5-field cron expression
    enabled: true
```

**Fields:**
- `name` — unique identifier (used for timer/service names, logs)
- `description` — human-readable purpose
- `command` — shell command to execute (can include environment variables)
- `schedule` — cron expression (minute hour * * day-of-week)
- `enabled` — whether the timer is active

## Operations

Use the dispatcher interfaces listed in the **Owner-Facing Script Interfaces** block above. Key operations:

- **Setup (first time):** `scripts-sync` generates systemd units from jobs.yaml and enables all enabled jobs.
- **Enable/Disable:** `scripts-enable` and `scripts-disable` modify jobs.yaml and resync systemd units.
- **Test:** `scripts-test` runs a job immediately and shows output.
- **View logs:** `scripts-view-logs` tails job logs (default 50 lines).
- **Check health:** `scripts-healthcheck` verifies all jobs are running and logs are fresh. Sends a desktop notification on failure.

## Design Principles

1. **No hardcoded paths** — Commands use `invoke-skill` which is on PATH (managed by install-assistant-tools)
2. **jobs.yaml is source of truth** — All state comes from here, nothing else
3. **Minimal layers** — Direct bash command execution, no intermediate scripts
4. **Environment-based** — Uses systemd user environment for AI_AGENT_COMMAND_TEMPLATE
5. **Cross-platform** — Core logic in Python, minimal shell usage

## Logs

All logs go to `logs/<name>/run.log`. Logs are appended, never rotated (manage manually or with logrotate).

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
2. Test manually: `scripts-test <name>`
3. Check systemd status: `systemctl --user status ai-<name>.service`

### View systemd journal for a job

```bash
journalctl --user -u ai-<name>.service -n 50 --no-pager
```

## Files

```
recurring-tasks/
  SKILL.md                    Documentation (this file)
  blueprint.yaml              Skill definition and interfaces
  jobs.yaml                   Job definitions (edit here)
  scripts/
    sync-units.py             Generate systemd units from jobs.yaml
    healthcheck.py            Monitor job health
    manage-job.py             CLI for enable/disable/test/view-logs
    invoke-agent.sh           Shell wrapper (called by invoke-skill)
    env.sh                    PATH setup (generated by install-assistant-tools)
  lessons/
    2026-07-06.md             Design decisions and lessons learned
  logs/
    <name>/run.log            Job execution logs
    healthcheck/run.log       Health check logs
```

Note: Systemd units are generated in `~/.config/systemd/user/ai-<name>.{service,timer}`. Do not edit these manually — they're regenerated from jobs.yaml.

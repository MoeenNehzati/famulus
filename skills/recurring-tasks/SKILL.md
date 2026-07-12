---
name: recurring-tasks
description: Manage recurring AI job automation via systemd timers. Define jobs in jobs.yaml, enable/disable/test them, and monitor health.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: system-assistant

Skill Version: 1

Uses Interfaces:
- `recurring-tasks.llm.default -> install-assistant-tools.llm.default@2`

Public Interfaces:
- `recurring-tasks.llm.default`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `scripts-disable` — Disable a job by setting enabled: false in jobs.yaml and syncing native scheduler entries.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-disable <name>`
- `scripts-enable` — Enable a job by setting enabled: true in jobs.yaml and syncing native scheduler entries.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-enable <name>`
- `scripts-ensure-agent-env` — Idempotently ensure recurring-tasks' systemd AI_AGENT_COMMAND_TEMPLATE is in place. Also run automatically by scripts-setup.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-ensure-agent-env --repo-root DIR --home DIR --bin-dir DIR [--dry-run]`
- `scripts-healthcheck` — Run pre-flight and per-job health checks for all enabled recurring tasks; sends a desktop notification on failure.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-healthcheck`
- `scripts-setup` — Verify prerequisites, sync native scheduler entries from jobs.yaml, install recurring health checks, and list active timers/tasks.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-setup [--migrate-cron]`
- `scripts-status` — List active recurring scheduler entries, next fire times, and service status.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-status`
- `scripts-sync` — Regenerate native scheduler entries from jobs.yaml.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-sync`
- `scripts-test` — Trigger a job immediately through the native scheduler, show output and status.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-test <name>`
- `scripts-view-logs` — Tail the run log for a job (default 50 lines).
  - `dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-view-logs <job-name> [--lines N]`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
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

See the **Owner-Facing Machine Interfaces** block above for the exact dispatcher commands.

## Architecture (Simplified)

```
jobs.yaml (source of truth)
    ↓
sync_units.py (generates systemd units)
    ↓
systemd timer fires on schedule
    ↓
systemd service runs the Python runner
    ↓
Executor parses command from jobs.yaml (typically: invoke-skill <job-name>)
    ↓
Logs to: logs/<job-name>/run.log
```

**Key simplifications:**
- ✓ No per-job shell runner scripts
- ✓ No invoke-agent.sh/run-skill.sh layers (invoke-skill is on PATH)
- ✓ Python executor appends logs without shell redirection
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

Use the dispatcher interfaces listed in the **Owner-Facing Machine Interfaces** block above. Key operations:

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

- `jobs.yaml` is the source of truth for job definitions.
- `logs/<name>/run.log` stores per-job output.
- `logs/healthcheck/run.log` stores healthcheck output.
- Setup writes machine-local launcher environment state during recurring-tasks setup.

Note: Systemd units are generated in `~/.config/systemd/user/ai-<name>.{service,timer}`. Do not edit these manually — they're regenerated from jobs.yaml.

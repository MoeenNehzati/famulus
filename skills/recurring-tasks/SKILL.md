---
name: recurring-tasks
description: Use when setting up, enabling, disabling, testing, viewing logs, or debugging recurring tasks managed as systemd user timers. Also use when the healthcheck monitor reports a problem, or when adding a new automated job.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: automation

Dependencies:
- install-assistant-tools

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `runners-daily-plan` — Systemd service entry point for the daily-plan job; sets DBUS environment and calls run-skill.sh.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks runners-daily-plan ...`
- `runners-email-triage` — Systemd service entry point for the email-triage job; sets DBUS environment and calls run-skill.sh.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks runners-email-triage ...`
- `scripts-disable-job` — Disables a job by setting enabled: false in jobs.yaml and syncing unit files.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-disable-job <name> [--jobs-file JOBS_FILE] [--no-sync]`
- `scripts-enable-job` — Enables a job by setting enabled: true in jobs.yaml and syncing unit files.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-enable-job <name> [--jobs-file JOBS_FILE] [--no-sync]`
- `scripts-env` — Exports PATH additions required by invoke-agent.sh in non-login contexts; intended to be sourced, not run directly.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-env (sourced by invoke-agent.sh — not invoked directly)`
- `scripts-healthcheck` — Runs pre-flight and per-job health checks for all enabled recurring tasks and sends a desktop notification on any failure.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-healthcheck ...`
- `scripts-invoke-agent` — Invokes a skill via the assistant command using the backend set in $ASSISTANT_DEFAULT (claude or codex).
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-invoke-agent <skill-name>`
- `scripts-job-utils` — Shared helper library providing set_enabled() used by enable-job and disable-job; not a standalone CLI tool.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-job-utils (internal library — not invoked directly)`
- `scripts-run-skill` — Substitutes the skill name into $AI_AGENT_COMMAND_TEMPLATE and executes the resulting command in a login shell.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-run-skill <skill-name>`
- `scripts-setup` — Verifies prerequisites, syncs systemd unit files from jobs.yaml, installs the healthcheck cron entry, and lists active timers.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-setup [--migrate-cron]`
- `scripts-status` — Lists all active ai-* timers; with a job name also shows service status and recent journal entries.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-status [job-name]`
- `scripts-sync-units` — Writes, updates, or removes systemd user unit files and per-job runner scripts to match jobs.yaml.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-sync-units [--unit-dir UNIT_DIR] [--jobs-file JOBS_FILE] [--migrate-cron]`
- `scripts-test-enable-disable` — Test suite for the enable-job and disable-job scripts.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-enable-disable ...`
- `scripts-test-job` — Triggers a job immediately via systemd, waits up to 6 minutes, and reports pass/fail with log and journal output.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-job <name> [--jobs-file JOBS_FILE]`
- `scripts-test-live-job` — End-to-end live test that creates a temporary skill and job, runs it through the real assistant backend, verifies output, and restores the original schedule.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-live-job --backend {claude|codex} [--keep-artifacts]`
- `scripts-test-sync-units` — Test suite for the sync-units script.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-sync-units ...`
- `scripts-view-logs` — Tails the run log for a job in real time; pass 'healthcheck' as the job name to view the monitor log.
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-view-logs <job-name> [lines]`
<!-- END BLUEPRINT INTERFACES -->
# Recurring Tasks

Manages AI-driven recurring jobs as **systemd user timers**. `jobs.yaml` is the
source of truth. Each enabled job invokes a skill non-interactively via assistant on a
cron-like schedule.

A separate **cron-based healthcheck** (`scripts-healthcheck`) runs every 4
hours to verify jobs are healthy and sends a desktop notification on failure. It
uses cron (not systemd) so it stays alive even if the systemd user session is the
thing that breaks.

Timers use `Persistent=true`: missed fires (machine off or asleep) run immediately
on the next wake or boot.

**Design principle:** Job-specific artifacts and success indicators are never
stored in `jobs.yaml` — derive them at runtime from `description` and `command`.

---

## Architecture

### Job invocation chain

```
jobs.yaml
  └─ sync-units.py
       ├─ ~/.config/systemd/user/ai-<name>.timer   (schedule + Persistent=true)
       ├─ ~/.config/systemd/user/ai-<name>.service  (ExecStart = runner)
       └─ scripts/runners/<name>.sh                 (sets env, calls run-skill.sh)

[on schedule] systemd fires ai-<name>.timer
  └─ ai-<name>.service starts scripts/runners/<name>.sh
       └─ scripts/run-skill.sh <name>
            └─ reads $AI_AGENT_COMMAND_TEMPLATE from systemd user environment
                 └─ scripts/invoke-agent.sh <name>
                      └─ backend-aware assistant launcher
                           ├─ Claude: assistant --local --claude -p "/<name>"
                           └─ Codex:  assistant --local --codex exec "$<name>"
                         output → logs/<name>/run.log
```

### Healthcheck monitoring chain

```
cron (every 4 hours, installed by setup.sh)
  └─ scripts/healthcheck.sh
       ├─ PRE-FLIGHT
       │    ├─ systemd user manager reachable? (state = running or degraded-unrelated)
       │    ├─ AI_AGENT_COMMAND_TEMPLATE set in systemd environment?
       │    └─ scripts/invoke-agent.sh exists and is executable?
       └─ PER ENABLED JOB
            ├─ unit files exist in ~/.config/systemd/user/?
            ├─ timer is active?
            ├─ last run Result = success?
            └─ log fresh? (mtime < 2 × scheduled interval)
       ├─ all pass → log "All checks passed"
       └─ any fail → log problems + notify-send desktop alert (-u critical)
       all outcomes logged to logs/healthcheck/run.log
```

### Key dependency: `AI_AGENT_COMMAND_TEMPLATE`

Every runner calls `run-skill.sh`, which substitutes `{skill}` in this variable
and runs the result. If the variable is absent from the systemd user environment,
every job fails immediately with:

```
AI_AGENT_COMMAND_TEMPLATE is not set.
```

It is set persistently in `~/.config/environment.d/20-ai-agent.conf` by
`install-assistant-tools`. If it disappears (e.g. file deleted), use the
`install-assistant-tools` skill to restore it.

---

## Files

### Generated per job

```
~/.config/systemd/user/
  ai-<name>.timer      # OnCalendar from jobs.yaml schedule; Persistent=true
  ai-<name>.service    # ExecStart = scripts/runners/<name>.sh

scripts/runners/
  <name>.sh            # sets PATH + DBUS env vars; calls run-skill.sh; redirects to run.log

logs/<name>/
  run.log              # all stdout/stderr from every run (appended)
```

### Monitoring

```
logs/healthcheck/
  run.log              # timestamped record of every healthcheck run, pass and fail
```

---

## Scripts

| Interface | Purpose |
|-----------|---------|
| `scripts-setup` | **Run on first install or after any change.** Syncs unit files from `jobs.yaml`, installs healthcheck cron entry, lists active timers. |
| `scripts-sync-units` | Writes/updates/removes systemd unit files and runner scripts to match `jobs.yaml`. Called by `scripts-setup`. |
| `scripts-run-skill` | Reads `$AI_AGENT_COMMAND_TEMPLATE`, substitutes `{skill}` → `<name>`, executes via `bash -lc`. Called by every runner. |
| `scripts-invoke-agent` | Backend-aware agent invoker. Claude uses `assistant --local --claude -p "/<name>"`; Codex uses `assistant --local --codex exec "$<name>"`. This is what `AI_AGENT_COMMAND_TEMPLATE` points to. |
| `scripts-healthcheck` | Cron-based monitor. Checks all enabled jobs; sends desktop notification on any failure. Logs every run to `logs/healthcheck/run.log`. |
| `scripts-enable-job` | Sets `enabled: true` in `jobs.yaml` and syncs. |
| `scripts-disable-job` | Sets `enabled: false` in `jobs.yaml` and syncs. |
| `scripts-test-job` | Starts the service immediately via systemd, waits up to 6 min, reports pass/fail with log and journal tail. |
| `scripts-test-live-job` | Creates a temporary skill + scheduled job, runs it through the real backend, verifies output, then restores the original schedule. |
| `scripts-view-logs` | Tails the run log live (Ctrl-C to stop; default 50 lines). Pass `healthcheck` to view the monitor log. |
| `scripts-status` | Lists all active timers. With a job name, also shows service status and recent journal entries. |

---

## `jobs.yaml` format

```yaml
jobs:
  - name: <job-name>       # used for unit filenames, runner name, and log directory
    description: "..."     # human-readable; also set as systemd unit Description=
    command: "..."         # shell command run by the runner; {skill_dir} is substituted
    schedule: "M H * * *" # 5-field cron expression; dom and month must be *
    enabled: true          # false = unit files removed, timer stopped
```

**`{skill_dir}`** in `command` is replaced with the absolute path to this skill's
directory at sync time.

**Schedule format:** standard 5-field cron. `*/N` works in minutes and hours.
Day-of-week (0–6) is supported. `dom` and `month` must be `*`.

---

## Operations

### First-time setup (new machine)

Use the `scripts-setup` interface (pass `--migrate-cron` when migrating from a
cron-based install to also remove the old crontab block).

This syncs all unit files, enables all timers, and installs the healthcheck cron
entry. Run once after cloning on a new machine.

### Enable / disable a job

Use the `scripts-enable-job` or `scripts-disable-job` interface with `<name>`.

### Add a new job

1. Add an entry to `jobs.yaml` with `enabled: true`.
2. Run the `scripts-setup` interface.
3. Test: use the `scripts-test-job` interface with `<name>`.

### View logs

Use the `scripts-view-logs` interface: pass `<name>` for a job run log, or
`healthcheck` to view the monitor log.

### Test a job immediately

Use the `scripts-test-job` interface with `<name>`.

### Run a live backend self-test

Use the `scripts-test-live-job` interface with `--backend claude` or
`--backend codex`.

### Run the healthcheck manually

Use the `scripts-healthcheck` interface.

### Debug a job

Multi-turn reasoning loop — do not script this:

1. Check recent output: use the `scripts-view-logs` interface with `<name>`.
2. Check timer, service, journal: use the `scripts-status` interface with `<name>`.
3. Infer expected behavior from `description` and `command` in `jobs.yaml` only.
4. Summarize: did systemd launch the job? Did it error? What is the most likely cause?
5. Propose a specific fix. Wait for user approval.
6. Apply fix; re-sync if unit files changed (use the `scripts-sync-units` interface).
7. Use the `scripts-test-job` interface with `<name>`.
8. Pass → done. Fail → return to step 1.

---

## Common failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| All jobs log "AI_AGENT_COMMAND_TEMPLATE is not set" | Variable missing from systemd user environment | Use the `install-assistant-tools` skill, or restore `~/.config/environment.d/20-ai-agent.conf` and run `systemctl --user set-environment ...` |
| Timer shows "not active" / unit not found | `setup.sh` never run, or units deleted | Use the `scripts-setup` interface |
| Job fails but timer is active | Job-level error (script bug, missing dependency, auth issue) | Check `logs/<name>/run.log`; use `scripts-status` with `<name>` |
| Healthcheck reports stale log | Timer missed fires or job produced no output | Test manually; check `Persistent=true` is set (re-run setup.sh) |
| Healthcheck never sends notifications | `notify-send` can't reach D-Bus from cron | Check `XDG_RUNTIME_DIR=/run/user/<uid>` and `DBUS_SESSION_BUS_ADDRESS` in healthcheck env |
| systemd user manager "degraded" alert | Some unit failed | Run `systemctl --user list-units --state=failed`; if not an AI unit, it's unrelated |

---

## Logs

```
logs/
  <name>/run.log        # appended by the runner on every job run
  healthcheck/run.log   # appended by healthcheck.sh on every 4-hour check
```

Logs are not automatically rotated. Trim manually or add logrotate if they grow.

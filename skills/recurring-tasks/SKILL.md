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
- `runners-daily-plan`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks runners-daily-plan ...`
- `runners-email-triage`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks runners-email-triage ...`
- `scripts-disable-job`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-disable-job ...`
- `scripts-enable-job`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-enable-job ...`
- `scripts-env`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-env ...`
- `scripts-healthcheck`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-healthcheck ...`
- `scripts-invoke-agent`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-invoke-agent ...`
- `scripts-job-utils`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-job-utils ...`
- `scripts-run-skill`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-run-skill ...`
- `scripts-setup`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-setup ...`
- `scripts-status`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-status ...`
- `scripts-sync-units`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-sync-units ...`
- `scripts-test-enable-disable`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-enable-disable ...`
- `scripts-test-job`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-job ...`
- `scripts-test-live-job`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-live-job ...`
- `scripts-test-sync-units`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-test-sync-units ...`
- `scripts-view-logs`
  - `dispatcher --caller-skill recurring-tasks recurring-tasks scripts-view-logs ...`
<!-- END BLUEPRINT INTERFACES -->
# Recurring Tasks

Manages AI-driven recurring jobs as **systemd user timers**. `jobs.yaml` is the
source of truth. Each enabled job invokes a skill non-interactively via assistant on a
cron-like schedule.

A separate **cron-based healthcheck** (`scripts/healthcheck.sh`) runs every 4
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

| Script | Purpose |
|--------|---------|
| `scripts/setup.sh` | **Run on first install or after any change.** Syncs unit files from `jobs.yaml`, installs healthcheck cron entry, lists active timers. |
| `scripts/sync-units.py` | Writes/updates/removes systemd unit files and runner scripts to match `jobs.yaml`. Called by `setup.sh`. |
| `scripts/run-skill.sh <name>` | Reads `$AI_AGENT_COMMAND_TEMPLATE`, substitutes `{skill}` → `<name>`, executes via `bash -lc`. Called by every runner. |
| `scripts/invoke-agent.sh <name>` | Backend-aware agent invoker. Claude uses `assistant --local --claude -p "/<name>"`; Codex uses `assistant --local --codex exec "$<name>"`. This is what `AI_AGENT_COMMAND_TEMPLATE` points to. |
| `scripts/healthcheck.sh` | Cron-based monitor. Checks all enabled jobs; sends desktop notification on any failure. Logs every run to `logs/healthcheck/run.log`. |
| `scripts/enable-job.py <name>` | Sets `enabled: true` in `jobs.yaml` and syncs. |
| `scripts/disable-job.py <name>` | Sets `enabled: false` in `jobs.yaml` and syncs. |
| `scripts/test-job.py <name>` | Starts the service immediately via systemd, waits up to 6 min, reports pass/fail with log and journal tail. |
| `scripts/test-live-job.py --backend <claude\|codex>` | Creates a temporary skill + scheduled job, runs it through the real backend, verifies output, then restores the original schedule. |
| `scripts/view-logs.sh <name> [lines]` | Tails the run log live (Ctrl-C to stop; default 50 lines). Pass `healthcheck` to view the monitor log. |
| `scripts/status.sh [name]` | Lists all active timers. With a job name, also shows service status and recent journal entries. |

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

```bash
scripts/setup.sh
```

This syncs all unit files, enables all timers, and installs the healthcheck cron
entry. Run once after cloning on a new machine. If migrating from a cron-based
install, add `--migrate-cron` to also remove the old crontab block.

### Enable / disable a job

```bash
python3 scripts/enable-job.py <name>
python3 scripts/disable-job.py <name>
```

### Add a new job

1. Add an entry to `jobs.yaml` with `enabled: true`.
2. Run `scripts/setup.sh`.
3. Test: `python3 scripts/test-job.py <name>`.

### View logs

```bash
scripts/view-logs.sh <name>       # job run log
scripts/view-logs.sh healthcheck  # monitor log
```

### Test a job immediately

```bash
python3 scripts/test-job.py <name>
```

### Run a live backend self-test

```bash
python3 scripts/test-live-job.py --backend claude
python3 scripts/test-live-job.py --backend codex
```

### Run the healthcheck manually

```bash
scripts/healthcheck.sh
```

### Debug a job

Multi-turn reasoning loop — do not script this:

1. Check recent output: `scripts/view-logs.sh <name>`
2. Check timer, service, journal: `scripts/status.sh <name>`
3. Infer expected behavior from `description` and `command` in `jobs.yaml` only.
4. Summarize: did systemd launch the job? Did it error? What is the most likely cause?
5. Propose a specific fix. Wait for user approval.
6. Apply fix; re-sync if unit files changed (`python3 scripts/sync-units.py`).
7. Run `python3 scripts/test-job.py <name>`.
8. Pass → done. Fail → return to step 1.

---

## Common failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| All jobs log "AI_AGENT_COMMAND_TEMPLATE is not set" | Variable missing from systemd user environment | Use the `install-assistant-tools` skill, or restore `~/.config/environment.d/20-ai-agent.conf` and run `systemctl --user set-environment ...` |
| Timer shows "not active" / unit not found | `setup.sh` never run, or units deleted | Run `scripts/setup.sh` |
| Job fails but timer is active | Job-level error (script bug, missing dependency, auth issue) | Check `logs/<name>/run.log` and `scripts/status.sh <name>` |
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

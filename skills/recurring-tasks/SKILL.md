---
name: recurring-tasks
description: Use when setting up, enabling, disabling, testing, viewing logs, or debugging recurring tasks managed as systemd user timers.
---

# Recurring Tasks

Category: automation

Manages recurring tasks as **systemd user timers**. `jobs.yaml` (in this skill directory) is the source of truth. Each enabled job gets a pair of unit files in `~/.config/systemd/user/` and a runner script in `scripts/runners/`. Logs live at `logs/<name>/run.log`.

Timers use `Persistent=true`, so missed fires (machine asleep or off at scheduled time) run immediately on the next wake or boot.

**Design principle:** Job-specific artifacts and success indicators are never stored in `jobs.yaml` — they are always inferred at runtime from the job's `description` and `command`. Do not add artifact paths, check lists, or state file references to `jobs.yaml`.

**Schedule format:** Standard 5-field cron expressions (`M H dom month dow`). `dom` and `month` must be `*`. `sync-units.py` converts them to systemd `OnCalendar=` format. Supported: exact values, `*` wildcards, `*/N` step syntax, single digit day-of-week (0=Sun … 6=Sat).

**Generated files per job:**
```
~/.config/systemd/user/
  ai-<name>.service    # runs the job via the runner script
  ai-<name>.timer      # fires on schedule, Persistent=true

scripts/runners/
  <name>.sh                # wrapper: job command + log redirect
```

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/setup.sh` | Verify prerequisites, sync units from `jobs.yaml`, list active timers |
| `scripts/sync-units.py` | Write/update/remove systemd unit files to match `jobs.yaml` |
| `scripts/enable-job.py <name>` | Set `enabled: true` in `jobs.yaml` and sync |
| `scripts/disable-job.py <name>` | Set `enabled: false` in `jobs.yaml` and sync |
| `scripts/test-job.py <name>` | Run a job immediately and report pass/fail |
| `scripts/view-logs.sh <name> [lines]` | Tail the run log (live, Ctrl-C to stop; default 50 lines) |
| `scripts/status.sh [name]` | List all timers; with job name, also shows service status and journal |

---

## Operations

### Setup

Run `scripts/setup.sh`.

On first run after migrating from a cron-based install, add `--migrate-cron` to also remove the old crontab block:

```
scripts/setup.sh --migrate-cron
```

### Enable / disable a job

```
scripts/enable-job.py <name>
scripts/disable-job.py <name>
```

### View logs

```
scripts/view-logs.sh <name>
```

### Test a job

```
scripts/test-job.py <name>
```

Starts the service immediately, waits up to 6 minutes for it to complete, then checks the run log and journal and reports pass/fail.

### Debug a job

Multi-turn reasoning loop — do not script this:

1. Check recent log output: `scripts/view-logs.sh <name>`
2. Check timer, service, and journal: `scripts/status.sh <name>`
3. Infer relevant state and artifacts from the job's `description` and `command` in `jobs.yaml`. Do not rely on hardcoded knowledge of what the job does — derive it from these fields only.
4. Summarize findings. State clearly: did systemd launch the job? Did the job error? What is the most likely cause?
5. Propose a specific fix. Wait for user approval.
6. If approved: apply the fix, then re-sync if unit files changed (`scripts/sync-units.py`).
7. Run `scripts/test-job.py <name>`.
8. If test passes → done. If not → return to step 1.

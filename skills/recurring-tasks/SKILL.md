---
name: recurring-tasks
description: Use when setting up, enabling, disabling, testing, viewing logs, or debugging Claude-related recurring cron jobs.
---

# Recurring Tasks

Category: automation

Manages Claude-related recurring cron jobs. `jobs.yaml` (in this skill directory) is the source of truth. The skill owns a single marked block in the user's crontab and never modifies anything outside it. Logs live at `~/.claude/skills/recurring-tasks/logs/<name>/run.log`.

**Design principle:** Job-specific artifacts and success indicators are never stored in `jobs.yaml` — they are always inferred at runtime from the job's `description` and `command`. Do not add artifact paths, check lists, or state file references to `jobs.yaml`.

**Crontab block format** (the skill owns this block and nothing outside it):
```
# --- claude-recurring BEGIN (managed by recurring-tasks skill — do not edit manually) ---
<schedule> <command> >> ~/.claude/skills/recurring-tasks/logs/<name>/run.log 2>&1
# --- claude-recurring END ---
```

---

## Operations

### Setup

1. Verify `claude` is reachable at its full path:
   ```bash
   /home/moeen/.local/bin/claude --version
   ```
2. Verify PyYAML is available:
   ```bash
   python3 -c "import yaml; print('ok')"
   ```
   If missing: `pip install pyyaml` or `sudo apt install python3-yaml`
3. Create log directories for all jobs in `jobs.yaml`:
   ```bash
   python3 -c "
   import yaml
   from pathlib import Path
   skill = Path('~/.claude/skills/recurring-tasks').expanduser()
   jobs = yaml.safe_load((skill / 'jobs.yaml').read_text()).get('jobs', [])
   for j in jobs:
       (skill / 'logs' / j['name']).mkdir(parents=True, exist_ok=True)
       print(f'Created logs/{j[\"name\"]}/')
   "
   ```
4. Sync crontab:
   ```bash
   ~/.claude/skills/recurring-tasks/scripts/sync-crontab.py
   ```
5. Show the user the resulting cron block for confirmation:
   ```bash
   crontab -l | grep -A 20 "claude-recurring BEGIN"
   ```

### Enable a job

```bash
~/.claude/skills/recurring-tasks/scripts/enable-job.py <name>
```

### Disable a job

```bash
~/.claude/skills/recurring-tasks/scripts/disable-job.py <name>
```

### View logs

```bash
~/.claude/skills/recurring-tasks/scripts/view-logs.sh <name>
```

### Test a job

```bash
~/.claude/skills/recurring-tasks/scripts/test-job.py <name>
```

The script schedules the job 1 minute from now using a temporary cron block, waits 90 seconds, checks the job's log and cron system log (`journalctl -u cron`), removes the temp block, and reports pass/fail. The temp block uses these markers — distinct from the managed block:
```
# --- claude-recurring TEST BEGIN (temporary) ---
MM HH * * * <command> >> <logfile> 2>&1
# --- claude-recurring TEST END ---
```
The TEST block is always removed regardless of outcome.

### Debug a job

Multi-turn reasoning loop — do not script this:

1. Read `logs/<name>/run.log` (last 50 lines):
   ```bash
   ~/.claude/skills/recurring-tasks/scripts/view-logs.sh <name> 50
   ```
2. Check cron's system log:
   ```bash
   journalctl -u cron --since "2 hours ago" --no-pager
   ```
3. Infer relevant state and artifacts from the job's `description` and `command` in `jobs.yaml`; inspect them. Do not rely on any hardcoded knowledge of what the job does — derive it from these fields only.
4. Summarize findings. State clearly: did cron launch the job? Did the job error? What is the most likely cause?
5. Propose a specific fix. Wait for user approval.
6. If approved: apply the fix.
7. Run **Test** (see above).
8. If test passes → done. If not → return to step 1.

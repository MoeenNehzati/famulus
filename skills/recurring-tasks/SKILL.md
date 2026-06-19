---
name: recurring-tasks
description: Use when setting up, enabling, disabling, testing, viewing logs, or debugging Claude-related recurring cron jobs.
---

# Recurring Tasks

Category: automation

Manages Claude-related recurring cron jobs. Owns a marked block in the user's crontab; `jobs.yaml` is the source of truth. Logs live at `scripts/../logs/<name>/run.log`.

**Design principle:** Job-specific artifacts and success indicators are never stored in `jobs.yaml` — they are always inferred at runtime from the job's `description` and `command`. Do not add artifact paths or check lists to `jobs.yaml`.

**Scripts:** Never call scripts directly from outside this skill.

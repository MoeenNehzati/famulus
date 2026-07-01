---
name: daily-plan
description: |
  Use when the user asks to plan their day, see what to work on today, check
  their schedule, or review today's actions. Triggers on "plan my day",
  "what should I do today", "what should I work on", "show my plan", or similar.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: automation

Dependencies:
- cloud-files
- g-calendar
- get-weather
- list-manager

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, run:

```bash
python3 skills/daily-plan/scripts/orchestrate.py
```

This skill gathers data from list-manager, g-calendar, get-weather, and cloud-files, then assembles them into a plan.

This will:
1. Check if a plan already exists for today
2. If it exists: show it
3. If it doesn't exist: generate it (gather data in parallel from all sources, assemble, save, display)

To regenerate the plan even if one exists:

```bash
python3 skills/daily-plan/scripts/orchestrate.py --forced
```

For algorithm details, see the docstring in `orchestrate.py`.

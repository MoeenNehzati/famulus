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
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `mutate-plan`
  - `dispatcher --caller-skill daily-plan daily-plan mutate-plan ...`
- `orchestrate`
  - `dispatcher --caller-skill daily-plan daily-plan orchestrate ...`
  - Generate today's plan, or refresh and show the existing one.
  - Regenerate today's plan even if one already exists.
- `plan-storage`
  - `dispatcher --caller-skill daily-plan daily-plan plan-storage ...`
- `render-plan`
  - `dispatcher --caller-skill daily-plan daily-plan render-plan ...`
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, run:

```bash
python3 skills/daily-plan/scripts/orchestrate.py
```

This skill gathers data from list-manager, g-calendar, get-weather, and cloud-files, then assembles them into a plan.

This will:
1. Check if a plan already exists for today
2. If it exists: refresh the injected Actions/Triage blocks from current list state, save, and show it
3. If it doesn't exist: generate the base plan, store selected action/triage ids, inject current rendered list blocks, save, and display

To regenerate the plan even if one exists:

```bash
python3 skills/daily-plan/scripts/orchestrate.py --forced
```

This skill persists two files per day in cloud storage:
- `plans/M-D-YY.md` - human-readable rendered plan with injected list sections
- `plans/M-D-YY.meta.json` - JSON metadata of the form
  `{"actions": [[id, situation], ...], "triage": [[id, situation], ...]}`
  where `situation` is `shown` or `hidden`

When showing an existing plan, the skill re-reads the current master lists,
rebuilds the visible Actions/Triage blocks, injects them between the HTML
markers in the stored plan, saves the refreshed rendering, and prints it.

Plan-local edits (`hide`, `show`, `keep`, `remove`, `add`) only change the
plan metadata. Master-list edits (`mark-done`, `reject`, `set-deadline`) also
propagate to the underlying lists through `list-manager`.

For the runtime algorithm and storage contract, see `scripts/plan_runtime.py`.

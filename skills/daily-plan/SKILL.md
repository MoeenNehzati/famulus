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
- `mutate-plan` — Apply a mutation (hide, show, keep, remove, mark-done, reject, set-deadline, add) to today's plan and display the refreshed result.
  - `dispatcher --caller-skill daily-plan daily-plan mutate-plan {hide,show,keep,remove,mark-done,reject,set-deadline,add} ...`
- `orchestrate` — Generate or display today's daily plan, optionally forcing regeneration.
  - `dispatcher --caller-skill daily-plan daily-plan orchestrate [--forced]`
  - Generate today's plan, or refresh and show the existing one.
  - Regenerate today's plan even if one already exists.
- `plan-storage` — Read, write, check existence of, or delete a plan file in cloud storage by date.
  - `dispatcher --caller-skill daily-plan daily-plan plan-storage read|write|exists|delete <date>`
- `render-plan` — Extract or reassemble sections of a plan file for rendering.
  - `dispatcher --caller-skill daily-plan daily-plan render-plan <extract|reassemble> <plan-file> <dir>`
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, invoke the `orchestrate` interface to generate or display today's plan.

This skill gathers data from list-manager, g-calendar, get-weather, and cloud-files, then assembles them into a plan.

The `orchestrate` interface:
1. Checks if a plan already exists for today
2. If it exists: refreshes the injected Actions/Triage blocks from current list state, saves, and shows it
3. If it doesn't exist: generates the base plan, stores selected action/triage ids, injects current rendered list blocks, saves, and displays

To regenerate the plan even if one exists, invoke `orchestrate` with `--forced`.

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
propagate to the underlying lists through `list-manager`. Use the `mutate-plan`
interface to apply these mutations.

For the runtime algorithm and storage contract, see `plan_runtime.py` in the skill's scripts directory.

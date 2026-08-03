---
name: daily-plan
description: |
  Use when the user asks to plan their day, see what to work on today, check
  their schedule, or review today's actions. Triggers on "plan my day",
  "what should I do today", "what should I work on", "show my plan", or similar.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: planning, personal-organization; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces: none

Public Interfaces:
- `daily-plan.interface.default`
- `daily-plan.interface.mutate-plan`
- `daily-plan.interface.orchestrate`
- `daily-plan.interface.plan-storage`
- `daily-plan.interface.render-plan`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `daily-plan.interface.mutate-plan` — Apply a mutation (hide, show, keep, remove, mark-done, reject, set-deadline, add) to a dated plan and display the refreshed result. Defaults to today when --date is omitted.
  - `dispatcher --caller-skill daily-plan daily-plan.interface.mutate-plan [--date <M-D-YY|YYYY-MM-DD>] {hide,show,keep,remove,mark-done,reject,set-deadline,add} ...`
- `daily-plan.interface.orchestrate` — Generate today's plan (or show the existing one, refreshing its Todo/Triage blocks from current list state). Pass --forced to regenerate even if a plan already exists.
  - `dispatcher --caller-skill daily-plan daily-plan.interface.orchestrate [--forced]`
- `daily-plan.interface.plan-storage` — Read, write, check existence of, or delete a plan file in cloud storage by date.
  - `dispatcher --caller-skill daily-plan daily-plan.interface.plan-storage read|write|exists|delete <date>`
- `daily-plan.interface.render-plan` — Extract or reassemble sections of a plan file for rendering.
  - `dispatcher --caller-skill daily-plan daily-plan.interface.render-plan <extract|reassemble> <plan-file> <dir>`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `daily-plan.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, invoke `orchestrate`. To force regeneration of an existing plan, pass `--forced`.

Data sources: `g-calendar` (schedule), `get-weather` (forecast), `list-manager` (todo/triage), `cloud-files` (plan persistence).

Two files per day in cloud storage:
- `plans/M-D-YY.md` - human-readable rendered plan with injected list sections
- `plans/M-D-YY.meta.json` - JSON metadata of the form
  `{"actions": [[id, situation], ...], "triage": [[id, situation], ...]}`
  where `situation` is `shown` or `hidden`

When showing an existing plan, the skill re-reads the current todo and triage master lists,
rebuilds the visible Todo/Triage blocks, injects them between the HTML
markers in the stored plan, saves the refreshed rendering, and prints it.

Plan-local edits (`hide`, `show`, `keep`, `remove`, `add`) only change the
plan metadata. Master-list edits (`mark-done`, `reject`, `set-deadline`) also
propagate to the underlying lists through `list-manager`. Use the `mutate-plan`
interface to apply these mutations. Omit `--date` for today's plan, or pass
`--date M-D-YY` / `--date YYYY-MM-DD` to mutate a different stored plan.

For the runtime algorithm and storage contract, see `plan_runtime.py` in the skill's scripts directory.

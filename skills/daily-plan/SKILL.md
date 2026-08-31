---
name: daily-plan
description: >-
  Use when the user asks to plan their day, decide what to work on today, or review an existing daily plan. Do not use for a standalone calendar or list request, or for an end-of-day wrap-up.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `daily-plan._rtx.interface.mutate-plan@1` — Apply a mutation (hide, show, keep, remove, mark-done, reject, set-deadline, add) to a dated plan and display the refreshed result. Defaults to today when --date is omitted.
  - `dispatcher --caller-skill daily-plan daily-plan._rtx.interface.mutate-plan [--date <M-D-YY|YYYY-MM-DD>] {hide,show,keep,remove,mark-done,reject,set-deadline,add} ...`
- `daily-plan._rtx.interface.orchestrate@1` — Generate today's plan (or show the existing one, refreshing its Todo/Triage blocks from current list state). Pass --forced to regenerate even if a plan already exists.
  - `dispatcher --caller-skill daily-plan daily-plan._rtx.interface.orchestrate [--forced]`
- `daily-plan._rtx.interface.render-plan@1` — Extract or reassemble sections of a plan file for rendering.
  - `dispatcher --caller-skill daily-plan daily-plan._rtx.interface.render-plan <extract|reassemble> <plan-file> <dir>`

<!-- END BLUEPRINT INTERFACES -->
When this skill is used, invoke `orchestrate`. To force regeneration of an existing plan, pass `--forced`.

Data sources: `online-calendar` (schedule), `get-weather` (forecast), `list-manager` (todo/triage), `cloud-files` (plan persistence).

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

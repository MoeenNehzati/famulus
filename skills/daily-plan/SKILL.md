---
name: daily-plan
description: >-
  Use when the user asks to plan their day, decide what to work on today, or review an existing daily plan. Do not use for a standalone calendar or list request, or for an end-of-day wrap-up.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: planning, personal-organization; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `daily-plan.source.gateway -> daily-plan._rtx.interface.mutate-plan@1`
- `daily-plan.source.gateway -> daily-plan._rtx.interface.orchestrate@1`
- `daily-plan.source.gateway -> daily-plan._rtx.interface.render-plan@1`

Public Interfaces:
- `daily-plan.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `daily-plan._rtx.interface.mutate-plan` — Apply a mutation (hide, show, keep, remove, mark-done, reject, set-deadline, add) to a dated plan and display the refreshed result. Defaults to today when --date is omitted.
  - Caller: `daily-plan`
  - Version: 1
  - Alternative: `indexed-or-add`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--date": "M-D-YY|YYYY-MM-DD"}, "positionals": ["hide|show|keep|remove|mark-done|reject|add", "actions|triage", "indices-or-item-id"], "stdin": null}
    Required options: []; positional arity: 3..3; stdin: forbidden
  - Alternative: `set-deadline`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--date": "M-D-YY|YYYY-MM-DD"}, "positionals": ["set-deadline", "actions|triage", "indices-or-item-id", "deadline-for-set-deadline"], "stdin": null}
    Required options: []; positional arity: 4..4; stdin: forbidden
- `daily-plan._rtx.interface.orchestrate` — Generate today's plan (or show the existing one, refreshing its Todo/Triage blocks from current list state). Pass --forced to regenerate even if a plan already exists.
  - Caller: `daily-plan`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--forced": true}, "positionals": [], "stdin": null}
    Required options: ["--forced"]; positional arity: 0..0; stdin: forbidden
- `daily-plan._rtx.interface.render-plan` — Extract or reassemble sections of a plan file for rendering.
  - Caller: `daily-plan`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["extract|reassemble", "plan-file", "dir"], "stdin": null}
    Required options: []; positional arity: 3..3; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `daily-plan.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
Before invoking any daily-plan interface, follow
`setup-python-environment.interface.repair-selected-packages` for this owner's exact
declaration `["keyring", "rich"]`. Complete the full Task 2 fingerprint procedure; on
any failure, stop before `orchestrate` or another daily-plan interface.

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

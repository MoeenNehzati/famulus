---
name: update-skill-guidelines
description: Update the shared skill-writing standards and keep their mechanical Git hook checks aligned. Use when changing references/skill-standards/skill-guidelines.md, adding/removing skill conventions, changing dependency or naming rules, or auditing whether .githooks/skill checks still reflect the guideline.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `update-skill-guidelines.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: update-skill-guidelines

## Goal

Keep the human standard and the mechanical gates in lockstep.

Primary files:

- `../../references/skill-standards/skill-guidelines.md`
- `../../.githooks/skill/`

Supporting files:

- `../../.githooks/pre-commit`
- any skill file affected by the changed convention

## Workflow

1. Read the proposed guideline change and identify each enforceable rule it adds, changes, or removes.
2. Open `../../references/skill-standards/skill-guidelines.md` and all scripts under `../../.githooks/skill/` side by side.
3. For every guideline rule:
   - If it is mechanically enforceable, make sure exactly one skill hook enforces it.
   - If the hook would reject valid skills, narrow the hook or soften the guideline.
   - If the guideline no longer contains the rule, remove the stale hook check.
   - If a hook checks a rule not stated in the guideline, either document the rule or remove the hook.
4. Keep hook categories clean:
   - `../../.githooks/skill/*`: skill standards from `../../references/skill-standards/skill-guidelines.md`.
   - `../../.githooks/git/*`: generic Git repository state checks.
   - `../../.githooks/pre-commit`: dispatcher only.
5. Apply the guideline and hook edits in one change set.

## Required Checks

Run:

```bash
bash ../../.githooks/pre-commit
```

If any check fails because the guideline intentionally changed, update the relevant hook and rerun. Do not leave a failing or knowingly stale hook.

## Output

Report:

- guideline rules changed
- hooks added, adjusted, or removed
- installer path updates, if any
- validation commands run

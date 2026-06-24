---
name: update-skill-guidelines
description: Update the shared skill-writing standards and keep their mechanical Git hook checks aligned. Use when changing references/skill-guidelines.md, adding/removing skill conventions, changing dependency or naming rules, or auditing whether .githooks/skill checks still reflect the guideline.
---

When this skill is used, begin with:

Skill: update-skill-guidelines

Category: automation

Dependencies: none

## Goal

Keep the human standard and the mechanical gates in lockstep.

Primary files:

- `references/skill-guidelines.md`
- `.githooks/skill/check-names`
- `.githooks/skill/check-dependencies`

Supporting files:

- `.githooks/pre-commit`
- `.githooks/pre-push`
- the installer skill's `SKILL.md`
- the installer script that configures assistant tools
- any skill file affected by the changed convention

## Workflow

1. Read the proposed guideline change and identify each enforceable rule it adds, changes, or removes.
2. Open `references/skill-guidelines.md` and all `.githooks/skill/*` scripts side by side.
3. For every guideline rule:
   - If it is mechanically enforceable, make sure exactly one skill hook enforces it.
   - If the hook would reject valid skills, narrow the hook or soften the guideline.
   - If the guideline no longer contains the rule, remove the stale hook check.
   - If a hook checks a rule not stated in the guideline, either document the rule or remove the hook.
4. Keep hook categories clean:
   - `.githooks/skill/*`: skill standards from `references/skill-guidelines.md`.
   - `.githooks/git/*`: generic Git repository state checks.
   - `.githooks/pre-commit` and `.githooks/pre-push`: dispatchers only.
5. Update the installer skill whenever tracked hook paths change.
6. Apply the guideline and hook edits in one change set.

## Required Checks

Run:

```bash
bash .githooks/skill/check-names
bash .githooks/skill/check-dependencies
bash .githooks/pre-commit
python3 tests/test_skill_metadata.py
```

When installer paths or behavior changed, also run:

```bash
bash -n <installer-script>
bash <installer-script> --dry-run --default-llm claude
```

If any check fails because the guideline intentionally changed, update the relevant hook and rerun. Do not leave a failing or knowingly stale hook.

## Output

Report:

- guideline rules changed
- hooks added, adjusted, or removed
- installer path updates, if any
- validation commands run

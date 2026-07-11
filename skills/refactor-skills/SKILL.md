---
name: refactor-skills
description: Use when auditing or refactoring an existing skill for convention compliance or structural improvement
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Dependencies: none

Interface Version: 1

Exported Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
**Skills are software modules. Refactoring a skill follows standard software refactoring discipline** — the same principles Fowler describes for code apply directly: behavior preservation is non-negotiable, moves are small and verified one at a time, and the safety net (characterization) comes before the first change.

## Workflow

### 1. Characterize (before touching anything)
Read the target skill's `SKILL.md`, `permissions.json`, and private runtime implementation files. Write a brief behavioral spec:
- What triggers this skill?
- What does it do, step by step?
- What does it produce (outputs, files written, commands run)?
- What sub-skills does it invoke?

This spec is the invariant. Refactoring must preserve it exactly.

### 2. Smell scan
Check the skill against every smell in `references/skill-smells.md`. List each violation with a direct quote from the file.

### 3. Plan moves
For each smell, identify the refactoring move from `references/skill-refactoring-catalog.md`. Order moves: safe first, structural last. Present the full plan and wait for user confirmation before changing anything.

### 4. Apply one move at a time
After each move, verify behavior is preserved against the characterization from step 1. Do not apply the next move until the current one is confirmed correct. If any move breaks behavior, revert it immediately.

### 5. Commit
Follow convention 6: show diff, confirm with user, commit and push.

---

@./../../references/skill-guidelines.md

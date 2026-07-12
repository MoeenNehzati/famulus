---
name: refactor-skills
description: Use when auditing or refactoring an existing skill for convention compliance or structural improvement
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-development-assistant

Skill Version: 1

Uses Interfaces: none

Public Interfaces:
- `refactor-skills.llm.default`
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
Read the target skill's `SKILL.md` and `blueprint.yaml`. Treat the generated
blueprint contract and declared interfaces as the public boundary. Inspect
private runtime implementation files only when necessary to characterize
externally visible behavior, and only after user approval. Write a brief
behavioral spec:
- What triggers this skill?
- What does it do, step by step?
- What does it produce (outputs, files written, commands run)?
- What sub-skills does it invoke?
- What LLM and machine interfaces does it expose, and how are they routed?

This spec is the invariant. Refactoring must preserve it exactly.

### 2. Smell scan
Check the skill against every smell in `references/skill-smells.md`. List each violation with a direct quote from the file.

### 3. Interface decomposition audit
Use `references/llm-interface-design.md` to decide whether the skill should
remain a single default LLM interface or be broken into named
`llm_interfaces/` surfaces. Identify:
- Whether `SKILL.md` should stay as the full workflow or become a router plus
  shared parent policy.
- Which use cases, if any, need separate read-only, mutating, provider-specific,
  or staged interfaces.
- Which behavior belongs in each interface, which behavior is shared policy,
  and which reference files each interface should load.
- Whether `blueprint.yaml` and generated blocks must be updated so declared LLM
  interfaces match the file layout.

### 4. Plan moves
For each smell, identify the refactoring move from `references/skill-refactoring-catalog.md`. Order moves: safe first, structural last. Present the full plan and wait for user confirmation before changing anything.

### 5. Apply one move at a time
After each move, verify behavior is preserved against the characterization from step 1. Do not apply the next move until the current one is confirmed correct. If any move breaks behavior, revert it immediately.

### 6. Commit
Follow convention 6: show diff, confirm with user, commit and push.

---

@./../../references/skill-guidelines.md
@./../../references/llm-interface-design.md

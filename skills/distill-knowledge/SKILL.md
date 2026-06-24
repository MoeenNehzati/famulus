---
name: distill-knowledge
description: Use when the user explicitly invokes this skill to preserve project-continuity knowledge, or automatically when ending, pausing, or changing tracks after work that produced decisions, failed paths, interface contracts, environment quirks, or user preferences worth preserving. Four or more substantive turns plus a transition cue is a sufficient auto-use signal, not a requirement. Do not auto-use for general "remember this" requests, short clarifications, or incidental mentions of switching inside another task.
---

Dependencies: none

# Distill Knowledge

## Purpose

Preserve project-continuity knowledge before attention moves elsewhere. This skill separates four outputs that are easy to conflate:

- workflow adjustments: changes to how agents should behave going forward, encoded in spec files
- interface updates: notes about interaction points future humans or agents must know
- project lessons: context not easily recoverable from the repo after the adjustment above
- memory candidates: optional user-level or cross-project facts worth proposing for durable memory

## Invocation Rules

If the user explicitly invokes `$distill-knowledge` or a namespaced equivalent such as `$moeen:distill-knowledge`, run the skill on the available current context. Do not refuse merely because an automatic trigger heuristic is not met. If the usable work segment is thin or unclear, say that you are not sure what lesson should be preserved, ask for one or two concrete pointers, and try again after the user responds.

Automatically invoke this skill when the user's latest message is primarily a transition, pause, or track-switch request and the recent work produced project-continuity knowledge worth preserving.

Four or more substantive project-work turns since the last distillation is a sufficient signal that the recent work may be worth distilling. It is not necessary. A shorter segment can still qualify when it contains important decisions, failed paths, interface contracts, environment quirks, or user preferences.

Good standalone/main-intent cues include: `switching`, `moving on`, `change tracks`, `new topic`, `pause this`, `park this`, `before we switch`, and `let's stop here`.

Do not automatically invoke this skill for:

- ordinary `remember this` requests
- one-off trivia or self-contained questions
- incidental words like "switch" inside a longer implementation request
- a short clarification while the same project thread is still active

When automatically invoking, first say: `One moment, I will distill the project knowledge before switching tracks.`

## Workflow

### 1. Summarize What Was Learned

Before touching any file, tell the user what was learned in this segment. Cover:

- decisions made and why
- failed paths and why they failed
- rules or conventions that emerged
- environment quirks or tool behavior
- unresolved risks or future cleanup

Keep it short — a few bullets. This surfaces the lessons so the user can correct or add context before anything is written.

### 2. Apply Workflow Adjustments

If any lesson implies a change to how agents should behave going forward, update the relevant files now. This is the functional step — it changes agent behavior, not just documentation.

**Machine-facing spec files** (primary targets):
- `skill-guidelines.md` and other files under `references/` — rules and conventions for agent behavior
- `SKILL.md` files — workflow instructions for specific skills
- Agent instructions, schemas, or config files agents read at runtime

**Human-facing interface surfaces** (secondary targets):
- README, AGENTS, contributor notes
- Script help text, CLI comments, and command examples
- Build/test workflow notes, generated-artifact regeneration commands

Update machine-facing files first: they encode rules that prevent future agents from repeating the same mistake. Human-facing docs follow the same session.

If no adjustment is needed, say so explicitly.

### 3. Update Project Lessons

Write a lesson entry **only if the lesson is not already captured** by the spec or interface files updated in step 2. Duplicating content that now lives in a spec file adds noise and diverges over time.

Locate the project lesson target using this precedence:

1. `lessons/README.md`
2. `lessons.md`
3. `lessons`
4. relevant files under `lessons/`

If no lesson target exists and the project would benefit from one, create a minimal `lessons` file at the repo root unless local instructions say otherwise.

Project lessons should capture knowledge not easily recovered from the current repo state:

- dead ends and why they failed
- user-visible sensitivities and acceptance criteria
- rejected designs or terminology
- local environment or tool quirks not worth encoding as a rule
- next-agent warnings that are project-specific and transient

Keep lessons short and durable. Prefer entries shaped as:

- Context: what area or problem this concerns
- Lesson: what future agents should know
- Use/Avoid: concrete guidance for next time

Do not duplicate what step 2 already encoded.

### 4. Propose Memory Candidates

After adjustments and lessons are handled, list memory candidates only when useful. Do not write memories automatically unless the user explicitly asks.

Good memory candidates are durable facts about:

- the user's workflow preferences
- recurring system or local setup behavior
- cross-project assistant behavior preferences
- repeated pitfalls that are not tied to one repo

For each candidate, include:

- candidate memory text
- why it is durable
- why it should not stay repo-only

If there are no useful memory candidates, say so.

## Thin or Unclear Context

Do not invent a lesson just because the skill was invoked. If the current context does not clearly expose what should be preserved, ask the user for pointers before deciding that there is nothing to write.

Ask a short question such as:

`I am not sure which lesson from this segment should be preserved. What should future agents know or avoid next time?`

After the user gives pointers, rerun the workflow using those pointers as the missing context.

## Report

End with a concise report:

- what was learned (already shown in step 1)
- spec or interface files changed, or why none were needed
- lesson entries added, or why none were needed
- memory candidates proposed, if any

Do not commit unless the user explicitly asks.

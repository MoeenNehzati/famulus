---
name: distill-knowledge
description: Use when the user explicitly invokes this skill to preserve project-continuity knowledge, or automatically when ending, pausing, or changing tracks after work that produced decisions, failed paths, interface contracts, environment quirks, or user preferences worth preserving. Four or more substantive turns plus a transition cue is a sufficient auto-use signal, not a requirement. Do not auto-use for general "remember this" requests, short clarifications, or incidental mentions of switching inside another task.
---

Dependencies: none

# Distill Knowledge

## Purpose

Preserve project-continuity knowledge before attention moves elsewhere. This skill separates three outputs that are easy to conflate:

- interface updates: repo-visible notes about interaction points future humans, agents, scripts, tests, builds, or workflows must know how to use or preserve
- project lessons: hidden or historical context not easily recoverable from the repo
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

### 1. Identify the Work Segment

Summarize the segment in one or two sentences for yourself before editing. Track:

- changed interaction contracts
- decisions the user accepted or rejected
- failed paths and why they failed
- unresolved risks or future cleanup
- project-specific user preferences or sensitivities
- environment quirks, commands, generated artifacts, or verification habits

### 2. Update Interface Notes First

Interface updates are not broad documentation. Document interaction points, not whole artifacts.

Inspect only the relevant repo surfaces before editing. Typical interface surfaces include:

- README, AGENTS, skill instructions, or contributor notes
- script help text, CLI comments, and command examples
- schema/config comments or reference files
- build/test workflow notes
- generated-artifact source-of-truth notes and regeneration commands
- public or agent-facing entrypoints

For scripts, capture the CLI contract: purpose, inputs, outputs, side effects, failure behavior, and verification command when relevant.

For TeX or prose projects, do not add commentary everywhere. Prefer entrypoints, build behavior, tool-dependent macros, generated outputs, section routing, submission constraints, and other non-human or workflow-facing interaction points.

If no interface update is needed, explicitly record that in the final report.

### 3. Update Project Lessons

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
- local environment or tool quirks
- important project-specific preferences
- next-agent warnings
- exact commands only when the command itself is part of the lesson

Keep lessons short and durable. Prefer entries shaped as:

- Context: what area or problem this concerns
- Lesson: what future agents should know
- Use/Avoid: concrete guidance for next time

Do not duplicate implementation details that the code or interface notes already make obvious.

### 4. Propose Memory Candidates

After interface updates and project lessons are handled, list memory candidates only when useful. Do not write memories automatically unless the user explicitly asks.

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

### 5. Report

End with a concise report:

- interface files changed, or why none were needed
- lesson files changed
- memory candidates proposed, if any
- verification performed

Do not commit unless the user explicitly asks.

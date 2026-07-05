---
name: prepare-handoff
description: Use when the user explicitly invokes this skill to prepare a handoff or preserve project continuity before pausing, ending, or switching tracks after work that produced decisions, failed paths, interface contracts, environment quirks, or preferences worth preserving. Do not auto-use for general "remember this" requests, short clarifications, or incidental mentions of switching inside another task.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: workflow-general-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

# Prepare Handoff

## Purpose

Prepare a clean handoff before attention moves elsewhere. The goal is to leave the project in a state where another developer, without access to additional chat context, can correctly maintain, debug, and extend it from the repo itself. Use that standard to decide what should change in workflows, docs, and residual lessons.

This skill separates four outputs that are easy to conflate:

- workflow updates: changes to machine-facing rules, specs, and instructions that should guide future agents
- documentation updates: changes to human-facing docs, notes, examples, or help text
- project lessons: residual context not captured by those workflow or documentation updates
- memory candidates: optional user-level or cross-project facts worth proposing for durable memory

## Invocation Rules

If the user explicitly invokes `$prepare-handoff` or a namespaced equivalent such as `$moeen:prepare-handoff`, run the skill on the available current context. Do not refuse merely because an automatic trigger heuristic is not met. If the usable work segment is thin or unclear, say that you are not sure what should be preserved, ask for one or two concrete pointers, and try again after the user responds.

Automatically invoke this skill when the user's latest message is primarily a transition, pause, end-of-session, or track-switch request and the recent work produced project-continuity knowledge worth preserving.

Four or more substantive project-work turns since the last handoff pass is a sufficient signal that the recent work may be worth preserving. It is not necessary. A shorter segment can still qualify when it contains important decisions, failed paths, interface contracts, environment quirks, or user preferences.

Good standalone/main-intent cues include: `switching`, `moving on`, `change tracks`, `new topic`, `pause this`, `park this`, `before we switch`, and `let's stop here`.

Do not automatically invoke this skill for:

- ordinary `remember this` requests
- one-off trivia or self-contained questions
- incidental words like `switch` inside a longer implementation request
- a short clarification while the same project thread is still active

When automatically invoking, first say: `One moment, I will prepare the handoff before switching tracks.`

## Workflow

### 1. Review the Whole Handoff Surface Before Writing

Before touching any file, do a full pass over the recent work and ask what a new developer would still be missing if they only had the repo and not this conversation. Then separate the candidate follow-up into three buckets:

- workflow changes: machine-facing specs, agent instructions, schemas, or skill instructions that should change
- documentation changes: human-facing docs, notes, examples, comments, or help text that should change
- lesson candidates: only items that would still remain useful after those workflow and documentation changes are made

This first pass is diagnostic only. Do not write files yet.

### 2. Present the Proposed Handoff Plan and Ask for User Opinion

Tell the user, briefly, what you think belongs in each bucket:

- decisions made and why
- failed paths and why they failed
- workflow or documentation updates you propose
- lesson candidates that seem residual rather than documentable
- unresolved risks or cleanup

Then ask for the user's opinion on the plan and wait for explicit approval before writing any workflow, documentation, or lesson files.

If a repo-structure issue affects where lessons would be written — for example, a root-level file named `lessons` blocks creation of a `lessons/` directory — include that in the proposal and ask how to handle it before writing.

### 3. Apply Workflow and Documentation Updates After Approval

After the user approves the plan, update workflow/spec files and documentation first. This is the functional step — it changes how future agents and contributors behave, and it should encode as much of the needed handoff context as possible directly into the repo.

**Machine-facing workflow/spec files** (primary targets):
- `SKILL.md` files and related agent instruction files
- files under `references/`
- schemas, validators, config, or other files agents read at runtime

**Human-facing documentation surfaces** (secondary targets):
- README, AGENTS, contributor notes
- script help text, CLI comments, and command examples
- build/test workflow notes

If no workflow or documentation adjustment is needed, say so explicitly.

### 4. Write Residual Lessons After Approval

Only after workflow and documentation updates are complete, write lessons for knowledge that is still not captured anywhere agents or contributors will actually read. Lessons are the residual high-value technical context that another developer would otherwise be missing even after reading the updated repo.

A lesson is worth writing only if it would materially change what the next developer tries, avoids, checks, or debugs, or save them meaningful time exploring options, validating assumptions, or ruling out known dead ends. If it would not change action or save real time, do not write it.

Good lessons are concrete, technical, and local to the work. Prefer items such as:

- what failed, and under what condition or constraint it failed
- which approach currently looks like the best bet, and why
- which assumption, interpretation, or shortcut turned out to be wrong
- which file, tool, interface, workflow, or environment detail is sensitive
- which validation, test, or inspection actually exposed the issue
- which constraint or tradeoff shaped the final direction

Do not write vague guidance, generic best practices, motivational summaries, or high-level takeaways that would not change what a future developer actually does in this repo.
When known, prefer naming exact files, tests, commands, tools, or interfaces rather than describing them abstractly.

Write lessons to `lessons/YYYY-MM-DD.md` using the local date for the repo/session. If that dated file already exists, append to it.

If a root-level file named `lessons` blocks the `lessons/` directory layout, do not write around it silently. Surface the conflict in step 2, get user approval on the migration/rename plan, and only then proceed.

For each lesson, make the action consequence explicit. Prefer entries shaped as:

- Context: exact area, file, tool, or task
- Observation: concrete fact learned
- Impact: why it matters technically
- Use/Avoid/Best bet: what the next developer should do differently

Before writing a lesson, ask: if someone knew this at the start, would they avoid a real mistake, dead end, wrong assumption, or wasted debugging time? If not, do not write it.

### 5. Propose Memory Candidates

After workflow/doc updates and residual lessons are handled, list memory candidates only when useful. Do not write memories automatically unless the user explicitly asks.

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

Do not invent a handoff plan just because the skill was invoked. If the current context does not clearly expose what should be preserved, ask the user for pointers before deciding there is nothing to write.

Ask a short question such as:

`I am not sure what this handoff should preserve. What should future agents know, update, or avoid next time?`

After the user gives pointers, rerun the workflow using those pointers as the missing context.

## Report

End with a concise report:

- what the approved handoff plan was
- workflow or documentation files changed, or why none were needed
- lesson entries added, or why none were needed
- memory candidates proposed, if any

Do not commit unless the user explicitly asks.

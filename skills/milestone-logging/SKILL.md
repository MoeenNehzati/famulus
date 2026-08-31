---
name: milestone-logging
description: Use when starting or completing substantive agent work that needs durable, role-labelled progress records and optional run recovery.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `milestone-logging._rtx.interface.record@1` — Append a role-labelled progress milestone or completion record. Progress supplies doing and optional prev; completion supplies the closing result as the done value instead of positional messages. Typed recovery state can also be mirrored into a durable run journal.
  - `dispatcher --caller-skill milestone-logging milestone-logging._rtx.interface.record [DOING] [PREV] [--role ROLE] [--done PREV] [--path] [--run ID] [--event EVENT] [--step STEP] [--task TASK] [--state STATE] [--attempt ATTEMPT] [--evidence PATH]`
  - milestone: Progress mode takes DOING and optional PREV. Completion mode takes --done PREV instead of positional messages, and --done requires its PREV value. Agent-authored records include --role ROLE. --path prints the selected path without recording. Typed event fields require --run ID.
- `milestone-logging._rtx.interface.timeline@1` — List milestone sessions, render one session with its transcript events, or reconstruct one durable run as text or JSON.
  - `dispatcher --caller-skill milestone-logging milestone-logging._rtx.interface.timeline [SESSION] [-l|--list] [--slow SECONDS] [--run ID] [--json]`
  - timeline: Session mode accepts an optional SESSION, list mode enumerates known sessions, and run mode uses --run ID; --json applies only to run mode.

<!-- END BLUEPRINT INTERFACES -->
Skill: milestone-logging

Use `record` before the first substantive action, before each distinct work item, every few tool calls, and at completion. Every record is role-labelled. A progress record names the work starting now and how the preceding piece ended; a completion record closes the final work item with its outcome.

If one `record` invocation fails, report that exact failure once and continue the task; do not invent a record or retry blindly.

Use the durable-run mode only for work that must outlive the current session. Put recovery-relevant state in its typed fields.

For a diagnostic read, invoke `timeline`; use its explicit list or run route to inspect existing records.

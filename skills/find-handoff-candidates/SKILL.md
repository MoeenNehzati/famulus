---
name: find-handoff-candidates
description: Use when you need a mechanical, non-interpretive scan of today's (or another day's) work sessions to find ones that had substantial activity but no completed handoff. Typically invoked by wrap-up, not directly by the user.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-interaction; topics: session-management, task-automation; visibility: hidden
Activation: skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `find-handoff-candidates.source.gateway -> find-handoff-candidates._rtx.interface.calibrate@1`
- `find-handoff-candidates.source.gateway -> find-handoff-candidates._rtx.interface.scan@1`
- `find-handoff-candidates.source.gateway -> prepare-handoff.interface.default@1`

Public Interfaces:
- `find-handoff-candidates.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `find-handoff-candidates.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Find Handoff Candidates

Invoke `scan` to identify sessions with substantial conversation since their last
completed handoff without opening or semantically judging transcript content.

- Relay every returned record verbatim. Do not re-judge candidates or filter by
  `handoff_status`; a `complete` session can be returned when substantial work
  followed its last completed handoff.
- Do not open, read, or summarize flagged transcripts. Use the returned project,
  timestamps, `gap_net_chars`, `handoff_status`, and `resume_hint` fields to
  present or persist each candidate. Follow `resume_hint` to resume the session
  and invoke `prepare-handoff.interface.default` there.
- Treat an absent transcript source as an empty source, not an error.

Invoke `calibrate` when the default thresholds appear too sensitive or too
permissive. Its output is diagnostic guidance only; it does not change the
thresholds.

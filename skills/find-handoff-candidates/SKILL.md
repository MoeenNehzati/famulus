---
name: find-handoff-candidates
description: >-
  Use when `wrap-up` needs to identify recent work sessions that may still require a handoff. Do not invoke directly for transcript review, interpretation, or summarization.
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

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `find-handoff-candidates._rtx.interface.calibrate` — Re-derive reference median/p75/p90 gap-size statistics per host from real transcripts in a lookback window, to check whether scan's default thresholds still make sense. Diagnostic only -- does not modify any parser file; read the output and edit the relevant parser's default_threshold by hand if it suggests new numbers.
  - Caller: `find-handoff-candidates`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--days": "N"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `find-handoff-candidates._rtx.interface.scan` — Scan session transcripts across every configured host (default: trailing 2 days), and report sessions whose conversation since their last completed handoff exceeds a per-host threshold, using mechanical extraction only (no LLM judgment).
  - Caller: `find-handoff-candidates`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--date": "YYYY-MM-DD", "--days": "N", "--min-gap-chars": "N"}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
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

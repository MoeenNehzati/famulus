---
name: find-handoff-candidates
description: >-
  Use when `wrap-up` needs to identify recent work sessions that may still require a handoff. Do not invoke directly for transcript review, interpretation, or summarization.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `find-handoff-candidates._rtx.interface.calibrate@1` — Re-derive reference median/p75/p90 gap-size statistics per host from real transcripts in a lookback window, to check whether scan's default thresholds still make sense. Diagnostic only -- does not modify any parser file; read the output and edit the relevant parser's default_threshold by hand if it suggests new numbers.
  - `dispatcher --caller-skill find-handoff-candidates find-handoff-candidates._rtx.interface.calibrate [--days N]`
  - No positionals. --days sets the lookback window (default 5). Output is human-readable per-host stats to stdout, not JSON -- this is a manual diagnostic tool, not meant for programmatic consumption.
- `find-handoff-candidates._rtx.interface.scan@1` — Scan session transcripts across every configured host (default: trailing 2 days), and report sessions whose conversation since their last completed handoff exceeds a per-host threshold, using mechanical extraction only (no LLM judgment).
  - `dispatcher --caller-skill find-handoff-candidates find-handoff-candidates._rtx.interface.scan [--min-gap-chars N] [--days N | --date YYYY-MM-DD]`
  - No positionals. --min-gap-chars overrides every host's built-in default threshold (each host is calibrated separately, since they differ by roughly an order of magnitude in bytes per unit of work) with a single shared value. --days (default 2) scans the trailing N days ending today, inclusive; --date pins to one exact day instead (mutually exclusive with --days; mainly useful for backtesting/calibration). Output is a JSON array; each entry's handoff_status is one of none, started, complete -- a complete entry can still be flagged if gap_net_chars (conversation since that completion) exceeds the threshold.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `prepare-handoff.interface.default@1` — Review recent work, obtain approval, encode project-local continuity, and close with exact machine-readable sentinels.
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

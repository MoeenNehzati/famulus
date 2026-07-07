---
name: find-handoff-candidates
description: Use when you need a mechanical, non-interpretive scan of today's (or another day's) work sessions to find ones that had substantial activity but no completed handoff. Typically invoked by wrap-up, not directly by the user.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: workflow-general-assistant

Dependencies:
- prepare-handoff

Interface Version: 1

Exported Script Interfaces:
- `scan`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `calibrate` — Re-derive reference median/p75/p90 gap-size statistics per host from real transcripts in a lookback window, to check whether scan's default thresholds still make sense. Diagnostic only -- does not modify any parser file; read the output and edit the relevant parser's default_threshold by hand if it suggests new numbers.
  - `dispatcher --caller-skill find-handoff-candidates find-handoff-candidates calibrate [--days N]`
  - No positionals. --days sets the lookback window (default 5). Output is human-readable per-host stats to stdout, not JSON -- this is a manual diagnostic tool, not meant for programmatic consumption.
- `scan` — Scan session transcripts across every configured host (default: trailing 2 days), and report sessions whose conversation since their last completed handoff exceeds a per-host threshold, using mechanical extraction only (no LLM judgment).
  - `dispatcher --caller-skill find-handoff-candidates find-handoff-candidates scan [--min-gap-chars N] [--days N | --date YYYY-MM-DD]`
  - No positionals. --min-gap-chars overrides every host's built-in default threshold (each host is calibrated separately, since they differ by roughly an order of magnitude in bytes per unit of work) with a single shared value. --days (default 2) scans the trailing N days ending today, inclusive; --date pins to one exact day instead (mutually exclusive with --days; mainly useful for backtesting/calibration). Output is a JSON array; each entry's handoff_status is one of none, started, complete -- a complete entry can still be flagged if gap_net_chars (conversation since that completion) exceeds the threshold.
<!-- END BLUEPRINT INTERFACES -->
# Find Handoff Candidates

## Purpose

Answer "which sessions today have unhandled-off work worth checking?" without any LLM reading of session content. All extraction (timestamps, project paths, handoff status, and the conversation-size gap since the last handoff) is done by the `scan` script via file metadata, a strict sentinel-marker regex, and a generic JSON field walk — never by summarizing or judging conversation content.

This exists so callers can flag missed handoffs without loading every session's transcript into context.

## How flagging works

The `prepare-handoff` skill emits two exact sentinel comments into its own output — `<!-- HANDOFF-SENTINEL: STARTED -->` when it presents a plan, `<!-- HANDOFF-SENTINEL: COMPLETE -->` once writes are done. `scan` regex-matches these exact forms (not the bare words), so ordinary conversation that merely mentions handoffs does not produce a false positive.

Rather than gating on raw session size, `scan` measures `gap_net_chars`: how much conversation happened since the last `COMPLETE` sentinel (or since the session started, if never handed off). That gap resets to zero on every `COMPLETE`, so a session can legitimately reappear with `handoff_status: complete` if substantial new work followed that handoff. Each entry's contribution to the gap excludes whatever field name that host's own opaque crypto blob uses (see the relevant per-host parser file for specifics) — chosen so unknown future fields are still counted rather than silently dropped.

## Rules for callers

- Relay `scan`'s output as-is. Do not re-judge which sessions matter, and do not filter by `handoff_status` — the gap threshold already decided every returned record needs attention, `complete` included.
- Do not open, read, or summarize the flagged sessions' own transcript content. Every field a caller might need to act on a flagged session (project, timestamps, gap size, and the `resume_hint` field to resume and invoke `prepare-handoff` there) is already in the record — pass those fields through verbatim, whether that means telling the user directly or persisting them elsewhere for later review.
- If a given host's transcript directory isn't present on this machine, `scan` simply finds nothing for that source — no error, no special-casing needed by the caller.

## Re-tuning thresholds

`scan`'s default gap thresholds are judgment calls calibrated from a thin, real-but-limited sample. If they start flagging too much or too little in practice, call the `calibrate` interface for fresh per-source reference statistics and a comparison against the current thresholds, then update the default thresholds by hand based on what it reports.

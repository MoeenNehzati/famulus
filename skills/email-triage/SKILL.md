---
name: email-triage
description: >-
  Use when the user asks for inbox-level email triage or processing. Do not use for ordinary email access, sending, or analysis of a single message.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `email-triage._rtx.interface.fetch-filtered-envelopes@1` — Fetch email envelopes for one account through email-client and emit only envelopes strictly after the triage watermark.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.fetch-filtered-envelopes -a <account> --after YYYY-MM-DD [--rescan-after ISO_CUTOFF] [--dedup-against todo|triage]`
- `email-triage._rtx.interface.scripts-clear-failure@1` — Clear a latched triage failure after its cause is fixed, without advancing the watermark.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-clear-failure [reason]`
- `email-triage._rtx.interface.scripts-filter-envelopes@1` — Filter JSON envelopes (from email-client's mail-list, piped via stdin) to those strictly after the triage watermark.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-filter-envelopes -a <account>   < envelopes.json`
- `email-triage._rtx.interface.scripts-finalize-triage@1` — Ordered, idempotent finalization of one triage run — writes metrics, then (only on success and only if no failure is latched) advances the watermark, recording the run id so a replayed call is a safe no-op.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-finalize-triage --run-id <id> --total-scanned N --added-todo N --added-triage N --skipped N [--deduped N] [--accounts a,b]`
- `email-triage._rtx.interface.scripts-get-cutoff@1` — Return the cutoff date for the current triage run, with a fallback if no watermark exists.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-get-cutoff`
- `email-triage._rtx.interface.scripts-log-decision@1` — Append a triage classification decision for one email to triage.log.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-log-decision <account> <id> <from> <subject> <DECISION> <reason>`
- `email-triage._rtx.interface.scripts-mark-failure@1` — Record that this triage run failed, so update-watermark refuses to advance and the scheduled health check reports it.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-mark-failure <reason>`
- `email-triage._rtx.interface.scripts-prune-log@1` — Drop triage.log entries older than 30 days and print a one-line summary.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-prune-log`
- `email-triage._rtx.interface.scripts-update-watermark@1` — Advance the triage watermark to the current timestamp. Refuses if scripts-mark-failure was called earlier in this run.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-update-watermark`
- `email-triage._rtx.interface.scripts-write-metrics@1` — Write metrics from a triage run (emails scanned, added to lists, skipped, deduped) to status.json for visibility and debugging.
  - `dispatcher --caller-skill email-triage email-triage._rtx.interface.scripts-write-metrics Write the triage run metrics (counts) to state/status.json with timestamps for post-run reporting.`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `email-triage.source.instructions-triage.interface.triage@2` — Scans emails received since the last triage run and routes extracted action items to the right list.
<!-- END BLUEPRINT INTERFACES -->
# Email Triage

Use `email-triage.interface.triage` for every request within this skill's trigger
scope. Load that interface's detailed instructions and begin triage directly.

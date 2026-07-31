---
name: email-triage
description: Use when asked to triage email, process the inbox, or surface action items from recent emails.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Skill Version: 1

Uses Interfaces:
- `email-triage.source.gateway -> email-triage.source.instructions-triage.interface.triage@2`
- `email-triage.source.instructions-triage -> email-client.interface.default@3`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-decision-sink.interface.scripts-log-decision@1`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-failure-clearer.interface.scripts-clear-failure@1`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-failure-sentinel.interface.scripts-mark-failure@1`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-finalize-run.interface.scripts-finalize-triage@1`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-log-compactor.interface.scripts-prune-log@1`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-mail-envelope-stream.interface.fetch-filtered-envelopes@1`
- `email-triage.source.instructions-triage -> email-triage.source.rtx-watermark-floor.interface.scripts-get-cutoff@1`
- `email-triage.source.instructions-triage -> list-manager.interface.default@1`
- `email-triage.source.rtx-envelope-gate -> common.interface.famulus-paths@1`
- `email-triage.source.rtx-failure-clearer -> common.interface.famulus-paths@1`
- `email-triage.source.rtx-failure-sentinel -> common.interface.famulus-paths@1`
- `email-triage.source.rtx-finalize-run -> common.interface.famulus-paths@1`
- `email-triage.source.rtx-mail-envelope-stream -> email-client.interface.mail-list@1`
- `email-triage.source.rtx-mail-envelope-stream -> list-manager.interface.cloud-read@1`
- `email-triage.source.rtx-watermark-floor -> common.interface.famulus-paths@1`
- `email-triage.source.rtx-watermark-writer -> common.interface.famulus-paths@1`
- `email-triage.source.rtx-write-metrics -> common.interface.famulus-paths@1`

Public Interfaces:
- `email-triage.interface.default`
- `email-triage.interface.fetch-filtered-envelopes`
- `email-triage.interface.scripts-clear-failure`
- `email-triage.interface.scripts-filter-envelopes`
- `email-triage.interface.scripts-finalize-triage`
- `email-triage.interface.scripts-get-cutoff`
- `email-triage.interface.scripts-log-decision`
- `email-triage.interface.scripts-mark-failure`
- `email-triage.interface.scripts-prune-log`
- `email-triage.interface.scripts-update-watermark`
- `email-triage.interface.scripts-write-metrics`
- `email-triage.interface.triage`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `email-triage.interface.fetch-filtered-envelopes` — Fetch email envelopes for one account through email-client and emit only envelopes strictly after the triage watermark.
  - `dispatcher --caller-skill email-triage email-triage.interface.fetch-filtered-envelopes -a <account> --after YYYY-MM-DD [--rescan-after ISO_CUTOFF] [--dedup-against todo|triage]`
- `email-triage.interface.scripts-clear-failure` — Clear a latched triage failure after its cause is fixed, without advancing the watermark.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-clear-failure [reason]`
- `email-triage.interface.scripts-filter-envelopes` — Filter JSON envelopes (from email-client's mail-list, piped via stdin) to those strictly after the triage watermark.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-filter-envelopes -a <account>   < envelopes.json`
- `email-triage.interface.scripts-finalize-triage` — Ordered, idempotent finalization of one triage run — writes metrics, then (only on success and only if no failure is latched) advances the watermark, recording the run id so a replayed call is a safe no-op.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-finalize-triage --run-id <id> --total-scanned N --added-todo N --added-triage N --skipped N [--deduped N] [--accounts a,b]`
- `email-triage.interface.scripts-get-cutoff` — Return the cutoff date for the current triage run, with a fallback if no watermark exists.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-get-cutoff`
- `email-triage.interface.scripts-log-decision` — Append a triage classification decision for one email to triage.log.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-log-decision <account> <id> <from> <subject> <DECISION> <reason>`
- `email-triage.interface.scripts-mark-failure` — Record that this triage run failed, so update-watermark refuses to advance and the scheduled health check reports it.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-mark-failure <reason>`
- `email-triage.interface.scripts-prune-log` — Drop triage.log entries older than 30 days and print a one-line summary.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-prune-log`
- `email-triage.interface.scripts-update-watermark` — Advance the triage watermark to the current timestamp. Refuses if scripts-mark-failure was called earlier in this run. Optionally accepts a run id to make repeated calls for the same run a replay-safe no-op.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-update-watermark [--run-id <id>]`
- `email-triage.interface.scripts-write-metrics` — Write metrics from a triage run (emails scanned, added to lists, skipped, deduped) to status.json for visibility and debugging.
  - `dispatcher --caller-skill email-triage email-triage.interface.scripts-write-metrics Write the triage run metrics (counts) to state/status.json with timestamps for post-run reporting.`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `email-triage.interface.default` — Primary LLM-facing skill instructions.
- `email-triage.interface.triage` — Scans emails received since the last triage run and routes extracted action items to the right list.
<!-- END BLUEPRINT INTERFACES -->
# Email Triage

Use `email-triage.interface.triage` for every request within this skill's trigger
scope. Load that interface's detailed instructions and begin triage directly.

---
name: email-triage
description: Use when asked to triage email, process the inbox, or surface action items from recent emails.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Skill Version: 2

Uses Interfaces:
- `email-triage.llm.default -> email-triage.llm.triage@2`
- `email-triage.llm.triage -> email-client.llm.default@3`
- `email-triage.llm.triage -> email-triage.machine.fetch-filtered-envelopes@1`
- `email-triage.llm.triage -> email-triage.machine.scripts-clear-failure@1`
- `email-triage.llm.triage -> email-triage.machine.scripts-get-cutoff@1`
- `email-triage.llm.triage -> email-triage.machine.scripts-log-decision@1`
- `email-triage.llm.triage -> email-triage.machine.scripts-mark-failure@1`
- `email-triage.llm.triage -> email-triage.machine.scripts-prune-log@1`
- `email-triage.llm.triage -> email-triage.machine.scripts-update-watermark@1`
- `email-triage.llm.triage -> list-manager.llm.default@1`
- `email-triage.machine.fetch-filtered-envelopes -> email-client.machine.mail-list@1`

Public Interfaces:
- `email-triage.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `fetch-filtered-envelopes` — Fetch email envelopes for one account through email-client and emit only envelopes strictly after the triage watermark.
  - `dispatcher --caller-skill email-triage email-triage.machine.fetch-filtered-envelopes -a <account> --after YYYY-MM-DD`
- `scripts-clear-failure` — Clear a latched triage failure after its cause is fixed, without advancing the watermark.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-clear-failure [reason]`
- `scripts-filter-envelopes` — Filter JSON envelopes (from email-client's mail-list, piped via stdin) to those strictly after the triage watermark.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-filter-envelopes -a <account>   < envelopes.json`
- `scripts-get-cutoff` — Return the cutoff date for the current triage run, with a fallback if no watermark exists.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-get-cutoff`
- `scripts-log-decision` — Append a triage classification decision for one email to triage.log.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-log-decision <account> <id> <from> <subject> <DECISION> <reason>`
- `scripts-mark-failure` — Record that this triage run failed, so update-watermark refuses to advance and the scheduled health check reports it.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-mark-failure <reason>`
- `scripts-prune-log` — Drop triage.log entries older than 30 days and print a one-line summary.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-prune-log`
- `scripts-update-watermark` — Advance the triage watermark to the current timestamp. Refuses if scripts-mark-failure was called earlier in this run.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-update-watermark`
- `scripts-write-metrics` — Write metrics from a triage run (emails scanned, added to lists, skipped, deduped) to status.json for visibility and debugging.
  - `dispatcher --caller-skill email-triage email-triage.machine.scripts-write-metrics Write the triage run metrics (counts) to state/status.json with timestamps for post-run reporting.`

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
- `triage` — Scans emails received since the last triage run and routes extracted action items to the right list.
  - binding: relative markdown path `llm_interfaces/triage.md`
<!-- END BLUEPRINT INTERFACES -->
# Email Triage

Use `email-triage.llm.triage` for every request within this skill's trigger
scope. Load that interface's detailed instructions and begin triage directly.

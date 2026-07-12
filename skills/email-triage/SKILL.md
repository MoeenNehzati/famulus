---
name: email-triage
description: Use when asked to triage email, process the inbox, or surface action items from recent emails. Reads emails since the last triage run (watermark) and syncs with the todo list and triage list.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Skill Version: 1

Uses Interfaces:
- `email-triage.llm.default -> email-client.llm.default@3`
- `email-triage.llm.default -> list-manager.llm.default@1`

Public Interfaces:
- `email-triage.llm.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
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

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
# Email Triage

Scans emails received since the last triage run. Extracts action items and routes them to the right list. Never adds events to the calendar automatically — the user decides.

Use the `email-client` skill to read and send email. Use the `list-manager` skill to read and update destination lists.

**IMPORTANT: Never ask the user for a lookback period or watermark date. The date always comes from the `scripts-get-cutoff` interface. If that interface emits a warning or fails, report it to the user — but do not ask them to supply a date instead.**

**Decision logging:** After every classification, invoke the `scripts-log-decision` interface:

`scripts-log-decision <account> <id> "<from>" "<subject>" <DECISION> "<reason>"`

`DECISION` values: `SKIP` (subject-only skip) · `NO_ACTION` (body read, nothing to do) · `TODO` (added to todo) · `POTENTIAL` (added to triage) · `DEDUP` (already exists in destination)  
`reason` = one sentence explaining the classification. Log: `triage.log`

**Two destination lists:**
- `todo` — directed, personal actions: bills to pay, replies owed, explicit follow-up commitments
- `triage` — anything the user may or may not act on: events, seminars, CFPs, summer schools, workshops, fellowship applications, optional signups

---

## Step 1 — Fetch new envelopes (run in parallel per account)

First, call `email-client`'s `accounts-list` to get the configured account nicknames —
do not assume or hardcode which ones exist. Triage every account it returns.

Two shell invocations per account:

1. `scripts-get-cutoff` — the coarse lookback date (day-level; IMAP can't filter finer than a day). Its own call — the date is short, fine to see.
2. **A single piped shell command**: `mail-list -a <account> --after <that date> | scripts-filter-envelopes -a <account>`. **Do not split this into two separate tool calls.** `mail-list`'s raw output is the full (unfiltered-by-time) envelope set for that lookback window — an interim result that gets narrowed by `scripts-filter-envelopes` to strictly-after-watermark. Piped as one shell command, that interim JSON flows process-to-process and never enters your context; only the filtered result (what `scripts-filter-envelopes` prints) does. Two separate tool calls would put the unfiltered set in your context first, which is what the pipe exists to avoid.

Run this per account returned by `accounts-list`, in parallel across accounts.

Reading always goes through `email-client`'s `mail-list`/`mail-read` interfaces — never call an IMAP CLI directly.

If `scripts-filter-envelopes` prints `(no new emails for …)`, skip that account in later steps. If stderr contains a `WARNING:` line, include it in the Step 6 report.

Each envelope is JSON: `id`, `flags` (IMAP flags — absence of `\Seen` means unread, `\Answered` means replied), `subject`, `from`, `date`, `message_id`.

**Skip immediately** (don't read body) when the subject alone makes it unambiguous: sales/discount offers, newsletter digests, GitHub notifications, delivery confirmations, social media digests, referral bonuses. For financial senders (banks, SoFi, Spotify, utilities): read the subject — skip if promotional, read the body if it could be a statement, payment due, or alert. **Log each skip with `SKIP` and one sentence why.**

**Never skip** if the subject suggests a message is waiting on a portal ("you have a message", "new message", "someone replied") — a human sent it; classify as Type 3 in Step 3.

---

## Step 3 — Read email bodies in batches

```bash
mail-read -a <account> <ID>
```

Batch up to 10 reads in parallel. Classify each email by sender type and targeting:

**Type 1 — Person → you** (individual sender, addressed to you or a small group)  
**Type 2 — Person → mass** (individual sender, sent to a list, newsletter, or broadcast)  
**Type 3 — Institution proxying a person** (portal message, ticket reply, secure message alert — a human initiated contact, even if unnamed)  
**Type 4 — Institution as itself** (automated report, statement, summary, marketing — no specific human is communicating through this)

**Routing:**
- **Types 1 & 3** always surface:
  - Reply expected (no `\Answered` in `flags`, asks a question or expects a response) → `todo`
  - Informational → `triage` if there's something to act on, otherwise `NO_ACTION`
- **Type 2** — treat like Type 4
- **Type 4** — route by new-information criterion:
  - Bill / payment due → `todo`
  - Payment received (someone sent you money) → `triage` (you may have a corresponding debt to mark off)
  - New event or opportunity → `triage`
  - Record of past activity or information you already have → `NO_ACTION`

**Follow-up commitments** (any type): if a prior reply contains an explicit promise (e.g. "I'll send you X in July"), add to `todo` regardless of type.

**Log every email read at this step** — one `scripts-log-decision` call per email with its classification (`NO_ACTION`, `TODO`, `POTENTIAL`) and one sentence why. Log `NO_ACTION` even when nothing is added.

---

## Step 4 — Read both destination lists via `list-manager` skill

Invoke the `list-manager` skill to read `todo` and `triage`.

---

## Step 5 — Add action items, deduplicating

Every item sent to the `list-manager` skill must be concrete enough for the list
skill to infer title, optional description, and optional deadline. Do not
manually format list storage lines here; pass the freeform action content and
destination list to the `list-manager` skill.

For every item added to `triage`, include the source email id in the description
so the originating message can be found again later.

**Format by category:**
- Bill: `Pay [Sender]; amount/context $[amount]; deadline [date]` → `todo`
- Reply: `Reply to [Name] re: [subject]` → `todo`
- Follow-up: `[action verb] [target]; deadline [timeframe]` → `todo`
- Portal / institution message (Type 3, informational): `Check message on [portal/system]` → `triage`
- Payment received: `Review: [Name] paid you $[amount]` → `triage`
- Event: `Attend [event name]; [date/time/location]` → `triage`
- CFP / application: `Submit to [name]; deadline [deadline]` or `Apply to [name]; deadline [deadline]` → `triage`
- Optional signup: `Sign up for [name]; deadline/date [date or deadline]` → `triage`

If deadline or date is unknown, omit rather than guess.

**Dedup:** before adding to `triage`, scan for a case-insensitive substring match on the key noun (sender name, event name, program name). If a match exists in any state (`[ ]`, `[+]`, or `[-]`), skip — the item has already been triaged. Log with `DEDUP` and note the matched item. Use the `list-manager` skill to add new items.

---

## Step 6 — Report

Summarize concisely:
- N emails scanned across both accounts
- Items added to `todo` (list them)
- Items added to `triage` (list them)
- Items skipped (already listed / no action / promotional)

---

## Step 7 — Update watermark and prune log

If any `list-manager` add/update in Step 5 failed (e.g. a validation error), invoke `scripts-mark-failure "<reason>"` and stop — do not call `scripts-update-watermark`. This keeps next run's lookback window covering the emails that didn't get filed, and surfaces the failure as a desktop notification via the scheduled health check.

Otherwise, after a successful run, invoke both interfaces:

- `scripts-update-watermark`
- `scripts-prune-log`

`scripts-prune-log` drops entries older than 30 days and prints a one-line summary.

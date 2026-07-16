# Email Triage

Scans emails received since the last triage run. Extracts action items and routes them to the right list. Never adds events to the calendar automatically — the user decides.

Use `email-client.llm.default` to read and send email. Use `list-manager.llm.default` to read and update destination lists.

**IMPORTANT: Never ask the user for a lookback period or watermark date. The date always comes from the `email-triage.machine.scripts-get-cutoff` interface. If that interface emits a warning or fails, report it to the user — but do not ask them to supply a date instead.**

**Decision logging:** After every classification, invoke the `email-triage.machine.scripts-log-decision` interface:

`email-triage.machine.scripts-log-decision <account> <id> "<from>" "<subject>" <DECISION> "<reason>"`

`DECISION` values: `SKIP` (subject-only skip) · `NO_ACTION` (body read, nothing to do) · `TODO` (added to todo) · `POTENTIAL` (added to triage) · `DEDUP` (already exists in destination)
`reason` = one sentence explaining the classification. Log: `triage.log`

**Two destination lists:**
- `todo` — directed, personal actions: bills to pay, replies owed, explicit follow-up commitments
- `triage` — anything the user may or may not act on: events, seminars, CFPs, summer schools, workshops, fellowship applications, optional signups

---

## Step 1 — Fetch new envelopes (run in parallel per account)

First, call `email-client.llm.default`'s `accounts-list` to get the configured account nicknames —
do not assume or hardcode which ones exist. Triage every account it returns.

Two interface calls per account:

1. `email-triage.machine.scripts-get-cutoff` — the coarse lookback date (day-level; IMAP can't filter finer than a day). Its own call — the date is short, fine to see.
2. `email-triage.machine.fetch-filtered-envelopes` with the account and that cutoff date. This composite interface fetches through the declared mail-list boundary and applies the exact watermark filter internally. Only its filtered result enters your context; never fetch unfiltered envelopes separately.

Run this per account returned by `accounts-list`, in parallel across accounts.

Reading always goes through `email-client.llm.default`'s `mail-list`/`mail-read` interfaces — never call an IMAP CLI directly.

If `email-triage.machine.fetch-filtered-envelopes` prints `(no new emails for …)`, skip that account in later steps. If stderr contains a `WARNING:` line, include it in the Step 6 report.

Each envelope is JSON: `id`, `flags` (IMAP flags — absence of `\Seen` means unread, `\Answered` means replied), `subject`, `from`, `date`, `message_id`.

**Skip immediately** (don't read body) when the subject alone makes it unambiguous: sales/discount offers, newsletter digests, GitHub notifications, delivery confirmations, social media digests, referral bonuses. For financial senders (banks, SoFi, Spotify, utilities): read the subject — skip if promotional, read the body if it could be a statement, payment due, or alert. **Log each skip with `SKIP` and one sentence why.**

**Never skip** if the subject suggests a message is waiting on a portal ("you have a message", "new message", "someone replied") — a human sent it; classify as Type 3 in Step 3.

---

## Step 3 — Read email bodies in batches

Use `email-client.llm.default`'s `mail-read` interface for each filtered email.
Batch up to 10 interface calls in parallel. Classify each email by sender type
and targeting:

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

**Log every email read at this step** — one `email-triage.machine.scripts-log-decision` call per email with its classification (`NO_ACTION`, `TODO`, `POTENTIAL`) and one sentence why. Log `NO_ACTION` even when nothing is added.

---

## Step 4 — Read both destination lists via `list-manager.llm.default`

Invoke `list-manager.llm.default` to read `todo` and `triage`.

---

## Step 5 — Add action items, deduplicating

Every item sent to `list-manager.llm.default` must be concrete enough for the list
skill to infer title, optional description, and optional deadline. Do not
manually format list storage lines here; pass the freeform action content and
destination list to `list-manager.llm.default`.

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

**Dedup:** before adding to `triage`, scan for a case-insensitive substring match on the key noun (sender name, event name, program name). If a match exists in any state (`[ ]`, `[+]`, or `[-]`), skip — the item has already been triaged. Log with `DEDUP` and note the matched item. Use `list-manager.llm.default` to add new items.

---

## Step 6 — Collect metrics and report

**Metrics tracking:** Count as you process emails:
- **total_scanned** = sum of all envelopes from all accounts (SKIP, NO_ACTION, TODO, POTENTIAL, DEDUP)
- **added_todo** = number of emails classified as TODO
- **added_triage** = number of emails classified as POTENTIAL
- **skipped** = number of emails classified as SKIP
- **deduped** = number of emails classified as DEDUP

Include these counts in your summary, then pass them to the metrics interface.

**Report summary:**
- N emails scanned across [account list]
- Items added to `todo` (list them) — count: X
- Items added to `triage` (list them) — count: Y
- Items skipped (already listed / no action / promotional) — count: Z

---

## Step 7 — Record metrics, update watermark, and prune log

If any `list-manager.llm.default` add/update in Step 5 failed (e.g. a validation error), invoke `email-triage.machine.scripts-mark-failure "<reason>"` and stop — do not call `email-triage.machine.scripts-update-watermark`. This keeps next run's lookback window covering the emails that didn't get filed, and surfaces the failure as a desktop notification via the scheduled health check.

After the failure's cause has been fixed, an operator may invoke
`email-triage.machine.scripts-clear-failure "<recovery reason>"` before starting
a fresh triage run. This clears only the latched error; it never advances the
watermark. Never clear a failure automatically in the same run that recorded
it.

Otherwise, after a successful run, invoke these interfaces in order:

1. Record the counts from Step 6 (total scanned, added to todo, added to triage, skipped, deduped)
2. Update watermark — advances the run timestamp so next scan only sees new emails
3. Prune log — drops entries older than 30 days and prints a one-line summary

These three steps in sequence ensure metrics are recorded, watermark is advanced, and old logs are cleaned up.

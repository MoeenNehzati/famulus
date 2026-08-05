# Email Triage

Scans emails received since the last triage run. Extracts action items and routes them to the right list. Never adds events to the calendar automatically — the user decides.

Use `email-client.interface.default` to read and send email. Use `list-manager.interface.default` to read and update destination lists.

**IMPORTANT: Never ask the user for a lookback period or watermark date. The date always comes from the `email-triage._rtx.interface.scripts-get-cutoff` interface. If that interface emits a warning or fails, report it to the user — but do not ask them to supply a date instead.**

**Decision logging:** After every classification, invoke the `email-triage._rtx.interface.scripts-log-decision` interface:

`email-triage._rtx.interface.scripts-log-decision <account> <id> "<from>" "<subject>" <DECISION> "<reason>"`

`DECISION` values: `SKIP` (subject-only skip) · `NO_ACTION` (body read, nothing to do) · `TODO` (added to todo) · `POTENTIAL` (added to triage) · `DEDUP` (already exists in destination)
`reason` = one sentence explaining the classification. Log: `triage.log`

**Two destination lists:**
- `todo` — directed, personal actions: bills to pay, replies owed, explicit follow-up commitments
- `triage` — anything the user may or may not act on: events, seminars, CFPs, summer schools, workshops, fellowship applications, optional signups

---

## Step 1 — Fetch new envelopes (run in parallel per account)

First, call `email-client.interface.default`'s `accounts-list` to get the configured account nicknames —
do not assume or hardcode which ones exist. Triage every account it returns.

Two interface calls per account:

1. `email-triage._rtx.interface.scripts-get-cutoff` — the coarse lookback date (day-level; IMAP can't filter finer than a day). Its own call — the date is short, fine to see.
2. `email-triage._rtx.interface.fetch-filtered-envelopes` with the account and that cutoff date. This composite interface fetches through the declared mail-list boundary and applies the exact watermark filter internally. Only its filtered result enters your context; never fetch unfiltered envelopes separately.

Run this per account returned by `accounts-list`, in parallel across accounts.

Reading always goes through `email-client.interface.default`'s `mail-list`/`mail-read` interfaces — never call an IMAP CLI directly.

If `email-triage._rtx.interface.fetch-filtered-envelopes` prints `(no new emails for …)`, skip that account in later steps. If stderr contains a `WARNING:` line, include it in the Step 6 report.

Each envelope is JSON: `id`, `flags` (IMAP flags — absence of `\Seen` means unread, `\Answered` means replied), `subject`, `from`, `date`, `message_id`.

**Skip immediately** (don't read body) when the subject alone makes it unambiguous: sales/discount offers, newsletter digests, GitHub notifications, delivery confirmations, social media digests, referral bonuses. For financial senders (banks, SoFi, Spotify, utilities): read the subject — skip if promotional, read the body if it could be a statement, payment due, or alert. **Log each skip with `SKIP` and one sentence why.**

**Never skip** if the subject suggests a message is waiting on a portal ("you have a message", "new message", "someone replied") — a human sent it; classify as Type 3 in Step 3.

**Manual historical rescan (operator-invoked, not part of a normal triage run):**
`email-triage._rtx.interface.fetch-filtered-envelopes` also accepts `--rescan-after
<ISO cutoff>` and `--dedup-against <todo|triage>`, for backfilling after a bug or
bootstrapping onto an account without editing the watermark file by hand.
`--rescan-after` replaces the stored watermark for that one call only (the real
watermark file is never read or written by a rescan). `--dedup-against` fetches the
named destination list internally and drops any candidate envelope whose
`message_id` already matches a `source.message_id` already present in that list —
this only works for entries created after this feature shipped and carrying a
`source` field (see Step 5); older entries have no `source` and cannot be deduped
this way. An operator runs this directly (outside the normal Step 1–7 flow); do not
invoke it automatically as part of a regular triage run.

---

## Step 3 — Read email bodies in batches

Use `email-client.interface.default`'s `mail-read` interface for each filtered email.
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

**Log every email read at this step** — one `email-triage._rtx.interface.scripts-log-decision` call per email with its classification (`NO_ACTION`, `TODO`, `POTENTIAL`) and one sentence why. Log `NO_ACTION` even when nothing is added.

---

## Step 4 — Read both destination lists via `list-manager.interface.default`

Invoke `list-manager.interface.default` to read `todo` and `triage`.

---

## Step 5 — Add action items, deduplicating

Every item sent to `list-manager.interface.default` must be concrete enough for the list
skill to infer title, optional description, and optional deadline. Do not
manually format list storage lines here; pass the freeform action content and
destination list to `list-manager.interface.default`.

**Concurrency:** `todo` and `triage` are each a single YAML file with no
built-in locking — a concurrent write (a second triage run overlapping this
one, or the user manually editing the list at the same time) can silently
clobber this run's additions if two writers race to read-modify-write the
same file. Guard against this:

- Issue additions to a given destination list (`todo` or `triage`) **one at a
  time, in sequence** — never fire multiple `list-manager.interface.default`
  add calls at the same destination list in parallel, even though Steps 1 and
  3 explicitly parallelize unrelated reads. Two lists (`todo` and `triage`)
  are independent files, so calls to different lists don't need to serialize
  against each other.
- The underlying list-manager write path supports an optional
  `--expected-revision <N>` guard: pass the `revision` value observed when
  the list was last read (Step 4) and the write is rejected — loudly, with no
  partial write — if another process has saved the file since. If the list
  read in Step 4 has no `revision` field at all (it predates this field, or
  has never had a mutating write since), pass `0` — that is the documented
  convention for "no revision yet", not a sign the guard doesn't apply. On
  rejection, re-read the list, re-check for a duplicate, and retry the single
  item; never assume the write succeeded and never skip re-reading.
- This is a per-write check, not a new batch-apply mechanism: the underlying
  create path already accepts multiple entries for one target in a single
  call, so multiple items destined for the *same* category can still be
  added together where that's natural.

Every entry created in `todo` or `triage` must carry a structured `source` in the
`entries` YAML passed to `list-manager.interface.default`, alongside its other fields:

```yaml
- title: Reply to Bob re: proposal
  deadline: 2026-08-05
  source:
    message_id: "<abc123@mail.example.com>"
    mailbox: work
```

`source.message_id` is the envelope's `message_id` field from Step 1/3 (required);
`mailbox` is the account nickname the email came from (optional but include it when
known). This is what lets a later historical rescan (see "Manual historical rescan"
near Step 1) deterministically skip messages already filed here instead of relying
on fuzzy title matching.

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

**Dedup:** before adding to `triage`, scan for a case-insensitive substring match on the key noun (sender name, event name, program name). If a match exists in any state (`[ ]`, `[+]`, or `[-]`), skip — the item has already been triaged. Log with `DEDUP` and note the matched item. Use `list-manager.interface.default` to add new items.

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

## Step 7 — Finalize the run (metrics + watermark), then prune log

**Run id:** before doing anything else in this step, mint one run id for this
triage run — any short unique token (e.g. a random hex string) is fine. Reuse
the *same* run id for every finalize call attempted in this run, including
retries. A fresh triage run (a new invocation of this skill) must mint a new
run id.

If any `list-manager.interface.default` add/update in Step 5 failed (e.g. a validation error), invoke `email-triage._rtx.interface.scripts-mark-failure "<reason>"` and stop — do not invoke the finalization interface below. This keeps next run's lookback window covering the emails that didn't get filed, and surfaces the failure as a desktop notification via the scheduled health check.

After the failure's cause has been fixed, an operator may invoke
`email-triage._rtx.interface.scripts-clear-failure "<recovery reason>"` before starting
a fresh triage run. This clears only the latched error; it never advances the
watermark. Never clear a failure automatically in the same run that recorded
it.

Otherwise, after a successful run, invoke:

1. `email-triage._rtx.interface.scripts-finalize-triage` with the run id from above
   and the counts from Step 6 (total scanned, added to todo, added to triage,
   skipped, deduped, accounts). This single call records the counters and then
   advances the watermark, in that order, as one step — it refuses to advance
   the watermark if recording the counters fails or if a failure is still
   latched from an earlier run, and it is safe to call again with the same run
   id if the caller is unsure whether the previous call actually landed (e.g.
   after a network error): a repeat with the same run id is a no-op, it will
   not double-advance the watermark or re-apply the counters. Only retry with
   a *new* run id if this is genuinely a new run.
2. Prune log — drops entries older than 30 days and prints a one-line summary

Two lower-level interfaces this one composes internally still exist and work
exactly as before for manual recovery, but normal triage runs should use
`scripts-finalize-triage` instead of calling them separately — calling them
apart no longer gives any ordering or replay-safety guarantee.

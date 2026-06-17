---
name: email-triage
description: Use when asked to triage email, process the inbox, or surface action items from recent emails. Scans the last 24 hours across both accounts and syncs with the todo list and potential-actions list.
---

# Email Triage

Category: automation

Scans emails received in the last 24 hours on both accounts. Extracts action items and routes them to the right list. Never adds events to the calendar automatically — the user decides.

**Sub-skills to invoke:** `email` (reading/sending), `lists` (list management). Never call their scripts directly — invoke the skill.

**Two destination lists:**
- `todo` — directed, personal actions: bills to pay, replies owed, explicit follow-up commitments
- `potential-actions` — anything the user may or may not act on: events, seminars, CFPs, summer schools, workshops, fellowship applications, optional signups

---

## Step 1 — Compute the date window

Use Python (not `date -d` — known DST bug). Default window is 24 hours; adjust N if user specifies a different range.

```python
from datetime import date, timedelta
N = 1  # days back; change to 7 for a week window, etc.
cutoff = date.today() - timedelta(days=N + 1)  # +1 because himalaya's `after` is strictly after
print(cutoff.isoformat())
```

---

## Step 2 — Fetch recent envelopes (run in parallel)

himalaya uses its own query language (not raw IMAP). `after <YYYY-MM-DD>` is **strictly after** that date, so use `cutoff` from Step 1.

```bash
himalaya envelope list -a nyu     after <cutoff>
himalaya envelope list -a personal after <cutoff>
```

Note FLAGS per row: `*` = unread · `R` = replied · blank = read, not replied.

**Skip immediately** (don't read body): marketing/promotional senders, mailing lists, GitHub notifications, automated system alerts with no human sender.

---

## Step 3 — Read email bodies in batches

```bash
himalaya message read -a <account> <ID>
```

Batch up to 10 reads in parallel. Classify each email:

| Category | Destination | Signal |
|----------|-------------|--------|
| **Bill / invoice** | `todo` | Amount due, due date, or payment link visible |
| **Reply needed** | `todo` | Real person, asks a question or expects a response, FLAGS has no `R` |
| **Follow-up commitment** | `todo` | Explicit promise made in a prior reply (e.g. "I'll send you X in July") |
| **Event / seminar** | `potential-actions` | Email about a specific event with date and time |
| **Opportunity / invite** | `potential-actions` | CFP, fellowship, workshop, optional signup, mass invite |
| **No action** | — | Everything else |

For **reply needed**: skip if the sender is the user themselves, if it's a mass CC, or if purely informational with no implied response needed.

---

## Step 4 — Read both destination lists via `lists` skill

Invoke the `lists` skill to read `todo` and `potential-actions`.

---

## Step 5 — Add action items, deduplicating

Every item must be a **concrete imperative sentence** — a specific thing to do, not a vague note.

**Format by category:**
- Bill: `Pay [Sender] – $[amount] due [date]` → `todo`
- Reply: `Reply to [Name] re: [subject]` → `todo`
- Follow-up: `[action verb] [target] – [timeframe]` → `todo`
- Event: `Attend [event name] – [date, time, location]` → `potential-actions`
- CFP / application: `Submit to [name] by [deadline]` or `Apply to [name] by [deadline]` → `potential-actions`
- Optional signup: `Sign up for [name] – [date or deadline]` → `potential-actions`

If deadline or date is unknown, omit rather than guess.

**Dedup:** before adding to either list, scan for a case-insensitive substring match on the key noun (sender name, event name, program name). If a match exists (checked or unchecked), skip. Use the `lists` skill to add new items.

---

## Step 6 — Report

Summarize concisely:
- N emails scanned across both accounts
- Items added to `todo` (list them)
- Items added to `potential-actions` (list them)
- Items skipped (already listed / no action / promotional)

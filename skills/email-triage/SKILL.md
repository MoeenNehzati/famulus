---
name: email-triage
description: Use when asked to triage email, process the inbox, or surface action items from recent emails. Scans the last 24 hours across both accounts and syncs with the todo list and calendar.
---

# Email Triage

Category: automation

Scans emails received in the last 24 hours on both accounts. Extracts action items (bills to pay, replies owed, events to add to calendar). Adds to the todo or potential-actions list only if no equivalent item already exists.

**Sub-skills to invoke:** `email` (reading/sending), `lists` (todo management), `g-calendar` (calendar). Never call their scripts directly — invoke the skill.

**Two destination lists:**
- `todo` — personal, directed actions: bills to pay, replies owed, follow-up commitments
- `potential-actions` — mass invites, optional opportunities: CFPs, summer schools, workshops, open applications, optional signups

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

Batch up to 10 reads in parallel. For each email, identify:

| Category | Destination | Signal |
|----------|-------------|--------|
| **Bill / invoice** | `todo` | Amount due, due date, or payment link visible |
| **Reply needed** | `todo` | Sent by a real person, asks a question or expects a response, FLAGS has no `R` |
| **Follow-up commitment** | `todo` | You made an explicit commitment in a prior reply (e.g. "I'll send you X in July") |
| **Event reminder** | calendar | Specific event with date/time you're already committed to; email is a reminder |
| **Opportunity / invite** | `potential-actions` | Mass invite, CFP, open application, optional workshop or summer school, signup form |
| **No action** | — | Everything else |

For **reply needed**: skip if the sender is the user themselves, if it's a mass CC, or if purely informational with no implied response needed.

---

## Step 4 — Handle event reminders via `g-calendar` skill

For each "event reminder" email: extract event title, date, time. Invoke the `g-calendar` skill to search for it and add it if not found. Adding it to the calendar IS the action — do not also add a todo.

---

## Step 5 — Read both destination lists via `lists` skill

Invoke the `lists` skill to read:
- `todo`
- `potential-actions`

---

## Step 6 — Add action items, deduplicating

**Format:**
- Bill: `Pay [Sender] – [amount/description]` → `todo`
- Reply: `Reply to [Name] re: [subject]` → `todo`
- Follow-up: `[imperative phrase] – [timeframe if known]` → `todo`
- Opportunity: short description of what it is and when → `potential-actions`

**Dedup:** before adding to either list, scan that list for a case-insensitive substring match on sender + topic. If a similar item already exists (checked or unchecked), skip. Use the `lists` skill to add new items.

---

## Step 7 — Report

Summarize concisely:
- N emails scanned across both accounts
- Items added to `todo`
- Items added to `potential-actions`
- Events added to calendar
- Items skipped (already listed / already on calendar / no action)

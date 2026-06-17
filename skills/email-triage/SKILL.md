---
name: email-triage
description: Use when asked to triage email, process the inbox, or surface action items from recent emails. Scans the last 24 hours across both accounts and syncs with the todo list and calendar.
---

# Email Triage

Category: automation

Scans emails received in the last 24 hours on both accounts. Extracts action items (bills to pay, replies owed, events to add to calendar). Adds to the todo list only if no equivalent item already exists.

**Invoke sub-skills as needed:** `email`, `lists`, `g-calendar`.

---

## Step 1 — Compute the date window

Use Python (not `date -d` — known DST bug):

```python
from datetime import date, timedelta
two_days_ago = date.today() - timedelta(days=2)
print(two_days_ago.isoformat())   # e.g. 2026-06-15 — used for himalaya's strictly-after filter
```

---

## Step 2 — Fetch recent envelopes (run in parallel)

himalaya uses its own query language (not raw IMAP). `after` is **strictly after**, so pass two days ago to include yesterday and today.

```bash
himalaya envelope list -a nyu     after <YYYY-MM-DD>
himalaya envelope list -a personal after <YYYY-MM-DD>
```

Note FLAGS per row: `*` = unread · `R` = replied · blank = read, not replied.

**Skip immediately** (don't read body): marketing/promotional senders, mailing lists, GitHub notifications, automated system alerts with no human sender.

---

## Step 3 — Read email bodies in batches

```bash
himalaya message read -a <account> <ID>
```

Batch up to 10 reads in parallel. For each email, identify:

| Category | Signal |
|----------|--------|
| **Bill / invoice** | Amount due, due date, or payment link visible |
| **Reply needed** | Sent by a real person, asks a question or expects a response, FLAGS has no `R` |
| **Event reminder** | Contains a specific event with date/time; email is a reminder or confirmation, not just a newsletter |
| **No action** | Everything else — skip |

For **reply needed**: skip if the sender is the user themselves, if it's a mass CC, or if the email is purely informational with no implied response needed.

---

## Step 4 — Check calendar for event emails

For each "event reminder" email: extract event title, date, time.

Search the calendar:
```
gcal.sh search "<event title>" --all-calendars --from <ISO-yesterday> --to <ISO+90d>
```

- **Found**: skip — already on calendar.
- **Not found**: add the event using the `g-calendar` skill, then note it in the final report (do NOT also add a todo for it — adding it to the calendar IS the action).

---

## Step 5 — Read the current todo list

```bash
/home/moeen/.claude/skills/lists/scripts/lists.sh read todo
```

(If `todo` doesn't appear in the list of lists, use whatever the user's primary task list is called.)

---

## Step 6 — Add action items, deduplicating

For each bill or reply-needed item:

**Format the todo text:**
- Bill: `Pay [Sender] – [amount/description]`
- Reply: `Reply to [Name] re: [subject]`

**Dedup check:** scan the existing todo list for a case-insensitive substring match on sender + topic or bill name. If a similar item already exists (checked or unchecked), skip.

**Add new items** using the `lists` skill (section 3.4). Today's date is the creation date.

---

## Step 7 — Report

Summarize concisely:
- N emails scanned across both accounts
- Items added to todo (list them)
- Events added to calendar (list them)
- Items skipped (already in todo / already on calendar / no action)

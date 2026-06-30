---
name: daily-plan
description: |
  Use when the user asks to plan their day, see what to work on today, check
  their schedule, or review today's actions. Triggers on "plan my day",
  "what should I do today", "what should I work on", "show my plan", or similar.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: automation

Dependencies:
- g-calendar
- get-weather
- list-manager

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, begin with:

Skill: daily-plan

`daily-plan` is a consumer skill. Never run scripts owned by dependency skills
directly — all reads and writes must go through the dependency skill's interface.

## 0. Overview

This skill produces a daily plan file saved to Google Drive under `plans/`,
then outputs it to the user. The plan has six sections:

- **Calendar** — today's timed events, optional location line, and free time
- **The Day** — 2-sentence weather summary and outfit/commute note
- **Due Soon** — obligations from the Due calendar entering the 5-day lookahead
  window; auto-injects missing items into todo and flags deadline mismatches
- **Upcoming** — all-day events (birthdays, holidays, academic calendar) in the
  next 7 days
- **Actions** — a ranked shortlist of todo items, initially marked as
  *suggestions*; once the user decides, it becomes *decisions* (with unchosen
  items removed)
- **Triage** — unreviewed potential-actions, written into the file so the user
  can decide at their own pace (no interactive back-and-forth required)

### Calendar taxonomy

Classify every calendar event by these rules, in order:

1. **Location** — any event (timed or all-day) from the **"Location"** calendar.
   Used only for commute inference and a single display line. Never shown as
   a section.
2. **Activities** — timed events (have a specific start time) from any other
   calendar. Shown in the Calendar section; drive free-time calculation.
3. **Due** — all-day events from the **"Due"** calendar. Processed in Step 4a;
   shown in the Due Soon section.
4. **Upcoming** — all-day events from any other calendar (Birthdays, Holidays,
   NYU Events, Talks when all-day, etc.). Shown in the Upcoming section.

**Reading `g-calendar` output:** `gcal.sh agenda --all-calendars` prints one
line per event in this format:
```
<start> -> <end>  <title>  [calendar: <name>, id: <id>]
```
- Calendar name for taxonomy classification is the `<name>` in `[calendar: <name>]`.
- All-day events have date-only timestamps (`2026-07-01`); timed events have
  datetime timestamps (`2026-07-01T14:00:00-04:00`). Use this to distinguish
  Activities from Due/Upcoming.

The skill is invoked in one of two modes:

- **Produce** (`"plan my day"`, `"make a plan"`, `"what should I do today"`):
  generate and persist the plan, then show it.
- **Output** (`"show my plan"`, `"what's my plan"`, `"output the plan"`):
  show the existing plan; if none exists, produce it first.

**In both modes, start by checking whether today's plan file already exists.**

## 1. Determine today's plan filename

Use the machine's local date as the source of truth for "today". Session or
prompt metadata dates are advisory only; if they disagree with `date`, follow
`date` and mention the mismatch in the response.

Run:

```bash
date +%-m-%-d-%y
```

This gives the filename key, e.g. `6-16-26` for June 16, 2026. The full plan
file lives at `GDrive:assistant/plans/<key>.md`, managed by the `plans.sh`
script.

## 2. Check whether today's plan already exists

```bash
scripts/plans.sh exists <key>
```

- Exit 0 → plan exists.
- Exit non-zero → plan does not exist.

**If the plan exists:**
- **Produce mode**: skip all production steps. Tell the user a plan for today
  already exists and show it (run step 8 below).
- **Output mode**: show the plan (run step 8). Done.

**If the plan does not exist:** proceed to step 3.

## 3. Gather inputs (all in parallel)

Invoke these skills simultaneously through their skill interfaces:

- `list-manager`: read `todo`
- `list-manager`: read `potential-actions`
- `g-calendar`: get today's agenda for all calendars
- `g-calendar`: get the next 7 days for all calendars
- `get-weather`: get today's weather

- The first calendar result gives today's timed events → used for
  free-time computation and the **Calendar** section.
- The second calendar result gives a 7-day window → used for Due Soon
  (filtered to 5 days, Due calendar only) and the **Upcoming** section
  (filtered to 7 days, all other non-Location all-day events).
- To target a different date (e.g. planning for tomorrow), ask `g-calendar` for
  that explicit date range in both calendar requests.
- Only request an explicit date range when the user asks for a date other than
  today; otherwise use default current-day behavior so calendar and weather stay
  aligned with the local system date.
- If `todo` is empty or doesn't exist, set the Actions section to a single
  line: `(nothing on the todo list)` and skip step 7.

## 4. Compute today's free time

Start from a fixed **10-hour work budget**.

**Commute inference — use the Location calendar first:**
From today's agenda, find any event from the **"Location"** calendar (all-day
or timed). Use its title to estimate a round-trip commute from Ridgewood,
Queens, NYC:
- "WFH", "Home", or similar → 0 commute
- Nearby Queens/Brooklyn (Bushwick, Williamsburg, Astoria, LIC) → 30-45 min RT
- "Office", "NYU", Manhattan → 45-75 min RT depending on area
- Farther out (Bronx, SI, NJ, far edges, multiple transfers) → 75-120 min RT
- If no Location calendar event exists today → fall back to per-event logic below

**Per-event logic (only when no Location calendar event):**
For each **Activity** (timed, non-Location) event today:
- Subtract its duration from the budget.
- If the event has an explicit **location** field → use the commute table above.
- If the event has **no location field at all** → add a fixed **90-minute** buffer.

**When a Location calendar event exists:**
- Subtract the commute once (not per timed event — you're already there).
- Subtract each Activity event's duration normally (no per-event commute buffer).

```
free_hours = max(0, 10 - sum(activity_durations) - commute)
```

Also compute free time in three buckets (for time-of-day filtering in step 7):

- **Morning**: 00:00-12:00
- **Afternoon**: 12:00-17:00
- **Evening**: 17:00+

Due and Upcoming all-day events never consume budget.

## 4a. Process Due events

**Missing calendars:** If no **"Due"** calendar appears in the 7-day agenda,
skip the rest of this step and add this line to the plan under a `## Due Soon`
heading:
```
Note: "Due" calendar not found — create one named "Due" for recurring obligations
and one named "Location" for daily location metadata. See §0 calendar taxonomy.
```
If no **"Location"** calendar appears in today's agenda, commute inference
falls back to per-event logic silently (no note needed).

From the 7-day agenda (already fetched in step 3), filter for: all-day events
from the **"Due"** calendar whose date falls within the next **5 days**
(today through today+4 inclusive).

For each Due event, look up the todo list for a matching item. **Match
criteria**: the Due event title (case-insensitive) is a substring of the todo
item title, AND the todo item deadline date equals the Due event date.

Apply the following logic per Due event:

| Match result | Action |
|---|---|
| No match found | Auto-add to todo via `list-manager` (use Due event title verbatim, deadline = Due event date, infer area×action placement). Record as "→ Added to todo" in Due Soon. |
| Match found, same deadline, **unchecked** | Suppress — already tracked. No mention in Due Soon. |
| Match found, same deadline, **checked** | Suppress — done this cycle. No mention in Due Soon. |
| Match found, **different deadline** | Record as "⚠ Mismatch" in Due Soon. |
| Match found, **no deadline set** | Record as "⚠ Mismatch" in Due Soon. |

If the Due Soon section ends up with nothing to show (all suppressed), omit
the section entirely from the plan.

**Auto-inject writes:** use `list-manager` to add the item to `todo` before
writing the plan. Use today's date as the creation date.

## 5. Compose "The Day" (weather)

From the weather script output, write **exactly 2 sentences**:

1. Overall conditions: temperature range, dominant weather, and any notable
   change (rain window, wind, etc.) during the day.
2. Practical note: what to wear and any commute/outdoor tip based on the
   conditions.

Example: *"Partly sunny and warm, 22-28°C with a chance of afternoon showers
around 3pm. A t-shirt and light jacket will do — bring an umbrella if you're
heading out after noon."*

## 6. Build the Upcoming section

From the 7-day agenda, filter for: **all-day events** that are:
- NOT from the "Location" calendar
- NOT from the "Due" calendar
- Fall **after today** (tomorrow through 7 days out)

This includes birthdays (Birthdays calendar), holidays (Holidays in Iran,
Holidays in United States), academic calendar events (NYU Events), etc.

Format each as:
```
- <Weekday, Month D>: <event title>
```

For events from the **Birthdays** calendar, append the days-until count:
```
- <Weekday, Month D>: <name>'s birthday (in N days)
```

If there are no qualifying events, write:
```
(none this week)
```

## 7. Estimate and rank todo items

For every unchecked item returned by the `list-manager` skill from `todo` (top-level
and nested), use the list item's parsed title, description, deadline, and
creation date:

- **Urgency**: the parsed deadline is authoritative and rises sharply as it
  approaches/passes; "this week" → moderate; "this summer"/no deadline → low
  background.
  "by end of today" / "today" → treat as highest urgency (same as overdue).
- **Time estimate**: rough duration in minutes/hours.
- **Time-of-day requirement** (optional): only set if the task text implies it.

Sort by urgency (most urgent first), break ties by fit. Greedily select items
whose time estimates sum to at most `free_hours`:

- If an item has a time-of-day requirement, only select it if that bucket has
  enough room; otherwise skip.
- Aim for 2-5 items. Don't suggest dozens; don't force 0 if any item fits.

## 8. Format and write the plan

Build the Markdown plan document in this order:

```markdown
# Plan: <Month D, YYYY>

## Calendar
Today: <location title>              ← only if Location calendar has an event today; omit line otherwise
- HH:MM: <event title> [@ <location>] (<duration>)
- ...
(no events today)                    ← only if no Activity events

Free time: ~<free_hours>h (10h budget - <busy>h activities - <commute>h commute)

## The Day
<Sentence 1: conditions + temperature range + notable changes.>
<Sentence 2: what to wear + commute/outdoor tip.>

## Due Soon
← omit this section entirely if all Due events are suppressed →
→ Added to todo: <title> (due <Weekday, Mon D>)   ← one line per auto-injected item
⚠ <title> — todo deadline: <X>, calendar: <Y>      ← one line per mismatch

## Upcoming
- <Weekday, Month D>: <event title>
- ...
(none this week)                     ← only if no qualifying events

## Actions (suggestions)
1. [ ] <item title> — <one-line reason: urgency + time estimate + fit>
2. [ ] <item title> — <one-line reason>
...
→ Tell me which items you're keeping and I'll finalize the plan.

## Triage
← omit this section entirely if no unreviewed potential-actions exist →

Review each potential action (accept / accept+today / reject / skip):
1. <item title>
2. <item title>
...
→ Reply with your decisions and I'll update todo and the plan.
```

Rules:
- `## Actions (suggestions)` marks this as the initial suggestion state.
- One-line reasons should be concrete: e.g. `"due today, ~30 min, do first"`.
- If `free_hours ≈ 0`: `"Today looks fully booked — no free time for todo items."`
- The `## Triage` section lists only unreviewed (`[ ]`) items from
  `potential-actions` — exclude `[+]` (accepted) and `[-]` (rejected).
- If no unreviewed items exist, omit `## Triage` entirely.
- If the Due Soon section is omitted, still run step 4a — the auto-inject
  writes happen regardless; the section is just not shown when everything is clean.

Then write the file:

```bash
scripts/plans.sh write <key>
```

Then read and display it:

```bash
scripts/plans.sh read <key>
```

No further prompting needed — the file itself tells the user what to respond to.

## 9. Potential actions triage

If the `potential-actions` list is empty or does not exist, skip this step.

**Only show items in `[ ]` state** — exclude `[+]` (already accepted) and `[-]` (already rejected). If no unreviewed items remain, skip this step.

Display each unreviewed item and ask what to do with it:

```
**Potential actions — what should I do with each?**

1. <item text>
2. <item text>
...

Options per item: accept (→ todo) | accept + today | reject | skip
```

- **accept**: mark `[+]` in `potential-actions`, add to `todo`
- **accept + today**: mark `[+]` in `potential-actions`, add to `todo`, add to today's plan actions
- **reject**: mark `[-]` in `potential-actions` (stays for reference, not shown again)
- **skip**: leave as `[ ]` (will appear next time)

Wait for the user's response, then in a single pass through the `list-manager` skill:

1. **Update `potential-actions` in-place**: ask `list-manager` to mark accepted
   items as accepted, rejected items as rejected, and leave skipped items
   unreviewed.

2. **Update `todo`**: ask the `list-manager` skill to accept each item into `todo`,
   preserving the item's parsed title, description, and deadline, with today's
   date as the todo creation date.

3. **If any items were accept+today**: read the current plan, append each as a new numbered item in the `## Actions` (or `## Actions (suggestions)`) section, then write the plan back:
   ```bash
   scripts/plans.sh read <key>
   # ... edit the Actions section ...
   scripts/plans.sh write <key>
   ```

4. Confirm all changes to the user (accepted/rejected/skipped counts, what was added to todo).

## 10. Deadline nudge

After the plan is shown and triage is complete, scan all **unchecked** `todo`
items from the `list-manager` skill for items whose parsed deadline is empty.

If any such items exist, show them and ask for deadlines:

```
**These todo items have no deadline — specify one for each, or skip:**

1. <item text>
2. <item text>
...
```

Wait for the user's response. For each item the user assigns a deadline to,
ask the `list-manager` skill to find the matching todo item and set that item's
deadline. The `list-manager` skill owns matching and representation details.

Confirm which items were updated. Items the user skips remain unchanged.

## 11. Handling user decisions on actions

When the user responds with which actions they're keeping:

1. Read the current plan:
   ```bash
   scripts/plans.sh read <key>
   ```
2. Edit the Actions section:
   - Remove items the user is not keeping.
   - Renumber remaining items starting from 1.
   - Change `## Actions (suggestions)` → `## Actions`.
3. Write the updated plan back:
   ```bash
   scripts/plans.sh write <key>
   ```
4. Show the user the updated plan.

## 12. Handling action checkmarks

When the user marks one or more actions as done (e.g., "mark 1 as done",
"check off 2 and 3", "done with 4"):

1. Read the current plan:
   ```bash
   scripts/plans.sh read <key>
   ```
2. In the plan, change `[ ]` → `[x]` for each marked action.
3. Write the updated plan back:
   ```bash
   scripts/plans.sh write <key>
   ```
4. For each marked action, find the matching item in the todo list and check
   it off by asking the `list-manager` skill to match the plan action text to an
   unchecked todo item and mark it checked. The action text in the plan
   (before the `—` separator) may be shortened or lightly reworded, so if
   `list-manager` cannot find a confident match, report that rather than guessing.
5. Confirm to the user which todo item(s) were checked off. If no confident
   match is found for an action, say so rather than guessing wrong.

## Out of scope

- This skill never modifies the calendar.
- No automatic/scheduled runs — manual invocation only.
- The only todo writes this skill performs are auto-inject in step 4a. It never
  removes or edits existing todo items except via the standard triage and
  checkmark flows.

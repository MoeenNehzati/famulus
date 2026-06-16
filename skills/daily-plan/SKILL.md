---
name: daily-plan
description: |
  Suggest what to work on today by combining the todo list with today's
  calendar. Shows today's calendar events, computes how much free time is
  available today (10-hour work budget minus calendar events and estimated
  commute), estimates urgency and time cost for each todo item, and proposes
  a ranked shortlist that fits the available time. Use when the user asks
  "what should I do today", "what should I work on", "plan my day", or similar.
---

When this skill is used, begin with:

Skill: daily-plan

Category: automation

## 0. Overview

This skill produces a daily plan file saved to Google Drive under `plans/`,
then outputs it to the user. The plan has four sections:

- **Calendar** — today's timed events and free time
- **The Day** — 2-sentence weather summary and outfit/commute note
- **Upcoming Events** — all-day events (birthdays, trips, etc.) in the next 7 days
- **Actions** — a ranked shortlist of todo items, initially marked as
  *suggestions*; once the user decides, it becomes *decisions* (with unchosen
  items removed)

The skill is invoked in one of two modes:

- **Produce** (`"plan my day"`, `"make a plan"`, `"what should I do today"`):
  generate and persist the plan, then show it.
- **Output** (`"show my plan"`, `"what's my plan"`, `"output the plan"`):
  show the existing plan; if none exists, produce it first.

**In both modes, start by checking whether today's plan file already exists.**

## 1. Determine today's plan filename

Run:

```bash
date +%-m-%-d-%y
```

This gives the filename key, e.g. `6-16-26` for June 16, 2026. The full plan
file lives at `GDrive:plans/<key>.md`, managed by the `plans.sh` script.

## 2. Check whether today's plan already exists

```bash
/home/moeen/.claude/skills/daily-plan/scripts/plans.sh exists <key>
```

- Exit 0 → plan exists.
- Exit non-zero → plan does not exist.

**If the plan exists:**
- **Produce mode**: skip all production steps. Tell the user a plan for today
  already exists and show it (run step 8 below).
- **Output mode**: show the plan (run step 8). Done.

**If the plan does not exist:** proceed to step 3.

## 3. Gather inputs (all in parallel)

Run all four of these simultaneously:

```bash
/home/moeen/.claude/skills/lists/scripts/lists.sh read todo
/home/moeen/.claude/skills/g-calendar/scripts/gcal.sh agenda --all-calendars
/home/moeen/.claude/skills/g-calendar/scripts/gcal.sh agenda --all-calendars --days 7
/home/moeen/.claude/skills/weather/scripts/weather.sh
```

- The first `agenda` call (no `--days`) gives today's timed events → used for
  free-time computation and the **Calendar** section.
- The second `agenda --days 7` call gives a 7-day window → used for the
  **Upcoming Events** section (filter to all-day events only, starting
  tomorrow).
- If `todo` is empty or doesn't exist, set the Actions section to a single
  line: `(nothing on the todo list)` and skip steps 5-6.

## 4. Compute today's free time

Start from a fixed **10-hour work budget**.

For each **timed** event (not all-day) that falls today:

- Subtract its duration from the budget.
- If the event has a **location** field, estimate a round-trip commute from
  Ridgewood, Queens, NYC:
  - Nearby Queens/Brooklyn (Bushwick, Williamsburg, Astoria, LIC) → 30-45 min RT
  - Manhattan → 45-75 min RT depending on area
  - Farther out (Bronx, SI, NJ, far edges, multiple transfers) → 75-120 min RT
  - Clearly remote/virtual (Zoom, Google Meet, Home, blank) → 0 commute
- If the event has **no location field at all**, add a fixed **90-minute**
  buffer.

```
free_hours = max(0, 10 - sum(event_durations + commute_or_buffer))
```

Also compute free time in three buckets (for time-of-day filtering in step 6):

- **Morning**: 00:00-12:00
- **Afternoon**: 12:00-17:00
- **Evening**: 17:00+

All-day events do not consume budget. If an all-day event implies a commute
(e.g. "Office"), subtract only the commute estimate (not a full-day duration).

## 5. Compose "The Day" (weather)

From the weather script output, write **exactly 2 sentences**:

1. Overall conditions: temperature range, dominant weather, and any notable
   change (rain window, wind, etc.) during the day.
2. Practical note: what to wear and any commute/outdoor tip based on the
   conditions.

Example: *"Partly sunny and warm, 22-28°C with a chance of afternoon showers
around 3pm. A t-shirt and light jacket will do — bring an umbrella if you're
heading out after noon."*

## 6. Extract upcoming all-day events

From the `--days 7` agenda output, filter for **all-day events only** (these
appear without a specific time, spanning full calendar dates) that fall
**after today** (i.e. tomorrow through 7 days out). Exclude today's all-day
events (already in the Calendar section).

Format each as:
```
- <Weekday, Month D>: <event title>
```

If there are no all-day events in the next 7 days, write:
```
(none this week)
```

## 7. Estimate and rank todo items

For every unchecked item on the todo list (top-level and nested), infer from
the item text and creation date `(MM/DD/YY)`:

- **Urgency**: explicit deadline → rises sharply as it approaches/passes;
  "this week" → moderate; "this summer"/no deadline → low background.
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
- HH:MM: <event title> [@ <location>] (<duration>)
- All day: <event title>
- ...
(no events today)   ← only if calendar is empty

Free time: ~<free_hours>h (10h budget - <busy>h meetings - <commute>h commute)

## The Day
<Sentence 1: conditions + temperature range + notable changes.>
<Sentence 2: what to wear + commute/outdoor tip.>

## Upcoming Events
- <Weekday, Month D>: <event title>
- ...
(none this week)   ← only if no all-day events in next 7 days

## Actions (suggestions)
1. [ ] <item text> — <one-line reason: urgency + time estimate + fit>
2. [ ] <item text> — <one-line reason>
...
```

Rules:
- `## Actions (suggestions)` marks this as the initial suggestion state.
- One-line reasons should be concrete: e.g. `"due today, ~30 min, do first"`.
- If `free_hours ≈ 0`: `"Today looks fully booked — no free time for todo items."`

Then write the file:

```bash
/home/moeen/.claude/skills/daily-plan/scripts/plans.sh write <key>
```

Then read and display it:

```bash
/home/moeen/.claude/skills/daily-plan/scripts/plans.sh read <key>
```

Add a brief note that the Actions section is a suggestion — the user can tell
you which items they're keeping and you'll update the file.

## 9. Handling user decisions on actions

When the user responds with which actions they're keeping:

1. Read the current plan:
   ```bash
   /home/moeen/.claude/skills/daily-plan/scripts/plans.sh read <key>
   ```
2. Edit the Actions section:
   - Remove items the user is not keeping.
   - Renumber remaining items starting from 1.
   - Change `## Actions (suggestions)` → `## Actions`.
3. Write the updated plan back:
   ```bash
   /home/moeen/.claude/skills/daily-plan/scripts/plans.sh write <key>
   ```
4. Show the user the updated plan.

## 10. Handling action checkmarks

When the user marks one or more actions as done (e.g., "mark 1 as done",
"check off 2 and 3", "done with 4"):

1. Read the current plan:
   ```bash
   /home/moeen/.claude/skills/daily-plan/scripts/plans.sh read <key>
   ```
2. In the plan, change `[ ]` → `[x]` for each marked action.
3. Write the updated plan back:
   ```bash
   /home/moeen/.claude/skills/daily-plan/scripts/plans.sh write <key>
   ```
4. For each marked action, find the matching item in the todo list and check
   it off:
   - Read the todo list:
     ```bash
     /home/moeen/.claude/skills/lists/scripts/lists.sh read todo
     ```
   - The action text in the plan (before the `—` separator) is often a
     shortened or lightly reworded version of the original todo item. Use
     judgment to identify the best match among unchecked items (`- [ ]`).
   - Change `- [ ]` → `- [x]` for the matched item.
   - Write the updated list back:
     ```bash
     /home/moeen/.claude/skills/lists/scripts/lists.sh write todo
     ```
5. Confirm to the user which todo item(s) were checked off. If no confident
   match is found for an action, say so rather than guessing wrong.

## Out of scope

- This skill never modifies the calendar.
- No automatic/scheduled runs — manual invocation only.

---
name: wrap-up
description: |
  Use when ending the work day or wrapping up a session. Reads today's plan,
  asks which incomplete actions were completed, prompts for calendar activity
  notes, and captures any new items for lists.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: planning, personal-organization, session-management; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `wrap-up.source.gateway -> daily-plan.interface.default@1`
- `wrap-up.source.gateway -> find-handoff-candidates.interface.default@1`
- `wrap-up.source.gateway -> list-manager.interface.default@1`
- `wrap-up.source.gateway -> prepare-handoff.interface.default@1`

Public Interfaces:
- `wrap-up.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `wrap-up.interface.default` — Collect one consolidated user response, orchestrate declared planning and list interfaces, relay mechanical handoff candidates without transcript inspection, and summarize results.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: wrap-up

## 0. Overview

End-of-day review. Reads today's plan, surfaces incomplete actions, collects
completions and notes from the user in a single prompt, then updates the plan
and lists accordingly.

## 1. Read today's plan

Invoke `daily-plan.interface.default` in output mode ("show my plan") to read
today's plan. If no plan exists, note that and skip to step 3.

## 2. Extract incomplete actions

From the `## Actions` section, collect all lines matching `- [ ] ...`.
Present them numbered (without the surrounding plan) so the user can
reference them by number.

## 3. Ask all questions in one message

Send a single message asking all of the following:

1. **Completions**: Which of the incomplete actions (listed by number) were
   actually completed today? ("all", "none", numbers/descriptions, or partial —
   e.g. "action 2: finished the tests but not the docs".)
2. **Unplanned work**: Did you do anything else today that wasn't on the plan?
   (These will be added to the plan as completed items.)
3. **Calendar notes**: Any notes, outcomes, or follow-ups from calendar events
   today worth capturing?
4. **New items**: Any new tasks or items to add to a list (todo, groceries,
   etc.)?
5. **Reminder**: Is there any code or work in a done state that hasn't been
   committed/pushed yet? If so, do that before wrapping up.

Wait for the user's full response before doing anything else.

## 4. Add unplanned completed work to the plan

For each item the user did that wasn't on the plan, invoke
`daily-plan.interface.default` to append an `## Unplanned Actions` section at
the end of the plan (if it doesn't already exist), then add each item as a
numbered completed entry:

```markdown
## Unplanned Actions
1. [x] <description>
2. [x] <description>
```

If the section already exists, append to it (continuing the numbering).

## 5. Mark planned completions

For each planned action the user says was completed:

1. Invoke `daily-plan.interface.default` to change `[ ]` → `[x]` on that
   action's line.
2. Invoke `list-manager.interface.default` to check off the matching todo item
   — fuzzy-match the action text (before `—`) against unchecked `- [ ]` items
   on the todo list. If no confident match is found, say so rather than
   guessing wrong.

### Partial completions

If the user says part X of an action was done and part Y remains, instead of
marking the action done or leaving it untouched:

1. Invoke `daily-plan.interface.default` to replace the action's line with the
   original parent line (kept as `- [ ]`, since it isn't fully done) followed
   by two indented sub-items:
   ```markdown
   - [ ] <original action text>
     - [x] <completed part X>
     - [ ] <remaining part Y>
   ```
2. Invoke `list-manager.interface.default` to apply the same split to the
   matching todo item: identify the matching todo item and ask the interface to
   split it into the parent plus completed and remaining sub-items.
   `wrap-up` must not reconstruct item metadata or representation details;
   leave that behavior behind `list-manager.interface.default`.

## 6. Add new list items

For each new item the user provided, invoke `list-manager.interface.default` to
add it to the appropriate list. Infer the list from context; default to `todo`.

## 7. Flag sessions needing handoff

Invoke `find-handoff-candidates.interface.default` with a scan request (default: trailing 2 days, so a session touched yesterday still surfaces even if this didn't run yesterday) to get a JSON array of session records. Every record returned already needs attention — the scan decides this via the gap-since-last-handoff threshold, including sessions with `handoff_status: complete` that had substantial new work afterward. Do not re-filter by `handoff_status`, and do not open, read, or summarize any flagged session's transcript content; this step is a pure relay of the interface's structured output, not an LLM judgment call.

Before adding anything, invoke `list-manager.interface.default` to read the current `triage` list and collect every `session_id` already present in an existing entry's description (any state — undecided, accepted, or rejected). Because the scan window overlaps across days, the same session can appear in more than one day's scan; skip any record whose `session_id` is already in that set — do not create a second triage entry for a session already tracked there.

For each remaining record, invoke `list-manager.interface.default` to add a `triage` entry:
- `title`: a short pointer, e.g. `"handoff check: <source> session <session_id> (<project>)"`.
- `deadline`: tomorrow's local date.
- `description`: every field from the record, plainly listed (session_id, source, project, start_time, last_activity, line_count, gap_net_chars, handoff_status, handoff_started_at, resume_hint) — do not summarize or drop fields; the description is the only place this information persists, and it must be enough for whoever reviews the triage item to resume the session and invoke `prepare-handoff.interface.default` there without re-scanning. Always include `session_id` even though it's also in the title, since the dedup check above depends on finding it in the description.

If nothing remains after dedup, skip this step silently — do not create empty or placeholder triage entries.

## 8. Confirm

Reply with a brief summary:
- Which actions were checked off (and which todo items matched).
- Any items that couldn't be matched (if any).
- Which new items were added and to which list.
- How many triage entries were added in step 7 (if none, omit this line).

Do not redisplay the full plan unless asked.

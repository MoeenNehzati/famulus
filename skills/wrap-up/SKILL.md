---
name: wrap-up
description: |
  Use when ending the work day or wrapping up a session. Reads today's plan,
  asks which incomplete actions were completed, prompts for calendar activity
  notes, and captures any new items for lists.
---

When this skill is used, begin with:

Skill: wrap-up

Category: automation

Dependencies:
- daily-plan
- list-manager

## 0. Overview

End-of-day review. Reads today's plan, surfaces incomplete actions, collects
completions and notes from the user in a single prompt, then updates the plan
and lists accordingly.

## 1. Read today's plan

Use the `daily-plan` skill in output mode ("show my plan") to read today's
plan. If no plan exists, note that and skip to step 3.

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

For each item the user did that wasn't on the plan, use the `daily-plan`
skill to append an `## Unplanned Actions` section at the end of the plan
(if it doesn't already exist), then add each item as a numbered completed
entry:

```markdown
## Unplanned Actions
1. [x] <description>
2. [x] <description>
```

If the section already exists, append to it (continuing the numbering).

## 5. Mark planned completions

For each planned action the user says was completed:

1. Use `daily-plan` to change `[ ]` → `[x]` on that action's line.
2. Use the `list-manager` skill to check off the matching todo item — fuzzy-match
   the action text (before `—`) against unchecked `- [ ]` items on the todo
   list. If no confident match is found, say so rather than guessing wrong.

### Partial completions

If the user says part X of an action was done and part Y remains, instead of
marking the action done or leaving it untouched:

1. Use `daily-plan` to replace the action's line with the original parent line
   (kept as `- [ ]`, since it isn't fully done) followed by two indented
   sub-items:
   ```markdown
   - [ ] <original action text>
     - [x] <completed part X>
     - [ ] <remaining part Y>
   ```
2. Use the `list-manager` skill to apply the same split to the matching todo item:
   identify the matching todo item and ask `list-manager` to split it into the parent
   plus completed and remaining sub-items. The `list-manager` skill owns preservation
   of item metadata and representation details.

## 6. Add new list items

For each new item the user provided, use the `list-manager` skill (§3.4) to add it
to the appropriate list. Infer the list from context; default to `todo`.

## 7. Confirm

Reply with a brief summary:
- Which actions were checked off (and which todo items matched).
- Any items that couldn't be matched (if any).
- Which new items were added and to which list.

Do not redisplay the full plan unless asked.

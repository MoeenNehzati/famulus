# List Structure (Legacy Markdown Format)

> **This document describes the old Markdown format used before migration to YAML.**
> It is kept as a reference for the legacy-import path. The current list format
> is YAML — see `SKILL.md` sections 3–4 for the live format.

A structured list file uses plain title lines as persistent headers, with
task items nested beneath them.

**Fixed:** file header format, title line syntax, task item format,
continuation-line format, indentation convention (2 spaces per level).
**Variable** (shown as `<placeholder>`): list name, state declarations,
title text, item descriptions, deadlines, dates.

---

## File header line (optional)

A list file may begin with a header line declaring its name and accepted
states. This line has no checkbox and is not a task item:

```
[<list-name>] [<state>] <meaning> · [<state>] <meaning> · ...
```

The skill reads this line to determine what checkbox states are valid for
that list. If absent, only `[ ]` and `[x]` are assumed valid.

---

## Persistent title lines

Plain `- <Title>` lines (no checkbox, no date) are permanent structural
headers. They are never checked, unchecked, or removed by the skill.

- Level 1 (area):   `- <Title>`
- Level 2 (action): `  - <Title>`

Task items begin at level 3:

- Level 3+ (tasks): `    - [<state>] (MM/DD/YY) <task title>`

All nested task levels (3, 4, 5, …) use the same `- [<state>] (MM/DD/YY) <title>`
format — there is no distinct format for sub-tasks or sub-sub-tasks.

## Item continuation lines

Each task item may have optional continuation lines immediately beneath it.
Continuation lines are indented two spaces more than the task line they
describe and do not begin with a checkbox.

- Description lines: freeform text; omit entirely if there is no description.
- Deadline line: `deadline: <deadline phrase or date>`; omit if there is no
  deadline.

For a level-3 task, continuation lines use 6 spaces. For a nested level-4 task,
continuation lines use 8 spaces. More generally, if a task line starts with
`N` spaces, its continuation lines start with `N+2` spaces.

If both description and deadline are present, put description lines first and
the `deadline:` line last.

For fuzzy matching (check/uncheck/remove), plain title lines are skipped —
they have no checkbox text to match against.

---

## Example

```
[<list-name>] [<state>] <meaning> · [<state>] <meaning>
                                    ← blank line here is optional
- <Area>
  - <Action>
    - [<state>] (MM/DD/YY) <task title>
      <freeform description, optional>
      deadline: <deadline phrase or date, optional>
      - [<state>] (MM/DD/YY) <sub-task title>
        <freeform description for the sub-task, optional>
        deadline: <deadline phrase or date, optional>
  - <Action>
    - [<state>] (MM/DD/YY) <task title>
- <Area>
  - <Action>
```

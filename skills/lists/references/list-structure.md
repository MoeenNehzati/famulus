# List Structure

A structured list file uses plain title lines as persistent headers, with
task items nested beneath them.

**Fixed:** file header format, title line syntax, task item format,
indentation convention (2 spaces per level).
**Variable** (shown as `<placeholder>`): list name, state declarations,
title text, task text, dates.

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

- Level 3+ (tasks): `    - [<state>] (MM/DD/YY) <task text>`

All nested task levels (3, 4, 5, …) use the same `- [<state>] (MM/DD/YY) <text>`
format — there is no distinct format for sub-tasks or sub-sub-tasks.

For fuzzy matching (check/uncheck/remove), plain title lines are skipped —
they have no checkbox text to match against.

---

## Example

```
[<list-name>] [<state>] <meaning> · [<state>] <meaning>
                                    ← blank line here is optional
- <Area>
  - <Action>
    - [<state>] (MM/DD/YY) <task text>
      - [<state>] (MM/DD/YY) <sub-task text>
  - <Action>
    - [<state>] (MM/DD/YY) <task text>
- <Area>
  - <Action>
```

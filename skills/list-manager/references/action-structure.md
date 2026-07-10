# Action Structure (Legacy Markdown Format)

> **This document describes the old Markdown format used before migration to YAML.**
> It is kept as a reference for the legacy-import path. The current category
> structure is enforced by JSONSchema — see `schemas/lists/todo.json` and
> `schemas/lists/task-list*.json`.

Extends `list-structure.md`. The area and action names below are **fixed** —
do not rename, reorder, or remove them.

**Fixed:** all area names (Research, Personal, Dev), all action names listed
under each area.
**Variable** (shown as `<placeholder>`): list name, state declarations,
task titles, item descriptions, deadlines, dates.

Note: action sets differ per area (Personal has Shop; others do not).

---

```
[<list-name>] <state-declarations>

- Research
  - Replies
  - Payments
  - Reading
  - Writing
  - Tasks
  - Misc
- Personal
  - Replies
  - Payments
  - Shop
  - Reading
  - Writing
  - Tasks
  - Misc
- Dev
  - Replies
  - Payments
  - Reading
  - Writing
  - Tasks
  - Misc
```

---
name: lists
description: |
  Manage personal text-based lists (groceries, todos, etc.) stored as
  Markdown checklist files on Google Drive under lists/, accessed via
  rclone. Use when the user asks to show, create, or delete a list, or to
  add, check off, uncheck, or remove an item (optionally nested under
  another item) on a list.
---

When this skill is used, begin with:

Skill: lists

Category: automation

## 0. The only commands this skill uses

Every operation goes through exactly one script, with two subcommands. Use
**only** these two invocation patterns — nothing else
(`scripts/lists.sh`, no `cd`, no other
arguments or flags):

- **Read**: `scripts/lists.sh read [name]`
  - No `name`: prints the names of all lists (one `<name>.md` per line, may
    be empty if none exist).
  - With `name`: prints that list's full contents (empty output if the list
    doesn't exist).
- **Write**: `scripts/lists.sh write <name>`
  with the new full file content piped via stdin (heredoc).
  - Non-empty stdin: overwrites `<name>.md` with that content (creates the
    file/directory if needed).
  - Empty stdin: deletes `<name>.md` if it exists (this is how a list is
    deleted — including when the last item is removed and you want to drop
    the file rather than leave an empty one).

For every operation below: do a `read` first to get current content, compute
the new content, then `write` it back. Never use raw `rclone` commands.

## 1. Storage model

- Each list is `<name>.md` under `GDrive:lists/` (managed entirely by the
  script above).
- List names: derive from the user's wording, lowercase, spaces replaced
  with hyphens (e.g. "grocery list" -> `grocery` or `groceries` — match
  against existing list names from `read` with no argument before picking a
  name for a *new* list).
- Creating an empty list (no items) isn't a separate action — a list comes
  into existence the first time an item is added to it, and disappears if
  emptied out (per the `write` semantics above).

## 2. File format

Each list file is a Markdown checklist, optionally nested via 2-space
indentation per level. Every item carries a creation date in
`(MM/DD/YY)` format immediately after the checkbox:

```
- [ ] (06/13/26) Plan trip
  - [ ] (06/13/26) Book flight
  - [ ] (06/10/26) Book hotel
- [ ] (06/13/26) Buy groceries
- [x] (06/12/26) Pay rent
```

- `- [ ] (MM/DD/YY) text` = unchecked item, created on that date
- `- [x] (MM/DD/YY) text` = checked item, created on that date
- The date is set once when the item is added and never changes
  (checking/unchecking doesn't update it).
- No section headers (`##`) — only nested checklist items.
- For matching an item by its text (in 3.5/3.6 and "under Y" in 3.4), use
  the text *after* the `(MM/DD/YY) ` prefix — the date itself is not part
  of the matchable text.

**`potential-actions` extended states** (this list only):
- `- [+] (MM/DD/YY) text` = accepted — item has been promoted to `todo`
- `- [-] (MM/DD/YY) text` = rejected — kept for reference, excluded from suggestions

These states are set by triage workflows (see §5), never by check/uncheck operations (§3.5).

## 3. Operations

### 3.1 List all lists

```bash
scripts/lists.sh read
```

### 3.2 Show / read a list

```bash
scripts/lists.sh read <name>
```

Present the contents to the user (render as a checklist). If output is
empty and the name doesn't appear in 3.1's output, the list doesn't exist —
report that and offer to create it (by adding the first item, see 3.4).

### 3.3 Delete a whole list

Confirm with the user first (destructive), then:

```bash
scripts/lists.sh write <name> <<'EOF'
EOF
```

(empty heredoc — this deletes the file per the write semantics in section 0)

### 3.4 Add an item

1. `read <name>` to get current content. Empty output + name not in 3.1's
   listing means the list doesn't exist yet — that's fine, proceed with
   empty content; the list will be created by the write below.
2. Get today's date: `date +%m/%d/%y` (already allowlisted separately from
   this skill's two commands). Use its output as `<date>` below.
3. Compose the new content:
   - **Plain add** ("add X"): append a new line `- [ ] (<date>) X` at the
     end of the content (after a trailing newline if the content is
     non-empty).
   - **Nested add** ("add X under Y"): fuzzy-match Y (case-insensitive
     substring match against Y's text, ignoring any `(MM/DD/YY) ` prefix)
     against existing lines.
     - 0 matches: report "couldn't find '<Y>' on this list", show the
       current list contents, do not write.
     - 2+ matches: show the matching lines (with their text) and ask the
       user which one they mean. Wait for their answer before proceeding.
     - 1 match: insert a new line `  - [ ] (<date>) X` (Y's indentation + 2
       spaces) immediately after Y's last existing child line, or
       immediately after Y itself if it has no children. A "child" of Y is
       a contiguous run of following lines whose indentation is strictly
       greater than Y's.
4. Write back:

```bash
scripts/lists.sh write <name> <<'EOF'
<full new file content>
EOF
```

5. Confirm to the user what was added and where (mention the date if
   useful, e.g. "added with today's date, 06/13/26").

### 3.5 Check / uncheck an item

1. `read <name>`. If the list doesn't exist (empty output, not in 3.1's
   listing), report not-found.
2. Fuzzy-match the target text (case-insensitive substring) against the
   item text of each line — the text after `- [ ] ` / `- [x] ` and after
   stripping any `(MM/DD/YY) ` prefix.
   - 0 matches: report "couldn't find '<text>' on this list", show the
     current list contents, do not write.
   - 2+ matches: show the matching lines and ask the user which one. Wait
     for their answer.
   - 1 match: toggle that line's checkbox: `[ ]` -> `[x]` for "check"
     requests, `[x]` -> `[ ]` for "uncheck" requests. Leave children's
     checkboxes unchanged.
3. Write the full content back via `write` (same pattern as 3.4 step 3). If
   this was the list's only item and checking/unchecking doesn't remove it,
   the content is still non-empty, so the list is not deleted.
4. Confirm to the user which item was checked/unchecked.

### 3.6 Remove an item

1. `read <name>`. If the list doesn't exist (empty output, not in 3.1's
   listing), report not-found.
2. Fuzzy-match the target text (case-insensitive substring) against item
   text (after stripping any `(MM/DD/YY) ` prefix, same as 3.5), same rules
   as 3.5 (0 matches -> report and stop; 2+ matches -> ask which one).
3. On a single match: identify the matched line and all of its descendant
   lines (contiguous following lines with strictly greater indentation —
   this is the cascade set).
   - If the cascade set contains only the matched line, remove it directly.
   - If the cascade set contains more than just the matched line, list the
     lines that will be removed and ask the user to confirm before
     proceeding.
4. Remove the matched line and (if confirmed) its descendants from the
   content. Write the result back via `write` (same pattern as 3.4 step 3).
   If the resulting content is empty, this deletes the list file (per
   section 0) — mention that to the user if it happens.
5. Confirm to the user what was removed.

## 4. General notes

- Always show the resulting list (or the relevant portion) after a
  write operation so the user can verify the change.
- If the user's request doesn't specify which list and there's exactly one
  list whose name plausibly matches the topic (e.g. "groceries" for "add
  milk"), use it. If there are multiple plausible matches or none, ask the
  user which list (offering to create a new one if none match).

## 5. potential-actions ↔ todo dependency

`potential-actions` is a staging list; `todo` holds committed actions.

**Item lifecycle:**
- Items start as `[ ]` (unreviewed).
- **Accepted** (`[+]`): mark `[+]` in `potential-actions` AND add the item to `todo` as `- [ ] (MM/DD/YY) <text>` (today's date). Both writes happen together in one pass.
- **Rejected** (`[-]`): mark `[-]` in `potential-actions`. Nothing added to `todo`.
- Items are never deleted from `potential-actions` — the `[+]`/`[-]` state is the audit trail.

**Dedup rule:** never add to `potential-actions` if a match already exists in any state (`[ ]`, `[+]`, or `[-]`). A previously rejected item is not re-added on future triage runs.

**Filtering:** only `[ ]` items are surfaced in suggestions (daily plan, triage prompts).

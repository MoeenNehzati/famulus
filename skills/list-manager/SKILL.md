---
name: list-manager
description: |
  Manage personal text-based lists (groceries, todos, etc.) stored as
  Markdown checklist files under assistant cloud storage, accessed through
  the skill-owned `lists.sh` script. Use when the user asks to show, create,
  or delete a list, or to add, check off, uncheck, or remove an item
  (optionally nested under another item) on a list.
---

When this skill is used, begin with:

Skill: list-manager

Category: automation

Dependencies:
- cloud-files

## 0. Commands

Lists are stored in cloud storage managed by the cloud-files skill. Every
operation goes through one script. Use **only** these invocation
patterns — no `cd`, no other tools:

| Subcommand | What it does |
|---|---|
| `scripts/lists.sh read` | List all list names (one `.md` per line) |
| `scripts/lists.sh read <name>` | Print full file contents (use sparingly — see §0.1) |
| `scripts/lists.sh write <name>` | Overwrite with stdin; empty stdin deletes the file |
| `scripts/lists.sh unchecked <name>` | Print only `[ ]` task lines (fresh read each call) |
| `scripts/lists.sh grep <name> <text>` | Fixed-string case-insensitive search with line numbers |
| `scripts/lists.sh toggle <name> <id> check\|uncheck` | Toggle checkbox by ID (atomic read-modify-write) |
| `scripts/lists.sh append <name>` | Append stdin item; auto-injects `<!-- #id -->` (atomic) |
| `scripts/lists.sh migrate <name>` | Add `<!-- #id -->` to every task line that lacks one |

Non-zero exit or stderr from any subcommand → stop; do not infer state; do not write.

### 0.1 Token efficiency

**Prefer atomic subcommands over raw `read`.** Each of `unchecked`, `grep`,
`toggle`, and `append` reads fresh from cloud internally — no cache, no
stale data risk. Only reach for `read <name>` when you genuinely need the
full file to understand structure (nested add, remove-with-cascade, §3.4/3.6).

**If you recently ran `unchecked` or `grep` and have IDs in context, go
directly to `toggle` — no second read needed.**

### 0.2 Bracket safety

`grep` uses `-niF` (fixed-string): brackets in titles or search terms are
always treated as literals. `toggle` finds its target via `<!-- #id -->` using
the same fixed-string search — unambiguous regardless of what the title
contains. `sed` in `toggle` targets a specific line number and substitutes the
checkbox token (`[ ]` or `[x]`) which always appears before any title text,
so brackets in titles are never touched.

## 1. Storage model

- Each list is `<name>.md` under `assistant/lists/` in cloud storage.
- List names: lowercase, spaces → hyphens. Match against existing names
  before picking a name for a new list.
- A list comes into existence the first time an item is added; it disappears
  when emptied (empty stdin to `write`).
- A list with only persistent title lines (no task items) is not deleted.

## 2. File format

Each list file is a Markdown checklist with optional nesting via 2-space
indentation. Every task line carries a creation date and a hidden stable ID:

```
- [ ] (06/13/26) Plan trip <!-- #a3f2 -->
  deadline: this month
  - [ ] (06/13/26) Book flight <!-- #b7c1 -->
    deadline: by Friday
  - [ ] (06/10/26) Book hotel <!-- #e209 -->
- [ ] (06/13/26) Buy groceries <!-- #f14d -->
- [x] (06/12/26) Pay rent <!-- #c88a -->
```

- `- [ ] (MM/DD/YY) title <!-- #xxxx -->` — unchecked
- `- [x] (MM/DD/YY) title <!-- #xxxx -->` — checked
- **Date**: set on creation; never changes.
- **ID** (`<!-- #xxxx -->`): 4 lowercase hex chars in an HTML comment at the
  end of the checkbox line. Auto-injected by `append`; added to existing items
  by `migrate`. Invisible in rendered Markdown. **Never shown to the user** —
  strip `<!-- #... -->` when displaying list items.
- **For matching** (§3.5/3.6 "under Y" in §3.4): match against title text
  after `(MM/DD/YY) ` and before ` <!-- #`. The date and ID are not matchable
  text.

**File header line (optional):**
```
[<list-name>] [<state>] <meaning> · [<state>] <meaning> · ...
```
Declares valid checkbox states. If absent, only `[ ]` and `[x]` are valid.

**Persistent title lines:** Plain `- Title` lines (no checkbox, no date)
as structural headers — never checked, unchecked, or removed:
- Level 1 (area):   `- Title`
- Level 2 (action): `  - Title`
- Task items begin at level 3: `    - [state] (MM/DD/YY) title <!-- #xxxx -->`

**Item continuation lines:**
```markdown
    - [ ] (06/24/26) Reply to Diego <!-- #d3a1 -->
      Follow up on the appendix draft.
      deadline: by Friday
```
- **title**: text on the checkbox line after `(MM/DD/YY) ` and before ` <!-- #`.
- **description**: optional freeform lines. Omit when there's no extra context.
- **`deadline:`**: optional. Put description lines first, then `deadline:`.
- Continuation lines: task indentation + 2 spaces (level-3 task → 6 spaces).
- A nested task is a checkbox line at its own indentation, not a continuation.

Plain title lines have no ID and are skipped for fuzzy matching. For task
items, match against title and optional continuation lines; preserve
continuation lines unless the user explicitly asks to edit them.

## 3. Operations

### 3.1 List all lists

```bash
scripts/lists.sh read
```

### 3.2 Show / read a list

```bash
scripts/lists.sh unchecked <name>
```

Present the output to the user with `<!-- #xxxx -->` stripped from each line.
By default show only unchecked items. If the user asks to see everything
(checked items too), use `scripts/lists.sh read <name>` and strip IDs before
displaying.

When displaying, omit any area or action title-line section that has no `[ ]`
task items beneath it. If `unchecked` returns `(no unchecked items)` and the
name doesn't appear in §3.1's output, the list doesn't exist — report that
and offer to create it.

### 3.3 Delete a whole list

Confirm with the user first (destructive), then:

```bash
scripts/lists.sh write <name> <<'EOF'
EOF
```

### 3.4 Add an item

1. Get today's date: `date +%m/%d/%y`.
2. Parse the user's freeform item text into **title**, optional **description**,
   optional **deadline**. If no deadline was given, ask; if the user says none,
   omit the `deadline:` line.
3. Add the item:

   **Plain add** ("add X"): pass only the item content — the ID is
   auto-injected by `append`:
   ```bash
   scripts/lists.sh append <name> <<'EOF'
   - [ ] (<date>) <title>
     <optional description>
     deadline: <optional deadline>
   EOF
   ```
   Omit description and/or `deadline:` when absent.

   **Nested add** ("add X under Y"): search for Y:
   ```bash
   scripts/lists.sh grep <name> "<Y text>"
   ```
   - 0 matches → report not found, show `unchecked <name>`, stop.
   - 2+ matches → show them (IDs stripped) and ask which. Wait.
   - 1 match → extract the ID from `<!-- #xxxx -->` in the grep output.
     For this structural edit (inserting at the right indentation level),
     read the full file, insert the new item after Y's last child, and write
     back via heredoc. Include `<!-- #xxxx -->` for the new item — generate a
     4-char hex ID that doesn't already appear in the file content.

   **Structured add** (structured lists, no explicit "under Y"): infer the
   best-fit area×action section; announce your choice. Read the full file,
   insert, write back via heredoc (include ID for the new item).

4. Confirm what was added (strip ID from confirmation).

### 3.5 Check / uncheck an item

If you recently ran `unchecked` or `grep` and already have the item's ID in
context, skip to step 3.

1. Find the item:
   ```bash
   scripts/lists.sh grep <name> "<text>"
   ```
   - 0 matches → report not found, show `unchecked <name>`, stop.
   - 2+ matches → show them (IDs stripped) and ask which. Wait.
   - 1 match → extract the 4-char hex ID from `<!-- #xxxx -->`.

2. Toggle:
   ```bash
   scripts/lists.sh toggle <name> <id> check      # or: uncheck
   ```
   The script reads fresh from cloud, finds the line by ID, toggles the
   checkbox, and writes back — all atomically.

3. Confirm which item was checked/unchecked (strip ID from output).

### 3.6 Remove an item

1. Find the item:
   ```bash
   scripts/lists.sh grep <name> "<text>"
   ```
   Same match rules as §3.5.

2. Extract the ID and read the full file to identify the cascade set
   (the matched line + all contiguous following lines with strictly greater
   indentation). A cascade set never includes persistent title lines.
   ```bash
   scripts/lists.sh read <name>
   ```

3. If the cascade set contains more than just the matched line, list the lines
   that will be removed (IDs stripped) and ask for confirmation.

4. Remove the matched line and (if confirmed) its descendants; write back:
   ```bash
   scripts/lists.sh write <name> <<'EOF'
   <full new content>
   EOF
   ```
   If the result is empty, the list file is deleted — mention this.

5. Confirm what was removed.

## 4. General notes

- **Never show `<!-- #xxxx -->` to the user.** Strip ID comments when
  displaying any list content.
- After a write, show the changed item(s) or relevant section — not the full
  list — unless the user asks.
- If the user's request doesn't specify which list and one name clearly
  matches, use it. Otherwise ask.

## 5. Migration

Run once per list to add IDs to existing items:

```bash
scripts/lists.sh migrate <name>
```

The script reads fresh from cloud, adds `<!-- #xxxx -->` to every task line
that lacks one (with collision-free IDs), and writes back. After migration all
`toggle` and ID-based operations work on that list.

## 6. potential-actions ↔ todo dependency

`potential-actions` is a staging list; `todo` holds committed actions.

**Item lifecycle:**
- Items start as `[ ]` (unreviewed).
- **Accepted** (`[+]`): mark `[+]` via `toggle` in `potential-actions` AND
  add the item to `todo` under the inferred best-fit area×action section
  (§3.4 placement logic), preserving title, description, and `deadline:`.
  Use today's date as the todo creation date. Generate a new ID for the todo
  entry. Both writes happen together.
- **Rejected** (`[-]`): mark `[-]` via `toggle` in `potential-actions`.
  Nothing added to `todo`.
- Items are never deleted from `potential-actions` — `[+]`/`[-]` is the
  audit trail.

**Dedup rule:** never add to `potential-actions` if a match already exists in
any state.

**Filtering:** only `[ ]` items are surfaced in suggestions.

## 7. Structured list configuration

The following lists follow the area×action structure defined in
`references/action-structure.md` (see also `references/list-structure.md`).
Detected by the presence of a file header line (starts with `[`) and
persistent title lines.

- `todo` — structured list; valid states declared in its file header
- `potential-actions` — structured list; valid states declared in its file header

When operating on these lists, apply display filtering (§3.2), placement
inference (§3.4), and title-line-skipping rules (§3.5, §3.6, §6).

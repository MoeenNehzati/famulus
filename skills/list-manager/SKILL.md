---
name: list-manager
description: |
  Manage personal structured lists (todos, potential actions, notes, etc.)
  stored as YAML files validated against JSONSchema under assistant cloud
  storage. Use when the user asks to show, create, or delete a list, or to
  add, update, check off, or query items on a list.
---

When this skill is used, begin with:

Skill: list-manager

Category: automation

Dependencies:
- cloud-files

## 0. Architecture

Lists are YAML files stored in cloud storage. Every operation follows this
three-step pattern:

1. **Download**: invoke the cloud-files skill to download the list to a temp
   file, e.g. `lists/todo.yaml` → `/tmp/todo.yaml`.
2. **Operate**: call `scripts/lists.py` on the local temp file.
3. **Upload**: invoke the cloud-files skill to upload the modified temp file
   back to `lists/todo.yaml`.

`lists.py` is a pure local operator — it has no cloud knowledge. Validation
runs inside `lists.py` before any write; if validation fails, `lists.py`
exits nonzero and the local file is untouched. Never call upload on failure.

```
invoke cloud-files skill → download lists/todo.yaml to /tmp/todo.yaml
python3 scripts/lists.py <subcommand> /tmp/todo.yaml <args>
invoke cloud-files skill → upload /tmp/todo.yaml to lists/todo.yaml
```

## 1. Subcommands

All subcommands take the local file path as their first argument.

| Subcommand | Signature | Description |
|---|---|---|
| `init` | `init <file> --schema <name>` | Create a new empty list file. |
| `read` | `read <file> [filters...]` | Full YAML or filtered flat list of entries. |
| `create-entry` | `create-entry <file> <target> [--entries <file>]` | Add entries to a category or as children of an entry. |
| `update` | `update <file> [--file <file>]` | Update fields on entries by ID. |
| `gen-id` | `gen-id <file> [--count n]` | Print n collision-free 6-char hex IDs (default 1). |

Migration from Markdown is a separate one-time script — see section 5.

Non-zero exit → stop; do not upload; report the error from stderr.

## 2. Storage model

- Each list is `<name>.yaml` under `lists/` in cloud storage.
- List names: lowercase, spaces → hyphens.
- The schema (type of list) is declared inside the YAML file as `schema: <name>`.
- Available schemas: `todo`, `potential-actions`, `default`.
- A list comes into existence on `init`; deleted by removing the cloud file.

## 3. File format

Each list is a YAML document with this top-level structure:

```yaml
schema: todo        # declares which JSONSchema to validate against
name: My Todo List  # human-readable name
categories:         # top-level categories
  - name: Work
    categories:     # nested subcategories (any depth)
      - name: Writing
        entries:    # entries (validated by the list's schema)
          - id: a3f2b9
            title: Reply to Diego
            created: "2026-06-29"
            state: incomplete
            deadline: "2026-07-04"
            location: home   # optional
            children:        # nested sub-entries (same schema)
              - id: b7c1e2
                title: Write intro
                created: "2026-06-29"
                state: done
                deadline: "2026-07-02"
```

### Entry types by schema

| Schema | Entry type | `state` values |
|---|---|---|
| `todo` | action | `incomplete`, `inprogress`, `done` |
| `potential-actions` | potential_action | `undecided`, `accepted`, `rejected` |
| `default` | entry | (any; no required state or deadline) |

All entries have: `id` (6-char hex), `title`, `created` (YYYY-MM-DD).
Actions and potential_actions also require: `state`, `deadline` (YYYY-MM-DD).
Optional on all task entries: `description`, `location`, `children`.

IDs are immutable — never update `id` or `created` via `update`.

## 4. Operations

### 4.1 List all lists

Invoke cloud-files skill to list files under `lists/`. Each `.yaml` file is a list.

### 4.2 Read / show a list

**Unfiltered (full structure):**
```bash
python3 scripts/lists.py read /tmp/todo.yaml
```

**Filtered (flat list of matching entries):**
```bash
python3 scripts/lists.py read /tmp/todo.yaml state=incomplete
python3 scripts/lists.py read /tmp/todo.yaml state=incomplete,inprogress
python3 scripts/lists.py read /tmp/todo.yaml state=incomplete location=home
python3 scripts/lists.py read /tmp/todo.yaml title~=Diego
```

Filter syntax:
- `key=value` — exact match; comma-separated values are OR'd
- `key~=value` — substring match
- Multiple distinct keys are AND'd

**To display to the user:** pipe through `scripts/beautify.py`:
```bash
python3 scripts/lists.py read /tmp/todo.yaml state=incomplete | python3 scripts/beautify.py
```

`beautify.py` strips IDs and formats hierarchically. The LLM sees raw YAML
(with IDs for `update`/`create-entry`); the user sees beautified output.

Flags:
- `--diff` — outputs diff-format text (wrap in ` ```diff ` block in your reply for green/red rendering in conversation)
- `--markdown` — outputs Markdown (strikethrough for done, bold for in-progress)
- `--no-color` — disables ANSI color codes (default: on)
- `-D` / `--no-descriptions` — hide entry descriptions (shown by default)

### 4.3 Create a new list

```bash
python3 scripts/lists.py init /tmp/mylist.yaml --schema todo --name "My Tasks"
# then upload to cloud
```

### 4.4 Add entries

Target is either a **category path** (`Work/Writing`) or a **6-char entry ID**
(adds as children of that entry). Input is a YAML list of entries on stdin or
`--entries <file>`. IDs are auto-assigned if absent.

```bash
python3 scripts/lists.py create-entry /tmp/todo.yaml Work/Writing <<'EOF'
- title: Draft intro
  state: incomplete
  created: "2026-06-29"
  deadline: "2026-07-15"
EOF
```

Bulk input via file:
```bash
python3 scripts/lists.py create-entry /tmp/todo.yaml Work/Writing --entries /tmp/new_entries.yaml
```

If the target category doesn't exist, `lists.py` exits nonzero and lists
available categories. Do not create categories on the fly.

### 4.5 Update entries

Input is a YAML list of partial updates keyed by `id`. Multiple entries in one
call. Fields `id` and `created` are immutable — attempts to change them exit
nonzero.

```bash
python3 scripts/lists.py update /tmp/todo.yaml <<'EOF'
- id: a3f2b9
  state: done
- id: b7c1e2
  deadline: "2026-07-20"
EOF
```

Via file:
```bash
python3 scripts/lists.py update /tmp/todo.yaml --file /tmp/updates.yaml
```

### 4.6 Generate IDs

```bash
python3 scripts/lists.py gen-id /tmp/todo.yaml          # one ID
python3 scripts/lists.py gen-id /tmp/todo.yaml --count 5
```

Prints one ID per line. IDs are collision-free against all existing IDs in the file.

## 5. Migration (Markdown → YAML)

Migration is a **one-time operation** handled by a separate standalone script,
not a `lists.py` subcommand. Requires `pip install dateparser`.

```bash
python3 scripts/migrate_md.py <src.md> <dst.yaml> --schema <name> [--name <list-name>]
```

Full workflow:

1. Invoke cloud-files skill: download `lists/todo.md` to `/tmp/todo.md`
2. Run: `python3 scripts/migrate_md.py /tmp/todo.md /tmp/todo.yaml --schema todo`
3. Invoke cloud-files skill: upload `/tmp/todo.yaml` to `lists/todo.yaml`
4. Invoke cloud-files skill: delete `lists/todo.md`

The script handles:
- `- [x]` / `- [ ]` checkboxes → `state` field
- `- [+]` / `- [-]` markers for potential-actions → `accepted` / `rejected`
- `(MM/DD/YY)` creation date prefix on each item
- Inline `(due: ...)` / `(deadline: ...)` tags
- `deadline: <phrase>` continuation lines — parsed relative to the entry's creation date
- Description continuation lines (any other indented text after an entry)
- Nested checkboxes → `children[]`
- Entries with no deadline fall back to their `created` date (flagged in stderr)

Validates output against the target schema before writing. Exits nonzero on failure.

## 6. potential-actions ↔ todo workflow

`potential-actions` is a staging list; `todo` holds committed actions.

- **Accept** an item: update its state to `accepted` in potential-actions AND
  create a matching entry in todo with state `incomplete`, using today's date.
- **Reject** an item: update its state to `rejected` in potential-actions.
- Items stay in potential-actions as audit trail.

**Dedup rule:** before adding to potential-actions, read and verify no entry
with the same title already exists in any state.

## 7. Token efficiency

- Prefer filtered `read` over full `read` — the LLM only sees relevant entries.
- Use `--entries` / `--file` to pass bulk operations; avoid exposing the LLM
  to full list content when only a subset is needed.
- After a write, re-read and beautify only the affected section to confirm.

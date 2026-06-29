# List Manager — YAML + JSONSchema Redesign

**Date:** 2026-06-29  
**Status:** Complete — pending implementation plan

---

## 1. Data Model & Schema Hierarchy

### File format

Lists move from `.md` (Markdown checklist) to `.yaml`. Each list file lives at
`assistant/lists/<name>.yaml` in cloud storage.

Top-level structure:

```yaml
schema: todo        # references schemas/lists/todo.json
name: todo
categories:
  - name: Work
    categories:
      - name: Writing
        entries:
          - id: a3f2
            title: Reply to Diego
            state: incomplete
            created: "2026-06-13"
            deadline: "2026-06-20"
            description: Follow up on the appendix draft.
            location: home          # omit = anywhere
```

### Categories

Categories are persistent structural containers — they have no time component
and no lifecycle. Fields: `name` (required), `categories` (optional, nested),
`entries` (optional). Omit `categories` or `entries` when absent (no null/empty
arrays).

### Entry type hierarchy

```
entry
└── task_entry   ← owns all task fields
    ├── action           ← state ∈ {incomplete, inprogress, done}
    └── potential_action ← state ∈ {undecided, accepted, rejected}
```

**`entry`** — base type (`schemas/types/entry.json`):

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | 4-char lowercase hex, auto-assigned |
| `title` | yes | string | |
| `created` | yes | string (date) | `YYYY-MM-DD`; set on creation, never changes |
| `description` | no | string | omit if absent |
| `children` | no | array of entry | omit if absent; same type as parent entry |

**`task_entry`** — extends `entry` (`schemas/types/task_entry.json`):

| Field | Required | Type | Notes |
|---|---|---|---|
| `state` | yes | string | narrowed to enum by leaf type |
| `deadline` | yes | string (date) | `YYYY-MM-DD` |
| `location` | no | string | omit = anywhere; e.g. `home`, `office` |

**`action`** (`schemas/types/action.json`):  
Extends `task_entry`. `state` ∈ `{incomplete, inprogress, done}`.

**`potential_action`** (`schemas/types/potential_action.json`):  
Extends `task_entry`. `state` ∈ `{undecided, accepted, rejected}`.

Both `action` and `potential_action` inherit `deadline`, `location`, and all
`entry` fields from `task_entry`. Adding a field to `task_entry` propagates
to both automatically.

### Date fields

Both `created` and `deadline` are strict ISO 8601 dates (`YYYY-MM-DD`),
enforced via JSONSchema `"format": "date"`. Free-form deadline phrases
("by Friday", "end of month") are **resolved by the AI to a concrete date
before any write**. The YAML layer never stores free-form date strings.

### JSONSchema file layout

```
schemas/
  types/
    entry.json
    task_entry.json
    action.json
    potential_action.json
  lists/
    todo.json            ← list schema; entries must be action
    potential-actions.json ← entries must be potential_action
    default.json         ← generic list; entries must be entry
```

Per-list schemas (`schemas/lists/*.json`) constrain which entry type is
allowed and declare the list's `schema` field value. Each YAML list file
declares `schema: <list-name>` at the top.

---

## 2. `lists.py` API & Validation Flow

### Architecture

`lists.py` is a **pure local file operator** — it has no cloud knowledge. Cloud
sync is handled by invoking the `cloud-files` skill (a declared dependency);
the list-manager skill never calls `cloud-files` scripts directly.

Skill workflow:
1. Invoke cloud-files skill: "download `lists/todo.yaml` to `/tmp/todo.yaml`"
2. Call `lists.py` subcommands on the local file
3. On success, invoke cloud-files skill: "upload `/tmp/todo.yaml` to `lists/todo.yaml`"
4. Clean up local temp file

`lists.py` exits nonzero on validation failure → skill stops at step 2, never
calls upload, local file is untouched.

All `lists.py` subcommands take a local file path as their first argument.

### Subcommands

| Subcommand | Signature | Description |
|---|---|---|
| `init` | `init <file> --schema <name>` | Create a new empty valid YAML list file. |
| `read` | `read <file> [key=value ...]` | Full raw YAML or filtered matching entries. |
| `create-entry` | `create-entry <file> <target> [--entries f]` | Append entries from stdin or file under `target`. Target is a category path (`Work/Writing`) or a 6-char entry ID (adds as children). Auto-assigns IDs. |
| `update` | `update <file> [--file f]` | Update fields on specific entries. Input is a YAML list of `{id, field: value, ...}`. Validates each change. |
| `gen-id` | `gen-id <file> [--count n]` | Print n collision-free 6-char hex IDs (default 1). |
| `migrate-md` | `migrate-md <src.md> <dst.yaml>` | Convert a Markdown list to YAML. See section 3. |

### Entry IDs

IDs are **6-char lowercase hex** (16⁶ = 16,777,216 slots per list). Generated
by sampling `/dev/urandom` (or `os.urandom` in Python) and retrying on the
rare collision within the file. Guaranteed unique within a list by construction.

### Filter syntax (`read`)

Filters are `key=value` positional arguments. Multiple values on the same key
use `,` (OR semantics). `~=` means substring match. Multiple distinct keys are
AND-ed.

```bash
lists.py read /tmp/todo.yaml                              # full list, raw YAML
lists.py read /tmp/todo.yaml state=incomplete
lists.py read /tmp/todo.yaml state=incomplete,inprogress
lists.py read /tmp/todo.yaml state=incomplete location=home
lists.py read /tmp/todo.yaml title~=Diego
```

Output is always raw YAML including IDs.

### Human-readable display (`scripts/beautify.py`)

A standalone pipe-able formatter — not a subcommand of `lists.py`. The LLM
calls `read` (gets raw YAML), then pipes through `beautify.py` when presenting
to the user (IDs stripped, hierarchically numbered, indented).

```bash
lists.py read /tmp/todo.yaml state=incomplete | scripts/beautify.py
```

### Bulk input shapes

**`create-entry` input** — YAML list of entries (no `id`; auto-assigned):
```yaml
- title: Reply to Diego
  state: incomplete
  created: "2026-06-29"
  deadline: "2026-07-04"
- title: Review draft
  state: incomplete
  created: "2026-06-29"
  deadline: "2026-07-10"
```

**`update` input** — YAML list of partial updates keyed by `id`:
```yaml
- id: a3f2b9
  state: done
- id: b7c1e2
  state: inprogress
  deadline: "2026-07-15"
```

### Validation flow

`create-entry` and `update` validate before modifying the local file:

```
input YAML (stdin or --file)
  → parse (pyyaml)              # syntax errors caught here
  → read file's schema field    # e.g. "todo"
  → load schemas/lists/todo.json
  → validate (jsonschema)       # required fields, date format, state enum
  → if invalid: print error, exit 1
  → if valid: modify local file in place
```

The local file is only modified when validation passes. The skill then uploads
the (always-valid) local file to cloud.

---

## 3. Migration (Markdown → YAML)

### Script: `scripts/migrate-to-yaml.py`

Migration is orchestrated by the skill. For each list:
1. Invoke cloud-files skill: "download `lists/todo.md` to `/tmp/todo.md`"
2. `lists.py migrate-md /tmp/todo.md /tmp/todo.yaml`
3. Invoke cloud-files skill: "upload `/tmp/todo.yaml` to `lists/todo.yaml`"
4. Invoke cloud-files skill: "delete `lists/todo.md`"

### Conversion rules

| Markdown element | YAML equivalent |
|---|---|
| File header line `[name] [state] meaning · ...` | `schema:` field (state declarations are now in JSONSchema) |
| Persistent title line `- Area` | Category `name:` at level 1 |
| Persistent title line `  - Action` | Category `name:` at level 2 |
| `- [ ] (MM/DD/YY) title <!-- #xxxx -->` | Entry with `state: incomplete`, `created: YYYY-MM-DD`, `id: xxxx` |
| `- [x] (MM/DD/YY) title <!-- #xxxx -->` | Entry with `state: done` (or equivalent final state) |
| `- [+]`, `[-]` etc. | Mapped to appropriate state per list type |
| Continuation line `description text` | `description:` field |
| Continuation line `deadline: phrase` | `deadline: YYYY-MM-DD` (resolved; see below) |
| Indented task (child item) | `children:` array on parent entry |

### Date conversion

- `created` dates: converted from `MM/DD/YY` → `YYYY-MM-DD` (unambiguous;
  2-digit year assumes 2000s).
- `deadline` values: passed through `dateparser` for resolution. Values that
  parse cleanly are converted. Any value `dateparser` cannot resolve
  confidently is flagged and presented to the AI for interactive resolution
  before the file is written.

### Post-migration

After all lists are migrated, SKILL.md invocation patterns switch from
`scripts/lists.sh` to `python3 scripts/lists.py`. Permission prefix entries in
`.claude/settings.json` are updated accordingly. The old `lists.sh` and
`number-unchecked.py` scripts are deleted.

---

## 4. Skill Updates

### Date resolution (new instruction)

When a user provides a deadline in any form (relative phrase, weekday name,
vague reference), the AI resolves it to `YYYY-MM-DD` using today's date as
reference before calling `lists.py`. No free-form date strings are passed to
the script.

### Invocation pattern changes

All `scripts/lists.sh` calls in SKILL.md become `python3 scripts/lists.py`.
Each operation is wrapped with cloud-files skill invocations:

```
# Before any operation on a list:
→ invoke cloud-files skill: download lists/<name>.yaml to /tmp/<name>.yaml

# After any mutating operation (only on lists.py exit 0):
→ invoke cloud-files skill: upload /tmp/<name>.yaml to lists/<name>.yaml
→ clean up /tmp/<name>.yaml
```

### Description update

The skill description changes from "Markdown checklist files" to "YAML files
validated against JSONSchema".

### Removed instructions

The following sections of the current SKILL.md become obsolete and are removed:
- File format prose (§2) — replaced by schema files
- `<!-- #id -->` injection rules — handled by `lists.py` internally
- State declaration header line format — replaced by `schema:` field + JSONSchema
- `migrate` subcommand instructions — replaced by `migrate-md`

---

## 5. Tests

The existing `tests/test_lists.sh` is deleted. Tests are rewritten in Python
using pytest and live in `tests/test_lists.py`. No cloud-files calls are made
in tests — all tests operate on local temp files.

### Schema validation tests (`tests/test_validation.py`)

One test module per schema type. Each covers:

| Case | Expected |
|---|---|
| Fully valid entry | passes |
| Missing required field (`title`, `created`, `deadline`, `state`) | fails with clear error |
| Invalid date format (`created`, `deadline`) | fails |
| State value outside enum | fails |
| Unknown extra field | fails (schemas use `additionalProperties: false`) |
| Valid `children` nesting | passes |
| `children` with wrong entry type | fails |

Types covered: `entry`, `task_entry`, `action`, `potential_action`, and each
list schema (`todo`, `potential-actions`, `default`).

### Subcommand behaviour tests (`tests/test_lists.py`)

Integration tests using `tmp_path` (pytest fixture) for isolated temp files.

| Subcommand | Cases |
|---|---|
| `init` | creates valid skeleton; schema field matches `--schema` arg |
| `read` (no filter) | returns full YAML |
| `read` (exact filter) | returns only matching entries |
| `read` (`~=` filter) | substring match across title and description |
| `read` (multi-value filter) | OR semantics within a key |
| `read` (multi-key filter) | AND semantics across keys |
| `create-entry` (category target) | entry added under correct category; ID auto-assigned |
| `create-entry` (ID target) | entry added as child of matching entry |
| `create-entry` (invalid entry) | exits nonzero; file unchanged |
| `create-entry` (bulk) | multiple entries added in one call |
| `update` (single field) | field updated; other fields preserved |
| `update` (multiple fields) | all fields updated atomically |
| `update` (invalid state) | exits nonzero; file unchanged |
| `update` (unknown ID) | exits nonzero with clear error |
| `gen-id` | returns 6-char hex; collision-free against existing IDs |
| `gen-id --count n` | returns n unique IDs |
| `migrate-md` | Markdown → YAML round-trip; dates converted; IDs preserved |
| `migrate-md` (free-form deadline) | flags unresolvable deadline; file not written |

### Test data

Fixture YAML and Markdown files in `tests/fixtures/`. Kept minimal — one small
valid file per list type, one invalid file per failure mode.

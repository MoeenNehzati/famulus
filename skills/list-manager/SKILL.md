---
name: list-manager
description: |
  Use whenever the user refers to any personal list they keep — a list of any
  name or topic (todo, shopping, reading, packing, gifts, projects, and any
  other) — or asks to see, add to, check off, complete, reorder, rename, set a
  deadline on, or remove items in one. Any phrasing like "my <X> list", "what's
  on my <X>", "add X to my list", "show my <X>", "mark X done" triggers this,
  whatever the list is called.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: productivity-general-assistant

Dependencies:
- cloud-files

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `beautify-list` — Render YAML list entries from stdin (diff-fenced). Pass YAML via stdin using `dispatcher --stdin`.
  - `dispatcher --caller-skill list-manager list-manager beautify-list`
  - Reads YAML from stdin and renders user-facing list output.
- `cloud-create-entry` — Add entries to a cloud list under a category path.
  - `dispatcher --caller-skill list-manager list-manager cloud-create-entry <name> <category/path> --cloud --entries /tmp/entry.yaml`
- `cloud-delete` — Delete one or more entries by id from a cloud list. Ids come after --cloud.
  - `dispatcher --caller-skill list-manager list-manager cloud-delete <name> --cloud <id> [<id>...]`
  - Delete one or more entries by ID from a cloud list.
- `cloud-init` — Create a new list in cloud storage.
  - `dispatcher --caller-skill list-manager list-manager cloud-init <name> --cloud --schema <schema>`
  - Create a new list in cloud storage.
- `cloud-read` — Read a cloud list by name (raw YAML), optionally filtered.
  - `dispatcher --caller-skill list-manager list-manager cloud-read <name> [filters] --cloud`
  - Read cloud list by name (raw YAML), optionally filtered.
- `cloud-read-beautify` — Read a cloud list by name and render it (diff-fenced, id-annotated). Relay stdout verbatim.
  - `dispatcher --caller-skill list-manager list-manager cloud-read-beautify <name> [filters] --cloud`
  - Read a cloud list by name and render it (diff-fenced), optionally filtered.
- `cloud-update` — Update entries in a cloud list from a patch file (keyed by id).
  - `dispatcher --caller-skill list-manager list-manager cloud-update <name> --cloud --file /tmp/patch.yaml`
  - file-mode: Update cloud list entries from a patch file.
  - stdin-mode: Update cloud list entries from a stdin patch.
- `create-entry` — Add entries to a local YAML list under a category path.
  - `dispatcher --caller-skill list-manager list-manager create-entry <file> <category/path> --entries /tmp/entry.yaml`
- `describe-schema` — Describe entry-level fields (types/required/enums) for a list schema.
  - `dispatcher --caller-skill list-manager list-manager describe-schema <schema> [field]`
  - First positional is the schema name (todo, triage, default); optional second positional is a field name, or '*'/omitted for all fields. Purely local and read-only -- no cloud variant needed.
- `generate-id` — Generate one or more collision-free 6-char entry IDs against a local list file.
  - `dispatcher --caller-skill list-manager list-manager generate-id <file> [--count N]`
- `init-list` — Create a new empty local YAML list file.
  - `dispatcher --caller-skill list-manager list-manager init-list <file> [--schema <name>]`
- `migrate-markdown` — Migrate a legacy Markdown list to YAML format.
  - `dispatcher --caller-skill list-manager list-manager migrate-markdown <source.md> <dest.yaml> --schema <schema>`
- `read-beautify` — Read a local YAML list file and render it for display (diff-fenced).
  - `dispatcher --caller-skill list-manager list-manager read-beautify <file> [filters]`
  - Read a local YAML list file and immediately return pretty output.
- `read-list` — Read a local YAML list file, optionally filtered (raw YAML output).
  - `dispatcher --caller-skill list-manager list-manager read-list <file> [filters]`
  - First positional is the local YAML file; remaining positionals are filters.
- `update-list` — Update entries in a local YAML list file from a patch file (keyed by id) or stdin.
  - `dispatcher --caller-skill list-manager list-manager update-list <file> --file /tmp/patch.yaml`
  - file-mode: Externally supported update mode; caller prepares the patch file.
  - stdin-batch: Internal convenience mode for the owning skill when feeding YAML directly.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: list-manager

## Rules

- **Show to user:** use `cloud-read-beautify`; relay stdout **verbatim** — it is pre-fenced and id-annotated. Do not reformat.
- **Ids:** every rendered row ends with `#id`. Use ids for all mutations — never row numbers. If ids aren't in context, run `cloud-read-beautify` first.
- **Required fields:** if the schema requires a field the user didn't provide, ask — do not invent it. For example, `todo` entries require `deadline`. The script validates this on create-entry and rejects entries with missing required fields; this prevents silently inventing values.
- **Creating entries:** if the target category path is not already in context, run `cloud-read-beautify` first to see the list structure. Do not guess category paths.
- **Missing categories:** do not invent; fail and report available categories.
- **Transport:** cloud operations go through cloud-files's `lists-*` interfaces; never bypass them.
- **Validation:** never upload after a local validation or mutation failure.
- **`triage`:** accepting an item also creates a matching `todo` (state `incomplete`, today's date); rejecting only changes state in `triage`.
- **Economy:** prefer filtered reads; after a write re-read only the affected portion.
- **Unsure what a field allows?** Use `describe-schema` instead of guessing — e.g. `describe-schema todo state` for just that field's spec, or `describe-schema todo` (or `describe-schema todo '*'`) for every field's type/required/enum. A filter or entry value outside a schema's enum is rejected with the valid values listed, but don't wait to be told — check first when unsure.

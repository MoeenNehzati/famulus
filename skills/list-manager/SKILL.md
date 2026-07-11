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

Exported Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `beautify-list` — Render YAML list entries from stdin (nested bullet-list markdown by default for todo/triage; --table for a flat GFM table, --diff for the legacy diff-fenced view). Pass YAML via stdin using `dispatcher --stdin`.
  - `dispatcher --caller-skill list-manager list-manager.machine.beautify-list [-D|--no-descriptions] [--markdown|--table|--diff] [--relative-deadlines] [--ids]`
  - Reads YAML from stdin and renders user-facing list output. No allowed_flags restriction: -D/--markdown/--table/--diff/--relative-deadlines/--ids all pass through.
- `cloud-create-entry` — Add entries to a cloud list under a category path.
  - `dispatcher --caller-skill list-manager list-manager.machine.cloud-create-entry <name> <category/path> --cloud --entries /tmp/entry.yaml`
- `cloud-delete` — Delete one or more entries by id from a cloud list. Ids come after --cloud.
  - `dispatcher --caller-skill list-manager list-manager.machine.cloud-delete <name> --cloud <id> [<id>...]`
  - Delete one or more entries by ID from a cloud list.
- `cloud-init` — Create a new list in cloud storage.
  - `dispatcher --caller-skill list-manager list-manager.machine.cloud-init <name> --cloud --schema <schema>`
  - Create a new list in cloud storage.
- `cloud-read` — Read a cloud list by name (raw YAML), optionally filtered. A filtered read preserves structure: same shape as the full doc, pruned to only branches containing a match -- ancestor categories/parent entries are kept, and a match is never duplicated as both a nested child and a top-level result.
  - `dispatcher --caller-skill list-manager list-manager.machine.cloud-read <name> [filters] --cloud`
  - Read cloud list by name (raw YAML), optionally filtered. Filtered output is a pruned tree, not a flat list of matches -- do not assume flat-list shape.
- `cloud-read-beautify` — Read a cloud list by name and render it (nested bullet-list markdown by default, id-annotated; --table for a flat GFM table, --diff for the legacy diff-fenced view). Relay stdout verbatim.
  - `dispatcher --caller-skill list-manager list-manager.machine.cloud-read-beautify <name> [filters] --cloud`
  - Read a cloud list by name and render it as nested bullet-list markdown by default, optionally filtered.
- `cloud-update` — Update entries in a cloud list from a patch file (keyed by id).
  - `dispatcher --caller-skill list-manager list-manager.machine.cloud-update <name> --cloud --file /tmp/patch.yaml`
  - file-mode: Update cloud list entries from a patch file.
  - stdin-mode: Update cloud list entries from a stdin patch.
- `create-entry` — Add entries to a local YAML list under a category path.
  - `dispatcher --caller-skill list-manager list-manager.machine.create-entry <file> <category/path> --entries /tmp/entry.yaml`
- `describe-schema` — Describe entry-level fields (types/required/enums) for a list schema.
  - `dispatcher --caller-skill list-manager list-manager.machine.describe-schema <schema> [field]`
  - First positional is the schema name (todo, triage, default); optional second positional is a field name, or '*'/omitted for all fields. Purely local and read-only -- no cloud variant needed.
- `generate-id` — Generate one or more collision-free 6-char entry IDs against a local list file.
  - `dispatcher --caller-skill list-manager list-manager.machine.generate-id <file> [--count N]`
- `init-list` — Create a new empty local YAML list file.
  - `dispatcher --caller-skill list-manager list-manager.machine.init-list <file> [--schema <name>]`
- `migrate-markdown` — Migrate a legacy Markdown list to YAML format.
  - `dispatcher --caller-skill list-manager list-manager.machine.migrate-markdown <source.md> <dest.yaml> --schema <schema>`
- `read-beautify` — Read a local YAML list file and render it for display (nested bullet-list markdown by default; --table for a flat GFM table, --diff for the legacy diff-fenced view).
  - `dispatcher --caller-skill list-manager list-manager.machine.read-beautify <file> [filters] [--sort FIELD] [-D|--no-descriptions] [--markdown|--table|--diff] [--no-ids] [-o FILE]`
  - Read a local YAML list file and immediately return pretty output. No allowed_flags restriction: --sort/-D/--markdown/--table/--diff/--no-ids/-o all pass through.
- `read-list` — Read a local YAML list file, optionally filtered (raw YAML output). A filtered read preserves structure: it returns the same shape as the input (full doc with categories, or a bare list) pruned to only branches containing a match -- every ancestor category and parent entry of a match is kept for context, and a match is never duplicated as both a nested child and an independent top-level result.
  - `dispatcher --caller-skill list-manager list-manager.machine.read-list <file> [filters]`
  - First positional is the local YAML file; remaining positionals are filters. Filtered output is a pruned tree (or pruned list, if the input itself was a bare list), not a flat list of matches -- do not assume flat-list shape.
- `update-list` — Update entries in a local YAML list file from a patch file (keyed by id) or stdin.
  - `dispatcher --caller-skill list-manager list-manager.machine.update-list <file> --file /tmp/patch.yaml`
  - file-mode: Externally supported update mode; caller prepares the patch file.
  - stdin-batch: Internal convenience mode for the owning skill when feeding YAML directly.

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: list-manager

## Rules

- **Show to user:** use `cloud-read-beautify`; relay stdout **verbatim** — it is pre-formatted nested bullet-list markdown, id-annotated. Do not reformat.
- **Ids:** every rendered row ends with `#id`. Use ids for all mutations — never row numbers. If ids aren't in context, run `cloud-read-beautify` first.
- **Required fields:** if the schema requires a field the user didn't provide, ask — do not invent it. For example, `todo` entries require `deadline`. The script validates this on create-entry and rejects entries with missing required fields; this prevents silently inventing values.
- **Creating entries:** if the target category path is not already in context, run `cloud-read-beautify` first to see the list structure. Do not guess category paths.
- **Missing categories:** do not invent; fail and report available categories.
- **Transport:** cloud operations go through cloud-files's `lists-*` interfaces; never bypass them.
- **Validation:** never upload after a local validation or mutation failure.
- **`triage`:** accepting an item also creates a matching `todo` (state `incomplete`, today's date); rejecting only changes state in `triage`.
- **Economy:** prefer filtered reads; after a write re-read only the affected portion.
- **Unsure what a field allows?** Use `describe-schema` instead of guessing — e.g. `describe-schema todo state` for just that field's spec, or `describe-schema todo` (or `describe-schema todo '*'`) for every field's type/required/enum. A filter or entry value outside a schema's enum is rejected with the valid values listed, but don't wait to be told — check first when unsure.
- **Ambiguous values:** when a field value is genuinely ambiguous, offer a few short, concrete options to pick from rather than guessing or asking an open-ended question. Keep options terse so the choice is quick to read and answer. E.g. a relative deadline ("end of the week"), or a task that implies a physical place (pick up/drop off/visit) with no `location` given.
- **`completed` / `modified`:** both are auto-stamped by `update-list`/`cloud-update` — never set them yourself or invent a value. `completed` is set once, the first time a patch itself transitions `state` into a finished value (`complete`/`accepted`/`rejected`); later unrelated edits never overwrite it. `modified` is a debugging aid only, stamped on every touch, and is never shown by any renderer. Pre-existing entries finished before these fields existed have no `completed` recorded and nothing backfills it — they render with no date badge until next explicitly touched.

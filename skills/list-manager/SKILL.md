---
name: list-manager
description: >-
  Use when the user asks to view or change a persistent personal list. Do not use for an ad hoc generated list, repository inventory, or prose checklist.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: personal-organization, storage-and-sync; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `list-manager.source.gateway -> list-manager._rtx.interface.beautify-list@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-create-entry@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-delete@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-init@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-list-categories@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-read-beautify@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-read@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.cloud-update@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.create-entry@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.describe-schema@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.generate-id@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.init-list@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.migrate-markdown@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.read-beautify@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.read-list@1`
- `list-manager.source.gateway -> list-manager._rtx.interface.update-list@1`

Setup Requires Setup Of:
- `connect-google.interface.setup@1`
Setup Order:
1. `connect-google.interface.setup`
2. `list-manager.interface.setup`

Public Interfaces:
- `list-manager.interface.default`
- `list-manager.interface.setup`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `list-manager._rtx.interface.beautify-list` — Render YAML list entries from stdin (nested bullet-list markdown by default for todo/triage; --table for a flat GFM table, --diff for the legacy diff-fenced view). Pass YAML via stdin using `dispatcher --stdin`.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["-D", "--no-descriptions", "--markdown", "--table", "--diff", "--relative-deadlines", "--ids"], "stdin": null}
    Required options: []; positional arity: 0..unbounded; stdin: permitted
- `list-manager._rtx.interface.cloud-create-entry` — Add entries to a cloud list under a category path.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `stdin-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--expected-revision": "N"}, "positionals": ["name", "category/path"], "stdin": null}
    Required options: ["--cloud"]; positional arity: 2..2; stdin: permitted
  - Alternative: `file-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--entries": "/tmp/entry.yaml", "--expected-revision": "N"}, "positionals": ["name", "category/path"], "stdin": null}
    Required options: ["--cloud", "--entries"]; positional arity: 2..2; stdin: forbidden
- `list-manager._rtx.interface.cloud-delete` — Delete one or more entries by id from a cloud list. Ids come after --cloud.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--expected-revision": "N"}, "positionals": ["name", "id", "id..."], "stdin": null}
    Required options: ["--cloud"]; positional arity: 2..unbounded; stdin: forbidden
- `list-manager._rtx.interface.cloud-init` — Create a new list in cloud storage.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--schema": "schema"}, "positionals": ["name"], "stdin": null}
    Required options: ["--cloud", "--schema"]; positional arity: 1..1; stdin: forbidden
- `list-manager._rtx.interface.cloud-list-categories` — Return cached cloud-list category paths, refreshing them after the local use countdown expires or on request.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--refresh": true}, "positionals": ["name"], "stdin": null}
    Required options: ["--cloud"]; positional arity: 1..1; stdin: forbidden
- `list-manager._rtx.interface.cloud-read` — Read a cloud list by name (raw YAML), optionally filtered. A filtered read preserves structure: same shape as the full doc, pruned to only branches containing a match -- ancestor categories/parent entries are kept, and a match is never duplicated as both a nested child and a top-level result.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true}, "positionals": ["name", "filters"], "stdin": null}
    Required options: ["--cloud"]; positional arity: 1..unbounded; stdin: forbidden
- `list-manager._rtx.interface.cloud-read-beautify` — Read a cloud list by name and render it (nested bullet-list markdown by default, id-annotated; --table for a flat GFM table, --diff for the legacy diff-fenced view), writing stdout or an optional output file.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "-o": true}, "positionals": ["name", "filters", "FILE"], "stdin": null}
    Required options: ["--cloud"]; positional arity: 1..unbounded; stdin: forbidden
- `list-manager._rtx.interface.cloud-update` — Update cloud-list entries from a YAML list of patch objects, each with a quoted string `id`; input is not a mapping keyed by id.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `file-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--expected-revision": "N", "--file": "/tmp/patch.yaml"}, "positionals": ["name"], "stdin": null}
    Required options: ["--cloud", "--file"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `stdin-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--cloud": true, "--expected-revision": "N"}, "positionals": ["name"], "stdin": null}
    Required options: ["--cloud"]; positional arity: 1..1; stdin: permitted
- `list-manager._rtx.interface.create-entry` — Add entries to a local YAML list under a category path.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `stdin-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--expected-revision": "N"}, "positionals": ["file", "category/path"], "stdin": null}
    Required options: []; positional arity: 2..2; stdin: permitted
  - Alternative: `file-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--entries": "/tmp/entry.yaml", "--expected-revision": "N"}, "positionals": ["file", "category/path"], "stdin": null}
    Required options: ["--entries"]; positional arity: 2..2; stdin: forbidden
- `list-manager._rtx.interface.describe-schema` — Describe entry-level fields (types/required/enums) for a list schema.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["schema", "field"], "stdin": null}
    Required options: []; positional arity: 1..2; stdin: forbidden
- `list-manager._rtx.interface.generate-id` — Generate one or more collision-free 6-char entry IDs against a local list file.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["file", "--count", "N"], "stdin": null}
    Required options: []; positional arity: 1..unbounded; stdin: forbidden
- `list-manager._rtx.interface.init-list` — Create a new empty local YAML list file.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["file", "--schema", "name"], "stdin": null}
    Required options: []; positional arity: 1..unbounded; stdin: forbidden
- `list-manager._rtx.interface.migrate-markdown` — Migrate a legacy Markdown list to YAML format.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["source.md", "dest.yaml", "--schema", "schema"], "stdin": null}
    Required options: []; positional arity: 3..unbounded; stdin: forbidden
- `list-manager._rtx.interface.read-beautify` — Read a local YAML list file and render it for display (nested bullet-list markdown by default; --table for a flat GFM table, --diff for the legacy diff-fenced view).
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["file", "filters", "--sort", "FIELD", "-D", "--no-descriptions", "--markdown", "--table", "--diff", "--no-ids", "-o", "FILE"], "stdin": null}
    Required options: []; positional arity: 1..unbounded; stdin: forbidden
- `list-manager._rtx.interface.read-list` — Read a local YAML list file, optionally filtered (raw YAML output). A filtered read preserves structure: it returns the same shape as the input (full doc with categories, or a bare list) pruned to only branches containing a match -- every ancestor category and parent entry of a match is kept for context, and a match is never duplicated as both a nested child and an independent top-level result.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["file", "filters", "--sort", "FIELD"], "stdin": null}
    Required options: []; positional arity: 1..unbounded; stdin: forbidden
- `list-manager._rtx.interface.update-list` — Update entries in a local YAML list file using a YAML sequence of patch objects supplied by file or stdin.
  - Caller: `list-manager`
  - Version: 1
  - Alternative: `file-mode`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--expected-revision": "N", "--file": "/tmp/patch.yaml"}, "positionals": ["file"], "stdin": null}
    Required options: ["--file"]; positional arity: 1..1; stdin: forbidden
  - Alternative: `stdin-batch`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--expected-revision": "N"}, "positionals": ["file"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: permitted

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `list-manager.interface.default` — Primary LLM-facing skill instructions.
- `list-manager.interface.setup` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: list-manager

## Rules

- **Show to user:** use `cloud-read-beautify`; relay stdout **verbatim** — it is pre-formatted nested bullet-list markdown, id-annotated. Do not reformat.
- **Ids and mutation patches:** every rendered row ends with `#id`. Mutations always use these stable ids, never row numbers. Patch input for `update-list` and `cloud-update` is a YAML list of objects. Every object must contain a string `id`; quote every `id`, never use an id-keyed YAML mapping, and never leave numeric-looking ids unquoted. If ids are not in context, run `cloud-read-beautify` first. For example:
  ```yaml
  - id: "421753"
    state: rejected
  - id: "010b76"
    state: rejected
  ```
- **Temporary numbering:** when the user explicitly asks for numbered items, retain a number-to-id mapping from that rendered result. Before mutating numbered selections, resolve them to their stable ids and report the resolved ids and intended change.
- **Unattended callers never get asked.** Several rules below say to ask the user or offer choices. Every one of them assumes a human is present. When this skill is invoked by another skill running on a schedule with nobody watching, a question is not a deferral — it strands the caller's entire run, and it strands it again on every later run, because the caller stops before the step that records its progress. So when the caller is an unattended run: apply the caller's own documented default if it has one for that field; otherwise stop and report the specific problem back to the caller so it can latch a failure and surface it through its health check. Never ask, and never invent a value the caller did not decide in advance. This applies to every "ask" and "offer choices" instruction in this file — required fields, ambiguous values, and category selection alike.
- **Required fields:** if the schema requires a field the user didn't provide, ask — do not invent it. For example, `todo` entries require `deadline`. The script validates this on create-entry and rejects entries with missing required fields; this prevents silently inventing values. **Exception — a calling skill's documented default:** when the caller is another skill whose own instructions specify what to use for that field when it is absent, apply that default and do not ask. A documented default is not an invented value — the caller decided it in advance, in writing, and is responsible for marking it as a default where that matters, such as by noting it in the title.
- **Creating entries:** if the target category path is not already in context, use `cloud-list-categories` first. If several paths fit, offer short concrete choices; do not guess category paths. For an unattended caller, report the candidate paths back instead of offering them.
- **Missing or stale categories:** if a create reports that its category no longer exists, refresh `cloud-list-categories` once and ask the user to choose a matching current path. Do not infer a replacement category or silently retry the write. For an unattended caller, report the stale path and the current candidates back instead of asking — a renamed category must not be resolved by guesswork, but it must also not hang a scheduled run forever.
- **Transport:** cloud operations go through cloud-files's `lists-*` interfaces; never bypass them.
- **Validation:** never upload after a local validation or mutation failure.
- **`triage`:** accepting an item also creates a matching `todo` (state `incomplete`, today's date); rejecting only changes state in `triage`.
- **Economy:** prefer filtered reads; after a write re-read only the affected portion.
- **Unsure what a field allows?** Use `describe-schema` instead of guessing — e.g. `describe-schema todo state` for just that field's spec, or `describe-schema todo` (or `describe-schema todo '*'`) for every field's type/required/enum. A filter or entry value outside a schema's enum is rejected with the valid values listed, but don't wait to be told — check first when unsure.
- **Ambiguous values:** when a field value is genuinely ambiguous, offer a few short, concrete options to pick from rather than guessing or asking an open-ended question. Keep options terse so the choice is quick to read and answer. E.g. a relative deadline ("end of the week"), or a task that implies a physical place (pick up/drop off/visit) with no `location` given. For an unattended caller, resolve it the same way a missing value is resolved: use the caller's documented default for that field if it has one, and otherwise report back rather than offering choices. An ambiguous value and an absent one are the same problem here — both need a decision nobody is present to make.
- **`completed` / `modified`:** both are auto-stamped by `update-list`/`cloud-update` — never set them yourself or invent a value. `completed` is set once, the first time a patch itself transitions `state` into a finished value (`complete`/`accepted`/`rejected`); later unrelated edits never overwrite it. `modified` is a debugging aid only, stamped on every touch, and is never shown by any renderer. Pre-existing entries finished before these fields existed have no `completed` recorded and nothing backfills it — they render with no date badge until next explicitly touched.
- **Concurrent writers / `--expected-revision`:** every list document carries an integer `revision` field, bumped by one on every successful mutating write. If a list has **never** had a mutating write since this field was introduced, it has no `revision` key at all — treat that as `revision: 0`, not as "unknown" or "unsupported"; do not skip the guard or invent a different number. When a caller may race with another writer (e.g. a scheduled run overlapping a manual edit, or two runs of the same skill), read the list first, note its `revision` (or use `0` if the key is absent), then pass `--expected-revision <that value>` on `create-entry`/`cloud-create-entry`/`update-list`/`cloud-update`/`cloud-delete`. A rejection (stale-revision error, nothing written) means another writer saved first — re-read the list, re-check for duplicates, and retry the single mutation; never assume the write went through and never skip the re-read. `--expected-revision` is optional and has no effect if omitted (existing unguarded call sites keep working).

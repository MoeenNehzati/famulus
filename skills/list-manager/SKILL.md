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

Public Interfaces:
- `list-manager.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `list-manager.interface.default` — Primary LLM-facing skill instructions.
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
- **Required fields:** if the schema requires a field the user didn't provide, ask — do not invent it. For example, `todo` entries require `deadline`. The script validates this on create-entry and rejects entries with missing required fields; this prevents silently inventing values. **Exception — a calling skill's documented default:** when the caller is another skill whose own instructions specify what to use for that field when it is absent, apply that default and do not ask. Asking is only correct when a human is there to answer; a skill running unattended cannot be unblocked by a question, and the ask would strand its whole run rather than defer one field. A documented default is not an invented value — the caller decided it in advance, in writing, and is responsible for marking it as a default where that matters, such as by noting it in the title.
- **Creating entries:** if the target category path is not already in context, use `cloud-list-categories` first. If several paths fit, offer short concrete choices; do not guess category paths.
- **Missing or stale categories:** if a create reports that its category no longer exists, refresh `cloud-list-categories` once and ask the user to choose a matching current path. Do not infer a replacement category or silently retry the write.
- **Transport:** cloud operations go through cloud-files's `lists-*` interfaces; never bypass them.
- **Validation:** never upload after a local validation or mutation failure.
- **`triage`:** accepting an item also creates a matching `todo` (state `incomplete`, today's date); rejecting only changes state in `triage`.
- **Economy:** prefer filtered reads; after a write re-read only the affected portion.
- **Unsure what a field allows?** Use `describe-schema` instead of guessing — e.g. `describe-schema todo state` for just that field's spec, or `describe-schema todo` (or `describe-schema todo '*'`) for every field's type/required/enum. A filter or entry value outside a schema's enum is rejected with the valid values listed, but don't wait to be told — check first when unsure.
- **Ambiguous values:** when a field value is genuinely ambiguous, offer a few short, concrete options to pick from rather than guessing or asking an open-ended question. Keep options terse so the choice is quick to read and answer. E.g. a relative deadline ("end of the week"), or a task that implies a physical place (pick up/drop off/visit) with no `location` given.
- **`completed` / `modified`:** both are auto-stamped by `update-list`/`cloud-update` — never set them yourself or invent a value. `completed` is set once, the first time a patch itself transitions `state` into a finished value (`complete`/`accepted`/`rejected`); later unrelated edits never overwrite it. `modified` is a debugging aid only, stamped on every touch, and is never shown by any renderer. Pre-existing entries finished before these fields existed have no `completed` recorded and nothing backfills it — they render with no date badge until next explicitly touched.
- **Concurrent writers / `--expected-revision`:** every list document carries an integer `revision` field, bumped by one on every successful mutating write. If a list has **never** had a mutating write since this field was introduced, it has no `revision` key at all — treat that as `revision: 0`, not as "unknown" or "unsupported"; do not skip the guard or invent a different number. When a caller may race with another writer (e.g. a scheduled run overlapping a manual edit, or two runs of the same skill), read the list first, note its `revision` (or use `0` if the key is absent), then pass `--expected-revision <that value>` on `create-entry`/`cloud-create-entry`/`update-list`/`cloud-update`/`cloud-delete`. A rejection (stale-revision error, nothing written) means another writer saved first — re-read the list, re-check for duplicates, and retry the single mutation; never assume the write went through and never skip the re-read. `--expected-revision` is optional and has no effect if omitted (existing unguarded call sites keep working).

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

Category: automation

Dependencies:
- cloud-files

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, begin with:

Skill: list-manager

## Workflow

This skill manages YAML lists stored under `lists/` in cloud storage.

**Cloud is the store.** Pass `--cloud` and give the list NAME as the first
positional; the script downloads, operates, and (for mutations) uploads:
```bash
lists.py read todo [filters...] --cloud
read_beautify.py todo [filters...] --cloud        # rendered, diff-fenced
lists.py create-entry todo Research/Writing --cloud
lists.py update todo --file patch.yaml --cloud
lists.py init groceries --schema default --cloud
```
Without `--cloud` the first positional is a local file path — that is the
engine used internally and by tests; not the user path.

Cloud transport goes through cloud-files's restricted `lists-*` interfaces,
which constrain access to the `lists/` directory. Validation runs before every
write; never bypass it.

## Showing a list to the user

For any human-facing read ("show my …", "what's on …", "what's left") run
`read_beautify <name> [filters] --cloud` and relay its stdout **verbatim** — it
comes wrapped in a ` ```diff ` fence, grouped, numbered, deadline-annotated, and
each row ends with its `#id` (ids are on by default). Do not re-number,
re-group, summarize, drop the ids, or rebuild it as your own bullets.

## Filtering: fields first

Choose which rows to show in this order:

1. **Field filter (preferred).** Express the request as `read`/`read_beautify`
   filters — one call, done in-script:
   - `key=v1,v2` — exact match, comma = OR (e.g. `state=incomplete`)
   - `key~=regex` — regex search on the field, case-insensitive
     (e.g. `title~=^Reply`, `deadline~=^2026-07`)
   Most requests ("what's not done", "replies", "due in July") are field filters.
2. **Semantic selection** (e.g. "the apartment ones") — render with ids
   (default), read the matching `#id`s from the output, and act on those. You
   pick which rows; the script still owns formatting. Never hand-format rows.

## Editing items the user points to

The rendered list already shows each row's `#id`, so a follow-up like "mark 66
done" or "push the apartment ones to Friday" resolves to ids **already in your
context** — no counting, no re-reading, no mapping row numbers. Write the patch
to a temp file and apply it in one call keyed by id:

```yaml
# /tmp/patch.yaml
- id: 1ce1a7
  state: done
- id: a3aaba
  state: done
```
```
lists.py update todo --cloud --file /tmp/patch.yaml
```

If the list is not already shown with ids in context, first
`read_beautify <name> [filters] --cloud` (ids on), then update. Do not grep the
raw YAML to hand-map numbers to ids.

## Other operations

- Add entries → `create-entry`; edit / check off / set deadline → `update`.
- Create a new list → `init`; generate fresh ids → `gen-id`.
- Migrate a legacy Markdown list → `migrate-markdown`.
- `beautify-list` (stdin) renders entries a caller already holds in memory;
  used by other skills, not the user path.

For exact calling conventions, use `blueprint.yaml` and the script `--help`.
For the live data contract, use `schemas/`.

## Semantic invariants

Keep these rules even if the scripts are obvious:

- Never upload a file after any local validation or mutation failure.
- Do not invent missing categories automatically; fail and report available
  categories instead.
- Before adding to `potential-actions`, verify there is no existing entry with
  the same title in any state.
- Accepting a `potential-actions` item also creates a matching `todo` entry
  with state `incomplete` and today's date.
- Rejecting a `potential-actions` item only changes its state there; the item
  remains as audit trail.

## Economy

- Prefer filtered reads over full reads.
- Use file/stdin batch modes for bulk mutations instead of exposing whole lists.
- After a write, re-read only the affected portion and beautify that output.

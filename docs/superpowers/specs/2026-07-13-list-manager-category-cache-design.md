# List-manager category cache

## Goal

Avoid downloading and rendering a full cloud list merely to discover its valid
category paths, while never silently choosing a replacement category.

## Scope

Add one public machine interface that returns category paths for a named cloud
list. It maintains an untracked local cache at
`skills/list-manager/tmp/categories.<name>.yaml`.

Each cache document contains the list name, ordered category paths, a
`remaining_uses` countdown, and the configured reset value. A normal category
lookup consumes one use. When the countdown is exhausted, that lookup refreshes
the category paths from cloud storage and resets the countdown. `--refresh`
forces the same refresh.

The initial reset value is 20 lookups.

## User-facing behavior

- For a new entry with no known category, consult the category interface rather
  than reading the full rendered list.
- If several cached paths plausibly match, ask the user to choose; do not infer
  one.
- If a create operation says the selected category no longer exists, refresh
  the category cache once and ask the user to select a matching current path.
- Never retry the write using a guessed replacement.

## Boundaries

The cache contains structure only, never entry titles, descriptions, ids, or
other todo content. It is local disposable state and is ignored by Git. It is
not a personal-preferences file and does not change deadline interpretation.

## Verification

Behavior tests cover first lookup, countdown decrement and refresh at zero,
explicit refresh, cache isolation by list name, and stale-category failure
handling at the LLM-instruction layer. Blueprint sync and the relevant skill
tests must pass.

---
name: list-manager
description: |
  Use when the user asks to show, create, delete, or modify a structured
  personal list or its entries.
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

This skill manages YAML list files stored under `lists/` in cloud storage.

For any operation on an existing list:
1. Invoke the `cloud-files` dependency to download the target file.
2. Run the appropriate local interface in this skill.
3. If the operation succeeded, upload the modified file.
4. If the operation failed, stop and do not upload.

`lists.py` is a local operator only; cloud transport stays in `cloud-files`.
Validation happens before writes. Never bypass it.

## Route by user intent

- Default to `read-beautify` for any human-facing read request: show,
  browse, render, preview, display, "what's on", "what do I have on", or
  similar phrasing where the user wants to see the list.
- Use `read-list` only when the user explicitly asks for raw, YAML,
  machine-readable, or similar underlying structured output.
- List available lists → invoke the `cloud-files` dependency on `lists/`.
- Create a new list → `init-list`.
- Add entries or subentries → `create-entry`.
- Edit entries, check items off, change state, title, description, deadline, or
  location → `update-list`.
- Generate fresh IDs for external orchestration → `generate-id`.
- Migrate a legacy Markdown list into YAML → `migrate-markdown`.
- Delete a list → delete the corresponding file through the `cloud-files`
  dependency.

For exact calling conventions, use `blueprint.yaml` and the script help.
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

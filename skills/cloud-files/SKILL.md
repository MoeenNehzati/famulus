---
name: cloud-files
description: >-
  Use when the user or another skill needs to read from or write to the configured LLM root of a remote. Do not use for local files or remote paths outside that LLM root.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `cloud-files._rtx.interface.lists-delete@1` — Delete a file from cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.lists-delete lists/<path>`
  - Delete list files from cloud storage. Restricted to lists/ directory.
- `cloud-files._rtx.interface.lists-read@1` — Read a file from cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.lists-read lists/<path>`
  - Read list files from cloud storage. Restricted to lists/ directory.
- `cloud-files._rtx.interface.lists-write@1` — Write content (from stdin) to a file in cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.lists-write lists/<path>`
  - Write list files to cloud storage. Restricted to lists/ directory.
- `cloud-files._rtx.interface.plans-delete@1` — Delete a file from cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.plans-delete plans/<path>`
  - Delete plan files from cloud storage. Restricted to plans/ directory.
- `cloud-files._rtx.interface.plans-read@1` — Read a file from cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.plans-read plans/<path>`
  - Read plan files from cloud storage. Restricted to plans/ directory.
- `cloud-files._rtx.interface.plans-write@1` — Write content (from stdin) to a file in cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.plans-write plans/<path>`
  - Write plan files to cloud storage. Restricted to plans/ directory.
- `cloud-files._rtx.interface.write-config@1` — Write ~/.config/cloud-files/config.json with the given remote LLM root. Relocated from install-assistant-tools.
  - `dispatcher --caller-skill cloud-files cloud-files._rtx.interface.write-config --home <dir> [--remote-llm-root <path>] [--dry-run]`
  - Write cloud-files config.json.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.default@1` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: cloud-files

## 0. Boundary

This skill owns Google Drive transport. Other skills should call this skill's
scripts rather than speaking to the Drive API directly.

Install-time config lives at `~/.config/cloud-files/config.json`.
Legacy OAuth credentials live at `~/.config/cloud-files/credentials.json`.

For shared Google setup or Drive reauthorization, first invoke
`connect-google.interface.default`. Its deterministic coordinator creates a
credential file, asks Drive's owner to probe live access, and stores the path
only after verification. Treat only `complete: true` as successful setup; report
an incomplete Drive result and retry through connect-google with the same file.

Existing legacy Drive credentials remain runtime-readable until a verified
credential-file binding replaces them. Do not offer legacy setup as a new route.

## 1. Preapproved LLM-root operations

Use `lists-read`, `lists-write`, `lists-delete` for routine list storage and `plans-read`, `plans-write`, `plans-delete` for plan storage within their respective directories.

Each interface takes a single positional path argument constrained to its directory prefix (`lists/` or `plans/`). Write interfaces read file content from stdin.

## 2. Separately prompted broader reads

A broader read from the Google Drive root is available via a script not registered as a dispatcher interface. It is intentionally not listed in `blueprint.yaml:suggested_permissions`.

If a script exits nonzero, report the visible error and do not infer remote
state beyond what the successful output established.

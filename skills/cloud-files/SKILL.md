---
name: cloud-files
description: |
  Read, write, and delete plain files under a configured Google Drive LLM root
  through skill-owned Python scripts. Use when another skill needs bounded
  cloud-file storage or a separately prompted broader read from the configured
  Drive root.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: external-integrations, storage-and-sync; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `cloud-files.source.gateway -> cloud-files._rtx.interface.ensure-oauth@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.lists-delete@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.lists-read@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.lists-write@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.plans-delete@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.plans-read@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.plans-write@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.setup-oauth@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.use-google-credential@1`
- `cloud-files.source.gateway -> cloud-files._rtx.interface.write-config@1`
- `cloud-files.source.gateway -> connect-google.interface.default@1`

Public Interfaces:
- `cloud-files.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `cloud-files.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: cloud-files

## 0. Boundary

This skill owns Google Drive transport. Other skills should call this skill's
scripts rather than speaking to the Drive API directly.

Install-time config lives at `~/.config/cloud-files/config.json`.
OAuth credentials live at `~/.config/cloud-files/credentials.json`.

For initial Google setup or reauthorization, use
`connect-google.interface.default` to install or reuse the shared Desktop OAuth
client, then return here for Drive authorization. This skill invokes its own
`cloud-files._rtx.interface.setup-oauth` interface with
`--from-json ~/.config/connect-google/client.json` and owns Drive credentials,
verification, and failures.

## 1. Preapproved LLM-root operations

Use `lists-read`, `lists-write`, `lists-delete` for routine list storage and `plans-read`, `plans-write`, `plans-delete` for plan storage within their respective directories.

Each interface takes a single positional path argument constrained to its directory prefix (`lists/` or `plans/`). Write interfaces read file content from stdin.

## 2. Separately prompted broader reads

A broader read from the Google Drive root is available via a script not registered as a dispatcher interface. It is intentionally not listed in `blueprint.yaml:suggested_permissions`.

If a script exits nonzero, report the visible error and do not infer remote
state beyond what the successful output established.

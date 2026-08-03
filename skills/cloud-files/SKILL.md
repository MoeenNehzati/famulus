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
- `cloud-files.source.gateway -> connect-google.interface.default@1`

Public Interfaces:
- `cloud-files.interface.default`
- `cloud-files.interface.ensure-oauth`
- `cloud-files.interface.lists-delete`
- `cloud-files.interface.lists-read`
- `cloud-files.interface.lists-write`
- `cloud-files.interface.plans-delete`
- `cloud-files.interface.plans-read`
- `cloud-files.interface.plans-write`
- `cloud-files.interface.setup-oauth`
- `cloud-files.interface.write-config`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `cloud-files.interface.ensure-oauth` — Check cloud-files OAuth status; print setup guidance or launch browser authorization as needed. Relocated from install-assistant-tools — invoke directly (caller-skill cloud-files) as part of connecting remotes.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.ensure-oauth --home <dir> [--dry-run]`
  - Check OAuth status and guide setup for cloud-files.
- `cloud-files.interface.lists-delete` — Delete a file from cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.lists-delete lists/<path>`
  - Delete list files from cloud storage. Restricted to lists/ directory.
- `cloud-files.interface.lists-read` — Read a file from cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.lists-read lists/<path>`
  - Read list files from cloud storage. Restricted to lists/ directory.
- `cloud-files.interface.lists-write` — Write content (from stdin) to a file in cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.lists-write lists/<path>`
  - Write list files to cloud storage. Restricted to lists/ directory.
- `cloud-files.interface.plans-delete` — Delete a file from cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.plans-delete plans/<path>`
  - Delete plan files from cloud storage. Restricted to plans/ directory.
- `cloud-files.interface.plans-read` — Read a file from cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.plans-read plans/<path>`
  - Read plan files from cloud storage. Restricted to plans/ directory.
- `cloud-files.interface.plans-write` — Write content (from stdin) to a file in cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.plans-write plans/<path>`
  - Write plan files to cloud storage. Restricted to plans/ directory.
- `cloud-files.interface.setup-oauth` — Run one-time OAuth2 setup for Google Drive access.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.setup-oauth [--from-json <client_json_path>] [--client-id <id> --client-secret <secret>] [--port <port>]`
  - OAuth setup for Google Drive access.
- `cloud-files.interface.write-config` — Write ~/.config/cloud-files/config.json with the given remote LLM root. Relocated from install-assistant-tools.
  - `dispatcher --caller-skill cloud-files cloud-files.interface.write-config --home <dir> [--remote-llm-root <path>] [--dry-run]`
  - Write cloud-files config.json.

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
`cloud-files.interface.setup-oauth` interface with
`--from-json ~/.config/connect-google/client.json` and owns Drive credentials,
verification, and failures.

## 1. Preapproved LLM-root operations

Use `lists-read`, `lists-write`, `lists-delete` for routine list storage and `plans-read`, `plans-write`, `plans-delete` for plan storage within their respective directories.

Each interface takes a single positional path argument constrained to its directory prefix (`lists/` or `plans/`). Write interfaces read file content from stdin.

## 2. Separately prompted broader reads

A broader read from the Google Drive root is available via a script not registered as a dispatcher interface. It is intentionally not listed in `blueprint.yaml:suggested_permissions`.

If a script exits nonzero, report the visible error and do not infer remote
state beyond what the successful output established.

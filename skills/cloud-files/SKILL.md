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

Category: system-assistant

Dependencies: none

Interface Version: 1

Exported Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Machine Interfaces:

Use the installed `dispatcher` command for this skill's machine interfaces:
- `ensure-oauth` — Check cloud-files OAuth status; print setup guidance or launch browser authorization as needed. Relocated from install-assistant-tools — invoke directly (caller-skill cloud-files) as part of connecting remotes.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.ensure-oauth ensure-oauth --home <dir> [--dry-run]`
  - Check OAuth status and guide setup for cloud-files.
- `lists-delete` — Delete a file from cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.lists-delete lists/<path>`
  - Delete list files from cloud storage. Restricted to lists/ directory.
- `lists-read` — Read a file from cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.lists-read lists/<path>`
  - Read list files from cloud storage. Restricted to lists/ directory.
- `lists-write` — Write content (from stdin) to a file in cloud storage under the lists/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.lists-write lists/<path>`
  - Write list files to cloud storage. Restricted to lists/ directory.
- `plans-delete` — Delete a file from cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.plans-delete plans/<path>`
  - Delete plan files from cloud storage. Restricted to plans/ directory.
- `plans-read` — Read a file from cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.plans-read plans/<path>`
  - Read plan files from cloud storage. Restricted to plans/ directory.
- `plans-write` — Write content (from stdin) to a file in cloud storage under the plans/ directory.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.plans-write plans/<path>`
  - Write plan files to cloud storage. Restricted to plans/ directory.
- `setup-oauth` — Run one-time OAuth2 setup for Google Drive access.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.setup-oauth [--from-json <client_json_path>] [--client-id <id> --client-secret <secret>] [--port <port>]`
  - OAuth setup for Google Drive access.
- `write-config` — Write ~/.config/cloud-files/config.json with the given remote LLM root. Relocated from install-assistant-tools.
  - `dispatcher --caller-skill cloud-files cloud-files.machine.write-config write-config --home <dir> [--remote-llm-root <path>] [--dry-run]`
  - Write cloud-files config.json.

Owner-Facing LLM Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `default` — Primary LLM-facing skill instructions.
  - binding: skill file `SKILL.md`
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: cloud-files

## 0. Boundary

This skill owns Google Drive transport. Other skills should call this skill's
scripts rather than speaking to the Drive API directly.

Install-time config lives at `~/.config/cloud-files/config.json`.
OAuth credentials live at `~/.config/cloud-files/credentials.json`.

If credentials are missing, place your Google OAuth client JSON at `~/.config/cloud-files/client.json` and run the one-time `setup-oauth` interface. This setup step is intentionally outside the dispatcher's normal skill access control.

If the OAuth app stays in Google Cloud **Testing**, Google may expire refresh tokens after about 7 days. If you do not want repeated re-authorization, use **OAuth -> Audience** and click **Publish app** / move the app to **In production** before running the setup.

## 1. Preapproved LLM-root operations

Use `lists-read`, `lists-write`, `lists-delete` for routine list storage and `plans-read`, `plans-write`, `plans-delete` for plan storage within their respective directories.

Each interface takes a single positional path argument constrained to its directory prefix (`lists/` or `plans/`). Write interfaces read file content from stdin.

## 2. Separately prompted broader reads

A broader read from the Google Drive root is available via a script not registered as a dispatcher interface. It is intentionally not listed in `permissions.json`.

If a script exits nonzero, report the visible error and do not infer remote
state beyond what the successful output established.

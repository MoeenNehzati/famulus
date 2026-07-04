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

Category: automation

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Owner-Facing Script Interfaces:

Use the installed `dispatcher` command for this skill's script interfaces:
- `lists-delete`
  - `dispatcher --caller-skill cloud-files cloud-files lists-delete ...`
  - Delete list files from cloud storage. Restricted to lists/ directory.
- `lists-read`
  - `dispatcher --caller-skill cloud-files cloud-files lists-read ...`
  - Read list files from cloud storage. Restricted to lists/ directory.
- `lists-write`
  - `dispatcher --caller-skill cloud-files cloud-files lists-write ...`
  - Write list files to cloud storage. Restricted to lists/ directory.
- `plans-delete`
  - `dispatcher --caller-skill cloud-files cloud-files plans-delete ...`
  - Delete plan files from cloud storage. Restricted to plans/ directory.
- `plans-read`
  - `dispatcher --caller-skill cloud-files cloud-files plans-read ...`
  - Read plan files from cloud storage. Restricted to plans/ directory.
- `plans-write`
  - `dispatcher --caller-skill cloud-files cloud-files plans-write ...`
  - Write plan files to cloud storage. Restricted to plans/ directory.
- `setup-oauth`
  - `dispatcher --caller-skill cloud-files cloud-files setup-oauth ...`
  - OAuth setup for Google Drive access.
<!-- END BLUEPRINT INTERFACES -->
When this skill is used, begin with:

Skill: cloud-files

## 0. Boundary

This skill owns Google Drive transport. Other skills should call this skill's
scripts rather than speaking to the Drive API directly.

Install-time config lives at `~/.config/cloud-files/config.json`.
OAuth credentials live at `~/.config/cloud-files/credentials.json`.

If credentials are missing, place your Google OAuth client JSON at `~/.config/cloud-files/client.json` and run `scripts/setup_oauth.py`. This one-time setup is intentionally outside `permissions.json`.

If the OAuth app stays in Google Cloud **Testing**, Google may expire refresh tokens after about 7 days. If you do not want repeated re-authorization, use **OAuth -> Audience** and click **Publish app** / move the app to **In production** before running `scripts/setup_oauth.py`.

## 1. Preapproved LLM-root operations

Use these scripts for routine LLM storage:

```bash
scripts/cp_llm.py <src>... <dst>
scripts/ls_llm.py [llm:pattern...]
scripts/rm_llm.py llm:pattern...
```

`cp_llm.py` is the canonical transfer interface. Exactly one side must be
remote and use the `llm:` prefix:

```bash
scripts/cp_llm.py llm:lists/todo.md /tmp/todo.md
scripts/cp_llm.py /tmp/todo.md llm:lists/todo.md
```

`ls_llm.py` and `rm_llm.py` interpret their `llm:` arguments as remote paths or
remote glob patterns under the configured `remote_llm_root`.

Legacy `read_llm_file.py`, `write_llm_file.py`, and `delete_llm_file.py`
remain in the skill directory for compatibility, but `cp_llm.py`, `ls_llm.py`,
and `rm_llm.py` are the preferred interface.

## 2. Separately prompted broader reads

For a broader read from Google Drive root, use:

```bash
scripts/read_remote.py [--list] [path]
```

It is intentionally not listed in `permissions.json`.

If a script exits nonzero, report the visible error and do not infer remote
state beyond what the successful output established.

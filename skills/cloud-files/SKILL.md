---
name: cloud-files
description: |
  Read, write, and delete plain files under a configured Google Drive LLM root
  through skill-owned Python scripts. Use when another skill needs bounded
  cloud-file storage or a separately prompted broader read from the configured
  Drive root.
---

When this skill is used, begin with:

Skill: cloud-files

Category: automation

Dependencies: none

## 0. Boundary

This skill owns Google Drive transport. Other skills should call this skill's
scripts rather than speaking to the Drive API directly.

Install-time config lives at `~/.config/cloud-files/config.json`.
OAuth credentials live at `~/.config/cloud-files/credentials.json`.

If credentials are missing, place your Google OAuth client JSON at `~/.config/cloud-files/client.json` and run `scripts/setup_oauth.py`. This one-time setup is intentionally outside `permissions.json`.

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

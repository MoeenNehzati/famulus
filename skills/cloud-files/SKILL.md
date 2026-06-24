---
name: cloud-files
description: |
  Read, list, write, and delete plain files under a configured cloud remote's
  `assistant/` prefix through a skill-owned script. Use when another skill
  needs narrow cloud-file storage without exposing arbitrary remote paths.
---

When this skill is used, begin with:

Skill: cloud-files

Category: automation

## 0. Boundary

This skill owns cloud file transport. Other skills should not mention or call
the underlying storage tool. They should ask this skill to operate on files
under the assistant storage prefix.

The script only permits paths under:

```text
<remote>:assistant/
```

It rejects absolute paths, empty paths for file operations, `..`, and remote
names containing path separators or `:`.

## 1. Commands

Use only this script:

```bash
scripts/cloud-files.sh <operation> [path]
```

Operations:

- `list [path]`: list files/directories under `assistant/<path>`; if `path`
  is omitted, list `assistant/`.
- `read <path>`: print `assistant/<path>` to stdout.
- `write <path>`: read stdin and overwrite `assistant/<path>`.
- `delete <path>`: delete `assistant/<path>`.

Environment:

- `CLOUD_FILES_REMOTE`: remote name, default `GDrive`.
- `CLOUD_FILES_TIMEOUT_SECONDS`: timeout for each remote operation, default
  `45`.

If the script exits nonzero, the remote state is unknown. Report the visible
error and do not infer that a file is missing or empty unless the script
successfully returned empty output.

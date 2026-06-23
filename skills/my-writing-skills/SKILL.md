---
name: my-writing-skills
description: Use when creating or editing a personal skill in the shared skills directory
---

## Git Safety

Before editing any skill file, verify the repo containing that file is on a
named branch (`git symbolic-ref HEAD` from the repo root). If it fails, check
out a named branch first. The pre-commit hook will block the eventual commit,
but catching this before editing avoids doing work that can't land.

@./../../references/skill-guidelines.md

**REQUIRED — NON-NEGOTIABLE:** Invoke `superpowers:writing-skills` and read it fully before proceeding. All upstream rules apply; the personal conventions in `skill-guidelines.md` are added on top.

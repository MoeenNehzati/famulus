---
name: git-workflow
description: Use when working in any git repo — committing, staging, checking branch state, or deciding whether to suggest a commit. Also use before editing files in any repo to verify branch safety.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: skill-making-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->

## Branch safety (always check first)

Before editing files in any repo, verify it is on a named branch:

```bash
git symbolic-ref HEAD
```

If this fails, the repo is in detached HEAD — check out a named branch before doing anything. Do not edit and then discover the work can't land.

**At session start:** run this in both the skills repo and the CWD repo (if any). If either is detached, check out the default branch first.

## Commit rules

- Never create commits, amend, or push unless explicitly asked.
- When a stable checkpoint is reached (completed subsection, resolved issue, passing tests, finished feature), note that it may be a good time to commit — but do not act without approval.
- When approved: help with staging specific files, drafting the commit message, and exact steps.

## Commit hygiene

- Stage specific files by name; avoid `git add -A` or `git add .` (risk of including `.env`, credentials, or large binaries).
- Never use `--no-verify` or `--no-gpg-sign` unless explicitly asked.
- Never force-push to `main`/`master`; warn if asked.
- Prefer new commits over amending, especially after a hook failure.

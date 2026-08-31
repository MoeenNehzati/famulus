---
name: git-workflow
description: >-
  Use when a task will edit a Git repository or the user asks to inspect or change Git state. Do not use solely because a read-only task happens inside a repository.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Used Interfaces: none
<!-- END BLUEPRINT INTERFACES -->

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

## Change ownership

- Unless the user explicitly says otherwise, interpret "changes" as the changes made in the current assistant session.
- Before staging, committing, restoring, stashing, or otherwise modifying git state, distinguish current-session changes from pre-existing or unrelated dirty work.
- Tell the user when unrelated dirty state exists, including the affected paths or areas at a useful level of detail.
- Do not stage, commit, restore, revert, stash, or otherwise touch unrelated dirty state unless the user explicitly asks for that scope.
- If ownership is ambiguous, ask before acting on the ambiguous paths.

## Commit hygiene

- Stage specific files by name; avoid `git add -A` or `git add .` (risk of including `.env`, credentials, or large binaries).
- Never use `--no-verify` or `--no-gpg-sign` unless explicitly asked.
- Never force-push to `main`/`master`; warn if asked.
- Prefer new commits over amending, especially after a hook failure.

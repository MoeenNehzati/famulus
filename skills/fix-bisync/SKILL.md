---
name: fix-bisync
description: |
  Diagnose rclone bisync failures by inspecting wrapper configuration, logs, state files, and filters; identify the first real failure and any concrete culprit files; propose prevention options; with user approval implement preventive changes; then either provide the repair command or run it depending on the user's preference.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Category: system-assistant

Dependencies: none

Interface Version: 1

Exported Script Interfaces: none
<!-- END BLUEPRINT CONTRACT -->
When this skill is used, begin with:

Skill: fix-bisync

## 1. Goal

Handle a bisync failure in four phases:

1. diagnose the current failure precisely
2. identify any concrete culprit files or other root causes
3. discuss prevention options and implement only approved ones
4. either print the repair command or run it, depending on the user's preference

The main job is to distinguish the first real failure from the later fallout.
A missing bisync state file is often an aftermath, not the original trigger.

## 2. Scope

Use this skill when the user wants to:
- understand why an rclone bisync job failed
- know which files are triggering bisync corruption or resync requests
- harden bisync against repeated failures
- decide whether to run a repair or just see the repair command

Do not use this skill when:
- the task is a generic rclone tutorial unrelated to a live bisync failure
- the user only wants a one-off sync command and not diagnosis

## 3. Investigation workflow

### Step 1: Resolve the active bisync job

Identify the effective job inputs first. Prefer reading the wrapper script before guessing.
Check values such as:
- `JOB_NAME`
- `REMOTE`
- `LOCAL`
- `WORKDIR`
- `LOGFILE`
- `FILTERS`
- any resync or lock settings

Common places to inspect:
- `rclone_bisync_*.sh`
- `rclone_bisync_job.sh`
- `bisync-filters.txt`

### Step 2: Inspect the current failure

Inspect the log and state files for the active job.
Look for the most recent relevant entries first, then walk backward to the first real fault.

Distinguish these cases explicitly:
- missing bisync state files
- `Must run --resync to recover`
- corrupted transfer or checksum/hash mismatch
- auth or remote listing failures
- path/workdir drift between runs
- transient build/runtime files changing during copy

If the visible failure is only:
- `cannot find prior Path1 or Path2 listings`
- missing `.path1.lst` / `.path2.lst`

then keep searching for the earlier event that invalidated or removed those state files.

State files live in `WORKDIR` as `${REMOTE//:/_}..${LOCAL_with_slashes_as_underscores}.path1.lst`
/ `.path2.lst` (plus `-err` / `-old` / `-dry` / `-new` variants left behind by aborted or dry
runs). Check these files and their mtimes directly rather than inferring state from the log
alone - it's a fast way to confirm whether a job is currently locked out vs. already healthy.

A `Bisync aborted. Must run --resync to recover.` line does not always mean the job is
*currently* locked out: if the wrapper retries (e.g. falls back to a plain `run_bisync` after a
failed `--resync`), a later entry in the same run may show `Bisync successful`. Read forward
from a critical error, not just backward, before concluding the job is stuck.

### Step 3: Flag culprit files

If a concrete file caused the failure, report it explicitly.
Prefer the exact path from the log and classify it, for example:
- volatile runtime database or WAL file
- build artifact such as `.log`, `.aux`, `.pdf`, `.fdb_latexmk`, `.fls`
- editor or cache file
- large generated output
- auth/config issue rather than a content file
- transient temp/partial file from another actively-running tool (e.g. `.foo.<hash>.partial`,
  editor swap files, AI-agent session files) - not just known build-artifact globs

If multiple files are implicated, separate:
- first confirmed culprit
- later correlated files

Do not overstate causality when the log only supports correlation.

Dynamic mtime-based excludes (e.g. "skip files modified in the last N minutes") only protect
against files that were *already* unstable before the run started - they do not protect against
a file being written, replaced, or renamed during the run's own transfer window. Treat any
"corrupted on transfer: md5 hashes differ" or "can't move object" error on an unfamiliar temp
file as this same TOCTOU class, even if it doesn't match a previously-seen glob, and even if a
previous incident was already "fixed" by excluding a different glob.

### Step 4: Propose prevention options

After diagnosis, propose prevention options ranked from narrowest to broadest.
Examples:
- exclude a specific volatile file or file family
- route build artifacts to a non-synced output directory
- stop a tool from writing into the synced tree
- adjust bisync filters
- change wrapper behavior or logging
- run a one-time resync after a critical failure
- shell-startup (or cron) health check that warns if `.path1.lst`/`.path2.lst` are missing, so a
  silent `AUTO_RESYNC=0` lockout isn't reported only via easy-to-ignore hourly notifications
- rclone's documented "set and forget" combo `--resilient --recover --max-lock 2m
  --conflict-resolve newer` for general hygiene - but note its limits: `--resilient` only
  bypasses access-test failures, missing listings, and filter-change detection, NOT file
  copy/move critical errors; `--recover` handles ungraceful interruptions (crashes), not clean
  critical-error aborts. Neither fully prevents the TOCTOU class above, though a wrapper-level
  retry-without-`--resync` fallback can still self-heal after one.

For each option, state briefly:
- what it fixes
- what it does not fix
- likely side effects
- whether it is narrow, moderate, or broad in scope

Do not implement preventive changes until the user approves.

### Step 5: Implement only approved prevention

Once the user approves a prevention strategy:
- make the minimal coherent change set
- avoid unrelated cleanup
- verify the changed configuration or filters
- explain any residual risk briefly

### Step 6: Repair the bisync state

After prevention is discussed, determine the appropriate repair command.
Derive it from the actual wrapper/config rather than guessing.
Typical example:
`RCLONE_AUTO_RESYNC=1 bash /path/to/rclone_bisync_<job>.sh`

**Check `--resync-mode` before running `--resync`.** Plain `--resync` is equivalent to
`--resync-mode path1`: Path1 unconditionally wins any conflict, silently overwriting Path2. If
LOCAL is the actively-edited working copy and Path1 is the remote (which may be stale after a
long lockout), the *wrapper's own suggested repair command* can overwrite weeks of local edits.

Before running a real `--resync`:
1. Identify which side is the "working copy" (usually LOCAL) vs. which side may be stale (often
   the remote, if it went unsynced during the lockout).
2. Run `--resync --dry-run` once with the default (`path1`) and once with `--resync-mode newer`,
   and diff the proposed transfers. Look specifically for cases where the default would
   overwrite a file that has a *more recent* mtime on the other side.
3. If the default mode would clobber actively-edited files, use `--resync-mode newer` (or
   `--resync-mode path2` if the working copy is fixed and known) and say so explicitly in the
   repair command - do not default to plain `--resync` just because that's what the error
   message or wrapper suggests.

If the user wants the command only:
- print the exact command
- explain in one sentence what it will do

If the user wants you to run it:
- restate the exact command first
- then run it
- summarize the result and any remaining errors

If the user's preference is unclear, default to showing the command rather than running it.

After a real repair, verify empirically rather than trusting the exit code alone:
- confirm `.path1.lst`/`.path2.lst` now exist with fresh mtimes
- check the log tail for `Bisync successful`, and for any `ERROR`/`Critical` lines *after* that
  point
- for any files identified in step 2/3 as at-risk, confirm the local (working-copy) content won
  by comparing `rclone md5sum` on the remote vs. local `md5sum`
- distinguish newly created `.conflict1`/`.conflict2` files (mtime within the repair's time
  window) from stale conflict files left over from earlier incidents - don't attribute old
  cruft to the current repair

## 4. Safety rules

Do not:
- delete bisync state files manually unless the user explicitly asks
- auto-run resync just because the log suggests it
- patch filters, wrapper scripts, or build settings without approval
- call a later symptom the root cause without checking earlier log entries

When a command could materially change remote/local state, make clear whether you are:
- diagnosing only
- proposing a repair command
- actually running the repair

## 5. Reporting format

Start with:
- `Mode: Explore`
- `Skill: fix-bisync`

Then use only the sections that are needed, for example:
- `Current failure`
- `Root cause`
- `Confirmed culprit`
- `Other suspects`
- `Why this recurs`
- `Prevention options`
- `Recommended prevention`
- `Repair command`
- `Repair result`
- `New issue discovered` (if the repair run itself surfaces a different/new culprit or error)

If the user asked a narrow question such as “what file is causing it?”, answer that first.

Keep the report concise and specific.

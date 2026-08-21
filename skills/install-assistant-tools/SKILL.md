---
name: install-assistant-tools
description: >-
  Use when the user asks to install, update, propagate, or repair the `assistant`, `collab`, `coauthor`, or workspace helper commands, including missing or stale launchers and shell integration. Do not use for unrelated software or plugin installation.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: assistant-installation, system-maintenance; visibility: listed
Activation: user-request; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-dev-link@2`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-install@2`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-launchers@3`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-scaffold@2`

Public Interfaces:
- `install-assistant-tools.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `install-assistant-tools.interface.default` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Install Assistant Tools

When this skill is used, begin with:

Skill: install-assistant-tools

Use capabilities declared for the current platform. The primary installer and
assistant commands are portable; the workspace helper is unavailable where
`tmux` is absent.

## Workflow

### 1. Choose the installation mode

Before running anything, ask explicitly:

> "Do you want development mode? This wires supported assistant configuration
> to a live repository checkout so skill and hook edits take effect
> immediately, instead of using the static plugin install."

Development mode is an explicit user choice. Never infer it from filesystem
probes. If selected, ask for the live repository path; do not substitute the
checkout from which this skill happens to be running. Static plugin mode needs
no separate repository path.

### 2. Run Phase 1 (installation)

Use `install-assistant-tools._rtx.interface.scripts-install`. Before invoking it,
collect the chosen mode, any required repository path, and the launchers the
user wants. Do not preselect optional helper commands; the orchestrator may
still ensure the required baseline command declared by its interface contract.
On an unfamiliar machine, invoke its documented dry-run mode first and show the
resulting plan.

If the blueprint catalog contains optional modules, the installer
lists each module, its affected Python packages, and package-index size
estimates when reliable wheel or source-archive metadata is available. Missing
metadata is shown as unavailable; it is never guessed. Select module IDs at
that prompt to install their complete dependency closure. Leaving the
selection blank installs only core modules. Experimental maturity and optional
installation tier are independent: experimental modules may be core, and
stable modules may be optional. Estimates use cached package-index records
when present and do not require network access merely to display the prompt.

The interface runs these phases in order:

1. universal scaffold, in every mode;
2. live-repository integration, only in development mode;
3. selected helper commands.

Use the narrower public interfaces only for targeted repairs:

| Repair | Interface |
|---|---|
| shared command floor and search path | `install-assistant-tools._rtx.interface.scripts-scaffold` |
| development-mode repository integration | `install-assistant-tools._rtx.interface.scripts-dev-link` |
| selected helper commands and profiles | `install-assistant-tools._rtx.interface.scripts-launchers` |

Do not invoke the development-mode repair during a static plugin install.

Interpret the scaffold capability report before continuing. A failed shared
dispatcher floor is fatal: report the named capability, affected workflows,
and reason, and do not proceed to later phases. A platform-scoped capability
reported as unsupported is non-fatal when the shared floor succeeded. A
dry-run reports the same capability status but must not write.

### 3. Phase 2 — connect remotes, then offer recurring automation

After Phase 1 completes, ask whether the user wants to connect their cloud
storage, calendar, and email accounts now. Explain that completing this in the
same session unlocks recurring email triage and daily planning. If yes, walk
through each account's own setup flow. Each service skill owns its credential
guidance; do not duplicate it here.

Then, whether or not remotes were connected now, ask whether the user wants
recurring triage and daily planning set up. If yes, hand off entirely to that
automation workflow, which owns prerequisite setup.

## Conflict and overwrite policy

- For ordinary link destinations, and for the skills-directory destination
  under the whole-directory strategy, a symlink to the desired target is left
  in place and reported as already linked; a symlink to a different target is
  replaced. There is no interactive prompt.
- Under the whole-directory strategy, an existing real skills directory is
  migrated only when it can be preserved safely: redundant links are removed,
  unique local entries move into the canonical tree, and preserved entries are
  excluded from version control when possible. A conflicting entry already in
  the canonical tree leaves the directory in place for manual resolution.
- Under the per-entry strategy, a legacy skills-directory symlink to the
  canonical tree is replaced by a real directory containing per-entry links. A
  skills-directory symlink to a foreign target is left in place and reported as
  skipped.
- Under the per-entry strategy, an existing real skills directory is preserved
  and repository skills are linked individually. Existing real conflicting
  entries are left untouched and reported as skipped.
- For other destinations, a real file or directory is never overwritten. It is
  left in place and reported as skipped.
- A missing source is reported as skipped.
- If the installer reports a configuration home as an unsafe symlink boundary,
  accept the warning and skip only the links named by that report.

Do not promise a backup, merge, replace-or-keep menu, rollback, or additional
conflict-policy options.

## Completion and failure handling

After installation, follow the environment-reload instruction reported by the
installer. Verify that each installed command is discoverable and that its help
request exits successfully. Skip a platform-scoped helper when the capability
report says it is unsupported.

If a command is unavailable, the shared runtime is incomplete, or another step
fails:

1. report the exact error and the failed phase;
2. ask, "Would you like me to adapt the implementation for this platform?";
3. change implementation only after the user agrees, and then make the minimum
   verified change.

## tmux workspace pane controls

Each new `tw` session installs prefix-required bindings for temporary workspace
reconfiguration:

- `prefix+e` opens a stateless scratch shell popup.
- `prefix+b` breaks the current pane into its own window without following it
  and pushes its source window, side, and approximate size onto a session-local
  LIFO stack.
- `prefix+j` restores the most recently broken pane, preferring its original
  window and falling back safely when that window no longer exists.
- `prefix+@` performs tmux's raw horizontal join without using the saved stack.
- `prefix+m` toggles a compact `btop` monitor pane on the right.
- `prefix+h` opens searchable binding help when `fzf` is available and plain
  help otherwise.

The existing `prefix+!` tmux break-and-follow binding remains unchanged. The
`llm` template also starts `btop` in its dedicated `logs` window.

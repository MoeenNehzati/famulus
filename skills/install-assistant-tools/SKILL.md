---
name: install-assistant-tools
description: Use when installing, repairing, updating, or propagating the assistant, collab, coauthor, or workspace helper commands on a machine, or when their launcher or shell integration is missing or stale.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: assistant-installation, system-maintenance; visibility: listed
Activation: user-request; persistent modifier: no

Skill Version: 3

Uses Interfaces: none

Public Interfaces:
- `install-assistant-tools.interface.default`
- `install-assistant-tools.interface.scripts-dev-link`
- `install-assistant-tools.interface.scripts-install`
- `install-assistant-tools.interface.scripts-launchers`
- `install-assistant-tools.interface.scripts-scaffold`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `install-assistant-tools.interface.scripts-dev-link` — Symlink Claude/Codex config dirs to a live repo checkout, register dev-mode hooks, set git hooksPath, export $AI. Requires an explicit repo path.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools.interface.scripts-dev-link --repo-root DIR [--no-claude] [--no-codex] [--home DIR] [--claude-home DIR] [--codex-home DIR] [--shell-rc FILE] [--dry-run]`
- `install-assistant-tools.interface.scripts-install` — Phase-1 orchestrator: asks the dev-mode question, then runs scaffold, optionally dev-link, then launchers.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools.interface.scripts-install [--dry-run] [--non-interactive] [--dev-mode|--no-dev-mode] [--repo-path DIR] [--agents LIST] [--default-llm {claude,codex}] [--home DIR] [--bin-dir DIR] [--shell-rc FILE] [--codex-home DIR] [--claude-home DIR]`
- `install-assistant-tools.interface.scripts-launchers` — Install per-agent bin launcher, profile config, worker dir, and ASSISTANT_DEFAULT for the given agents. Direct invocation of this interface installs exactly the --agents selection; when this launcher installation runs as part of the phase-entry orchestrator, assistant is additionally forced into the installed set regardless of selection, because it is a required invoke-skill prerequisite (feedback item 18) and is not user-selectable. Worker directories are created under the platform Famulus state dir in plugin mode (--mode plugin, the default plugin-cache checkout is a public/immutable tree) or under <repo-root>/workers in development mode (--mode development, an explicit live checkout).
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools.interface.scripts-launchers --repo-root DIR --agents LIST [--home DIR] [--bin-dir DIR] [--codex-home DIR] [--claude-home DIR] [--shell-rc FILE] [--default-llm {claude,codex}] [--mode {development,plugin}] [--dry-run]`
- `install-assistant-tools.interface.scripts-scaffold` — Install the dispatcher + invoke-skill launchers and put the bin dir on PATH. Universal floor, mode-independent.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools.interface.scripts-scaffold --repo-root DIR [--home DIR] [--bin-dir DIR] [--shell-rc FILE] [--dry-run]`

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

Use `install-assistant-tools.interface.scripts-install`. Before invoking it,
collect the chosen mode, any required repository path, and the launchers the
user wants. Do not preselect optional helper commands; the orchestrator may
still ensure the required baseline command declared by its interface contract.
On an unfamiliar machine, invoke its documented dry-run mode first and show the
resulting plan.

The interface runs these phases in order:

1. universal scaffold, in every mode;
2. live-repository integration, only in development mode;
3. selected helper commands.

Use the narrower public interfaces only for targeted repairs:

| Repair | Interface |
|---|---|
| shared command floor and search path | `install-assistant-tools.interface.scripts-scaffold` |
| development-mode repository integration | `install-assistant-tools.interface.scripts-dev-link` |
| selected helper commands and profiles | `install-assistant-tools.interface.scripts-launchers` |

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

---
name: install-assistant-tools
description: >-
  Use when the user asks to install, update, propagate, or repair the `assistant`, `collab`, `coauthor`, or workspace helper commands, including missing or stale launchers and shell integration. Do not use for unrelated software or plugin installation.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `install-assistant-tools._rtx.interface.scripts-dev-link@2` — Project live checkout skills and hooks into isolated context homes, install milestone and agent-timeline when referenced, and set local git hooksPath.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-dev-link --repo-root DIR [--no-claude] [--no-codex] [--home DIR] [--claude-home DIR] [--codex-home DIR] [--shell-rc FILE] [--dry-run]`
- `install-assistant-tools._rtx.interface.scripts-doctor@1` — Diagnose one explicit context; mode is never inferred from cwd and no state is mutated.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-doctor --mode {standard,development} [--checkout ABSOLUTE_PATH] [--home DIR] [--json]`
- `install-assistant-tools._rtx.interface.scripts-install@2` — Choose mode, confirm choices, apply once, diagnose the same context, and explain optional next steps without invoking them.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-install [--dry-run] [--non-interactive --yes] [--dev-mode --repo-path DIR|--no-dev-mode] [--agents LIST] [--default-llm {claude,codex}] [--optional-modules LIST] [--home DIR] [--bin-dir DIR] [--shell-rc FILE] [--codex-home DIR] [--claude-home DIR]`
- `install-assistant-tools._rtx.interface.scripts-launchers@3` — Install per-agent bin launcher, profile config, worker dir, and durable launchers.json backend selection for the given agents. Direct invocation of this interface installs exactly the --agents selection; tw selects one complete tmux-workspace, tw, tw-break, tw-join, tw-monitor, and tw-help bundle. When this runs as part of the five-stage apply orchestrator, assistant is additionally forced into the installed set regardless of selection, because it is a required invoke-skill prerequisite (feedback item 18) and is not user-selectable. background_run is installed with invoke-skill. Worker directories are created under the platform Famulus state dir in standard mode or under the selected isolated context in development mode (--mode development, an explicit live checkout).
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-launchers --repo-root DIR --agents LIST [--home DIR] [--bin-dir DIR] [--codex-home DIR] [--claude-home DIR] [--shell-rc FILE] [--default-llm {claude,codex}] [--mode {development,plugin}] [--dry-run]`
- `install-assistant-tools._rtx.interface.scripts-scaffold@2` — Install the shared launcher floor using an exact context; persist PATH only for standard mode.
  - `dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-scaffold --repo-root DIR [--home DIR] [--bin-dir DIR] [--shell-rc FILE] [--dry-run]`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `connect-google.interface.default@1` — Route Google OAuth-client preparation according to whether a valid Desktop client is already installed.
- `recurring-tasks.interface.default@1` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Install Assistant Tools

Begin by reporting `Skill: install-assistant-tools`.

## Apply workflow

Use `scripts-install` for every fresh install, repeat, update, or repair. It
publishes exactly five stages:

1. Choose `standard` or `development`. Never infer a context from the working
   directory. Development requires an explicit existing checkout.
2. Confirm the resolved source, state, runtime, command directory, isolated
   homes, backend, helpers, and optional modules. A dry run stops here without
   effects; unattended mutation requires `--yes`.
3. Apply once to the resolved context: candidate runtime, shared command floor,
   development-only projections, and selected helpers. Required failure stops
   later effects. Repeating this stage reconciles the same context.
4. Diagnose that same context and report pointer/source/runtime consistency,
   command origins, launcher configuration, manifest health, and recurring
   registration summary.
5. Explain that `connect-google` connects selected Google services and
   `recurring-tasks` creates and manages recurring AI jobs. Show the user how
   to invoke each skill, but do not invoke either or make installation success
   depend on them.

Do not preselect optional helpers. If optional modules are available, show
their module IDs, affected packages, and cached package-index size estimates.
Mark missing estimates unavailable; never guess. Core versus optional and
stable versus experimental are independent classifications.

## Context rules

Standard installation uses platform Famulus roots and persists only the
managed command-directory search-path entry. Development activation keeps
runtime, state, assistant homes, jobs, logs, and projections under the selected
checkout's `.famulus/` tree. The checkout itself is not an assistant home.

Development activation is not a sandbox. State that the launched assistant
retains its host-granted filesystem, network, subprocess, and approval
authority, and refuse to present an untrusted checkout as isolated execution.

Installed commands and development adapters locate the active runtime through
their self-locating resolver and the selected context's `current.json`. Do not
depend on the current directory or original clone location. Do not instruct
the user to persist `AI`, `FAMULUS_REPO_ROOT`, or `ASSISTANT_LOGS`.

Backend selection is durable in `launchers.json`. Treat
`ASSISTANT_DEFAULT` only as a process-local override and never as installation
state.

Managed assistants receive exactly these writable access roots from the
selected context: `assistant_logs_root`, `recurring_config_root`,
`recurring_state_root`, `email_triage_state_root`, `list_manager_lock_root`,
`list_manager_cache_root`, and `llm_wakeup_root`. Treat
`recurring_config_root` as writable scheduled-command authority: secrets embedded in job strings may be exposed. Use indirect credential references instead of embedding credentials in job strings.
The installer does not inspect job strings for secrets. It persists these roots
to Codex user configuration and Claude user settings; treat IDE/app enforcement
as unverified until its dedicated qualification runs.

## Diagnosis and recovery

Use the declared read-only diagnostic interface. Pass `--mode standard`, or
`--mode development --checkout <absolute-path>`, and optionally `--json`.
Never infer mode and never mutate state during diagnosis.

If the active source is missing, report the exact missing source, restore the
package or checkout, then use `scripts-install` again with the same explicit
context. Do not repoint the runtime or native scheduler entries by hand.

Use narrower interfaces only for a demonstrated targeted repair:

| Repair | Interface |
| --- | --- |
| shared command floor and search path | `scripts-scaffold` |
| development repository integration | `scripts-dev-link` |
| selected helper commands and profiles | `scripts-launchers` |
| read-only context diagnosis | declared diagnostic interface |

Do not run development repair for a standard installation.

Before replaying uninstall or purge artifacts, the installer automatically
delegates exact-context registration, sentinel, and owner teardown to the
recurring runtime. It must stop before artifact replay when native inventory or
teardown verification is uncertain. Ordinary uninstall removes only unchanged
manifest-owned artifacts. Purge additionally removes exact-identity immutable
runtime/bootstrap, launcher, and generated profile artifacts. Neither operation
recursively deletes mutable configuration, credentials, or recurring history,
revokes remote credentials, or deletes arbitrary mutable user data.

## Completion

After apply, follow the printed environment-reload instruction. Verify every
installed command resolves from the selected context and that its help request
exits successfully. Re-run the read-only diagnostic interface; if recurring jobs exist, also ask
`recurring-tasks` for status and healthcheck in that context.

If a required phase fails, report its exact error and do not claim later stages
completed. A platform-scoped helper reported unsupported is non-fatal when the
shared command floor succeeded.

## Conflict policy

- Leave a link already targeting the desired source in place; replace a link
  targeting a different source only where the installer owns that destination.
- Preserve real user files and directories. Migrate a skills directory only
  when every unique entry can be retained without conflict.
- Preserve foreign skills-directory links and real conflicting entries, and
  report them as skipped.
- Treat a missing source or unsafe home symlink boundary as a named skip or
  failure from the installer; do not promise an undeclared backup or rollback.

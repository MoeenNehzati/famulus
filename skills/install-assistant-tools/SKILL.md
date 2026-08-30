---
name: install-assistant-tools
description: >-
  Use when the user asks to install, update, propagate, or repair the core Famulus command floor and development integration. Do not use for optional interactive launchers or unrelated software.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: assistant-installation, system-maintenance; visibility: listed
Activation: user-request; persistent modifier: no

Skill Version: 3

Uses Interfaces:
- `install-assistant-tools.source.gateway -> connect-google.interface.default@1`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-dev-link@2`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-doctor@1`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-install@2`
- `install-assistant-tools.source.gateway -> install-assistant-tools._rtx.interface.scripts-scaffold@2`
- `install-assistant-tools.source.gateway -> recurring-tasks.interface.default@1`

Setup Requires Setup Of: none
Setup Order:
1. `install-assistant-tools.interface.setup`

Public Interfaces:
- `install-assistant-tools.interface.default`
- `install-assistant-tools.interface.diagnose`
- `install-assistant-tools.interface.setup`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `install-assistant-tools._rtx.interface.scripts-dev-link` — Project live checkout skills and hooks into isolated context homes, install milestone and agent-timeline when referenced, and set local git hooksPath.
  - Caller: `install-assistant-tools`
  - Version: 2
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--claude-home": "DIR", "--codex-home": "DIR", "--dry-run": true, "--home": "DIR", "--no-claude": true, "--no-codex": true, "--repo-root": "DIR", "--shell-rc": "FILE"}, "positionals": [], "stdin": null}
    Required options: ["--repo-root"]; positional arity: 0..0; stdin: forbidden
- `install-assistant-tools._rtx.interface.scripts-doctor` — Diagnose one explicit context; mode is never inferred from cwd and no state is mutated.
  - Caller: `install-assistant-tools`
  - Version: 1
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--checkout": "ABSOLUTE_PATH", "--home": "DIR", "--json": true, "--mode": "standard|development"}, "positionals": [], "stdin": null}
    Required options: ["--mode"]; positional arity: 0..0; stdin: forbidden
- `install-assistant-tools._rtx.interface.scripts-install` — Choose mode, confirm choices, apply once, diagnose the same context, and explain optional next steps without invoking them.
  - Caller: `install-assistant-tools`
  - Version: 2
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--bin-dir": "DIR", "--claude-home": "DIR", "--codex-home": "DIR", "--dev-mode": true, "--dry-run": true, "--home": "DIR", "--non-interactive": true, "--optional-modules": "LIST", "--repo-path": "DIR|--no-dev-mode", "--shell-rc": "FILE", "--yes": true}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
- `install-assistant-tools._rtx.interface.scripts-scaffold` — Install the shared launcher floor using an exact context; persist PATH only for standard mode.
  - Caller: `install-assistant-tools`
  - Version: 2
  - Alternative: `owner`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--bin-dir": "DIR", "--dry-run": true, "--home": "DIR", "--repo-root": "DIR", "--shell-rc": "FILE"}, "positionals": [], "stdin": null}
    Required options: ["--repo-root"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `install-assistant-tools.interface.default` — Primary LLM-facing skill instructions.
- `install-assistant-tools.interface.diagnose` — Diagnose one explicitly selected Famulus installation context through the canonical read-only child diagnostic route.
- `install-assistant-tools.interface.setup` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Install Assistant Tools

Begin by reporting `Skill: install-assistant-tools`.

## Apply workflow

Use `scripts-install` for every fresh install, repeat, update, or repair. It
publishes exactly five stages:

1. Choose `standard` or `development`. Never infer a context from the working
   directory. Development requires an explicit existing checkout.
2. Confirm the resolved source, state, runtime, command directory, isolated
   homes and optional modules. A dry run stops here without
   effects; unattended mutation requires `--yes`.
3. Apply once to the resolved context: candidate runtime, shared command floor,
   and development-only projections. Required failure stops
   later effects. Repeating this stage reconciles the same context.
4. Diagnose that same context and report pointer/source/runtime consistency,
   command origins, manifest health, and recurring
   registration summary.
5. Explain that `connect-google` connects selected Google services and
   `recurring-tasks` creates and manages recurring AI jobs. Show the user how
   to invoke each skill, but do not invoke either or make installation success
   depend on them.

If optional modules are available, show
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
core installed command resolves from the selected context and that its help request
exits successfully. Re-run the read-only diagnostic interface; if recurring jobs exist, also ask
`recurring-tasks` for status and healthcheck in that context.

If a required phase fails, report its exact error and do not claim later stages
completed.

## Conflict policy

- Leave a link already targeting the desired source in place; replace a link
  targeting a different source only where the installer owns that destination.
- Preserve real user files and directories. Migrate a skills directory only
  when every unique entry can be retained without conflict.
- Preserve foreign skills-directory links and real conflicting entries, and
  report them as skipped.
- Treat a missing source or unsafe home symlink boundary as a named skip or
  failure from the installer; do not promise an undeclared backup or rollback.

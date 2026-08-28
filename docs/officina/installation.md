# Famulus installation

For ordinary setup, ask your assistant to use `install-assistant-tools`. The
same workflow performs a first install, a repeat install, an update, or a
repair. This guide explains the lifecycle and recovery boundaries.

Famulus requires a plugin-capable host and Python 3.11 or newer for the first
bootstrap. Initial setup may use the network to obtain the pinned runtime and
hash-locked packages.

## Installation contexts

Every installation has one explicit context:

| Context | Source | Durable roots | Intended use |
| --- | --- | --- | --- |
| `standard` | The installed Famulus package | Platform Famulus config, data, and state roots | Normal use |
| `development` | One explicit existing checkout | That checkout's `.famulus/` tree | Editing and testing a live checkout |

A development context has a stable installation ID, so several checkouts can
coexist without sharing runtime, assistant homes, jobs, logs, or manifests.
Their job definitions and history remain isolated, but every installation
shares one native scheduler set for the host account. The checkout itself is
never used as an assistant home. Its isolated homes and all installer-owned
development state remain inside `.famulus/`.

Development activation is a convenience boundary, not a sandbox. It projects
the checkout's skills, references, instructions, hooks, and helper commands
into the isolated context, but the launched assistant still has whatever
filesystem, network, subprocess, and approval authority its host grants. Do
not activate an untrusted checkout.

## First discovery and normal discovery

First use begins with the host's plugin registry. The host reports the
installed package directory, and the assistant invokes the package-relative
installer interface from that registered package. Do not guess the source from
the current directory.

After apply, installed commands and development adapters follow one stable
chain:

```text
installed command or development adapter
  -> self-locating fixed resolver
  -> the selected context's runtime/current.json
  -> active managed runtime
```

The resolver validates the pointer and selected source before entering the
managed runtime. Normal command, repair, and scheduled paths do not depend on
the original clone location or the caller's current directory.

## The five stages

The installer always publishes exactly these stages:

1. **Choose context.** Select `standard`, or `development` with an explicit
   checkout. Context is never inferred from the working directory.
2. **Confirm choices.** Review source, state, runtime, command directory,
   isolated homes, launcher backend, helpers, and optional modules. A dry run
   stops here without effects; unattended apply requires explicit confirmation.
3. **Apply once.** Build and verify a candidate runtime, activate it
   transactionally, reconcile the shared command floor, apply development-only
   projections when selected, and install selected helpers. Repeating this
   stage updates or repairs the same context idempotently.
4. **Diagnose.** Read back the same context and report source, pointer, runtime,
   command origin, launcher configuration, manifest, hooks, and recurring
   registration summary. This stage is read-only.
5. **Optional next steps.** Explain account connection and recurring jobs. This
   stage performs no setup and cannot change installation success.

Core apply installs no Google credentials and creates no recurring jobs.

## Launcher and environment ownership

The selected assistant backend is durable configuration in the context's
`launchers.json`. Re-applying without a new backend preserves that file.
`ASSISTANT_DEFAULT` may override a single launched process, but it is not
persisted as installation state.

Do not persist `AI`, `FAMULUS_REPO_ROOT`, or `ASSISTANT_LOGS`. Installed
commands derive their context through the resolver. Development activation
supplies only the selected checkout-local values to the launched process, and
log roots are derived from the active context.

Standard installation persists only the managed command-directory entry on
the user's search path. It does not project a checkout into normal assistant
homes.

## Apply, update, and repair

Ask the assistant to use `install-assistant-tools` for all of these operations.
For unattended operation, provide the mode explicitly; development additionally
requires an absolute checkout, and non-interactive mutation requires `--yes`.
Optional module selection uses module IDs. A missing package-size estimate is
reported as unavailable rather than guessed.

Apply constructs a candidate before replacing `current.json`. A failed
candidate or interrupted publication leaves the last valid active runtime in
place. Re-run the same apply after correcting the reported cause.

If the selected source disappeared, Stage 4 reports that exact source and a
repair command. Restore the standard package or development checkout first,
then apply the same explicit context again. Do not repoint scheduler entries or
copy runtime files by hand.

## Read-only diagnosis

Use `install-assistant-tools._rtx.interface.scripts-doctor` with `--mode
standard`, or with `--mode development --checkout <absolute-path>`. Add
`--json` for the schema-versioned report. Doctor does not infer a context and
does not mutate installation or scheduler state.

Valid recurring registrations appear as a summary, not an install failure.
Broken or source-missing registrations include the recurring-owned recovery
route.

## Recurring jobs and context removal

`recurring-tasks` owns job definitions, logs, outcomes, native registrations,
health checks, migration, and removal. All installations share one native
scheduler set for the host account. Setup, sync, enable, or disable replaces
that set from the active installation's complete enabled-job configuration;
the last successful scheduling operation becomes its owner. Job definitions,
logs, outcomes, and in-flight state remain local to each installation.

Before uninstalling a context:

1. Use `recurring-tasks` to disable its enabled jobs.
2. Invoke its `scripts-remove-context` operation for that exact context. This
   removes the shared registrations only if that context is their current
   owner; a non-owner removal leaves them unchanged. Job configuration and
   history are preserved.
3. Run the installer uninstaller only after its recurring preflight reports no
   shared registrations owned by that installation.

On first standard sync, recurring-tasks adopts recognized legacy standard
registrations and mutable job state. It does not adopt ambiguous or foreign
registrations. Linux installs one shared periodic healthcheck sentinel. macOS
and Windows provide the same healthcheck on demand but do not install that
independent sentinel.

## Uninstall versus purge

Ordinary uninstall replays the selected context's manifest and removes only
unchanged installer-owned artifacts. User-modified files, credentials, worker
content, recurring configuration, and recurring history are preserved.

Purge additionally removes recorded immutable runtime/bootstrap, launcher, and
generated profile artifacts when identity checks still prove ownership. It
still does not recursively delete mutable configuration or credentials, revoke
remote accounts, erase arbitrary user data, delete recurring history, or remove
a development context's stable installation ID. Uninstall and purge are
therefore not credential revocation.

If recurring registrations remain, both operations refuse before deletion.
Use recurring-tasks' context removal first; the installer intentionally never
deletes scheduler state.

## Optional Stage-5 workflows

After installation succeeds, invoke these skills separately if wanted:

- `connect-google` connects selected Google services. Ask: `Connect Famulus to
  Google.` Review [security and privacy](../security-and-privacy.md) before
  granting scopes.
- `recurring-tasks` creates and manages recurring AI jobs. Ask: `Set up a
  recurring Famulus job.` Review the unattended execution boundary before
  enabling it.

Neither workflow is required to install Famulus.

## Post-install verification

After every apply:

1. Follow the printed environment-reload instruction.
2. Confirm `dispatcher`, `invoke-skill`, `llm-wakeup`, and `lw` resolve from the
   selected context's command directory.
3. Request help from each installed optional launcher.
4. Run Stage 4 again for the exact context and review every failed check.
5. If recurring jobs exist, run recurring-tasks status and healthcheck for that
   same context.

See [Dispatcher](dispatcher.md), [Launchers](../launchers.md), and
[Security and Privacy](../security-and-privacy.md) for the corresponding
runtime and trust boundaries.

# Unified Famulus Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one five-stage Famulus installation flow whose standard and
development modes differ only in resolved paths, resource source, and activation
scope.

**Architecture:** One `InstallationContext` feeds one idempotent apply workflow,
one managed-runtime implementation, one pointer/resolver contract, one launcher
implementation, and one manifest format. Standard mode uses platform user roots
and persists only the Famulus command directory on `PATH`. Development mode uses
`<checkout>/.famulus` and thin checkout-relative adapters that activate an
isolated child process. Uninstall remains a separate, context-resolved manifest
replay. `recurring-tasks` consumes the same context through its own bounded,
downstream compatibility work; installation never registers a job.

**Design provenance:** This document consolidates the earlier five-stage,
installation-simplification, and isolated-development drafts. Those drafts were
removed after the consolidated design passed independent simplicity, capability,
and cross-platform audits. This is the sole authoritative installation plan.

## Non-negotiable constraints

- Present exactly five user-visible stages: choose mode, confirm choices,
  install, verify/report, and explain optional next steps.
- Use one context resolver and one apply workflow. Do not create a development
  installer, global clone registry, path DSL, lifecycle state machine, or
  compatibility facade.
- Treat a repeated install, update, and repair as the same idempotent `apply`
  operation. Keep uninstall/purge as a separate manifest consumer.
- Development requires an explicit existing checkout. Cloning, checkout moves,
  and Git revision management are outside installation scope.
- Development writes beneath `<checkout>/.famulus` plus one explicitly owned,
  checkout-local `core.hooksPath` entry in `.git/config`. It never mutates global
  `PATH`, shell startup, Windows user environment, normal assistant homes, or
  standard Famulus paths.
- Standard mode may persist only `FamulusPaths.user_bin` on `PATH`. It must not
  persist `AI`, `FAMULUS_REPO_ROOT`, `ASSISTANT_LOGS`, `ASSISTANT_DEFAULT`,
  `PYTHONPATH`, `PYTHONHOME`, or `VIRTUAL_ENV`.
- Preserve dry-run, non-interactive use, capability reporting, conflict-safe
  writes, current-source bootstrap, interruption-safe runtime activation,
  manifest-safe removal, and pinned managed-Python/`uv` bootstrap behavior.
- Preserve public commands `dispatcher`, `invoke-skill`, `llm-wakeup`, and `lw`,
  plus selected agent and tmux helpers where supported.
- Standard skill discovery remains plugin/host-owned. Checkout skill, hook, and
  profile projections are development-only.
- Stage 5 only tells the user what `connect-google` and `recurring-tasks` do and
  how to invoke them. It invokes neither and does not affect install success.
- Installation never owns Google/service credentials, assistant credentials,
  certificate-signing keys, workers, sessions, or arbitrary mutable user data.
- Use canonical node/blueprint workflows and the repository test runner. Real
  Linux/macOS/Windows CI evidence is required for platform claims.
- Preserve unrelated worktree changes and stage only task-owned paths.

## Five user-visible stages

1. **Choose mode.** Ask for standard or development. Development additionally
   requires the checkout path. No second installer is invoked.
2. **Confirm choices.** Show mode, source, resolved data/config/state/runtime/bin
   roots, assistant homes, selected default backend, helper capabilities, PATH
   behavior, and development isolation warning. Require explicit confirmation
   unless `--yes`; dry-run stops after rendering the intended effects.
3. **Install.** Resolve the context once and run the common idempotent `apply`
   flow: bootstrap, candidate runtime, atomic pointer, launcher configuration,
   commands, mode-appropriate profiles/projections, and manifest.
4. **Verify and report.** Verify every required and selected capability through
   the selected context. Any required failure makes installation fail. Report
   concrete paths, repair command, and unsupported optional capabilities.
5. **Explain optional next steps.** Tell the user that `connect-google` connects
   Famulus to Google services and `recurring-tasks` creates/manages recurring AI
   jobs. Show how to invoke each skill. For development mode, say recurring jobs
   remain pinned to that checkout context and that isolation is not an OS
   security sandbox.

## Minimal architecture

### InstallationContext and the one known root

```python
@dataclass(frozen=True)
class InstallationContext:
    mode: Literal["standard", "development"]
    source_root: Path
    development_root: Path | None
    paths: FamulusPaths
    codex_home: Path
    claude_home: Path
    installation_id: str
```

The context stores resolved facts, not duplicated policy. It does not retain an
ambient environment or `persist_user_bin`; PATH persistence is derived from
`mode`. The resolver receives an explicit environment only as input.

For standard mode, the one initially known root is the platform user-data root
derived from operating-system conventions. For development mode it is the
explicit checkout, from which `.famulus` is derived. Every other Famulus path is
computed by `FamulusPaths`; none depends on where a standard plugin clone or
cache happens to live.

That target-root statement is distinct from first-install source discovery. The
bootstrap chain is explicit:

```text
first install:
host skill registry -> install-assistant-tools/SKILL.md
  -> package-relative phase script -> invoked source checkout/plugin cache

after install:
PATH/checkout adapter -> self-locating fixed resolver
  -> <resolved runtime_root>/current.json -> managed Python/module
```

Tests must prove both chains without relying on cwd, `AI`, or
`FAMULUS_REPO_ROOT`.

`resolve_famulus_paths(..., environ=mapping)` must use that mapping exclusively.
It must not fall through to `os.environ`. Empty or relative XDG, APPDATA, or
LOCALAPPDATA overrides are rejected, and every returned root is absolute.

Before any development write or removal, canonicalize the checkout and every
writable `.famulus` boundary. Reject symlink/junction escapes into normal
assistant homes, standard Famulus roots, or outside the canonical checkout.

`installation_id` supplies scheduler-safe identity without a global registry:

- standard uses the reserved ID `standard` and retains legacy scheduler names;
- development creates a random immutable ID at `.famulus/install-id` on first
  apply, then reuses it across repair/update and checkout moves;
- ordinary uninstall and purge preserve it. This avoids cross-owner scheduler
  inspection and gives a later reinstall the same identity.

### Development activation environment

Interactive activation and scheduler persistence are separate contracts.

Interactive `famulus-env` inherits the current process environment so GUI,
locale, proxy/certificate, SSH-agent, keychain, and desktop-session behavior
continues to work. It then overrides only the context selectors
(`HOME`/`USERPROFILE`, XDG or APPDATA roots, and assistant homes) and
removes known conflicting Famulus/Python selectors (`AI`,
`FAMULUS_REPO_ROOT`, `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV`). It prepends
`paths.user_bin` to the inherited `PATH`; it does not remove unrelated host-tool
directories. Before launch, verify every Famulus-managed command exists and
resolves from the development command directory, so a missing command cannot
silently fall back to a stable Famulus installation. Resolve the host `code`
executable before activation; `codex`/`claude` are launched through the managed
agent contract where applicable.

The scheduler descriptor stores only deterministic context overrides—not the
interactive environment. At job execution, those overrides are merged into a
bounded scheduler baseline: Windows `SystemRoot`/`WINDIR`, `COMSPEC`, `PATHEXT`,
`TEMP`/`TMP`; POSIX locale/temp; and present GUI/session transport variables such
as `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, and
`DBUS_SESSION_BUS_ADDRESS`. Credentials, tokens, proxy secrets, and unrelated
ambient variables are never serialized. Reject CR/LF in stored values, and
encode spaces, quotes, `%`, `!`, and Unicode per backend.

`FamulusPaths.user_bin` remains the command directory in both modes; development
normally computes it below the isolated home. `.famulus/bin` is only a stable
activation-bootstrap directory, not a second command directory.

`famulus-env` exposes two explicit interfaces:

```text
famulus-env exec -- <argv>          # construct environment, then exec
famulus-env export --shell <shell>  # emit trusted assignments for parent evaluation
```

`tools/dev-code`, `tools/dev-code.cmd`, and `.envrc` are small, generic,
repository-tracked adapters. They derive the checkout from their own location.
The dev-code adapters call `famulus-env exec`; `.envrc` evaluates the all-or-
nothing output of `famulus-env export`. Windows uses an executable
`.famulus/bin/famulus-env.cmd` bootstrap with an absolute interpreter, not an
extensionless shebang script. Test shell escaping and no partial export on
error.

The installer creates, manifests, and removes only `.famulus`; it never rewrites
or owns the tracked adapters. The one exception is checkout-local Git-hook
parity: apply records the previous local `core.hooksPath`, writes
`<checkout>/.githooks` to that checkout's `.git/config`, and uninstall restores
the previous value only if the installed value still matches. This must not
affect another repository opened from dev-code, its VS Code terminal, or a
direnv shell. Opening the checkout normally does not activate development mode.

Development uses fresh isolated on-disk assistant state. Credential import is
outside this plan, but interactive activation inherits ambient credential/token
variables as part of the host environment; it is therefore not an authentication
boundary. Scheduler descriptors never capture those values. The launcher/doctor
must state that isolated homes are not a security sandbox: jobs, keychains,
external services, network access, and arbitrary filesystem effects may escape
the directory boundary.

### Runtime, pointer, and bootstrap

Both modes use the same relative layout beneath their own
`FamulusPaths.runtime_root`. Pointer schema 3 stores exact addresses, not policy:

```json
{
  "schema_version": 3,
  "release_id": "<release id>",
  "runtime_source": "<absolute immutable release directory>",
  "python_bin": "<absolute managed Python>",
  "repository_config": "<absolute validated repository config>",
  "launcher_resources": "<absolute validated resource directory>"
}
```

Standard `launcher_resources` is copied into the immutable release;
development points to the live checkout. Consumers validate the exact path and
need no `source_mode` branch.

Migration order is reader-first: deploy a resolver supporting schemas 1-3,
prove old pointers still launch, atomically activate schema 3, then install
schema-3-dependent shims. The fixed resolver derives `runtime_root` from its own
installed path. For an allowlist of managed modules, it injects the validated
`--runtime-root`; caller attempts to supply or override that argument fail. The
managed agent reads schemas 1-3 from that root. Windows shims also pin the
absolute bootstrap interpreter because a resolver `.py` is not self-executing.

The installer preserves the existing current-source bootstrap: if imported
Officina code is not package-relative to the invoked installation source,
`_phase_entry.py` re-execs from the invoked source. The child gets a clean
`PYTHONPATH` containing only that source, never inherited entries. This ensures
an update builds from the source being installed rather than stale installed
modules.

### Durable launcher selection

The selected default backend lives atomically at
`FamulusPaths.config_root / "launchers.json"`:

```json
{"schema_version": 1, "default_backend": "claude"}
```

Create it when absent; preserve an existing valid choice unless the user
explicitly changes it; reject invalid schema/backends; and preserve a modified
file during uninstall. `ASSISTANT_DEFAULT` remains only a process-local
compatibility override. The managed agent launcher owns the reader/writer unless
reuse proves a separate module necessary.

### Lifecycle and ownership

`apply(context, choices)` is the only mutation route for fresh install,
reinstall/update, and repair. On an existing recognized installation it verifies
and refreshes owned artifacts; it is not an error. Narrow repair entry points
may remain private wrappers around the same components, or be explicitly retired
with documented replacement commands—never silently dropped.

Uninstall is a separate command that resolves the same context and replays its
manifest. It removes only identity-matching owned artifacts. Ordinary uninstall
preserves launcher configuration if modified, credentials, workers, sessions,
jobs/data, and the development installation ID. Purge additionally removes
identity-matching installer-owned runtime releases, pointer, unmodified launcher
configuration, generated profiles, and other immutable install state; it still
does not recursively delete mutable user state or credentials.

Managed-runtime candidate creation remains transactional. Download/checksum,
`uv`, managed-Python, unsupported-platform, or required-capability failures must
leave the previous pointer and working installation intact.

### Recurring-tasks downstream contract

Scheduling is not an installation phase or success condition. It is a separate,
`recurring-tasks`-owned compatibility workstream using its own skill and
blueprint workflow.

All production scheduler entry points obtain one `ScheduleContext` from a single
factory fed by the active `InstallationContext` or a safe serialized descriptor;
direct default construction is forbidden outside legacy tests. The context pins:

- `installation_id` and derived scheduler namespace;
- fixed runtime resolver and, on Windows, absolute bootstrap interpreter;
- launcher bin, recurring config/state/log roots, and selected backend;
- deterministic context environment overrides, never a captured ambient
  environment.

Native unit/plist/task/wrapper names include the namespace. The standard context
reserves legacy names for migration; two development checkouts with identical
job names must coexist across sync, status, test, disable, repair, and uninstall.
Scheduler ownership records are also namespaced by `installation_id`; presence
checks inspect only that namespace. The Linux healthcheck is per-context: its
cron marker and log are namespaced, and it invokes a managed healthcheck module
through the context resolver rather than a skill-source file. Standard legacy
owner records and healthcheck markers receive explicit migration/removal logic.

The scheduled executor becomes a managed module invoked as
`resolver -m <module>` with explicit jobs/log paths. This removes direct runner-
file coupling, but it does not make installed skills independent of the active
plugin/checkout source. Runtime upgrades and plugin-cache replacement followed
by successful `apply` must repoint jobs safely; missing active source must yield
a clear doctor/repair failure. Survival after source removal is not promised.

## Capability matrix

| Capability | Standard | Development |
|---|---|---|
| known root | OS user-data convention | explicit checkout |
| data/config/state/runtime | platform user roots | `<checkout>/.famulus` |
| command discovery | persistent user-bin PATH | child-process PATH only |
| managed Python/`uv` | shared pinned transactional bootstrap | same implementation, isolated root |
| pointer/resolver | schema 1-3 reader, standard root | same reader, isolated root |
| launcher resources | immutable release copy | exact live checkout path |
| launcher selection | context `launchers.json` | isolated `launchers.json` |
| install/update/repair | idempotent common `apply` | same `apply` |
| uninstall/purge | context manifest replay | same replay, `.famulus` containment |
| skill/hook projection | existing plugin/host behavior | checkout-only isolated projection |
| Git hooks | existing host/plugin behavior | identity-tracked checkout-local setting |
| assistant authentication | existing host state | fresh isolated on-disk state; ambient credentials may be reused |
| recurring jobs | legacy standard namespace | installation-ID namespace |
| command closure | `invoke-skill` includes `background_run` | same closure |
| tmux bundle | selected all-or-none where supported | same bundle |
| instruction helpers | `milestone`/`agent-timeline` when referenced | same rule |
| targeted repair | preserved wrapper or explicit retirement | same contract |
| Stage-5 prompt | informational only | informational plus isolation warning |

## Implementation workstreams

### Task 1: Resolve paths and activate a clone-local context

**Files:**
- Create: `src/officina/install/context.py`
- Create: `src/officina/install/development_activation.py`
- Modify: `src/officina/common/famulus_paths/__init__.py`
- Modify: `src/officina/install/blueprint.yaml`
- Create/modify corresponding `src/officina/install/blueprints/*.yaml`
- Create: `tools/dev-code`, `tools/dev-code.cmd`, `.envrc`
- Modify: `.gitignore`
- Create: `tests/test_officina_install_context.py`
- Create: `tests/test_officina_development_activation.py`
- Modify: `tests/test_officina_famulus_paths.py`

- [ ] Test explicit environment isolation, empty/relative root rejection,
  absolute results, spaces/separators/Unicode, and no `os.environ` fallback.
- [ ] Test real-path containment for symlink/junction escape and stable-home
  canaries before install, repair, and uninstall.
- [ ] Implement the minimal context and installation-ID rules; derive policy
  rather than storing duplicate environment/PATH fields.
- [ ] Implement generated `.famulus/bin/famulus-env` and static tracked adapters.
  Test missing runtime, no stable fallback, clean Python environment, and the
  security-boundary warning. Test `exec` and `export` with real shell/cmd
  invocation, spaces/Unicode, no partial output, GUI/session canaries, absolute
  `code` resolution, inherited host-tool PATH retention, and exact managed-command
  origin at `paths.user_bin`.
- [ ] Regenerate the affected install node and run:

```bash
./repo_checks.py --task tests:shared --selector tests/test_officina_famulus_paths.py --selector tests/test_officina_install_context.py --selector tests/test_officina_development_activation.py --jobs 1
```

### Task 2: Unify pointers, launchers, and durable launcher configuration

**Files:**
- Create: `src/officina/launchers/__init__.py`
- Create: `src/officina/launchers/agent.py`
- Create/modify corresponding `src/officina/launchers/blueprint.yaml` and
  `src/officina/launchers/blueprints/*.yaml`
- Modify: `src/officina/install/managed_runtime.py`
- Modify: `src/officina/install/runtime_pointer.py`
- Modify: `src/officina/install/resolvers/launch.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_launcher/*.py`
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Delete superseded agent wrapper assets under
  `skills/install-assistant-tools/_rtx/assets/bin/`
- Modify/create focused tests under `tests/` and
  `skills/install-assistant-tools/_rtx/tests/`

- [ ] Test reader-first schema migration, exact `launcher_resources` validation,
  standard immutable resources, development live resources, adversarial
  `--runtime-root` override, and Windows bootstrap interpreter pinning.
- [ ] Test atomic `launchers.json` creation, preservation, explicit change,
  validation, manifest identity, modified-file uninstall behavior, and the
  process-local `ASSISTANT_DEFAULT` override.
- [ ] Preserve transactional pinned bootstrap: candidate failure at download,
  checksum, `uv`, Python, or capability verification leaves the active pointer
  unchanged.
- [ ] Move agent policy into the managed module and retain shell-native helpers
  only where they are genuinely shell-native.
- [ ] Run the managed-runtime, pointer, resolver, agent-launcher, and installer
  launcher suites through `repo_checks.py`.

### Task 3: Implement one idempotent apply workflow

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- Modify: `skills/install-assistant-tools/_rtx/_config_bridge.py`
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Delete: `skills/install-assistant-tools/_rtx/_google_onboarding.py`
- Modify/delete corresponding tests under
  `skills/install-assistant-tools/_rtx/tests/`

- [ ] Test both contexts follow candidate runtime -> scaffold -> mode-owned
  projections -> helpers -> verification, and required failure stops effects.
- [ ] Test current-source re-exec with stale imported Officina modules and
  hostile inherited `PYTHONPATH`; the child selects only the invoked source.
- [ ] Test first-install discovery from host registry to `SKILL.md` to
  package-relative phase code, and postinstall discovery from shim/adapter to
  self-locating resolver to the selected context's `current.json`.
- [ ] Define `apply` for fresh, repeated, update, and repair calls. Resolve
  context once and pass exact fields; do not rediscover mode downstream.
- [ ] Preserve standard plugin-owned skill/hook behavior. Run `_config_bridge`
  checkout projections only for development; keep launcher profiles conflict-safe.
- [ ] Remove Google onboarding and signing-key provisioning from installation.
  Preserve lazy key ownership in `skill-certifier`.
- [ ] Test five-stage output, PATH policy, dry-run, `--yes`, noninteractive mode,
  and whether narrow repair commands are preserved or retired. Require
  `background_run` with `invoke-skill`; treat `tmux-workspace`, `tw`, `tw-break`,
  `tw-join`, `tw-monitor`, and `tw-help` as one selected bundle; require
  `milestone` and `agent-timeline` whenever projected instructions reference
  them, or omit the dependent instructions and report the capability unsupported.

### Task 4: Make manifest uninstall and purge context-safe

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

- [ ] Record digest/target/value/block identity for files, links, registry
  values, shell blocks, launchers, and generated configuration.
- [ ] Resolve the target context explicitly for uninstall; never choose a
  manifest by cwd, plugin cache, or ambient environment.
- [ ] Test ordinary uninstall versus purge exactly as defined above, including
  modified config, retained mutable state, install ID, stable canaries,
  interruption, repeated replay, symlink/junction escape, and conflict-safe
  restore/preservation of the checkout's prior local `core.hooksPath`.
- [ ] Prove lifecycle operations do not mutate tracked development adapters or
  unrelated dirty checkout files.

### Task 5: Publish, satisfy downstream scheduling, and verify

> **Required ownership:** use `skill-maker` for the public installation-skill
> change. Use `recurring-tasks` ownership and its blueprint workflow for the
> downstream scheduler changes. Installation must remain successful before
> recurring-task setup.

**Installation/public files:**
- Modify `skills/install-assistant-tools/SKILL.md`, its public/nested blueprints,
  `docs/officina/installation.md`, and `.github/workflows/python-tests.yml`
- Delete the Google-onboarding blueprint
- Keep this file as the sole installation plan; do not recreate parallel
  standard/development implementation plans

**Recurring-tasks-owned files:**
- Modify `_schedule_backend/_base_backend.py` and all three platform backends
- Modify `_setup_runner.py`, `_job_control.py`, `_healthcheck_probe.py`,
  `_unit_writer.py`, `_job_executor.py`, and `_install_owner.py`
- Add managed recurring executor and healthcheck modules/resources and focused
  owner/healthcheck tests
- Modify/regenerate recurring-tasks blueprints through their owner workflow

- [ ] Publish exactly the five stages. Keep Stage 5 informational and explain
  `connect-google` and `recurring-tasks` in user language.
- [ ] Add one schedule-context factory and prohibit production default
  construction. Sanitize activation environment; test quotes, `%`, `!`, Unicode,
  CR/LF rejection, and absence of secret-canary variables.
- [ ] Namespace native scheduler identifiers by `installation_id`, migrate
  standard legacy names, and test two contexts across sync/status/test/disable/
  repair/uninstall.
- [ ] Namespace install-owner records and registration-presence checks. Namespace
  Linux healthcheck markers/logs and invoke the managed healthcheck module. Test
  two contexts plus standard legacy owner/marker migration and removal.
- [ ] Invoke the executor as a managed module with explicit jobs/log roots. Test
  runtime upgrades, cache replacement followed by `apply`, and clear doctor/
  repair failure when active source is missing.
- [ ] Regenerate every affected install, launcher, and recurring-tasks node.
- [ ] Run focused suites, then:

```bash
./repo_checks.py --task tests:install --jobs 1
./repo_checks.py --suite validators --jobs 1
./repo_checks.py --suite full
```

- [ ] Add real Linux/macOS/Windows CI jobs covering both contexts, spaces and
  Unicode, stable canaries, pointer migration, bootstrap rollback, and native
  scheduler behavior where CI permits. Record skips and native limitations.

## Completion gate

Implementation is complete only when:

- both modes share context, paths, runtime, pointer, resolver, launcher, apply,
  manifest, and verification contracts;
- standard persists only its user-bin PATH entry;
- development activation is checkout-local and stable roots/homes remain
  byte-for-byte unchanged, including adversarial link layouts;
- `launchers.json` owns backend selection without persisted `ASSISTANT_DEFAULT`;
- repeated apply is idempotent and failed candidates preserve the active runtime;
- uninstall and purge have distinct, identity-safe behavior;
- jobs coexist across contexts without captured ambient-environment or scheduler
  runner-file coupling; missing active source produces a clear doctor/repair
  failure, while scheduling remains outside installer execution;
- Stage 5 is message-only and accurately explains both optional skills; and
- Linux, macOS, and Windows lifecycle evidence passes with limits recorded.

## Independent audit record

Three independent audits reviewed the first unified draft for simplicity,
capability parity, and cross-platform/downstream correctness. All accepted the
one-installer/two-context architecture but rejected that draft until this plan:

- restored durable `launchers.json` ownership;
- reduced lifecycle to one idempotent apply route plus separate uninstall;
- retained current-source re-exec and transactional bootstrap guarantees;
- made development adapters tracked and `.famulus` the only installer-owned
  development surface;
- removed credential import and standard projection expansion;
- added real-path containment and the non-sandbox warning;
- separated recurring-task ownership while specifying context propagation,
  safe environment, scheduler namespacing, and a managed executor; and
- restored bootstrap, targeted-repair, purge, and platform evidence requirements.

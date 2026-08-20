# Unified Famulus Installation Downstream Adjustments Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adjust every consumer whose paths, launcher selection, persistent
environment, or scheduler registration assumptions change under the unified
Famulus installation architecture.

**Architecture:** The installer remains the sole owner of installation context,
runtime activation, commands, and manifests. Downstream components consume its
validated context instead of rediscovering a checkout or relying on persistent
environment variables. `recurring-tasks` owns all scheduler state and migration;
other skills retain their existing behavior and receive focused isolation tests
unless those tests expose a real context leak.

**Tech Stack:** Python standard library, native per-user schedulers (systemd,
launchd, Windows Task Scheduler), Officina blueprints, YAML/JSON configuration,
and the repository test runner.

**Spec:** `docs/plans/unified-famulus-installation.md`

## Global constraints

- This is a companion to, not a replacement for, the unified installation plan.
  The unified plan owns installer implementation; this file owns affected
  consumers and rollout order.
- Installation must succeed without configuring Google or registering recurring
  jobs. Stage 5 remains informational.
- Do not reintroduce persistent `AI`, `FAMULUS_REPO_ROOT`, `ASSISTANT_LOGS`,
  `ASSISTANT_DEFAULT`, `PYTHONPATH`, `PYTHONHOME`, or `VIRTUAL_ENV`.
- A consumer may receive an `InstallationContext`, a validated runtime-root
  argument, or a recurring-owned serialized descriptor. It must not infer the
  active installation from cwd, a repository walk, or a plugin-cache path.
- Standard and development installations must coexist. Two development
  checkouts must also coexist, including when their job names match.
- Development installation writes remain checkout-local. A later, explicit
  recurring operation may create namespaced native registrations in the host's
  canonical per-user scheduler locations; recurring-tasks records and removes
  those external effects by `installation_id`.
- Existing standard scheduler entries, owner records, cron markers, job
  configuration, run history, and credentials must migrate in place. Never
  silently discard mutable user state.
- Scheduler files may persist only the bounded environment defined by the
  unified plan. They must never serialize ambient credentials, tokens, or the
  complete invoking environment.
- Use canonical blueprint regeneration and certification workflows for every
  changed registered node. Do not hand-edit generated `SKILL.md` contract blocks.
- Preserve unrelated worktree changes and stage only explicitly owned paths.

## Impact inventory

| Consumer | Why it is affected | Required outcome |
|---|---|---|
| `recurring-tasks` | It currently defaults paths from the host, stores one checkout owner, uses global scheduler names, and embeds skill-source runner paths. | Consume one active installation context; namespace registrations and ownership; run managed modules through the fixed resolver. |
| recurring job definitions | Jobs currently set `ASSISTANT_DEFAULT` themselves and `llm-wakeup` names a launcher file. | Use the context-selected backend and managed module invocation; retain per-job overrides only when explicitly configured. |
| recurring mutable state | `jobs.yaml`, run logs, and outcome records currently live in replaceable skill source. | Move mutable configuration and history to context config/state roots; keep bundled defaults immutable and migrate legacy state before source replacement. |
| recurring manager environment | Linux setup writes global `AI_AGENT_COMMAND_TEMPLATE` state that cannot distinguish installations. | Retire the global channel and render the exact resolver/module command from each descriptor. |
| `milestone` and `agent-timeline` | Their default log root follows `HOME`; installation will stop exporting `ASSISTANT_LOGS`. | Standard uses the normal home; development uses the activated isolated home; an explicit process-local override remains supported. |
| stateful Famulus workflows | Google services, email-triage, list-manager, wakeup, handoff discovery, and skill-source discovery use `HOME`, assistant homes, or `FamulusPaths`. | Prove standard and development roots remain separate under interactive and scheduled activation; change code only where a test demonstrates leakage. |
| arbitrary child tools | Development activation changes `HOME` and XDG/APPDATA selectors for every child, not only Famulus. | Preserve executable discovery and inherited session transport, deliberately isolate home-scoped files, and document that Git/SSH/other tool configuration may differ. |
| dispatcher and session hooks | Older compatibility paths still recognize `AI`, while the new resolver carries exact repository configuration and launcher resources. | Production launch never needs `AI`; retain legacy/offline compatibility only where named and tested. |
| public docs and generated metadata | Current docs describe two phases, persistent variables, plugin/dev terminology, and old recurring ownership. | Describe the five stages, two contexts, exact path discovery chain, migration, and optional skills consistently. |
| CI and repository checks | Cross-platform coexistence and migration behavior are new integration obligations. | Run focused tests locally and real Linux/macOS/Windows lifecycle probes with explicit platform limitations. |

## Explicit non-impacts

- `connect-google` remains a separately invoked skill. The installer changes only
  the Stage-5 message; it does not call Google setup or move/copy credentials.
- Email-triage and list-manager do not need a new path abstraction. They already
  use `FamulusPaths`; they need context-isolation coverage first.
- `llm-wakeup` keeps its public scheduling behavior, but its queue/policy root,
  transcript roots, and provider executable discovery require activation tests.
- Certification keys, workers, assistant sessions, credentials, and arbitrary
  skill data remain outside installer manifests and migration.

---

### Task 1: Expose the minimum read-only installation-context handoff

**Files:**
- Modify: `src/officina/install/context.py`
- Modify: `src/officina/install/runtime_pointer.py`
- Modify/create: `src/officina/install/blueprints/*.yaml`
- Modify: `tests/test_officina_install_context.py`
- Modify: `tests/test_officina_runtime_pointer.py`

**Interfaces:**
- Consumes: schema-3 `current.json`, an immutable context record selected by
  that pointer, an absolute fixed `runtime_root`, and the explicit process
  environment used to resolve `FamulusPaths`.
- Produces:

```python
@dataclass(frozen=True)
class InstalledContextRecord:
    schema_version: Literal[1]
    release_id: str
    mode: Literal["standard", "development"]
    installation_id: str
    source_root: Path
    development_root: Path | None
    codex_home: Path
    claude_home: Path

def load_active_context(
    *, runtime_root: Path, environ: Mapping[str, str]
) -> InstallationContext:
    """Load and validate the active installation without mutating it."""
```

- [ ] Write failing tests proving the loader returns `standard` for the reserved
  standard identity and the exact development context for an isolated runtime.
- [ ] Test rejection of a relative root, absent/malformed pointer, mismatched
  computed runtime root, invalid installation ID, missing launcher resources,
  and caller attempts to substitute another checkout through `AI` or cwd.
- [ ] Write `installation-context.json` inside the immutable candidate release.
  Add its absolute path to schema-3 `current.json`; one atomic pointer replace
  then selects runtime code and context together. Validate that the record is
  inside `runtime_source` and that its `release_id` matches the pointer.
- [ ] Interrupt publication before and after every write/replace boundary and
  prove readers observe either the complete previous pair or the complete new
  pair, never a mixed pointer/context combination.
- [ ] The record stores resolved facts; all platform paths are recomputed
  through `FamulusPaths` and checked against `runtime_root` when read.
- [ ] Implement the loader as a thin reader over the context and pointer
  contracts from the unified plan. Do not create a second path policy or scan
  for installations.
- [ ] Regenerate the affected install blueprints and run:

```bash
./repo_checks.py --task tests:shared --selector tests/test_officina_install_context.py --selector tests/test_officina_runtime_pointer.py --jobs 1
```

### Task 2: Make recurring-tasks context-owned and multi-installation safe

**Files:**
- Create: `skills/recurring-tasks/_rtx/_schedule_context.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_base_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_linux_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_osx_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_windows_backend.py`
- Modify: `skills/recurring-tasks/_rtx/_schedule_backend/_linux_registration_check.py`
- Modify: `skills/recurring-tasks/_rtx/_setup_runner.py`
- Modify: `skills/recurring-tasks/_rtx/_unit_writer.py`
- Modify: `skills/recurring-tasks/_rtx/_job_control.py`
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_install_owner.py`
- Modify/retire: `skills/recurring-tasks/_rtx/_ensure_agent_env.py`
- Modify: `skills/recurring-tasks/_rtx/blueprints/rtx-ensure-agent-env.yaml`
- Modify: `skills/recurring-tasks/_rtx/tests/test_ensure_agent_env.py`
- Create/modify focused tests under `skills/recurring-tasks/_rtx/tests/`

**Interfaces:**
- Consumes: `load_active_context(runtime_root=..., environ=...)` from Task 1.
- Produces:

```python
@dataclass(frozen=True)
class ScheduleDescriptor:
    schema_version: Literal[1]
    installation_id: str
    runtime_root: Path
    runtime_resolver: Path
    bootstrap_python: Path | None
    launcher_bin: Path
    backend_executables: Mapping[Literal["claude", "codex"], Path]
    jobs_file: Path
    log_root: Path
    config_root: Path
    state_root: Path
    native_registration_root: Path | None
    default_backend: Literal["claude", "codex"]
    environment: Mapping[str, str]

def build_schedule_context(
    *, descriptor: ScheduleDescriptor, live: bool = True
) -> ScheduleContext:
    """Build the only production ScheduleContext construction route."""
```

- [ ] Write failing tests showing that `_setup_runner.py`, `_unit_writer.py`,
  `_job_control.py`, and `_healthcheck_probe.py` all call the shared factory.
  Prohibit direct default construction outside explicit legacy tests.
- [ ] Write descriptor round-trip tests for spaces, quotes, `%`, `!`, Unicode,
  and all supported platforms. Reject relative paths, CR/LF, unknown keys,
  unsupported schema versions, and mismatched `installation_id`.
- [ ] Accept a descriptor only from its canonical context config path with no
  symlink component and user-only permissions where enforceable. Require every
  authority-bearing field to equal the active context/current pointer and
  platform adapter: runtime/resolver, Windows bootstrap interpreter, launcher
  bin, backend executables, jobs/config/state/log roots, native registration
  root, backend selection, and the exact derived environment allowlist. Mutable
  roots must be contained in the context; external registration/executable
  paths must equal their independently resolved canonical values.
- [ ] Add swapped/stale descriptor tests: same installation ID with another
  release, altered resolver/log/registration root, stale backend, replaced
  symlink, injected `AI`/`PYTHONPATH`/secret, and changed backend executable.
- [ ] Test that the persisted environment contains only deterministic context
  selectors and the bounded platform scheduler baseline. Include a secret
  canary in the invoking environment and prove it is absent from every unit,
  plist, task wrapper, and descriptor.
- [ ] Namespace systemd units, launchd labels/plists, Windows tasks/wrappers,
  install-owner records, registration scans, log roots, and Linux healthcheck
  markers by `installation_id`. Reserve existing names for `standard`.
- [ ] Define one platform adapter contract for canonical native registration
  locations and discovery: Linux user systemd units, macOS LaunchAgents, and
  Windows Task Scheduler plus wrapper storage. These are recurring-owned host
  effects, never development activation state; creation requires an explicit
  recurring operation and every effect is recorded by installation ID.
- [ ] Require a non-null adapter-derived `native_registration_root` for every
  supported live scheduler. The production factory has no registration-root
  override; `live=False` tests may construct a descriptor through a dedicated
  test adapter whose canonical root is temporary. Add a negative test proving a
  live control request cannot redirect registrations outside its validated
  installation-ID namespace.
- [ ] Resolve absolute Claude/Codex backend executables before rendering a
  descriptor, persist only validated executable paths, and invoke those exact
  paths in scheduled runs. Setup/doctor fails with a named missing backend;
  isolated `HOME` or ambient `PATH` must never cause fallback to another binary.
- [ ] Test two development contexts with the same job name through sync,
  status, test, enable, disable, repair, healthcheck, and `remove-context`. Each
  operation must leave the other context byte-for-byte unchanged.
- [ ] Add reader-first migration for the legacy standard owner record,
  scheduler names, and `# ai-recurring-healthcheck` cron marker. Migration must
  preserve jobs and run history and be idempotent after interruption.
- [ ] Replace checkout equality as the scheduler identity check with
  `installation_id`. Preserve source-path diagnostics so a missing active
  plugin/checkout produces a repair instruction rather than cross-context
  adoption.
- [ ] Retire the public `scripts-ensure-agent-env` route and remove
  `AI_AGENT_COMMAND_TEMPLATE` from `environment.d` and the live systemd manager
  only when its value exactly matches the legacy Famulus value. Descriptor-
  rendered commands replace it; modified or foreign values are preserved.
- [ ] Test standard plus two development contexts without any global manager
  environment mutation. Include legacy file/session cleanup, repeated cleanup,
  missing systemd manager, and modified-value preservation.
- [ ] Run real platform registration/discovery/removal tests for standard and
  two development contexts. Assert native host locations contain only the
  namespaced recurring artifacts and that no standard Famulus data/config/state
  or assistant home is mutated by a development registration.
- [ ] Define healthcheck capability per platform: Linux retains a namespaced
  cron sentinel independent of systemd; macOS and Windows report that an
  independent second scheduler is unsupported and provide on-demand managed
  healthcheck only. Test notification failure, source removal, migration, and
  `remove-context` without claiming cross-platform independent-sentinel parity.
- [ ] Run the focused owner, setup, backend, job-control, healthcheck, and live
  smoke tests through `repo_checks.py`.

### Task 3: Move scheduled execution and healthcheck onto managed modules

**Files:**
- Create: `src/officina/recurring/__init__.py`
- Create: `src/officina/recurring/control.py`
- Create: `src/officina/recurring/executor.py`
- Create: `src/officina/recurring/healthcheck.py`
- Create/modify: `src/officina/recurring/blueprint.yaml`
- Create/modify: `src/officina/recurring/blueprints/*.yaml`
- Modify: `skills/recurring-tasks/_rtx/_job_executor.py`
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_unit_writer.py`
- Modify: `skills/recurring-tasks/_rtx/_setup_runner.py`
- Modify: `skills/recurring-tasks/_rtx/_jobs_config.py`
- Create: `skills/recurring-tasks/_rtx/default_jobs.yaml`
- Retire after migration: `skills/recurring-tasks/_rtx/jobs.yaml`
- Modify: `src/officina/common/configuration.schema.json`
- Modify: `skills/recurring-tasks/_rtx/tests/test_job_executor.py`
- Modify: `skills/recurring-tasks/_rtx/tests/test_enable_disable.py`
- Modify: `skills/recurring-tasks/_rtx/tests/test_production_invocation.py`
- Modify: `skills/recurring-tasks/_rtx/tests/test_setup_runner.py`

**Interfaces:**
- Consumes: fixed resolver plus injected `--runtime-root`, descriptor, operation,
  job name, jobs path, and log root.
- Produces these resolver-approved module entries:

```text
resolver -m officina.recurring.control <setup|sync|enable|disable|status|test|healthcheck|view-logs|remove-context> [arguments]
resolver -m officina.recurring.executor --descriptor FILE --job NAME
resolver -m officina.recurring.healthcheck --descriptor FILE
```

The fixed resolver injects the validated `--runtime-root ROOT` after resolving
`current.json`; registrations and callers never supply it.

- [ ] Write failing tests proving rendered scheduler entries contain managed
  module names and explicit descriptor/job/log inputs, never `_job_executor.py`,
  `_healthcheck_probe.py`, a checkout-relative `launch.py`, or ambient
  `sys.executable`.
- [ ] Extend the resolver's managed-module allowlist and test that callers
  cannot inject or override `--runtime-root`.
- [ ] Route every public recurring control operation through
  `officina.recurring.control`. It loads the active context from the injected
  root, validates any descriptor against it, and then performs exactly the
  requested operation. No control route may default to host paths or construct
  `ScheduleContext` directly.
- [ ] Move stable control, job-editing, executor, backend, and healthcheck code
  needed after source disappearance into the managed release. The
  `recurring-tasks` skill retains public behavior/configuration ownership and
  invokes those modules; installation never invokes recurring setup.
- [ ] Change the bundled jobs so ordinary agent jobs inherit
  `ScheduleDescriptor.default_backend`. Preserve an explicit per-job backend
  override as structured job configuration rather than an inline
  `ASSISTANT_DEFAULT=...` shell assignment.
- [ ] Make `<recurring_config_root>/jobs.yaml` the mutable source of truth and
  `<recurring_state_root>/logs/` the run-history root. `default_jobs.yaml` is an
  immutable template copied only when context configuration is absent; it is
  never edited by enable/disable.
- [ ] Add an atomic, idempotent standard migration from legacy source-tree
  `jobs.yaml`, logs, latest outcomes, and in-flight records. Locate the legacy
  source through the recorded owner or existing registration—not cwd. Copy and
  verify before switching writers; preserve the source on failure. If both old
  and new mutable state differ, stop with both paths and require an explicit
  choice; never merge or overwrite silently.
- [ ] Create context config/state directories with user-only write permissions
  where the platform supports them. Test interrupted copy, retry, absent legacy
  source, already-removed plugin cache, malformed records, and unchanged run
  history after successful migration.
- [ ] Render `llm-wakeup` through the managed resolver/module contract; retain
  its schedule, enabled state, and exit-code success semantics.
- [ ] Test runtime update, pointer replacement, plugin-cache replacement
  followed by `apply`, missing active source, command paths containing spaces,
  Windows bootstrap-interpreter use, and Unicode job/log paths. Run both Claude
  and Codex scheduled launch probes on Linux/macOS/Windows with isolated `HOME`;
  assert the descriptor's absolute backend executable is the process origin.
- [ ] Regenerate recurring and managed-runtime nodes, then run:

```bash
./repo_checks.py --task tests:shared --selector skills/recurring-tasks/_rtx/tests/test_job_executor.py --selector skills/recurring-tasks/_rtx/tests/test_production_invocation.py --selector skills/recurring-tasks/_rtx/tests/test_setup_runner.py --jobs 1
```

### Task 4: Verify path-sensitive helpers and stateful skills

**Files:**
- Modify: `scripts/milestone.py` only if isolation tests fail
- Modify: `scripts/agent-timeline.py` only if isolation tests fail
- Create: `tests/test_install_context_consumers.py`
- Modify focused tests under:
  - `skills/cloud-files/_rtx/tests/`
  - `skills/g-calendar/_rtx/tests/`
  - `skills/email-client/_rtx/tests/`
  - `skills/email-triage/_rtx/tests/`
  - `skills/list-manager/_rtx/tests/`
  - `skills/connect-google/_rtx/tests/`
  - `skills/find-handoff-candidates/_rtx/tests/`
  - `skills/skill-drift/_rtx/tests/`
  - `src/officina/wakeup/tests/`

**Interfaces:**
- Consumes: the standard or development activation environment created by the
  unified installer.
- Produces: no new public API. This task is a compatibility gate.

- [ ] Launch `milestone --path` and `agent-timeline` under both activation
  contexts. Assert standard resolves the normal home, development resolves
  below `<checkout>/.famulus`, and process-local `ASSISTANT_LOGS` remains an
  explicit override without being persisted by installation.
- [ ] Inventory every production `Path.home()`, `expanduser()`, XDG/APPDATA,
  assistant-home, and Famulus-path consumer. Classify each as deliberately
  standard, deliberately development-isolated, process-local override, or a
  leak. Keep the inventory in named parametrized test cases, not a new runtime
  registry.
- [ ] Run connect-google, cloud-files, g-calendar, email-client, email-triage,
  and list-manager path probes under both contexts. Place stable canary files in
  each host path and prove development neither reads nor writes them. Verify
  service-owned binding configuration as well as shared Google descriptors.
- [ ] Test `llm-wakeup` queue/policy state, Claude/Codex transcript lookup, and
  provider executable discovery. Treat `LLM_WAKEUP_HOME` and provider path/bin
  selectors as process-local overrides; do not persist them during install.
- [ ] Test handoff transcript discovery and skill-drift host-skill discovery
  against the active assistant homes. Standard must see standard sources;
  development must see its projected checkout and isolated transcripts.
- [ ] Run a generic child-process probe showing that development deliberately
  hides host-home Git/SSH/other dotfile canaries while retaining inherited PATH,
  GUI/session transport, proxy/certificate variables, and resolved host
  executables. Document this as isolation behavior, not an OS sandbox.
- [ ] Repeat the relevant email-triage probe through a sanitized recurring
  descriptor. Confirm its status file lands in the selected context and that
  ambient credential variables are absent from the serialized scheduler
  environment.
- [ ] If a probe fails, inject the already-resolved `FamulusPaths` or an explicit
  environment mapping at that consumer boundary. Do not introduce a new root
  variable or duplicate platform path rules.
- [ ] Confirm `connect-google` still owns fresh login/setup in each context and
  that no installer manifest claims its client or credential files.
- [ ] Cover `_assistant_desktop_notify.py` if scheduler execution still calls
  it: its selected home/session environment must match the descriptor, and
  notification failure must not mutate another context.
- [ ] Run the new compatibility test plus the affected focused skill tests.

### Task 5: Remove production dependence on legacy root/default variables

**Files:**
- Create: `src/officina/install/doctor.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `skills/install-assistant-tools/SKILL.md`
- Modify: `skills/install-assistant-tools/blueprint.yaml`
- Modify: `skills/install-assistant-tools/blueprints/gateway.yaml`
- Modify: `skills/install-assistant-tools/_rtx/blueprint.yaml`
- Create: `skills/install-assistant-tools/_rtx/blueprints/rtx-install-doctor.yaml`
- Modify: `llmhooks/inject_dispatcher_context.py` only where standard immutable
  resource loading requires it
- Create: `tests/test_officina_install_doctor.py`
- Modify: `tests/test_officina_repository_configuration.py`
- Modify: `tests/test_dispatcher_route_smoke.py`
- Modify/create focused launcher and hook tests under `tests/`

**Interfaces:**
- Consumes: pointer-carried `repository_config`, pointer-carried
  `launcher_resources`, `launchers.json`, and explicit offline test arguments.
- Produces: production dispatcher and hook entry paths that require no `AI`,
  `FAMULUS_REPO_ROOT`, or persisted `ASSISTANT_DEFAULT`.
- Produces the exact diagnostic routes:

```text
install-assistant-tools.interface.diagnose
dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-doctor --mode standard [--json]
dispatcher --caller-skill install-assistant-tools install-assistant-tools._rtx.interface.scripts-doctor --mode development --checkout <absolute-path> [--json]
```

- [ ] Classify every remaining use of `AI`, `FAMULUS_REPO_ROOT`,
  `ASSISTANT_DEFAULT`, `COLLAB_DEFAULT`, `COAUTHOR_DEFAULT`, and
  `BACKGROUND_RUN_DEFAULT` as production, process-local override, offline
  compatibility, test fixture, or stale documentation. Classify
  `ASSISTANT_LOGS` in Task 4. Record the classification in test names or adjacent
  comments, not a new compatibility registry.
- [ ] Write a production launch test with all three variables absent and cwd in
  an unrelated directory. Verify dispatcher, assistant, collab, coauthor, and
  background-run select the active context and configured backend.
- [ ] Keep `ASSISTANT_DEFAULT` only as the documented process-local override.
  `launchers.json` supplies the durable default, including scheduled jobs.
- [ ] Preserve the three agent-specific backend variables as process-local
  overrides only. The managed-launcher workstream in the unified plan owns
  replacing old resource wrappers; this task verifies standard copies and
  development launchers with every legacy root absent.
- [ ] Keep `AI` support only in explicitly offline/legacy APIs if removal would
  break a named supported caller. Production resolver and managed launcher
  tests must fail if they consult it.
- [ ] Verify standard hooks load immutable release resources and development
  hooks load the exact live checkout resources without repository walking.
- [ ] Add a read-only installation diagnostic route owned by
  `install-assistant-tools`. It resolves the explicitly selected context and
  reports human-readable output plus schema-versioned JSON containing mode,
  installation ID, pointer/context consistency, runtime, source/resources,
  launcher configuration, manifest health, command origins, and recurring
  descriptor/registration summary. It performs no repair.
- [ ] Export the instruction route and nested machine interface in the public
  and runtime blueprints. The instruction route asks standard versus
  development and requires an absolute checkout for development; it never
  infers mode from cwd. Stage 4 calls the same machine interface. `--json`
  emits only the schema-versioned object; the default emits human-readable
  diagnostics.
- [ ] Test healthy, absent, malformed, mixed-release, missing-source, stale
  command, corrupt manifest, and recurring-registration cases. Every failure
  names the exact safe `apply`, recurring `remove-context`, or source-restoration
  action; Stage 4 invokes the same diagnostic implementation rather than
  maintaining a second checker.
- [ ] Run dispatcher, repository-configuration, launcher, and hook-focused
  suites through `repo_checks.py`.

### Task 6: Rewrite public guidance and regenerate ownership metadata

**Files:**
- Modify: `README.md`
- Modify: `docs/security-and-privacy.md`
- Modify: `docs/officina/installation.md`
- Modify: `docs/officina/dispatcher.md`
- Modify: `docs/launchers.md`
- Modify: `docs/agent-milestone-logging.md`
- Modify: `skills/recurring-tasks/SKILL.md`
- Modify: `skills/recurring-tasks/blueprint.yaml`
- Modify: `skills/recurring-tasks/blueprints/gateway.yaml`
- Modify: `skills/recurring-tasks/_rtx/blueprint.yaml`
- Modify: `skills/recurring-tasks/_rtx/blueprints/*.yaml`
- Modify generated runtime dependency metadata only through its canonical
  regeneration workflow

**Interfaces:**
- Consumes: completed behavior from Tasks 1-5.
- Produces: one consistent public explanation and current registered-node
  ownership.

- [ ] Replace the old two-phase/plugin-versus-dev narrative with the five-stage
  flow and standard/development context terminology.
- [ ] Cover every user-visible lifecycle explicitly: host/plugin discovery of
  the installer, first apply, repeat/update/repair, development activation and
  its home-config/non-sandbox warning, Stage-4 diagnostics, ordinary uninstall
  versus purge, recurring `remove-context`, legacy migration, missing-source
  recovery, and post-install verification.
- [ ] Document the discovery chain precisely: host registry to package-relative
  installer on first use; command/adapter to self-locating resolver to
  `current.json` afterward. State explicitly that normal paths do not depend on
  clone location.
- [ ] Document `launchers.json` as the durable backend owner and
  `ASSISTANT_DEFAULT` as process-local only. Remove instructions to persist
  `AI`, `FAMULUS_REPO_ROOT`, or `ASSISTANT_LOGS`.
- [ ] Rewrite recurring's invariant from one-checkout ownership to one owner per
  `installation_id`; document coexistence, managed execution, context-specific
  jobs/logs, standard migration, Linux-only independent sentinel, on-demand
  macOS/Windows healthcheck, disable/remove-before-installer-uninstall, and the
  missing-source repair case.
- [ ] Keep the Stage-5 wording informational: explain that `connect-google`
  connects Google services and `recurring-tasks` creates/manages recurring AI
  jobs, then show how to invoke each skill.
- [ ] Regenerate affected blueprints, certificates where required, repository
  inventory, and runtime dependency metadata through their owner workflows.
- [ ] Run documentation-reference, blueprint, certification, and validator
  suites.
- [ ] Build the public site with `scripts/docs-site.py build` and verify staged
  README/installation/launcher/security links and sitemap entries. Confirm that
  `docs/plans/` remains excluded from the public site.

### Task 7: Execute migration and cross-platform acceptance in safe order

**Files:**
- Modify: `.github/workflows/python-tests.yml`
- Modify: `src/officina/repository/checks/runner.py` if new focused selectors
  need suite ownership
- Create/modify: cross-platform lifecycle tests under `tests/` and
  `skills/recurring-tasks/_rtx/tests/`

**Interfaces:**
- Consumes: reader-first installer migration and Tasks 1-6.
- Produces: evidence that an existing standard installation and new isolated
  development contexts remain usable throughout rollout.

- [ ] Roll out readers first: schema-3-aware resolver, active-context loader,
  recurring descriptor reader, and legacy standard scheduler-name reader.
- [ ] Apply the standard installation and migrate its recurring registrations.
  Verify job definitions, enabled state, owner identity, run history, and
  healthcheck remain intact before removing legacy writers. Report what was
  detected, preserved, and migrated, the active installation ID, and the exact
  safe recovery command if migration stops.
- [ ] Create two development contexts with identical job names. Verify their
  paths, assistant homes, launcher defaults, registrations, logs, healthchecks,
  owner records, and uninstalls remain isolated from each other and standard.
- [ ] Simulate failed runtime update, interrupted scheduler migration, missing
  plugin source, moved development checkout, and modified user configuration.
  Assert recovery uses `apply`/repair without adopting or deleting another
  context.
- [ ] Add `recurring-tasks remove-context` as the sole owner of native
  registration, sentinel, and owner-record teardown. It preserves canonical
  jobs, descriptor, and run history. Installer uninstall/purge performs a
  read-only preflight and refuses while that context has registrations, naming
  the removal command; once cleared it proceeds without deleting recurring
  mutable state. Test failed/repeated teardown and no cross-context deletion.
- [ ] Add Linux, macOS, and Windows CI coverage for path spaces/Unicode,
  launcher selection, descriptor escaping, scheduler identifiers, and
  uninstall isolation. Where hosted CI cannot operate a native user scheduler,
  record the limitation and run renderer/migration tests rather than claiming a
  live scheduler pass.
- [ ] After merge, verify the deployed Pages commit and representative README,
  installation, launcher, security, and sitemap routes. Do not treat either
  file under `docs/plans/` as a published user document.
- [ ] Run final local gates:

```bash
./repo_checks.py --task tests:install --jobs 1
./repo_checks.py --suite validators --jobs 1
./repo_checks.py --suite full
```

## Completion gate

This downstream migration is complete only when:

- no production consumer needs a persisted checkout-root, log-root, or launcher-
  default variable;
- recurring setup is still a separate user-invoked capability and never an
  installer success condition;
- standard plus two development contexts can own identically named jobs without
  collisions or cross-removal;
- scheduled executor and healthcheck entries target managed modules through the
  fixed context resolver;
- standard scheduler state migrates without losing job configuration, enabled
  state, owner identity, or run history;
- installer uninstall refuses active registrations and recurring-owned
  `remove-context` clears only that context while preserving its mutable data;
- milestone logs and every tested `FamulusPaths` consumer stay inside the active
  context;
- Google setup remains independent and credentials remain outside installer
  ownership;
- public docs and generated ownership metadata match the implemented contracts;
  and
- Linux, macOS, and Windows evidence passes, with unsupported live-scheduler
  operations reported as limitations rather than silently skipped.

## Independent audit record

Three independent read-only audits traced the plan against the live repository:

- dependency/call-site coverage: `PASS`;
- scheduler, cross-platform, lifecycle, and security coverage: `PASS`; and
- product, documentation, diagnostics, and rollout coverage: `PASS`.

The audits rejected earlier drafts until this plan added crash-consistent
pointer/context selection, sealed recurring control ingress, canonical mutable
recurring state, retirement of `AI_AGENT_COMMAND_TEMPLATE`, descriptor
validation, native-registration ownership, absolute backend executables,
recurring-owned teardown before installer removal, explicit platform
healthcheck capability, all HOME-sensitive Famulus consumers, a concrete
diagnostic interface, public/security documentation, and publication checks.

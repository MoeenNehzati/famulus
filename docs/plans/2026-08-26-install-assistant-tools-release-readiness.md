# Install Assistant Tools Release-Readiness Plan

> **For implementation:** Use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Do not commit without explicit authorization.

**Goal:** Make `install-assistant-tools` safe, predictable, and ready for public
release while keeping deterministic work in code rather than the LLM.

**Current verdict:** Not ready to ship.

## Target behavior

The installer owns the complete lifecycle:

1. resolve and validate the installation context and choices;
2. display one complete plan;
3. obtain approval without reconstructing that plan;
4. apply it under a per-context lifecycle lock;
5. diagnose and qualify the installed result;
6. print exact reload, recovery, and optional-next-step guidance.

The LLM selects the declared interface, relays interactive prompts, and reports
structured results. It must not infer paths, defaults, installation state,
verification commands, recovery steps, or uninstall targets.

Two contexts remain supported:

- `standard`, using platform Famulus roots;
- `development`, using an explicit existing checkout and state beneath
  `<checkout>/.famulus/`.

Development mode is not a security sandbox. Context must never be inferred
from the working directory.

## Why release is blocked

1. **Artifact routing is not trustworthy.** During the 2026-08-26 audit,
   dispatcher routes resolved to stale worktree commit `e62dcdae` instead of
   audited checkout `a7cbdf86`. Install later failed with `malformed source
   blueprint`, while doctor compiled from the stale worktree.
2. **Choice handling is unsafe.** Missing or invalid input can silently become
   standard mode, Claude, core-only installation, or a reduced helper set.
3. **Confirmation is incomplete.** Stage 2 omits optional modules and does not
   clearly distinguish install, preserve, replace, skip, and unsupported
   actions.
4. **Reapply can change durable preferences.** An omitted backend currently
   becomes Claude instead of preserving the existing selection.
5. **The script/LLM boundary is inconsistent.** The skill asks the LLM to
   repeat verification and invent reload or next-step guidance that code can
   produce deterministically.
6. **Removal has no declared public route.** Uninstall and purge are documented,
   but the existing CLI mutates by default and is not exposed through a safe
   installation-skill interface.

The historical routing failure is evidence of the release-artifact problem,
not a stable reproduction recipe. Qualification must test the exact candidate
artifact every time.

---

## Task 1: Qualify the exact release artifact

**Problem:** Source tests can pass while dispatcher executes a different
checkout, cache, or generated contract.

**Change:** Add candidate-artifact qualification through the real dispatcher,
not by importing the source checkout directly. For every declared installer
interface, the qualification must verify:

- that the route compiles successfully;
- which source file and gateway executable implement it;
- which artifact root and commit those files belong to.

Record complete failure output and the selected dispatcher and registry
context. These identity checks matter because a successful source-level test
does not prove that plugin discovery or dispatcher routing selected the same
artifact.

**How it fixes the problem:** A release can pass only when the routes users will
invoke resolve to the audited candidate. A stale cache or worktree therefore
fails qualification instead of being mistaken for evidence that the candidate
works.

**Files:**

- Create: `tests/test_install_release_artifact_routes.py`
- Modify: `tests/test_dispatcher_route_smoke.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_codex_install.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_claude_install.py`
- Modify: `.github/workflows/python-tests.yml`

**Acceptance criteria:**

- [ ] Install, doctor, scaffold, development-link, and launcher interfaces all
  compile from one exact candidate artifact.
- [ ] A stale discoverable checkout cannot capture any route.
- [ ] The observed malformed-source-contract case is covered.
- [ ] Invoking install without confirmation creates no runtime, manifest, PATH,
  configuration, or launcher effects.

---

## Task 2: Resolve choices and render one exact plan

**Problem:** Prompt and CLI defaults can reinterpret user intent, and Stage 2
does not display every consequential choice.

**Change:** Build one validated choice-resolution layer and one immutable plan
model. Choice resolution must finish before the plan is rendered so that the
displayed plan is the exact object later approved and applied. The installer
must not ask the LLM to remember choices or reconstruct the plan between
stages.

Interactive behavior:

- accept only displayed values plus `cancel`;
- blank selects only the displayed default;
- invalid input reprompts;
- EOF and keyboard interrupt cancel with no effects;
- keep one long-lived process from plan display through approval, preventing a
  second invocation from resolving different defaults or state.

Unattended behavior requires `--yes` plus one explicit disposition from each
pair:

- `--default-llm` or `--keep-backend`;
- `--agents` or `--keep-helpers`;
- `--optional-modules` or `--core-only`.

Preservation flags are invalid for a fresh context when no prior value exists,
because there is then nothing to preserve. An omitted backend remains `None`
internally so the configuration layer can distinguish “keep the current value”
from “select Claude”; Claude is only the first-install default.

The plan must display:

- context, source, installation identity, and owned roots;
- runtime and command directories;
- current and requested backend;
- helper and optional-module actions;
- affected packages and available cached size estimates;
- PATH and assistant-configuration effects;
- security and development trust warnings.

Helper selection is additive for this release: omitted existing helpers are
preserved. Helper-specific removal is out of scope.

**How it fixes the problem:** Every accepted input has one meaning, reapply no
longer turns omission into an unintended change, and approval covers the
complete resulting state rather than a partial summary. Keeping the approved
plan in one process also removes an LLM-controlled handoff between preview and
mutation.

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_launchers.py`

**Acceptance criteria:**

- [ ] Missing, conflicting, or unknown unattended choices fail before candidate
  construction.
- [ ] Interactive cancellation, EOF, interrupt, and invalid-then-valid input are
  covered for each prompt family.
- [ ] Fresh-install and reapply plans are byte-stable for identical inputs.
- [ ] Reapply preserves an existing backend and helpers unless an explicit
  change is confirmed.
- [ ] Dry-run prints the same plan but is explicitly a non-authoritative preview
  that cannot later be approved.
- [ ] A new development installation ID is generated in memory and displayed
  without persisting state before approval.

---

## Task 3: Serialize lifecycle changes and report failures

**Problem:** Another install or uninstall can change relevant state between
confirmation and the first write. Current failures also leave the LLM to infer
which earlier effects remain.

**Change:** Add a lifecycle layer shared by install and uninstall. The plan
records fingerprints for every state element that affects its actions. After
approval, the installer acquires a per-context lock and checks those
fingerprints again before writing:

- capture plan precondition fingerprints;
- after approval, acquire a per-context lock;
- revalidate every fingerprint before the first write;
- return `plan-stale` with no new effects when state changed;
- hold the lock through post-apply verification.

Add a structured `InstallationResult` containing schema version, plan identity,
stage, stable outcome code, summary, completed effects, uncertain effects,
verification status, and exact recovery action. This result is produced by the
lifecycle code because only that code knows which phases completed. Do not
claim rollback when an apply-stage failure may have left earlier effects.

**How it fixes the problem:** The lock prevents concurrent lifecycle operations
from interleaving, while fingerprint revalidation catches changes made before
the lock was acquired. Structured effect reporting gives users precise recovery
information without asking the LLM to infer state from console output.

**Files:**

- Create: `src/officina/install/lifecycle.py`
- Create: `src/officina/install/result.py`
- Create: `tests/test_officina_install_lifecycle_lock.py`
- Create: `tests/test_officina_install_result.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: installer and uninstall tests

**Acceptance criteria:**

- [ ] Concurrent install/install and install/uninstall tests prove serialized
  writes.
- [ ] Pointer, manifest, backend, PATH, hook, and context drift each produce
  `plan-stale` without new effects.
- [ ] The exact displayed development installation ID is persisted only after
  approval and revalidation.
- [ ] Failure injection after every apply phase reports exact completed,
  uncertain, and recovery fields.

---

## Task 4: Make verification and guidance authoritative

**Problem:** Doctor does not verify every documented surface, while the skill
asks the LLM to rerun checks and invent environment guidance.

**Change:** Keep doctor passive and read-only. Extend it to inspect runtime and
source identity, command origin and ownership, launcher configuration, manifest,
hooks, assistant access roots, and recurring registration summary. Doctor is
the safe diagnostic route: users must be able to run it without launching an
installed command or changing the installation.

Create a separate active qualification component for command `--help` probes.
Separating it from doctor makes the execution boundary visible and testable. It
may run only commands whose origin and ownership doctor has already proven,
using closed stdin, bounded environment, captured output, and a fixed timeout.
Do not describe this active component as read-only.

Have scaffold return a structured PATH outcome instead of leaving the LLM to
deduce shell state from prose. After successful verification, the installer
prints exactly one reload result and stable optional requests for
`connect-google` and `recurring-tasks`, explicitly stating that neither was
invoked.

**How it fixes the problem:** Passive diagnosis remains safe, active execution
is explicitly bounded, and Stage 4 becomes the authoritative machine result.
Reload and next-step guidance comes from the code that knows what changed; the
LLM only relays the structured outcome.

**Files:**

- Modify: `src/officina/install/doctor.py`
- Create: `src/officina/install/qualification.py`
- Create: `tests/test_officina_install_qualification.py`
- Modify: `skills/install-assistant-tools/_rtx/_scripts_doctor.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- Modify: `skills/install-assistant-tools/_rtx/_shell_block.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: doctor, scaffold, launcher, and installer tests

**Acceptance criteria:**

- [ ] Doctor remains context-explicit and effect-free.
- [ ] Hook and command-ownership failures appear in the diagnostic schema.
- [ ] Active probes reject foreign or modified commands and handle nonzero exit,
  timeout, bounded environment, and closed stdin.
- [ ] A required verification failure returns nonzero and prevents Stage 5.
- [ ] PATH output distinguishes changed, preserved, and already-active state.
- [ ] No-change output says: `No reload needed: PATH was already active and
  preserved.`

---

## Task 5: Expose safe uninstall and purge

**Problem:** Removal is documented but lacks a declared interface with the same
planning and confirmation guarantees as installation.

**Change:** Add `install-assistant-tools.interface.uninstall@1`, backed by
`install-assistant-tools._rtx.interface.scripts-uninstall@1`. This makes removal
a public, versioned operation with the same dispatcher and gateway controls as
installation, rather than an undocumented call into implementation code.

Interactive removal uses one process:

1. select `uninstall`, `purge`, or `cancel`;
2. render that operation's exact plan;
3. approve or cancel;
4. lock, revalidate, and apply.

`--plan` requires `--operation uninstall|purge` and exits without effects.
Unattended ordinary removal requires `--apply --yes`; purge requires
`--purge --yes`. Requiring distinct purge syntax prevents an ordinary uninstall
request from escalating into deletion of user state. The plan separately
identifies removed, preserved-modified, preserved-mutable, skipped-foreign,
recurring-teardown, and uncertain actions so ownership uncertainty is visible
before approval.

**How it fixes the problem:** Removal becomes reachable through a safe declared
boundary rather than private scripts or LLM-authored deletion commands. The
separate plan and confirmation rules protect modified, foreign, and mutable
state from being treated as ordinary owned artifacts.

**Files:**

- Create: `skills/install-assistant-tools/_rtx/_scripts_uninstall.py`
- Create: `skills/install-assistant-tools/_rtx/tests/test_scripts_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: uninstall tests and `docs/officina/installation.md`
- Regenerate contracts with `famulus:regenerate-blueprints`; do not edit
  generated contract blocks manually.

**Acceptance criteria:**

- [ ] Mutation routes cannot compile without confirmation.
- [ ] Gateway-level tests cover preview, cancellation, stale plans, ordinary
  uninstall, purge, foreign artifacts, modified owned artifacts, and uncertain
  recurring inventory.
- [ ] Uncertain recurring teardown stops before artifact replay.

---

## Task 6: Align contracts, documentation, and release gates

**Problem:** Public instructions and current behavior disagree, and existing
tests validate several old behaviors rather than the intended contract.

**Change:** After Tasks 1–5 stabilize behavior, update the public contract once:

- bump `scripts-install@2` to `scripts-install@3`;
- regenerate source and gateway contracts once;
- give v2 callers an explicit migration error instead of silently interpreting
  old arguments under new semantics;
- update `SKILL.md`, the installation guide, and README to describe only
  implemented behavior;
- add end-to-end semantic tests for the complete standard, development,
  reapply, and removal flows.

Internal implementation terminology should use `standard` when it means the
public standard context; `plugin` remains appropriate only for host plugin
discovery.

The version bump is deliberately last. Regenerating contracts while behavior is
still changing would create repeated generated churn and make it harder to tell
whether documentation describes the final interface.

**How it fixes the problem:** Code, generated contracts, tests, and user-facing
instructions become one testable release surface. Existing v2 callers also get
a deterministic failure with migration guidance instead of an ambiguous
behavior change.

**Files:**

- Regenerate: `skills/install-assistant-tools/SKILL.md` and interface contracts
- Modify: `docs/officina/installation.md`
- Modify: `README.md`
- Modify: semantic installer and contract tests

**Acceptance criteria:**

- [ ] Every documented executable route is declared, and every declared route
  is documented.
- [ ] Standard and development tests cover all five stages, confirmation,
  cancellation, no pre-confirmation mutation, verification, and Stage 5.
- [ ] Reapply tests prove preservation of backend and helpers.
- [ ] Uninstall and purge tests use the declared gateway rather than private
  implementation imports.

## Verification

Run focused tests while implementing each task, then run:

```text
./repo_checks.py --task tests:shared \
  --selector tests/test_install_release_artifact_routes.py \
  --selector tests/test_dispatcher_route_smoke.py \
  --selector skills/install-assistant-tools/_rtx/tests/test_install.py \
  --selector skills/install-assistant-tools/_rtx/tests/test_doctor.py \
  --selector skills/install-assistant-tools/_rtx/tests/test_scaffold.py \
  --selector skills/install-assistant-tools/_rtx/tests/test_launchers.py \
  --selector skills/install-assistant-tools/_rtx/tests/test_uninstall.py \
  --selector tests/test_officina_install_doctor.py \
  --selector tests/test_officina_install_qualification.py \
  --selector tests/test_officina_install_lifecycle_lock.py \
  --selector tests/test_officina_install_result.py \
  --selector tests/test_officina_blueprint_graph.py
./repo_checks.py --suite validators
./repo_checks.py --suite full --verbose
```

The `Python Tests` workflow must pass the exact candidate SHA for Ubuntu,
macOS, and Windows `unified installation lifecycle` jobs. Candidate-route and
lifecycle tests may not skip. Claude client-install health is required on all
three platforms. Codex host enforcement is required on Linux; Windows must
instead pass its native launcher, context, doctor, and uninstall qualifications.

Finally, perform disposable standard and development lifecycles through the
registered candidate artifact, including preview, confirmation, reapply,
diagnosis, active qualification, reload guidance, uninstall, and purge.

## Ship gate

The installer component is ready only when:

- every declared interface resolves inside the exact candidate artifact;
- no install or removal mutation occurs before complete confirmation;
- reapply preserves durable state unless an explicit change is approved;
- doctor remains passive and active qualification is bounded;
- skill text and public documentation match implemented behavior;
- local, hosted-platform, and disposable lifecycle qualifications pass.

Publication also requires the repository-wide mechanism in
`docs/plans/unified-release-mechanism.md`: synchronized versions and manifests,
changelog, runtime lock, notices and licenses, repository and secret checks,
immutable tag and GitHub Release, and public Claude/Codex install verification.
That mechanism is still marked proposed; component readiness alone is not
permission to publish.

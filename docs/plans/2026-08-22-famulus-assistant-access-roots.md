# Famulus Assistant Access Roots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Codex and Claude the minimum durable access required by Famulus workflows while preserving user configuration and supporting exact, crash-recoverable reversal.

**Architecture:** Bind canonical access roots to one validated `InstallationContext`; never persist process-local path overrides. Reconcile those roots into Codex user TOML and Claude user JSON with distinct manifest ownership records and a recoverable pending-operation journal. Separate deterministic three-OS configuration tests from genuine host-enforcement probes.

**Tech Stack:** Python standard library, `FamulusPaths`/`InstallationContext`, installer manifest, pytest, GitHub Actions on Ubuntu/macOS/Windows.

**Spec:** `docs/plans/unified-famulus-installation.md`

## Global Constraints

- Canonical roots are milestone logs, recurring-task config/state, email-triage state, list-manager lock/cache state, and the existing `selected_home/.local/share/llm-wakeup` store on all three platforms.
- Never grant whole Famulus config/data/state roots, Google credential roots, runtime/skill directories, caches outside managed state, assistant homes, or installer state.
- `resolve_assistant_access_roots(context)` accepts no ambient home/platform/override input. Add the selected home to the versioned installation-context record. Canonicalize existing path components in both modes; reject overlaps with credential, runtime, assistant-home, and installer-state roots, and additionally require development roots beneath `<checkout>/.famulus`.
- Managed launches ignore `ASSISTANT_LOGS`, `EMAIL_TRIAGE_STATE_DIR`, `LIST_MANAGER_CLOUD_LOCK_DIR`, and `LLM_WAKEUP_HOME`; these remain explicit process-local compatibility/test overrides only and are never copied into host configuration.
- Standard installation is a singleton bound to canonical Codex and Claude homes. Reapply with different homes fails with recovery guidance; development installations remain isolated per checkout.
- Reapply is idempotent. Uninstall removes only unchanged values introduced by the selected installation. Missing, malformed, edited, duplicate, orphaned, nested, or ambiguous ownership evidence is preserved and reported.
- Codex hooks and access roots use separate marked blocks in the same `config.toml`; the access block is confined to the selected `writable_roots` array. Claude hooks remain in `settings.local.json`; access roots are manifest-owned values in `settings.json`, because JSON has no comments.
- Writing `recurring_config_root` grants scheduled-command authority and may expose secrets users embedded in job strings. Installation preview/documentation must state that accepted risk and recommend indirect credential references; this change does not claim to enforce a secret-free command grammar. Credential directories remain excluded.
- Evidence labels are strict: `config`, `OS-write`, or `host-enforcement`. One cannot satisfy another.

## Audit Record

- Lifecycle and reversibility audit: PASS; no remaining findings.
- Least-privilege and path-boundary audit: PASS; no remaining findings.
- Cross-platform test/evidence audit: PASS; Node 22, pinned clients, evidence labels, Linux Codex enforcement, protected Claude qualification, and unverified IDE/app surfaces are explicitly scoped.
- User decision: use separate Famulus-owned blocks for Codex hooks and Codex access roots.

---

### Task 1: Bind and normalize assistant-facing state

**Files:**

- Create: `src/officina/install/assistant_access.py`
- Modify: `src/officina/install/context.py`
- Modify: `src/officina/install/__init__.py`
- Modify: `src/officina/install/managed_runtime.py`
- Modify: `src/officina/install/runtime_pointer.py`
- Modify: `skills/email-triage/_rtx/_decision_sink.py`
- Modify: `skills/email-triage/_rtx/_log_compactor.py`
- Modify: `skills/list-manager/_rtx/_yaml_store.py`
- Test: `tests/test_officina_assistant_access.py`
- Test: `tests/test_officina_managed_runtime.py`
- Test: `tests/test_officina_runtime_pointer.py`
- Test: affected email-triage and list-manager tests

**Interfaces:**

- Produces: `resolve_assistant_access_roots(context: InstallationContext) -> tuple[Path, ...]` and a versioned context record containing `selected_home`.
- Consumes: existing `FamulusPaths`, standard/development context validation, runtime publication, and pointer loading.

- [ ] **Step 1: Write failing path and boundary tests.** Cover Linux/XDG, macOS, Windows/AppData, Unicode/spaces, ignored process overrides, development containment, and standard/development symlinks into credential/runtime/assistant/install roots. Assert the exact ordered roots and explicit absence of excluded roots.
- [ ] **Step 2: Run the new tests and verify RED.** Run `python3 -m pytest -q tests/test_officina_assistant_access.py tests/test_officina_managed_runtime.py tests/test_officina_runtime_pointer.py`; failures must identify missing `selected_home`, missing resolver, or unguarded boundaries.
- [ ] **Step 3: Implement the minimal context and resolver changes.** Version the installation-context record, persist and validate canonical `selected_home` through managed-runtime publication and `load_context_from_pointer()`, and implement the pure resolver. Preserve the legacy wakeup location on every OS; use `state_root/list-manager/{locks,cache}` for list-manager.
- [ ] **Step 4: Write failing consumer migration tests.** Require `email_triage_state_root/triage.log`, guarded copy-only migration when the destination is absent, managed list-manager cache/lock locations, and managed-launch override clearing.
- [ ] **Step 5: Run consumer tests and verify RED.** Run the affected email-triage, list-manager, and managed-runtime tests; failures must show the legacy runtime-owned paths or honored managed overrides.
- [ ] **Step 6: Implement the minimal consumer migrations.** Never grant either runtime directory; retain overrides only on explicitly unmanaged/test entrypoints.
- [ ] **Step 7: Run the complete Task 1 slice and verify GREEN.** Run all path, context, consumer, email-triage, and list-manager tests named by the changed files.

### Task 2: Add crash-recoverable host configuration ownership

**Files:**

- Create: `skills/install-assistant-tools/_rtx/_assistant_access_config.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: `src/officina/install/doctor.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_assistant_access_config.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_dev_link_hooks.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Test: `tests/test_officina_install_doctor.py`

**Interfaces:**

- Consumes: Task 1 `resolve_assistant_access_roots(context)` and the selected-home context field.
- Produces: mandatory `reconcile_assistant_access(context, manifest)` apply step; manifest kinds `codex_access_array_block` and `json_array_values`; read-only doctor status and reversible uninstall handling.

- [ ] **Step 1: Write failing Codex grammar tests.** Cover absent/existing table and key, inline-to-multiline normalization, pre-existing roots, CRLF, non-string arrays, duplicate tables/keys, all malformed marker arrangements, and coexistence with the current hook block. Require only introduced roots between `# >>> famulus-access >>>` and `# <<< famulus-access <<<` inside the one selected array while preserving foreign order.
- [ ] **Step 2: Write failing Claude structural tests.** Target exactly `context.claude_home/settings.json`; prove `settings.local.json` remains untouched. Cover absent/existing/wrong-type/malformed JSON, duplicate/pre-existing roots, unrelated edits, reapply, and uninstall.
- [ ] **Step 3: Run configuration tests and verify RED.** Run `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_assistant_access_config.py`; failures must identify the absent reconciler and ownership kinds.
- [ ] **Step 4: Implement deterministic config transforms and ownership records.** Add distinct `codex_access_array_block` and `json_array_values` kinds so the existing hook `marker_block` cannot collide. Bind standard manifests to canonical Codex/Claude homes and reject a changed-home reapply; keep development manifests checkout-local.
- [ ] **Step 5: Write failing crash, reapply, and uninstall tests.** Cover durable intent before write, immediately-before-replace external edits, after write/before manifest commit, after commit, modified owned content, created-file deletion, file-mode preservation, two-standard-home rejection, and two-development-checkout isolation.
- [ ] **Step 6: Run lifecycle tests and verify RED.** Run focused manifest/install/uninstall tests and require failures at the missing journal, compare-and-replace, recovery, or manifest-replay behavior.
- [ ] **Step 7: Implement the minimal recoverable transaction.** Record target, created-file fact, pre/post identities, ownership data, and mode before replacement. Replace only when current bytes match the preimage. Atomically write plus flush/fsync and directory sync where supported. Recovery settles only a proven pre-state or intended post-state. Delete a created file only when verified removal leaves it empty.
- [ ] **Step 8: Wire mandatory apply and read-only diagnosis.** Reconcile before verification in standard and development apply. Doctor checks the exact target, roots, ownership state, pending operations, and privileged recurring-config warning.
- [ ] **Step 9: Run all Task 2 tests and verify GREEN.** Run focused config, manifest, hook, install, uninstall, and doctor tests.

### Task 3: Prove configuration and access without overstating coverage

**Files:**

- Create: `skills/install-assistant-tools/_rtx/tests/assistant_access_probe.py`
- Create: `skills/install-assistant-tools/_rtx/tests/test_assistant_access_e2e.py`
- Modify: `.github/workflows/python-tests.yml`

**Interfaces:**

- Consumes: installed Codex/Claude access configuration from Task 2.
- Produces: structured `.repo-checks/assistant-access-<os>.json` evidence labeled `config`, `OS-write`, or `host-enforcement`.

- [ ] **Step 1: Write failing probe tests.** `config/OS-write` must prove the synthetic sibling control is absent from policy and write only allowed canaries. `host-enforcement` must attempt allowed and control canaries and require success/denial respectively. Test structured evidence and cleanup after success or partial failure.
- [ ] **Step 2: Run probe tests and verify RED.** Run `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_assistant_access_e2e.py`; failures must identify the missing probe behavior.
- [ ] **Step 3: Implement the minimal probes and deterministic matrix integration.** In the unified Ubuntu/macOS/Windows job, install into temporary homes and require exact policy resolution, valid host config, foreign-setting preservation, reapply stability, OS-write canaries, configuration-layer denied assertions, and uninstall restoration. Set `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HOME`/`USERPROFILE`, and AppData/XDG variables explicitly.
- [ ] **Step 4: Add pinned client qualification.** Use `actions/setup-node@v4` with Node 22, `@openai/codex@0.149.0`, and `@anthropic-ai/claude-code@2.1.237`. Require the pinned `codex --version` on Linux and run allowed/denied probes through `codex sandbox` without `--add-dir` or bypass flags; label only that Linux result `host-enforcement`.
- [ ] **Step 5: Scope Claude and IDE evidence honestly.** Run `claude doctor` on all runners as `client-install-health` only. Gate authenticated three-OS Claude probes behind `FAMULUS_RUN_CLAUDE_ACCESS_SMOKE=1`. Treat skipped probes as no evidence. Treat Codex VS Code/app inheritance as unverified until a dedicated extension harness exists.
- [ ] **Step 6: Route tests and artifacts explicitly.** Add focused tests to unified `tests:shared` selectors and upload `.repo-checks/assistant-access-<os>.json` separately.
- [ ] **Step 7: Run Task 3 and repository checks.** Run focused tests and `repo_checks.py` locally. Before completion require the deterministic three-OS matrix and Linux Codex enforcement smoke; report Claude and IDE/app qualification as run, skipped, unsupported, or unverified.

## Completion Criteria

- Install/reapply yields the exact canonical roots without granting credential or runtime trees.
- Crash recovery and uninstall preserve foreign or edited configuration and reverse only proven Famulus ownership.
- Deterministic policy/configuration/OS-write tests pass on Linux, macOS, and Windows.
- Linux Codex allowed/denied sandbox controls pass. Claude authenticated and Codex IDE/app enforcement are claimed only when their dedicated qualification actually ran.

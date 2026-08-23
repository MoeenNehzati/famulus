# Famulus Assistant Access Roots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow TDD and stop commit-ready; do not commit without explicit approval.

**Goal:** Give Codex and Claude the minimum durable access needed by Famulus workflows while preserving user configuration and supporting exact, crash-recoverable reversal.

**Architecture:** Bind canonical access roots to one validated `InstallationContext`; never persist process-local path overrides. Reconcile those roots into Codex user TOML and Claude user JSON with distinct manifest ownership records and a recoverable pending-operation journal. Separate deterministic three-OS configuration tests from genuine host-enforcement probes.

**Tech Stack:** Python standard library, `FamulusPaths`/`InstallationContext`, installer manifest, pytest, GitHub Actions on Ubuntu/macOS/Windows.

**Spec:** `docs/plans/unified-famulus-installation.md`, extended by the constraints below.

## Global constraints

- Canonical roots are milestone logs, recurring-task config/state, email-triage state, list-manager lock/cache state, and the existing `selected_home/.local/share/llm-wakeup` store on all three platforms.
- Never grant whole Famulus config/data/state roots, Google credential roots, runtime/skill directories, caches outside managed state, assistant homes, or installer state.
- `resolve_assistant_access_roots(context)` accepts no ambient home/platform/override input. Add the selected home to the versioned installation-context record. Canonicalize existing path components in both modes; reject overlaps with credential, runtime, assistant-home, and installer-state roots, and additionally require development roots beneath `<checkout>/.famulus`.
- Managed launches ignore `ASSISTANT_LOGS`, `EMAIL_TRIAGE_STATE_DIR`, `LIST_MANAGER_CLOUD_LOCK_DIR`, and `LLM_WAKEUP_HOME`; these remain explicit process-local compatibility/test overrides only and are never copied into host configuration.
- Standard installation is a singleton bound to canonical Codex and Claude homes. Reapply with different homes fails with recovery guidance; development installations remain isolated per checkout.
- Reapply is idempotent. Uninstall removes only unchanged values introduced by the selected installation. Missing, malformed, edited, duplicate, orphaned, nested, or ambiguous ownership evidence is preserved and reported.
- Writing `recurring_config_root` grants scheduled-command authority and may expose secrets users embedded in job strings. Installation preview/documentation must state that accepted risk and recommend indirect credential references; this change does not claim to enforce a secret-free command grammar. Credential directories remain excluded.
- Evidence labels are strict: `config`, `OS-write`, or `host-enforcement`. One cannot satisfy another.

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

- [ ] Write failing Linux/XDG, macOS, Windows/AppData, Unicode/space, override, and development-isolation tests. Cover standard and development symlinks into credential/runtime/assistant/install roots. Assert exact roots and explicit absence of excluded roots.
- [ ] Version the installation-context record, persist and validate canonical `selected_home` through managed-runtime publication and `load_context_from_pointer()`, and implement pure `resolve_assistant_access_roots(context)`. Preserve the legacy wakeup location on every OS; use `state_root/list-manager/{locks,cache}` for list-manager.
- [ ] Move `email-triage/_rtx/triage.log` to `email_triage_state_root/triage.log` with guarded copy-only legacy migration when the destination is absent. Move list-manager category cache from `_rtx/tmp` to its managed cache root. Never grant either runtime directory.
- [ ] Make managed execution consume canonical context paths and clear path overrides; retain overrides only on explicitly unmanaged/test entrypoints.
- [ ] Run the path, context, consumer, email-triage, and list-manager tests.

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

- [ ] Write failing Codex grammar tests for absent/existing table and key, inline-to-multiline normalization, pre-existing roots, CRLF, non-string arrays, duplicate tables/keys, and all malformed marker arrangements. Place only introduced roots between `# >>> famulus-access >>>` and `# <<< famulus-access <<<` inside the one selected array; preserve foreign order outside it.
- [ ] Write failing Claude tests targeting exactly `context.claude_home/settings.json`; leave `settings.local.json` untouched. Cover absent/existing/wrong-type/malformed JSON, duplicate/pre-existing roots, unrelated edits, reapply, and uninstall.
- [ ] Introduce distinct `codex_access_array_block` and `json_array_values` manifest kinds so the existing Codex hook `marker_block` cannot collide. Bind standard manifests to canonical Codex/Claude homes and reject a changed-home reapply; verify development manifests remain checkout-local.
- [ ] Record a durable pending operation before each config replacement, including target, created-file fact, pre/post identities, ownership data, and file mode. Replace only after a confined compare-and-replace proves the current bytes still equal the recorded preimage; an intervening edit preserves both config and pending intent. Commit only after atomic write plus flush/fsync and directory sync where supported. Recovery may settle only a proven pre-state or intended post-state.
- [ ] Preserve an existing file's mode; create new user config with a conservative mode. Reapply replaces only unchanged owned content. Uninstall uses the same atomic helper and deletes a file only when `created_file` is true and verified removal leaves it empty.
- [ ] Wire reconciliation into mandatory standard/development apply before verification. Extend doctor with read-only checks for exact target, roots, ownership state, pending operations, and the privileged recurring-config warning.
- [ ] Add fault-injection tests before write, immediately-before-replace external edits, after write/before manifest commit, and after commit; coexistence tests with the current Codex hook block; two-standard-home rejection tests; and two-development-checkout isolation tests.
- [ ] Run all focused config, manifest, hook, install, uninstall, and doctor tests.

### Task 3: Prove configuration and access without overstating coverage

**Files:**

- Create: `skills/install-assistant-tools/_rtx/tests/assistant_access_probe.py`
- Create: `skills/install-assistant-tools/_rtx/tests/test_assistant_access_e2e.py`
- Modify: `.github/workflows/python-tests.yml`

- [ ] Build two explicit probe modes. `config/OS-write` asserts the synthetic sibling control is absent from policy and writes only allowed canaries; `host-enforcement` attempts allowed and control canaries and requires success/denial respectively. Emit only structured path/status evidence under `.repo-checks`; test cleanup on success and partial failure.
- [ ] In the Ubuntu/macOS/Windows unified-installation matrix, install into temporary homes and require exact policy resolution, valid host config, foreign-setting preservation, reapply stability, OS-write canaries, denied-policy assertions at the configuration layer, and uninstall restoration. Set `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HOME`/`USERPROFILE`, and AppData/XDG variables explicitly.
- [ ] Add `actions/setup-node@v4` with Node 22 plus `@openai/codex@0.149.0` and `@anthropic-ai/claude-code@2.1.237` to the unified job. Require the pinned `codex --version` on Linux, then run allowed and denied probes through `codex sandbox` with no `--add-dir` or bypass flags. Label this Linux-only result `host-enforcement`; macOS/Windows remain `config` plus `OS-write` until a native harness exists.
- [ ] Run `claude doctor` on all runners as `client-install-health` evidence only; JSON parsing/reconciliation tests provide `config` evidence. Add `FAMULUS_RUN_CLAUDE_ACCESS_SMOKE=1` authenticated allowed/denied probes for protected/manual three-OS qualification; a skipped smoke proves nothing.
- [ ] Treat Codex VS Code/app inheritance as documented upstream behavior, not tested enforcement. Add a dedicated extension harness later or report those surfaces as unverified; do not use the CLI sandbox result as their proof.
- [ ] Add the new focused tests explicitly to unified `tests:shared` selectors rather than relying on `tests:install` or skip-prone plugin/lifecycle tests. Upload `.repo-checks/assistant-access-<os>.json` separately.
- [ ] Run focused tests and `repo_checks.py` locally. Before completion, require the deterministic three-OS matrix and Linux Codex enforcement smoke; report Claude and IDE/app qualification separately as run, skipped, unsupported, or unverified.

## Completion criteria

- Install/reapply yields the exact canonical roots without granting credential or runtime trees.
- Crash recovery and uninstall preserve foreign or edited configuration and reverse only proven Famulus ownership.
- Deterministic policy/configuration/OS-write tests pass on Linux, macOS, and Windows.
- Linux Codex allowed/denied sandbox controls pass. Claude authenticated and Codex IDE/app enforcement are claimed only when their dedicated qualification actually ran.

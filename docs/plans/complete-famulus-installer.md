# Complete Famulus Installer Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` to execute one remaining task at a time. Update
> this file's status and evidence before advancing to the next task.

**Goal:** Make a successful Phase 1 result mean that the complete selected
Famulus profile is installed, verified, recoverable, and exactly uninstallable.

**Architecture:** One per-home lock encloses runtime preparation, retained
credential-worker certificate operations, pointer activation, journaled owner
mutations, final profile verification, pruning, and journal completion. Durable
intents and exact resource observations distinguish resumable expected/intended
states from terminal third states.

**Tech stack:** Python 3.11 managed by `uv`, `keyring==25.6.0`, Ed25519 through
`cryptography`, native file locks and credential stores, confined atomic-file
helpers, JSON transaction/manifest records, repository blueprints, and pytest.

> **Canonical plan:** This is the single source of truth for the complete
> Famulus installer workstream. Historical SDD reports under `.superpowers/`
> are execution evidence, not competing plans.

Status: active

Last updated: 2026-08-14

Branch: `codex/isolated-llm-testing`

Current base commit: `3af05b0babc7141be6508677c456e42024e10e37`

## Vision

A successful Phase 1 installation means that the complete selected Famulus
profile is installed, usable, attributable to one manifest, and recoverable
after interruption. Success is not “some launchers were copied.” It means:

- the active Officina runtime is an immutable, verified managed release rather
  than ambient Python;
- a supported native credential store was selected and round-trip tested;
- the certificate private key, public record, and active selector agree and can
  be reopened by a fresh managed process;
- every selected command, profile, PATH/config entry, and required directory is
  installed and smoke-tested;
- every owned mutation was journaled before its effect and reflected in the
  uninstall manifest after verification;
- interruption before activation preserves the previous installation, while
  interruption after activation leaves an exact resumable transaction;
- uninstall removes only this installation's ownership, and purge removes
  retained runtime and certificate state only through explicit verified APIs;
- the same public package installation is proven in an isolated VM without
  exposing the maintainer checkout or secrets.

The installer remains script-owned and deterministic. The LLM may choose and
invoke a supported workflow, but it does not improvise mutation order, trust
decisions, recovery, or deletion.

## Definition of done

This workstream is complete only when all of the following are true:

1. `_phase_entry.py` is the sole fresh-install entrypoint and holds one
   per-home `InstallLock` across recovery, preparation, activation, owner
   mutations, verification, pruning, and journal completion.
2. Minimum and maximal profiles install their exact declared command and
   dependency closure without ambient interpreter fallback.
3. Credential preflight, certificate preparation, selector commit, and restart
   recovery run through one retained audited managed worker and closed protocol.
4. Every non-dry scaffold, launcher, developer-link, logical configuration, and
   ownership mutation passes through `MutationRecorder` or a specialized
   certificate transaction.
5. Recovery handles process death at every durable boundary without guessing,
   following untrusted paths, deleting a third state, or leaking secret bytes.
6. Default uninstall and explicit purge are reference-safe, serialized, and
   idempotent.
7. Local lifecycle tests, repository validators, full hooks, and platform
   contracts pass without new skips or bypasses.
8. Candidate and published-package installation pass the isolated VM acceptance
   scenario with sanitized evidence and the configured cheap acting-LM tier.
9. Public installation documentation describes the exact executable workflow,
   prerequisites, success result, recovery route, and current limitations.

## Non-negotiable constraints

- Certificate signing is mandatory; no degraded unsigned installation exists.
- Only audited native keyring backends are accepted in production.
- Secrets never enter argv, environment variables, journals, manifests, logs,
  diagnostics, reports, or serialized worker responses.
- Packages are installed only into the managed runtime.
- A durable journal records intent before every owned effect.
- A changed request or third observed state fails closed.
- Pre-activation failure preserves the previous runtime and certificate.
- Post-activation failure retains an exact recoverable journal; it is not
  reported as rollback.
- Dry-run performs zero writes, including lock/state-root creation.
- No compatibility branch may restore manifest-after-effect behavior.
- No hook is bypassed and no platform test is labeled live unless it ran on that
  native platform.

## Target architecture and transaction order

The final Phase 1 order is:

```text
parse and validate selections
  -> acquire per-home InstallLock
  -> load and recover transaction journal
  -> prepare and verify candidate managed runtime
  -> start retained managed credential worker
  -> probe audited native store
  -> prepare certificate mutation intent
  -> durably acknowledge and apply certificate candidate state
  -> save prepared transaction
  -> activate runtime pointer
  -> mark transaction committed
  -> durably acknowledge and commit certificate selector
  -> run scaffold owner through MutationRecorder
  -> run optional developer-link owner through MutationRecorder
  -> run selected launcher owner through MutationRecorder
  -> verify complete selected profile from stable entrypoints
  -> save retained runtime and certificate ownership
  -> prune eligible releases
  -> mark transaction complete and remove journal
  -> release worker and InstallLock
```

The transaction has two recovery regions:

- **Before runtime activation:** preserve the old pointer and certificate
  selector; remove only exact candidate runtime/certificate material.
- **After runtime activation:** retain and resume the journal. Recovery verifies
  the active pointer, certificate selector, completed mutation IDs, actual
  resource states, and manifest ownership before continuing.

## Current implementation status

### Completed and committed

| Unit | Result | Commit(s) |
|---|---|---|
| Home-scoped locking and durable records | Confined persistent lock; atomic manifest/journal; exact recovery invariants | `5fd240e1` |
| Stable certificate paths and migration | Stable Famulus data root; fail-closed legacy migration and enumeration | `907d3b16`, `95eb2f53`, `1528fee9` |
| Native credential preflight | Pinned `keyring==25.6.0`; closed JSON child protocol; backend allowlist and containment | `30026458`, `6bea3129` |
| Managed runtime preparation | Prepared release, immutable resolver bundles, pointer v3, safe activation and pruning | `1dc65787`, `68f0d72b` |
| Transactional certificate primitives | Exact stage/commit/abort and cleanup authority | `76bbd5fe`, `0e4fcbc6` |
| 6A managed credential worker | Retained audited backend, bounded explicit-exec transport, cleanup and PID absence proof | `00e6efb0` through `8cb3e3bb` |
| 6B1 certificate intent authority | Closed canonical intent schema and restart-addressable public-file quarantine authority | `99ba56ea` |
| 6B2 certificate lifecycle/recovery | Nonmutating prepare, deterministic selector build/stage state machine, restart recovery | `ddd896d4` |
| 6B3 worker/journal handshake | Certificate worker commands, durable ACK ordering, child-death recovery | `8404ab39` |
| 6C1 generic mutation recorder | Journal v3 logical resources, deterministic IDs, ownership deltas, bounded observers | `3af05b0b` |

Every committed unit above completed focused TDD, scoped independent review,
repository validators, gitleaks, and its normal hook. Native Windows retained-
handle behavior remains platform-CI evidence where noted in the corresponding
tests and blueprints.

### Implemented and reviewed, currently staged

#### 6C2A: deterministic publication and shared helper propagation

Status: complete and independently reviewed clean; not committed because four
unmodified 6C2B/6C3 callers keep the full hook red.

Implemented behavior:

- `MutationRecorder` invokes `apply(JournalMutation)` only after durable pending
  publication and a second exact observation.
- `atomic_publish_bytes`, `atomic_publish_symlink`,
  `atomic_publish_empty_directory`, and `atomic_unlink_exact_symlink` use
  mutation-ID-derived `.famulus-build-*` authority.
- POSIX publication retains no-follow parent descriptors, validates one-link
  build identity/content/mode/name, synchronizes file and parent, and resumes
  partial or read-only builds after process death.
- Native publication retains no-reparse handles and checks
  `FILE_STANDARD_INFO.NumberOfLinks == 1` before read-only repair, after
  write-data reopen, and immediately before truncation.
- Recorder-owned regular-file intentions must remain owner-readable so final
  digest observation is possible; mode `0000` is rejected before pending/effect.
- `make_link`, `make_copy`, shell-rc replacement, and base/platform launcher
  installation require a recorder and stable operation key for live calls.
- Source reads are bounded and revalidated; rc input has a closed UTF-8/export/
  marker grammar and atomic full-file publication.

Exact staged implementation inventory:

| Path | Staged responsibility |
|---|---|
| `skills/install-assistant-tools/_rtx/_state_record.py` | Changes `MutationRecorder.mutate(..., apply=...)` so `apply` receives the exact durable `JournalMutation`; rejects unpublishable or owner-unreadable intended file modes before pending state or effects; strengthens live-name file observation. |
| `skills/install-assistant-tools/_rtx/_fs_links.py` | Makes `make_link` and `make_copy` recorder-owned live operations with stable operation keys, bounded source reads, source identity revalidation, normalized modes, deterministic publication, and typed missing-recorder/source failures. |
| `skills/install-assistant-tools/_rtx/_shell_block.py` | Makes `ensure_rc_vars` a recorder-owned atomic whole-file mutation; validates one canonical export per requested key, rejects CR/LF/NUL/marker injection and malformed existing blocks, preserves eligible ordinary mode, and reparses rendered output. |
| `skills/install-assistant-tools/_rtx/_install_launcher/_base_launcher.py` | Adds stable per-file operation keys, recorder propagation, deterministic generated/static launcher publication, bounded static-source verification, and fail-before-write behavior. |
| `skills/install-assistant-tools/_rtx/_install_launcher/_linux_launcher.py` | Supplies stable scaffold/agent operation keys and propagates the recorder through dispatcher, wakeup, invoke-skill, and agent launcher bundles. |
| `skills/install-assistant-tools/_rtx/_install_launcher/_windows_launcher.py` | Supplies the corresponding native launcher operation keys and recorder propagation while using platform-observable normalized modes. |
| `src/officina/common/atomic_files.py` | Adds bounded regular-file reads; publication-mode normalization; deterministic byte, symlink, empty-directory, and exact-symlink-unlink primitives; restartable POSIX and native retained-authority implementations; hard-link, reparse, mode, target-kind, and crash-boundary checks. |
| `skills/install-assistant-tools/_rtx/blueprints/rtx-init.yaml` | Declares the recorder-owned filesystem/link/copy/rc contracts, private-parent and lock preconditions, owner-readable intended-file rule, and atomic-files dependency. |
| `skills/install-assistant-tools/_rtx/blueprints/rtx-install-launcher-init.yaml` | Declares stable launcher operation keys, mandatory live recorder propagation, deterministic publication, bounded sources, and dry-run zero-write behavior. |
| `src/officina/common/blueprints/atomic-files.yaml` | Declares deterministic build authority, eligible target kinds, normalized mode bounds, retained namespace inputs, byte/symlink/directory/delete effects, recovery rules, and truthful concurrency/platform boundaries. |
| `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py` | Proves the changed `apply(JournalMutation)` ordering, intended-mode rejection, and stale live-name observation boundaries. |
| `skills/install-assistant-tools/_rtx/tests/test_link_utils.py` | Proves missing-recorder refusal, source eligibility/identity revalidation, special-bit removal, and zero pre-publication effects. |
| `skills/install-assistant-tools/_rtx/tests/test_rc_block.py` | Proves exact rc grammar, mode behavior, restart adoption, input bounds, CRLF/marker rejection, and no pending/effect before validation. |
| `skills/install-assistant-tools/_rtx/tests/test_install_launcher.py` | Proves live recorder requirements, dry-run zero writes, stable operation-key propagation, bounded sources, and normalized copy modes. |
| `tests/test_officina_atomic_files.py` | Proves deterministic-build resume and real child-death boundaries, target/build races, hard-link and special-file refusal, mode repair, native retained-handle order/cleanup, and platform-gated native cases. |

Changed interfaces that later owners must use exactly:

- `MutationRecorder.mutate(..., apply: Callable[[JournalMutation], None]) -> str`;
  the callback receives the already-durable pending record and may derive only
  its deterministic build authority from that record.
- `make_link(src, dst, dry_run, *, recorder, operation_key) -> None` and
  `make_copy(src, dst, dry_run, *, recorder, operation_key) -> None`;
  `recorder=None` is valid only for dry-run.
- `ensure_rc_vars(rc_file, updates, dry_run, *, recorder, operation_key,
  label="user") -> None`; live callers cannot write through a `Manifest`
  fallback.
- Base and platform launcher installation methods accept and propagate
  `recorder`; each `LauncherFileSpec` carries one stable `operation_key`.
- `normalize_publication_mode`, `read_regular_file_bytes_bounded`,
  `atomic_publish_bytes`, `atomic_publish_symlink`,
  `atomic_publish_empty_directory`, and `atomic_unlink_exact_symlink` are the
  common publication boundary. Deterministic operations receive the journal's
  mutation ID as `build_id` and the recorder-captured state as
  `expected_before`.

Embedded documentation is part of this staged unit, not an unrelated rewrite:
all callables and classes in the seven touched production modules were migrated
to the repository's canonical structured docstring format, including exact
repository dependency edges. The three owned blueprints were updated with the
same behavior, effect, recovery, concurrency, and platform boundaries. No
separate tracked implementation report is authoritative.

Known incomplete integrations are explicit:

- `_install_scaffold.py` still calls launcher methods and `ensure_rc_vars`
  through the removed manifest-era signatures; Task 6C2B owns that migration.
- `_agent_launchers.py` still calls `make_link` and `ensure_rc_vars` through the
  removed signatures; Task 6C2C owns that migration.
- `_config_bridge.py` still calls `make_link` and `ensure_rc_vars` through the
  removed signatures; Task 6C3 owns that migration.
- Hook and end-to-end fixtures that directly call the old launcher signature
  remain red until those owner migrations land.
- These are the only accepted stacked-integration failures. New failures are
  not classified as expected debt.

Tracked documentation consolidation in this checkpoint:

- create `docs/plans/complete-famulus-installer.md` as this canonical plan;
- update `docs/plans/isolated-llm-testing.md` to point here;
- delete superseded
  `docs/superpowers/plans/2026-08-11-complete-famulus-installer.md` and
  `docs/superpowers/plans/2026-08-11-complete-famulus-vm-acceptance.md` after
  merging their unique requirements here;
- keep ignored `.superpowers/sdd` briefs/reports only as historical execution
  evidence, explicitly noncanonical and unnecessary for resuming the work.

Evidence at this checkpoint:

- atomic owner suite: `172 passed, 14 skipped`;
- link/rc/launcher owner suites: `45 passed`;
- manifest suite: `142 passed, 4 failed`, exactly the four deferred legacy
  callers and no additional failure;
- validator pool: `274 passed`;
- staged repository/docstring validators: `31 passed`;
- documentation/link/catalog tests: `33 passed`;
- syntax, staged diff, and staged gitleaks: clean;
- final scoped review: clean after five fix/re-review rounds.

Latest normal commit attempt on 2026-08-14: gitleaks passed and the shared
suite produced `2981 passed, 25 skipped, 2 failed, 4 errors`. The two failures
are scaffold install/repair dry-run calls using the removed positional
`manifest` launcher argument. The four setup errors are direct dispatcher
launcher fixtures using the removed `manifest=` keyword. No commit was created;
HEAD remains `3af05b0b`. These failures are resolved only by the production
owner migrations below; compatibility fallbacks and hook bypasses are
forbidden.

The staged implementation consists of 15 source/blueprint/test files. This plan
is the only tracked documentation that accompanies that staged unit.

## Remaining execution plan

### Task 6C2B: journal the scaffold owner

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_scaffold.py`
- Test adjacency: `skills/install-assistant-tools/_rtx/tests/test_install.py`

**Required behavior:**

- [ ] Require a recorder for every non-dry scaffold entry before the first
  directory, launcher, PATH, registry, manifest, or certificate write.
- [ ] Journal each declared bin and shell-rc parent directory separately;
  existing directories retain mode, new executable directories use `0755`, and
  new config directories use `0700`.
- [ ] Install dispatcher, `llm-wakeup`, `lw`, and `invoke-skill` through the
  reviewed 6C2A launcher APIs using stable operation keys.
- [ ] Journal the POSIX PATH block as one exact rc-file mutation.
- [ ] Journal Windows PATH as one logical registry mutation that adds only the
  declared component and preserves type and unrelated components.
- [ ] Remove the ambient scaffold certificate provisioner and its production
  import. Certificate mutation belongs exclusively to the managed worker and
  Task 6D orchestration.
- [ ] Prove dry-run and missing-recorder entrypoints perform zero writes.
- [ ] Inject failure/death before and after every effect and prove exact
  expected/intended recovery with no hidden `parents=True` writes.

**Acceptance gate:** scaffold owner tests, manifest tests, blueprint/platform
tests, repository validators, syntax, staged diff, gitleaks, and scoped review
must pass. The known scaffold legacy failure must disappear.

### Task 6C2C: journal selected agent launchers and fix registry ownership

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_agent_launchers.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-agent-launchers.yaml`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-init.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_launchers.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Test adjacency: `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`

**Required behavior:**

- [ ] Journal bin, Codex/Claude home, worker-root, and per-agent directory
  creation without claiming shared or retained user-data directory ownership.
- [ ] Journal POSIX links and Windows copies for selected commands, the shared
  runtime helper, optional batch files, `tmux-workspace`, and `tw`.
- [ ] Render and atomically publish exact Codex/Claude profile TOML; preserve an
  existing regular machine-local profile as an explicit unowned no-write.
- [ ] Journal Claude setting links and exact deletion of known legacy coder
  symlinks; changed targets fail closed.
- [ ] Journal POSIX `ASSISTANT_DEFAULT` through the rc owner.
- [ ] Add closed `registry_path_component` and `registry_value` manifest entry
  kinds. Never delete the whole Windows PATH value.
- [ ] Preserve an already-exact unowned `ASSISTANT_DEFAULT` without claiming
  it; reject a conflicting unowned value.
- [ ] Remove owner-level verification; Task 6D performs final profile
  verification after all mutations.
- [ ] Prove missing-recorder and dry-run entrypoints are zero-write.

**Acceptance gate:** launcher, uninstall, manifest, blueprint/platform,
validator, syntax, diff, gitleaks, and scoped-review gates pass on the stacked
6C2A–6C2C state.

### Task 6C3: journal developer-link and logical configuration owners

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_config_bridge.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-config-bridge.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_dev_link.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_dev_link_hooks.py`
- Modify state/atomic owners only if a reviewed missing primitive is proven.

**Required behavior:**

- [ ] Route every non-dry config link, JSON/TOML/rc publication, hook chmod,
  exact legacy deletion, Git `core.hooksPath`, and Windows `AI` mutation through
  the recorder with stable resource identities and operation keys.
- [ ] Use the closed Git-config observer/apply environment from 6C1; ambient
  `GIT_DIR`, work-tree, config, index, and object variables cannot redirect it.
- [ ] Atomically publish complete Claude JSON and Codex TOML bytes; no in-place
  truncation or unlink-then-link gap remains.
- [ ] Refuse legacy multi-path skills-directory moves and top-level-link
  conversions before the first developer-link write, with manual remediation
  guidance. Supporting those migrations requires a separate composite journal
  and is not hidden inside the generic recorder.
- [ ] Journal or explicitly classify parent-directory and hook-mode effects;
  no unrecorded write remains.
- [ ] Prove dry-run and missing-recorder paths are zero-write.

**Acceptance gate:** all four legacy 6C2A integration failures are gone; the
complete staged owner stack passes the exact full hook without bypass. Commit
the coherent 6C2 owner migration only after scoped review is clean.

### Task 6D: orchestrate the complete recoverable Phase 1 transaction

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/install.py`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py` only for proven
  orchestration gaps
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`
- Test adjacency: scaffold, launcher, credential, certificate, and runtime suites

**Required behavior:**

- [ ] Implement the exact transaction order defined above under one
  `InstallLock` and one retained managed credential worker.
- [ ] Recover any existing journal before preparing new state.
- [ ] Invoke stable certificate migration, mandatory native-store preflight,
  certificate intent/ACK/apply, runtime activation, selector commit, all 6C
  owners, final verification, retained ownership, pruning, and journal removal
  in that order.
- [ ] Preserve old pointer/selector before activation; retain exact journaled
  state after activation.
- [ ] Minimum profile requires the universal command floor and certificate
  roundtrip. Maximal profile adds every supported selected launcher and declared
  dependency smoke.
- [ ] Direct repair validates the active v3 pointer, resolver bundle, trust
  manifest, managed interpreter, native backend, and certificate before any
  owner mutation.
- [ ] Inject process death or failure at every arrow in the transaction order
  and prove fresh-process recovery.
- [ ] Record retained runtime and certificate entries before pruning, prune only
  after complete verification, and remove the journal last.

**Integration obligations carried from completed units:** invoke certificate
migration under the lock; keep signing mandatory; retain the exact audited
backend instance through certificate operations; serialize rotation; verify
pre-selector load failures cannot orphan a staged pair; call pruning only after
the complete transaction.

**Acceptance gate:** complete installer lifecycle suites, all adjacent owner and
security suites, repository validators, the exact full hook, gitleaks, and
independent review pass without new skips.

### Task 7: implement reference-safe uninstall and purge

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-install-uninstall.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

**Required behavior:**

- [ ] Acquire the same per-home lock and recover/reconcile any transaction
  before reading or replaying manifest ownership.
- [ ] Default uninstall removes only this installation's owned public commands
  and configuration; retain managed runtime and complete certificate lifecycle
  with explicit reasons.
- [ ] Purge verifies and removes exact private certificate targets before public
  state, then removes only ownerless runtime/cache paths under canonical roots.
- [ ] Preserve resources owned by another installation and every user-modified
  third state.
- [ ] Correct legacy registry PATH handling so uninstall never deletes the
  entire value.
- [ ] Prove repeated uninstall/purge, partial committed install, failure during
  secret/public removal, contention, dead lock owner, and user sentinels.

**Acceptance gate:** uninstall and end-to-end lifecycle suites plus certificate,
lock, blueprint, validator, hook, gitleaks, and independent-review gates pass.

### Task 8: automate complete installation acceptance

**Files:**

- Modify: `skills/install-assistant-tools/_rtx/tests/install_test_utils.py`
- Create: `skills/install-assistant-tools/_rtx/tests/test_complete_install_profiles.py`
- Modify: Codex/Claude local and GitHub installation tests
- Modify: `.github/workflows/python-tests.yml`
- Modify: `tests/test_repository_test_checks.py`

**Required behavior:**

- [ ] Map every declared runtime dependency and public command to a minimum or
  maximal profile probe or an explicit reviewed platform exclusion.
- [ ] Run local production-shaped Codex and Claude plugin lifecycle tests:
  install, fresh-process certificate reload, command smokes, reinstall without
  rotation, default uninstall, rediscovery, and purge.
- [ ] Maintain checked-in native-platform skip allowlists and fail on every
  unapproved skip.
- [ ] Run credential/certificate native jobs on Linux, macOS, and Windows; mocks
  do not satisfy the native retained-handle/registry requirement.
- [ ] Separate deterministic product failures from sandbox-only network or
  loopback restrictions, but never convert a required acceptance run to skip.

**Acceptance gate:** local full suite, native CI matrix, inventory/skip policy,
repository validators, hooks, and independent review are green.

### Task 9: publish the canonical operator workflow

**Files:**

- Modify: `README.md`
- Modify: `docs/officina/installation.md`
- Modify: `skills/install-assistant-tools/SKILL.md`
- Modify generated skill/catalog documentation through its owning generator

**Required behavior:**

- [ ] Document prerequisites, plugin and development modes, the sole Phase 1
  entrypoint, minimum/maximal profiles, expected success report, and exact
  retry/recovery route.
- [ ] Remove direct live scaffold/launcher mutation instructions; repair routes
  must acquire the same lock and recorder.
- [ ] Document default uninstall versus purge and retained credential/runtime
  behavior.
- [ ] Keep Google and recurring onboarding outside the core installation verdict.
- [ ] Regenerate derived docs and validate every public command/example.

**Acceptance gate:** documentation, generated-doc, link/catalog, blueprint,
repository validator, full hook, and independent final-plan review are green.

### Final gate: isolated VM candidate and public-package acceptance

After Tasks 6D–9 are committed and green, run both the committed candidate and
the published package through one versioned verifier and profile matrix in
fresh Ubuntu 24.04 QEMU/KVM overlays.

Global VM constraints:

- Never mount or expose the maintainer checkout to the guest.
- Candidate source, documentation, command catalog, scenario, and acting-model
  configuration are immutable and digest-bound before guest transfer.
- Secret stdin bytes never enter argv, environment, manifests, logs, serial
  output, retained files, or reports.
- Acceptance runs as the unprivileged guest. Installation of declared OS
  prerequisites is a separate recorded operator action.
- Missing KVM, assistant CLI, Secret Service prerequisite, required network, or
  an approved native platform is a failed dedicated acceptance run, not a skip.
- One persistent session means one supervised D-Bus/Secret Service lifetime for
  one scenario run; it does not persist across logout, reboot, or overlay reuse.
- The default acting-LM tier is `cheap`, resolved once from a digest-bound
  configuration to `gpt-5.6-luna` and passed explicitly as
  `codex exec --model <resolved-model>`. Ambient or implicit expensive-model
  selection is forbidden.
- Candidate and public runs use independent clean overlays. Minimum and maximal
  profiles also use independent overlays; reinstall is an in-run idempotency
  check, not a substitute for a fresh profile baseline.

#### VM-1: immutable candidate and documentation inputs

**Files:**

- Create: `test_support/isolated_lm/artifact.py`
- Modify: `test_support/isolated_lm/model.py`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_artifact.py`
- Test: `tests/test_isolated_lm_cli.py`

**Interfaces:**

- Produce `CandidateArtifact(kind, source_commit, tree_sha256,
  archive_sha256, documentation_sha256, byte_size)`.
- Add `prepare-candidate --archive PATH --docs PATH --provenance PATH`.
- Add exact candidate/document digests and guest staging paths to the run
  manifest.

- [ ] Build from an exact clean commit with `git archive`; independently bind
  commit, tree, canonical `git ls-tree` digest, archive digest, documentation
  digest, and locally trusted provenance.
- [ ] Reject source symlinks, FIFOs, devices, traversal, duplicate archive
  entries, changing or oversized inputs, extraction escape, mismatched Git
  metadata, and cross-filesystem non-atomic publication.
- [ ] Use descriptor-relative no-follow reads, same-filesystem staging, hash
  while copying, file/parent synchronization, and atomic publication. Never
  execute archive content on the host.
- [ ] Transfer only the two recorded immutable files with shell-free exact SSH
  identity/known-host arguments and verify guest SHA-256 before success.

**Gate:** `tests/test_isolated_lm_artifact.py`, `tests/test_isolated_lm_cli.py`,
and `tests/test_isolated_lm_qemu.py` pass.

#### VM-2: bounded secret stdin

**Files:**

- Modify: `test_support/isolated_lm/cli.py`
- Modify: `test_support/isolated_lm/qemu.py`
- Test: `tests/test_isolated_lm_cli.py`
- Test: `tests/test_isolated_lm_qemu.py`

**Interface:** add internal `exec_with_secret_stdin(argv, *,
secret_supplier, max_stdin_bytes=4096) -> BoundedProcessResult`; the result
records only stdin byte count, never the bytes or their digest.

- [ ] Generate the run-specific unlock secret in the host acceptance process,
  retain it only in a mutable bounded buffer, send it directly to SSH stdin,
  and zero the buffer after completion. Never create a host secret file.
- [ ] Keep ordinary public VM `exec` on `DEVNULL` stdin and require an explicit
  command separator for the secret-bearing internal route.
- [ ] Drain stdout/stderr concurrently under existing caps and reject overflow,
  supplier failure, timeout, and interrupted transport without secret-bearing
  diagnostics.
- [ ] Scan SSH argv, `/proc` command lines, environments, JSON, captured streams,
  run manifests, and retained files for literal and encoded secret canaries.

**Gate:** complete isolated-LM CLI and QEMU suites pass.

#### VM-3: supervised Secret Service session

**Files:**

- Create: `test_support/isolated_lm/session.py`
- Modify: `test_support/isolated_lm/model.py`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_session.py`
- Test: `tests/test_isolated_lm_cli.py`

**Interfaces:** add `start-session`, `session-exec`, and `stop-session`; produce
`GuestSessionRecord(session_id, supervisor_pid, bus_address_file, ready_file,
lifecycle)`. The bus environment stays in a guest-private mode-`0600` file and
is never returned in host JSON.

- [ ] Start `dbus-run-session` and `gnome-keyring-daemon --unlock` as the
  unprivileged guest through VM-2's stdin channel.
- [ ] Validate random session ID, supervisor PID/argv, readiness marker,
  bus-address file, bus socket identity, and session relationship before every
  `session-exec`.
- [ ] Cover missing executables, unlock failure, supervisor death, PID reuse,
  socket replacement, unrelated sessions, a second SSH process joining the
  same session, timeout, and teardown.
- [ ] Stop only the recorded supervisor; under one deadline prove its PID/argv
  and exact bus socket are absent. Never broadly kill guest D-Bus/keyring
  processes.

**Gate:** session, CLI, and QEMU suites pass.

#### VM-4: versioned complete-install scenario

**Files:**

- Create: `test_support/isolated_lm/scenario.py`
- Create: `test_support/isolated_lm/scenarios/complete-install-v1.json`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_scenario.py`
- Maintain: `references/installation/complete-install-commands-v1.json`
- Maintain: `references/installation/complete-install-commands-v1.schema.json`

**Interfaces:** add `run-scenario --scenario complete-install-v1
--acquisition {candidate,public} --config PATH`; produce a versioned
`ScenarioReport` with per-step verdict, exact command identity, bounded
evidence, contamination state, resolved acting-model identity, and final
verdict. The acceptance config closes `agent.model_tier`, `agent.models`, and
optional explicit `agent.model_override`.

- [ ] Reject unknown fields/steps, duplicate IDs, free-form or unbounded
  commands, secret-bearing fields, missing cleanup/profile, acquisition-invalid
  assertions, unknown model tiers, and ambient-model fallback.
- [ ] Resolve the acting model once during preflight; record config digest,
  requested tier, resolved model, Codex CLI version, and explicit-override
  status. A changed tier/override produces a separate comparison verdict.
- [ ] Bind every step to a schema-validated command ID from the documentation
  bundle; fail if the catalog digest differs from the manifest record.
- [ ] Record OS prerequisites; acquire candidate or public package; verify
  provenance; run Phase 1 minimum or maximal; probe every required command and
  dependency; reload/sign/verify from a fresh process; reinstall without key
  rotation; run `get-weather` certifier/drift current→stale→current in a
  disposable committed dev checkout; default-uninstall; remove/restart the host
  plugin; prove visibility; reinstall; purge; and run leak/process/socket checks.
- [ ] Give every step one timeout and output cap, always run cleanup after
  failure, use closed error categories, and record any unplanned human action
  as contamination.
- [ ] Seed literal, Base64, hex, escaped, and PEM-like canaries through failure
  paths and scan every JSON object, captured stream, serial log, report, guest
  staging file, extracted artifact, and host manifest.

**Gate:** scenario and CLI suites pass with no implicit expensive-model route.

#### VM-5: sanitized evidence and cleanup certificate

**Files:**

- Create: `test_support/isolated_lm/evidence.py`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_evidence.py`

**Interface:** add `extract-report --output-dir PATH`; publish only
schema-approved report JSON, version inventories, digests, bounded public logs,
and cleanup proof.

- [ ] Allowlist exact guest evidence paths. Reject symlinks, special files,
  traversal, oversized or changing artifacts, unknown fields, digest mismatch,
  and encoded canaries.
- [ ] Transfer into same-filesystem host staging, validate size/schema/digests,
  scan secrets, synchronize, and atomically publish; partial extraction cannot
  publish a successful evidence directory.
- [ ] Certify absence of run-owned QEMU and Secret Service PIDs, bus socket, SSH
  listener, temporary stdin files, guest candidate staging, and report staging.
  Retain overlay/serial output only under the operator's explicit evidence
  policy.

**Gate:** evidence and CLI suites pass.

#### VM-6: committed-candidate acceptance

**Documentation updated by the run:** `docs/isolated-lm-testing.md` and
`docs/plans/isolated-lm-testing.md`; sanitized execution evidence remains
ignored under `.superpowers/sdd` and is never a plan dependency.

- [ ] Require a clean committed candidate, build/digest immutable source,
  documentation, command catalog, scenario, and cheap-model configuration, and
  record the resolved model before either profile.
- [ ] Install and record `dbus-daemon` and `gnome-keyring` prerequisites, then
  prove the sealed baseline contains no Famulus state or reusable keyring
  secret.
- [ ] Run minimum and maximal candidate scenarios from separate fresh overlays
  without unplanned guidance. Missing prerequisites, any skip, contamination,
  secret leak, or unverified cleanup is `fail`.
- [ ] Reserve `inconclusive` only for post-preflight verifier failures with
  closed codes `verifier_crash`, `evidence_corrupt`, or
  `host_observation_lost` when no product assertion can be evaluated.
- [ ] Stop through the manifest-bound command and independently verify exact
  QEMU/listener/session absence.

**Gate:** all isolated-LM host/image/guest/QEMU/CLI/artifact/session/scenario/
evidence suites, repository validators, hooks, and the candidate report pass.

#### VM-7: pinned public-package acceptance

- [ ] With explicit user authorization, merge and publish through the normal
  release path; record public commit, marketplace manifest digest, package
  version, and payload digest. Never substitute a local cache.
- [ ] Start new minimum and maximal overlays from the same sealed baseline and
  install the same prerequisite/host versions. Reuse the candidate run's
  digest-bound model configuration for comparability.
- [ ] Resolve the public marketplace package and require installed source commit
  and payload digest to equal the expected published artifact before any
  post-acquisition assertion counts.
- [ ] Require identical scenario/verifier versions and post-acquisition
  assertion sets. Any behavioral difference fails; explain version-only
  differences.
- [ ] Close package readiness only after candidate and public `PASS`, then run
  documentation, blueprint, platform, repository-validator, full-hook,
  gitleaks, and independent final-review gates before the final documentation
  commit.

## Final verification and completion rule

No unit is complete because its focused tests pass once. Each remaining unit
requires:

1. strict RED-to-GREEN evidence for each behavior change;
2. focused and adjacent owner/security tests;
3. blueprint, platform-neutral, documentation, and repository validators;
4. syntax and working/staged diff checks;
5. staged gitleaks;
6. the normal full hook without bypass when the unit is integration-complete;
7. independent scoped review with every Critical and Important finding fixed;
8. an update to this plan's status and evidence before proceeding.

The workstream is complete only when the definition of done is satisfied and
both candidate and published-package VM acceptance are green.

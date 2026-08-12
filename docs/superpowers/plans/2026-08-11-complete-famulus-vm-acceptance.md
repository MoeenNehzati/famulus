# Complete Famulus VM Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exercise the committed candidate and then the published Famulus package through the same complete, secret-safe installation scenario inside a fresh isolated Ubuntu VM.

**Architecture:** Extend the existing manifest-bound VM CLI with immutable candidate/document inputs, bounded SSH stdin, a supervised guest session, versioned scenarios, and sanitized evidence extraction. Candidate and public gates use different acquisition methods but the same post-acquisition verifier and profile matrix.

**Tech Stack:** Existing direct QEMU/KVM harness, Ubuntu 24.04 cloud image, OpenSSH, GNOME Keyring, D-Bus, Codex CLI, JSON manifests, SHA-256 provenance, pytest.

## Global Constraints

- Plan A (`2026-08-11-complete-famulus-installer.md`) must be committed and verified first.
- Never mount or expose the maintainer checkout to the guest.
- Candidate and documentation inputs are immutable, digest-bound, and copied through bounded channels.
- Secret stdin bytes never enter argv, environment, manifests, logs, serial output, or reports.
- “Persistent session” means one supervised D-Bus/Secret Service session for one acceptance run, not persistence across logout or reboot.
- The public package gate cannot pass until the resolved marketplace commit and payload digest equal the expected published artifact.
- Missing KVM, assistant CLI, Secret Service prerequisite, or network access is a failed dedicated acceptance run, not a skip.
- Live acceptance runs as the unprivileged guest; prerequisite installation is a separate recorded operator action.

---

### Task 1: Add immutable candidate and documentation inputs

**Files:**
- Create: `test_support/isolated_lm/artifact.py`
- Modify: `test_support/isolated_lm/model.py`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_artifact.py`
- Test: `tests/test_isolated_lm_cli.py`

**Interfaces:**
- Produces: `CandidateArtifact(kind, source_commit, tree_sha256, archive_sha256, documentation_sha256, byte_size)`.
- Adds CLI: `prepare-candidate --archive PATH --docs PATH --provenance PATH`.
- Adds manifest fields: candidate/docs digests and exact guest staging paths.

- [ ] **Step 1: Add RED provenance/containment tests**

Cover symlinks, FIFOs, device files, changing files, oversized inputs, traversal, mismatched Git tree/archive/provenance metadata, archive extraction escape, duplicate entries, and cross-filesystem publication.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_isolated_lm_artifact.py tests/test_isolated_lm_cli.py`

- [ ] **Step 3: Implement bounded staging and digest records**

The builder starts from an exact clean commit and runs `git archive --format=tar <commit>`. It records `git rev-parse <commit>^{tree}`, a canonical SHA-256 over `git ls-tree -r --full-tree <commit>`, the archive digest, and documentation digest in locally trusted build-provenance JSON. The verifier reads the archive entry set and content, recomputes the canonical tree digest, and requires equality with the provenance tree digest; at build time the commit/tree pair must match the independently queried reviewed repository. Use descriptor-relative reads with no-follow checks. Copy to same-filesystem staging, hash while copying, fsync, and atomically publish. Never execute archive content on the host.

- [ ] **Step 4: Add guest transfer command**

Use `scp`/SFTP-equivalent shell-free argv with exact known-host and identity constraints. Transfer only the two recorded immutable files into the selected ready run; verify their guest SHA-256 before reporting success.

- [ ] **Step 5: Run isolated suites and commit**

Run: `python3 -m pytest -q tests/test_isolated_lm_artifact.py tests/test_isolated_lm_cli.py tests/test_isolated_lm_qemu.py`

```bash
git add test_support/isolated_lm/artifact.py test_support/isolated_lm/model.py test_support/isolated_lm/cli.py tests/test_isolated_lm_artifact.py tests/test_isolated_lm_cli.py
git commit -m "feat: bind VM runs to immutable Famulus candidates"
```

---

### Task 2: Add bounded secret stdin to VM exec

**Files:**
- Modify: `test_support/isolated_lm/cli.py`
- Modify: `test_support/isolated_lm/qemu.py`
- Test: `tests/test_isolated_lm_cli.py`
- Test: `tests/test_isolated_lm_qemu.py`

**Interfaces:**
- Adds internal `exec_with_secret_stdin(argv, *, secret_supplier, max_stdin_bytes=4096)`; no public path argument transports the unlock secret.
- Produces: `BoundedProcessResult` metadata with stdin byte count only, never bytes or digest.

- [ ] **Step 1: Add RED secret-channel tests**

Use a canary stdin value and assert it is absent from SSH argv, `/proc` cmdline, environment capture, JSON, stdout/stderr, timeout diagnostics, run manifest, and retained files. Reject size overflow, supplier failure before/during transport, and commands without explicit `--`.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_isolated_lm_cli.py tests/test_isolated_lm_qemu.py -k stdin`

- [ ] **Step 3: Implement exact stdin transport**

Generate the run-specific unlock secret in the host acceptance process through `secret_supplier`; keep it only in a mutable bounded buffer and pass it directly to SSH stdin. Do not create a host file. Concurrently drain stdout/stderr under the existing caps; zero the mutable buffer after process completion and retain no copy. The ordinary public `exec` command continues to use `DEVNULL` stdin.

- [ ] **Step 4: Run the complete VM CLI suite and commit**

Run: `python3 -m pytest -q tests/test_isolated_lm_cli.py tests/test_isolated_lm_qemu.py`

```bash
git add test_support/isolated_lm/cli.py test_support/isolated_lm/qemu.py tests/test_isolated_lm_cli.py tests/test_isolated_lm_qemu.py
git commit -m "feat: add secret-safe VM command stdin"
```

---

### Task 3: Supervise one headless Secret Service session

**Files:**
- Create: `test_support/isolated_lm/session.py`
- Modify: `test_support/isolated_lm/model.py`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_session.py`
- Test: `tests/test_isolated_lm_cli.py`

**Interfaces:**
- Adds CLI: `start-session`, `session-exec`, `stop-session`.
- Produces: `GuestSessionRecord(session_id, supervisor_pid, bus_address_file, ready_file, lifecycle)`.
- Session environment is stored in a guest-private `0600` file and never returned in host JSON.

- [ ] **Step 1: Add RED lifecycle and identity tests**

Cover missing `dbus-run-session`, missing `gnome-keyring-daemon`, stdin unlock failure, supervisor death, PID reuse, socket replacement, second SSH process in the same session, unrelated-session failure, timeout, and teardown.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_isolated_lm_session.py tests/test_isolated_lm_cli.py -k session`

- [ ] **Step 3: Implement the guest supervisor**

Start `dbus-run-session` and `gnome-keyring-daemon --unlock` as the unprivileged guest using the Task 2 stdin channel. The supervisor creates a random session ID, private bus-address file, readiness marker, and PID record. `session-exec` validates all identities before executing with the inherited bus address.

- [ ] **Step 4: Implement exact teardown evidence**

Stop the supervisor, wait under one deadline, verify recorded PID/argv is absent, verify the exact bus socket is gone, and mark the session stopped. Do not broadly kill D-Bus or keyring processes.

- [ ] **Step 5: Run focused tests and commit**

Run: `python3 -m pytest -q tests/test_isolated_lm_session.py tests/test_isolated_lm_cli.py tests/test_isolated_lm_qemu.py`

```bash
git add test_support/isolated_lm/session.py test_support/isolated_lm/model.py test_support/isolated_lm/cli.py tests/test_isolated_lm_session.py tests/test_isolated_lm_cli.py
git commit -m "feat: supervise VM Secret Service sessions"
```

---

### Task 4: Define and execute the complete installation scenario

**Files:**
- Create: `test_support/isolated_lm/scenario.py`
- Create: `test_support/isolated_lm/scenarios/complete-install-v1.json`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_scenario.py`

**Interfaces:**
- Adds CLI: `run-scenario --scenario complete-install-v1 --acquisition {candidate,public}`.
- Produces: versioned `ScenarioReport` with per-step pass/fail, exact command identity, bounded evidence, contamination status, and final verdict.

- [ ] **Step 1: Add RED schema and ordering tests**

Reject unknown fields/steps, duplicate IDs, unbounded commands, secret-bearing fields, missing cleanup, missing profile, and acquisition-specific assertions. Assert cleanup runs after any failure.

- [ ] **Step 2: Encode exact scenario steps**

The scenario records OS packages/versions; installs Codex; acquires candidate or public plugin; verifies provenance; runs one selected Phase 1 profile; probes every required command/dependency; reloads/signs/verifies from a fresh process; reinstalls without key rotation; runs exact `get-weather` certifier/drift current→stale→current checks in a disposable committed dev checkout; exercises default uninstall, host plugin removal, host restart visibility, reinstall, purge; and performs leak/process/socket checks. Minimum and maximal are separate scenario runs from independent clean overlays for both candidate and public gates; reinstall remains an in-run idempotency check.

Each step references a command ID from Plan A's
`references/installation/complete-install-commands-v1.json`, not free-form argv.
The catalog contains exact Codex marketplace/plugin acquisition,
`_phase_entry.py --profile minimum|maximal`, command probes, certifier/drift,
manifest uninstall, purge, plugin/marketplace removal, and provenance commands
plus expected exit codes and JSON schemas. Scenario loading validates the
catalog with `complete-install-commands-v1.schema.json` and fails if its SHA-256
differs from the documentation bundle digest record in the run manifest.

- [ ] **Step 3: Implement deterministic execution and reporting**

Use only catalog-bound scenario commands and manifest-bound artifacts. Each step has one timeout and output cap. Errors use closed categories; reports exclude raw secret-bearing streams. Record human intervention as contamination.

- [ ] **Step 4: Add malicious leak tests**

Seed literal, Base64, hex, escaped, and PEM-like canaries into failure paths. Scan every JSON object, stdout/stderr capture, serial log, scenario report, guest staging file, extracted artifact, and host manifest.

- [ ] **Step 5: Run focused tests and commit**

Run: `python3 -m pytest -q tests/test_isolated_lm_scenario.py tests/test_isolated_lm_cli.py`

```bash
git add test_support/isolated_lm/scenario.py test_support/isolated_lm/scenarios/complete-install-v1.json test_support/isolated_lm/cli.py tests/test_isolated_lm_scenario.py
git commit -m "feat: automate complete Famulus VM acceptance"
```

---

### Task 5: Add sanitized evidence extraction and cleanup certification

**Files:**
- Create: `test_support/isolated_lm/evidence.py`
- Modify: `test_support/isolated_lm/cli.py`
- Test: `tests/test_isolated_lm_evidence.py`

**Interfaces:**
- Adds CLI: `extract-report --output-dir PATH`.
- Produces only schema-approved report JSON, version inventories, digests, bounded public logs, and cleanup proof.

- [ ] **Step 1: Add RED allowlist and race tests**

Reject guest symlinks, special files, traversal, oversized artifacts, mutation during extraction, unknown report fields, and secret canaries in all encodings. Assert partial extraction never publishes a successful evidence directory.

- [ ] **Step 2: Implement allowlisted extraction**

Copy selected files through exact SSH argv to same-filesystem host staging, validate size/schema/digests, scan canaries, fsync, and atomically publish. The extractor cannot request arbitrary guest paths.

- [ ] **Step 3: Implement cleanup certificate**

Record absence of run-owned QEMU and Secret Service PIDs, exact bus socket, SSH listener, temporary stdin files, guest candidate staging, and report staging. Preserve the VM overlay/serial log only when the operator selects retained evidence.

- [ ] **Step 4: Run focused tests and commit**

Run: `python3 -m pytest -q tests/test_isolated_lm_evidence.py tests/test_isolated_lm_cli.py`

```bash
git add test_support/isolated_lm/evidence.py test_support/isolated_lm/cli.py tests/test_isolated_lm_evidence.py
git commit -m "feat: extract sanitized VM acceptance evidence"
```

---

### Task 6: Run committed-candidate acceptance

**Files:**
- Modify: `docs/isolated-lm-testing.md`
- Modify: `docs/plans/isolated-lm-testing.md`
- Modify: `docs/superpowers/plans/2026-08-11-isolated-lm-vm-foundation.md`
- Create: ignored report under `.superpowers/sdd/2026-08-11-complete-famulus-install-acceptance/`

- [ ] **Step 1: Build production-shaped immutable inputs from a clean commit**

Create a source/plugin artifact and public-documentation bundle from exact `HEAD`; record SHA-256 and source commit. Verify `git status --porcelain` is empty before building.

- [ ] **Step 2: Prepare a fresh VM and operator prerequisites**

Install `dbus-daemon` and `gnome-keyring` as a separately recorded sudo step; record package versions. Prove the baseline contains no Famulus state or reusable keyring secret before scenario start.

- [ ] **Step 3: Run `complete-install-v1` with candidate acquisition**

Run the minimum profile from one fresh overlay and the maximal profile from a second fresh overlay, with no unplanned human guidance. Missing KVM, host CLI, Secret Service, network, or another declared prerequisite; any skip; contamination; secret leak; or unverified cleanup is `fail`. `Inconclusive` is reserved only for a verifier internal error after environment preflight passes and no product assertion can be evaluated; its closed codes are `verifier_crash`, `evidence_corrupt`, and `host_observation_lost`.

- [ ] **Step 4: Stop the VM and independently verify cleanup**

Use the manifest-bound stop command, then verify no exact QEMU process/listener/session artifacts remain. Retain sanitized evidence and the overlay only if the verdict requires diagnosis.

- [ ] **Step 5: Run repository gates and document the verdict**

Run: `python3 -m pytest -q tests/test_isolated_lm_host.py tests/test_isolated_lm_image.py tests/test_isolated_lm_guest.py tests/test_isolated_lm_qemu.py tests/test_isolated_lm_cli.py tests/test_isolated_lm_artifact.py tests/test_isolated_lm_session.py tests/test_isolated_lm_scenario.py tests/test_isolated_lm_evidence.py`

Run: `python3 repo_checks.py --suite validators --repository-view working`

Commit only documentation and code; never commit VM state, secrets, or the ignored report.

---

### Task 7: Publish and run pinned public-package acceptance

**Files:**
- Modify: `docs/isolated-lm-testing.md`
- Modify: `docs/plans/isolated-lm-testing.md`
- Modify: `README.md` only if the published commands differ from candidate evidence

- [ ] **Step 1: Merge and publish through the repository's normal release path**

This requires explicit user authorization at execution time. Record the resulting public commit, marketplace manifest digest, and package version. Do not substitute a local cache.

- [ ] **Step 2: Prepare a new fresh VM from the same sealed baseline**

Do not reuse the candidate-acceptance overlay or keyring. Install the same recorded OS prerequisites and assistant-host version.

- [ ] **Step 3: Run `complete-install-v1` with public acquisition**

Resolve the marketplace package, then verify its installed source commit and payload digest equal the expected published artifact before any post-acquisition assertion counts.

- [ ] **Step 4: Compare candidate and public reports**

Require identical scenario/verifier versions and post-acquisition assertion sets. Explain any version-only difference; any behavioral difference fails the public gate.

- [ ] **Step 5: Close Workstream 1 package readiness only on PASS**

Update the historical failure with the corrective commit and both evidence identities. Leave later LM-usability scenario work open. Run docs, validators, platform-neutral tests, and full precommit hooks before the final documentation commit.

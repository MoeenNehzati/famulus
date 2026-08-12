# Complete Famulus Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a successful Phase 1 install mean that the complete selected Famulus profile, including secure certificate signing, is installed and verified without ambient-Python or partial-install ambiguity.

**Architecture:** Serialize every home-scoped install mutation, prepare the managed runtime before activation, enforce an audited native keyring backend through a closed-output child probe, and keep certificate public state in the stable Famulus data root. A durable transaction journal makes pointer activation and later scaffold mutations recoverable; the manifest remains the exact uninstall authority.

**Tech Stack:** Python 3.11 managed by `uv`, Python `keyring==25.6.0`, Ed25519 through `cryptography`, native OS file locks, confined atomic-file helpers, pytest, repository blueprints and validators.

## Global Constraints

- `_phase_entry.py` is the only fresh-install entrypoint; `_install_scaffold.py` is repair-only.
- Certificate signing is mandatory and never downgraded to an optional capability.
- Accept only audited native keyring backends; never accept alternate, file, chained, custom, Null, Fail, or test backends in production.
- Never place secrets in argv, environment variables, diagnostics, manifests, journals, logs, or reports.
- Never install packages into the ambient Python interpreter.
- One exclusive per-home lock serializes install, repair, certificate migration, and uninstall.
- Pre-commit failures preserve the old pointer and active certificate identity and remove the exact candidate and staged key.
- Post-pointer failures retain a recoverable journal and report exact state; never claim rollback.
- Default uninstall retains the managed runtime and complete certificate lifecycle; purge removes both private and public certificate material or fails closed.
- Minimum and maximal profiles have explicit required-capability sets; external account onboarding is not part of the installation verdict.
- Use TDD for every behavior change and preserve all Git hooks.

---

### Task 1: Serialize home-scoped installation and make records durable

**Files:**
- Create: `src/officina/install/install_lock.py`
- Create: `src/officina/install/blueprints/install-lock.yaml`
- Modify: `src/officina/install/blueprint.yaml`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Test: `tests/test_officina_install_lock.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`

**Interfaces:**
- Produces: `InstallLock(path: Path, timeout_seconds: float = 30.0)` context manager.
- Produces: `InstallBusyError` with static code `install_busy`.
- Produces: atomic `Manifest.save()` and `TransactionJournal` operations used by Tasks 4–7.

- [ ] **Step 1: Add failing lock and atomic-record tests**

```python
def test_second_installer_times_out_without_mutation(tmp_path):
    lock_path = tmp_path / "install" / "operation.lock"
    with InstallLock(lock_path, timeout_seconds=1):
        with pytest.raises(InstallBusyError, match="install_busy"):
            with InstallLock(lock_path, timeout_seconds=0):
                pytest.fail("contending operation entered")

def test_dead_owner_releases_kernel_lock(tmp_path):
    lock_path = tmp_path / "install" / "operation.lock"
    process = start_lock_holder(lock_path)
    wait_until_locked(process)
    process.kill()
    process.wait(timeout=5)
    with InstallLock(lock_path, timeout_seconds=1):
        assert lock_path.is_file()

def test_manifest_replace_is_atomic_and_parent_durable(tmp_path, monkeypatch):
    fsync_calls = record_fsync_calls(monkeypatch)
    manifest = Manifest(tmp_path / "state" / "install-manifest.json")
    manifest.record("file", path=str(tmp_path / "bin" / "dispatcher"))
    assert json.loads(manifest.path.read_text())["version"] == 2
    assert manifest.path.parent in fsync_calls
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `python3 -m pytest -q tests/test_officina_install_lock.py skills/install-assistant-tools/_rtx/tests/test_install_manifest.py`

Expected: FAIL because `install_lock` and atomic manifest behavior do not exist.

- [ ] **Step 3: Implement the lock and journal primitives**

```python
@dataclass(frozen=True)
class JournalMutation:
    mutation_id: str
    kind: str
    path: str
    expected_before: dict[str, object]
    intended_after: dict[str, object]
    ownership_entry: dict[str, object] | None

@dataclass(frozen=True)
class TransactionJournal:
    transaction_id: str
    phase: Literal["prepared", "committed", "complete"]
    prior_release_id: str | None
    candidate_release_id: str
    resolver_bundle_id: str
    staged_key_id: str | None
    pending_mutation: JournalMutation | None
    completed_mutation_ids: tuple[str, ...]

class InstallLock:
    def __enter__(self) -> "InstallLock":
        self._descriptor = _open_confined_lock(self.path)
        _acquire_platform_lock(self._descriptor, self.timeout_seconds)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _release_platform_lock(self._descriptor)
        os.close(self._descriptor)
```

Use `fcntl.flock(LOCK_EX | LOCK_NB)` on POSIX and `msvcrt.locking` on Windows. Keep one opened descriptor for the context lifetime; never delete a lock to recover a dead owner. Write manifest and journal JSON through `atomic_replace_bytes()` with a confined home-state root. Each pending mutation records its exact path, stat/digest-based expected-before state, intended-after state, and manifest ownership entry. Recovery validates the actual state and either adopts the completed mutation, performs an untouched pending mutation, or fails closed on any third state.

- [ ] **Step 4: Add concurrency regressions**

Cover install/install, install/uninstall, repair contention, timeout with unchanged tree digest, process death, malformed journal, journal symlink, and manifest symlink. Assert one writer and no lost entries.

- [ ] **Step 5: Run focused and blueprint tests**

Run: `python3 -m pytest -q tests/test_officina_install_lock.py skills/install-assistant-tools/_rtx/tests/test_install_manifest.py tests/test_interface_injection_migration.py tests/test_typed_blueprint_schemas.py`

- [ ] **Step 6: Commit**

```bash
git add src/officina/install/install_lock.py src/officina/install/blueprints/install-lock.yaml src/officina/install/blueprint.yaml skills/install-assistant-tools/_rtx/_state_record.py tests/test_officina_install_lock.py skills/install-assistant-tools/_rtx/tests/test_install_manifest.py
git commit -m "feat: serialize Famulus installation state"
```

---

### Task 2: Move certificate identity to stable Famulus state

**Files:**
- Modify: `src/officina/common/famulus_paths/__init__.py`
- Modify: `src/officina/common/certificate_records.py`
- Modify: `src/officina/common/blueprints/famulus-paths.yaml`
- Modify: `src/officina/common/blueprints/certificate-records.yaml`
- Modify: `src/officina/common/certification_view.py`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: `skills/skill-certifier/_rtx/blueprints/rtx-certifier.yaml`
- Modify: `skills/skill-drift/_rtx/_check_drift_state.py`
- Modify: `skills/skill-drift/_rtx/blueprints/rtx-check-drift-state.yaml`
- Test: `tests/test_officina_certificate_records.py`
- Test: `tests/test_officina_certification_view.py`
- Test: `skills/skill-certifier/_rtx/tests/test_certifier.py`
- Test: `skills/skill-drift/_rtx/tests/test_drift_check.py`

**Interfaces:**
- Produces: `FamulusPaths.certificate_state_root` and `.certificate_public_key_root`.
- Produces: `CertificateStatePaths(public_key_root, active_key_id, legacy_public_key_root)`.
- Produces: `migrate_legacy_certificate_state(repo_root, paths, *, secret_backend)`; caller must hold `InstallLock`.

- [ ] **Step 1: Add RED tests for stable ownership and migration**

```python
def test_plugin_cache_replacement_preserves_certificate_identity(tmp_path, backend):
    first = provision_certificate_signing_material(state_paths(tmp_path), secret_backend=backend)
    second = provision_certificate_signing_material(state_paths(tmp_path), secret_backend=backend)
    assert second.key_id == first.key_id

@pytest.mark.parametrize("stable_exists", [False, True])
def test_legacy_state_migrates_idempotently(tmp_path, backend, stable_exists):
    legacy, stable = make_matching_certificate_states(tmp_path, backend, stable_exists)
    migrate_legacy_certificate_state(legacy, stable, secret_backend=backend)
    first = stable.active_key_id.read_bytes()
    migrate_legacy_certificate_state(legacy, stable, secret_backend=backend)
    assert stable.active_key_id.read_bytes() == first

def test_conflicting_legacy_and_stable_state_fails_without_rotation(tmp_path, backend):
    legacy, stable = make_conflicting_certificate_states(tmp_path, backend)
    before = backend.snapshot()
    with pytest.raises(CertificateStateConflict):
        migrate_legacy_certificate_state(legacy, stable, secret_backend=backend)
    assert backend.snapshot() == before
```

Also prove certifier and drift read the same stable root, plugin-cache removal does not remove it, and no private key exists under either repository.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest -q tests/test_officina_certificate_records.py tests/test_officina_certification_view.py skills/skill-certifier/_rtx/tests/test_certifier.py skills/skill-drift/_rtx/tests/test_drift_check.py`

- [ ] **Step 3: Implement explicit stable paths**

```python
@dataclass(frozen=True)
class CertificateStatePaths:
    root: Path
    public_key_root: Path
    active_key_id: Path

def certificate_state_paths(*, platform: str, home: Path) -> CertificateStatePaths:
    root = resolve_famulus_paths(platform=platform, home=home).data_root / "certificates"
    return CertificateStatePaths(
        root=root,
        public_key_root=root / "public-keys",
        active_key_id=root / "public-keys" / "active-key-id",
    )
```

Remove implicit production derivation from `repo_root/skills/skill-certifier`. Retain a separately named `legacy_certificate_public_key_root(repo_root)` only for one-time migration and tests. Propagate the stable root through certifier and drift rather than using an environment override.

- [ ] **Step 4: Implement fail-closed migration**

Under the install lock: validate both roots without following symlinks; migrate identical/missing state atomically; reject mismatched selectors, public bytes, or private-key identity; never generate a replacement key to resolve a conflict.

- [ ] **Step 5: Run focused, authorization, and schema tests**

Run: `python3 -m pytest -q tests/test_officina_certificate_records.py tests/test_officina_certification_view.py skills/skill-certifier/_rtx/tests/test_certifier.py skills/skill-drift/_rtx/tests/test_drift_check.py tests/test_interface_injection_migration.py tests/test_nested_module_v5_schemas.py`

- [ ] **Step 6: Commit**

```bash
git add src/officina/common/famulus_paths/__init__.py src/officina/common/certificate_records.py src/officina/common/certification_view.py src/officina/common/blueprints/famulus-paths.yaml src/officina/common/blueprints/certificate-records.yaml skills/skill-certifier/_rtx/_node_certifier.py skills/skill-certifier/_rtx/blueprints/rtx-certifier.yaml skills/skill-certifier/_rtx/tests/test_certifier.py skills/skill-drift/_rtx/_check_drift_state.py skills/skill-drift/_rtx/blueprints/rtx-check-drift-state.yaml skills/skill-drift/_rtx/tests/test_drift_check.py tests/test_officina_certificate_records.py tests/test_officina_certification_view.py
git commit -m "feat: retain certificate identity across plugin updates"
```

---

### Task 3: Enforce and probe native credential storage

**Files:**
- Create: `src/officina/install/credential_preflight.py`
- Create: `src/officina/install/blueprints/credential-preflight.yaml`
- Modify: `src/officina/install/blueprint.yaml`
- Modify: `src/officina/common/secret_store.py`
- Modify: `src/officina/common/blueprints/secret-store.yaml`
- Test: `tests/test_officina_credential_preflight.py`
- Test: `tests/test_officina_secret_store.py`

**Interfaces:**
- Produces: `CredentialPreflightCode` enum with the five closed codes from the spec.
- Produces: `probe_native_store(*, backend, token_factory, timeout_seconds) -> CredentialPreflightResult`.
- Produces CLI: `python -I -m officina.install.credential_preflight --json` with closed JSON only.

- [ ] **Step 1: Write adversarial RED tests**

```python
@pytest.mark.parametrize("backend", [PositivePlaintextBackend(), ChainerBackend(), CustomBackend()])
def test_preflight_rejects_non_native_backend_even_when_roundtrip_works(backend):
    result = probe_native_store(backend=backend, token_factory=fixed_tokens)
    assert result.code is CredentialPreflightCode.UNSUPPORTED_BACKEND
    assert backend.writes == []

def test_backend_exception_cannot_leak_probe_secret(capsys):
    secret = "CANARY-" + "a" * 64
    result = run_malicious_backend(secret)
    captured = capsys.readouterr()
    combined = captured.out + captured.err + json.dumps(result)
    for canary in encoded_canaries(secret):
        assert canary not in combined

def test_cleanup_requires_final_absence_check():
    backend = RetainingDeleteBackend()
    result = probe_native_store(backend=backend, token_factory=fixed_tokens)
    assert result.code is CredentialPreflightCode.CLEANUP_FAILED
```

Cover `PYTHON_KEYRING_BACKEND`, keyring config/path injection, entry points, absent D-Bus, absent service, locked backend, permission denial, collision regeneration, false delete, retained-after-delete, kill-after-store, hang-during-delete, and timeout. Exercise the real child/parent protocol and scan stdout, stderr, JSON, journals, manifests, and retained test logs for literal, Base64, hexadecimal, JSON-escaped, and PEM-like canaries.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_officina_credential_preflight.py tests/test_officina_secret_store.py`

- [ ] **Step 3: Implement the concrete allowlist**

For `keyring==25.6.0`, accept only:

```python
NATIVE_BACKENDS = {
    "linux": {"keyring.backends.SecretService.Keyring", "keyring.backends.libsecret.Keyring"},
    "darwin": {"keyring.backends.macOS.Keyring"},
    "win32": {"keyring.backends.Windows.WinVaultKeyring"},
}
```

Reject `keyring.backends.chainer.ChainerBackend` even if its selected child is native. Check exact module plus class, package version, and platform before any store.

- [ ] **Step 4: Implement the closed child protocol**

```json
{"schema_version":1,"ok":false,"code":"backend_locked","backend":"keyring.backends.SecretService.Keyring"}
```

Catch all backend exceptions inside the child and emit only installer-authored fields. The parent generates a collision-checked non-secret target ID and retains it for cleanup; pass that ID through a dedicated non-secret argument. The child generates the probe secret and never returns it. After any abnormal child exit or timeout, the parent launches a separate bounded cleanup/final-absence child for the retained target. Never echo backend exception text. Final lookup must be `None` before `ok:true`; a cleanup-child timeout is `cleanup_failed`, not success.

- [ ] **Step 5: Run focused and platform-neutral tests**

Run: `python3 -m pytest -q tests/test_officina_credential_preflight.py tests/test_officina_secret_store.py tests/validate_platform_neutral.py`

- [ ] **Step 6: Commit**

```bash
git add src/officina/install/credential_preflight.py src/officina/install/blueprints/credential-preflight.yaml src/officina/install/blueprint.yaml src/officina/common/secret_store.py src/officina/common/blueprints/secret-store.yaml tests/test_officina_credential_preflight.py tests/test_officina_secret_store.py
git commit -m "feat: require native credential storage"
```

---

### Task 4: Split runtime preparation from activation and version resolver bundles

**Files:**
- Modify: `src/officina/install/managed_runtime.py`
- Modify: `src/officina/install/runtime_pointer.py`
- Modify: `src/officina/install/resolvers/launch.py`
- Modify: `src/officina/install/blueprints/managed-runtime.yaml`
- Modify: `src/officina/install/blueprints/runtime-pointer.yaml`
- Modify: `src/officina/install/blueprints/launcher-entry.yaml`
- Test: `tests/test_officina_managed_runtime.py`
- Test: `tests/test_officina_runtime_pointer.py`
- Test: `tests/test_officina_launcher_entry.py`

**Interfaces:**
- Produces: `PreparedRelease(release_id, release_dir, python_bin, repository_config, trusted_interpreter_roots, resolver_bundle_id)`.
- Produces: `prepare_candidate_release(*, runtime_root: Path, manifest_path: Path, platform: str, uv_bin: Path, python_version: str, repo_root: Path, include_optional_dependencies: bool) -> PreparedRelease` with no pointer mutation.
- Produces: `activate_prepared_release(runtime_root, prepared) -> RuntimePointer`.
- Produces: pointer schema v3 with `resolver_bundle_id`.

- [ ] **Step 1: Add RED preparation/activation/recovery tests**

```python
def test_prepare_candidate_does_not_write_current_pointer(runtime_fixture):
    prepared = prepare_candidate_release(**runtime_fixture.kwargs)
    assert prepared.python_bin.exists()
    assert not (runtime_fixture.runtime_root / "current.json").exists()

def test_every_prepare_failure_removes_exact_candidate(runtime_fixture, monkeypatch):
    monkeypatch.setattr(managed_runtime, "_validate_candidate_runtime", raise_probe_failure)
    with pytest.raises(ManagedRuntimeError):
        prepare_candidate_release(**runtime_fixture.kwargs)
    assert list((runtime_fixture.runtime_root / "releases").iterdir()) == []

def test_pointer_v3_selects_immutable_resolver_bundle(runtime_fixture):
    pointer = activate_fixture(runtime_fixture)
    assert pointer.resolver_bundle_id == runtime_fixture.bundle_id
    assert load_bundle(pointer).manifest_digest == runtime_fixture.bundle_digest

def test_old_pointer_remains_launchable_after_new_bundle_publish(runtime_fixture):
    old_pointer = activate_fixture(runtime_fixture)
    publish_second_bundle(runtime_fixture)
    assert run_bootstrap(old_pointer, "--help").returncode == 0
```

Inject failure after directory creation, venv, dependencies, wheel, probe, artifact metadata, bundle publication, pointer validation, pointer replace, and journal-before-advance boundary.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_officina_managed_runtime.py tests/test_officina_runtime_pointer.py tests/test_officina_launcher_entry.py`

- [ ] **Step 3: Implement `PreparedRelease` and exact cleanup**

Wrap preparation in `try/except BaseException`; on failure validate the freshly allocated release directory is an immediate child with the exact generated ID, remove it, fsync `releases/`, and re-raise. Do not prune older releases here.

- [ ] **Step 4: Implement immutable resolver bundles and pointer v3**

Publish each bundle at `runtime_root/resolvers/bundles/<sha256>/` with resolver bytes, trust JSON, and a digest manifest. Keep the fixed bootstrap resolver dependency-free and have it load `current.json`, validate `resolver_bundle_id`, then execute that bundle. Preserve schema v1/v2 reading for existing installs; all new writes are v3.

- [ ] **Step 5: Implement activation and pruning**

Activation validates the full prepared release and bundle, atomically writes v3 `current.json`, and returns its pointer. After the complete installer transaction—not inside activation—retain current plus one previous successful release and prune older exact release children.

- [ ] **Step 6: Run focused suites**

Run: `python3 -m pytest -q tests/test_officina_managed_runtime.py tests/test_officina_runtime_pointer.py tests/test_officina_launcher_entry.py tests/test_officina_install_info.py`

- [ ] **Step 7: Commit**

```bash
git add src/officina/install/managed_runtime.py src/officina/install/runtime_pointer.py src/officina/install/resolvers/launch.py src/officina/install/blueprints/managed-runtime.yaml src/officina/install/blueprints/runtime-pointer.yaml src/officina/install/blueprints/launcher-entry.yaml tests/test_officina_managed_runtime.py tests/test_officina_runtime_pointer.py tests/test_officina_launcher_entry.py
git commit -m "refactor: prepare managed runtimes before activation"
```

---

### Task 5: Make certificate provisioning transactional

**Files:**
- Modify: `src/officina/common/certificate_records.py`
- Modify: `src/officina/common/blueprints/certificate-records.yaml`
- Test: `tests/test_officina_certificate_records.py`

**Interfaces:**
- Produces: `StagedCertificateKey(key_id, public_key_path, secret_target, created)`.
- Produces: `stage_certificate_signing_material(paths, *, secret_backend) -> StagedCertificateKey`.
- Produces: `commit_staged_certificate(paths, staged, *, secret_backend) -> CertificateSigningKey`.
- Produces: `abort_staged_certificate(paths, staged, *, secret_backend) -> None`.

- [ ] **Step 1: Add RED failure-injection and concurrency tests**

Test failures after private-secret store, public-key creation, selector intent journaling, selector write, and verification. Before selector replacement, assert the losing pair is absent and existing active material is unchanged. After selector replacement, recovery inspects the selector: when it names the staged key, retain the pair and resume as committed; a third/malformed selector state fails closed without deleting either potentially active pair. Run two provisioners under the install lock and assert one key ID and no orphan targets.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_officina_certificate_records.py`

- [ ] **Step 3: Implement stage/commit/abort**

Stage writes a uniquely identified secret and public key without touching the selector. Commit atomically creates/replaces the selector only after verifying the pair through the same `secret_backend`. Abort clears the exact secret through that backend, verifies lookup returns `None`, removes the exact public file, and fsyncs the directory. The orchestrator binds the concrete allowlisted backend identity at preflight and requires it unchanged for stage, commit, post-commit verification, abort, and purge. Cleanup failure raises a typed error containing no secret.

- [ ] **Step 4: Preserve idempotency and rotation semantics**

Existing valid active material returns `created=False`; reinstall never rotates it. Explicit rotation continues through its existing separate API and shares the serialization requirement.

- [ ] **Step 5: Run certificate and certifier suites**

Run: `python3 -m pytest -q tests/test_officina_certificate_records.py tests/test_officina_certification_view.py skills/skill-certifier/_rtx/tests/test_certifier.py`

- [ ] **Step 6: Commit**

```bash
git add src/officina/common/certificate_records.py src/officina/common/blueprints/certificate-records.yaml tests/test_officina_certificate_records.py
git commit -m "fix: make certificate provisioning transactional"
```

---

### Task 6: Orchestrate a recoverable complete Phase 1 install

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/_rtx/_install_scaffold.py`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_scaffold.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

**Interfaces:**
- Fresh install: `_phase_entry.py --profile {minimum,maximal}`; existing optional-dependency flags remain compatible aliases and conflicting combinations fail.
- Repair: `_install_scaffold.py` validates the active v3 pointer and credential preflight before mutation.
- Journal recovery follows the audited prepared/committed table.

- [ ] **Step 1: Add RED parser, ordering, and recovery tests**

Assert exact order:

```text
lock → recover journal → prepare runtime → native-store probe → migrate/stage key
→ publish bundle → write prepared journal → activate pointer → mark committed
→ commit selector → scaffold → optional dev link → selected launchers
→ verify commands → finalize manifest → prune → clear journal
```

Test a crash or injected exception at every arrow. Verify exact retained state and that re-run resumes safely.

- [ ] **Step 2: Add RED repair-only tests**

Direct scaffold with missing/corrupt/escaped/symlinked v3 pointer, resolver bundle, trust manifest, or secret backend must exit before launcher/PATH/manifest writes and print the full Phase 1 command. A valid repair uses the active managed interpreter for preflight and certificate verification.

- [ ] **Step 3: Verify RED**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_install.py skills/install-assistant-tools/_rtx/tests/test_scaffold.py skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

- [ ] **Step 4: Implement the orchestration**

Use shell-free subprocesses with explicit timeout and closed JSON parsing. Never capture or re-emit raw credential-child stderr. Move certificate code out of ambient scaffold imports; scaffold consumes only the verified result from the managed child.

Before selector replacement and every scaffold/dev-link/launcher write, persist a complete `JournalMutation`. After the mutation, verify its intended state, append its ID to `completed_mutation_ids`, and atomically add its ownership entry to the manifest. Recovery compares the actual path/state with both journal states; it never guesses from a mutation name. Record `managed_runtime` and `certificate_state` retained-state entries during successful commit so uninstall has exact roots and key targets.

- [ ] **Step 5: Implement profile verification**

Minimum requires the shared command floor and mandatory certificate roundtrip. Maximal sets `include_optional_dependencies=True`, selects every platform-supported launcher, checks every declared package via managed Python metadata, and runs each command smoke. Unknown or missing profile capability exits nonzero.

- [ ] **Step 6: Run installer lifecycle suites**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests tests/test_officina_managed_runtime.py tests/test_officina_credential_preflight.py tests/test_officina_certificate_records.py`

- [ ] **Step 7: Commit**

```bash
git add skills/install-assistant-tools/_rtx/_phase_entry.py skills/install-assistant-tools/_rtx/_install_scaffold.py skills/install-assistant-tools/_rtx/_state_record.py skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml skills/install-assistant-tools/_rtx/blueprints/rtx-install-scaffold.yaml skills/install-assistant-tools/_rtx/tests/test_install.py skills/install-assistant-tools/_rtx/tests/test_scaffold.py skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py
git commit -m "feat: make Phase 1 installation complete and recoverable"
```

---

### Task 7: Implement retention-aware uninstall and purge

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/_install_uninstall.py`
- Modify: `skills/install-assistant-tools/_rtx/_state_record.py`
- Modify: `skills/install-assistant-tools/_rtx/blueprints/rtx-install-uninstall.yaml`
- Test: `skills/install-assistant-tools/_rtx/tests/test_uninstall.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

**Interfaces:**
- Default uninstall retains runtime/cache and the complete certificate lifecycle and reports both stable paths/targets.
- `--purge` removes exact retained runtime/config/certificate state after manifest-owned command/config removal.

- [ ] **Step 1: Add RED combined lifecycle tests**

Cover successful install, partial committed install, default uninstall, purge, repeated uninstall, private-key deletion failure, public-state deletion failure, user-owned sentinels, and native plugin removal handoff. Assert no heuristic deletion. At the real uninstall entrypoint, also cover install/uninstall and repair/uninstall contention, dead lock-owner recovery, and prepared/committed journal reconciliation before replay.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_uninstall.py skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

- [ ] **Step 3: Implement retained-state manifest entries**

Acquire `InstallLock` before reading the journal or manifest and hold it through final record deletion/update. Reconcile prepared/committed transactions through the same recovery API as install before replay. Add explicit `managed_runtime` and `certificate_state` entry kinds. Default replay leaves them with a reason. Purge clears every exact private target and verifies absence before deleting matching public state, then deletes runtime/cache only within canonical Famulus roots.

- [ ] **Step 4: Run uninstall and security tests**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests/test_uninstall.py skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py tests/test_officina_certificate_records.py tests/test_officina_install_lock.py`

- [ ] **Step 5: Commit**

```bash
git add skills/install-assistant-tools/_rtx/_install_uninstall.py skills/install-assistant-tools/_rtx/_state_record.py skills/install-assistant-tools/_rtx/blueprints/rtx-install-uninstall.yaml skills/install-assistant-tools/_rtx/tests/test_uninstall.py skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py
git commit -m "feat: uninstall retained Famulus state explicitly"
```

---

### Task 8: Expand automated installation acceptance and no-skip enforcement

**Files:**
- Modify: `skills/install-assistant-tools/_rtx/tests/install_test_utils.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_codex_install.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_claude_install.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_codex_github_install.py`
- Modify: `skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py`
- Create: `skills/install-assistant-tools/_rtx/tests/test_complete_install_profiles.py`
- Modify: `.github/workflows/python-tests.yml`
- Modify: `tests/test_repository_test_checks.py`

**Interfaces:**
- Produces minimum/maximal profile inventory mapping each dependency and public command to a smoke or reviewed platform exclusion.
- Produces checked-in expected-skip allowlists for Linux, macOS, and Windows native credential jobs.

- [ ] **Step 1: Add failing inventory and skip-policy tests**

Require all declared runtime dependencies to appear in one profile probe and all public commands to have a platform result. Parse `pytest -rs`; fail on any skip not in the platform allowlist.

- [ ] **Step 2: Add full local plugin lifecycle tests**

For Codex and Claude: install production-shaped local plugin, run full Phase 1 minimum and maximal profiles, verify certificate reload from a new process, run every command smoke, reinstall without rotation, perform default uninstall plus native plugin removal, restart host discovery, then repeat with purge.

- [ ] **Step 3: Keep public GitHub tests honest**

Record the resolved cache commit/payload digest. Fail when it differs from the expected published commit. Keep public network acquisition separate from candidate correctness and do not silently retry or skip.

- [ ] **Step 4: Run installer and repository-test suites**

Run: `python3 -m pytest -q skills/install-assistant-tools/_rtx/tests tests/test_repository_test_checks.py`

- [ ] **Step 5: Run native CI-equivalent suites available on this host**

Run: `python3 scripts/run-python-tests.py --suite precommit --verbose`

Add three explicit workflow jobs:

- `native-keyring-linux-headless`: install `dbus-daemon` and `gnome-keyring`, start one D-Bus/Secret Service session, run preflight plus install and fresh-process key reload inside it, assert an outside-session process fails closed, and compare `pytest -rs` to the Linux allowlist;
- `native-keyring-macos`: require `keyring.backends.macOS.Keyring`, run install/reload/uninstall tests, and compare skips to the macOS allowlist;
- `native-keyring-windows`: require `keyring.backends.Windows.WinVaultKeyring`, run install/reload/uninstall tests, and compare skips to the Windows allowlist.

All three jobs fail on backend mismatch, prerequisite absence, or unexpected skip. Record macOS and Windows results as required CI evidence; do not claim them locally.

- [ ] **Step 6: Commit**

```bash
git add skills/install-assistant-tools/_rtx/tests/install_test_utils.py skills/install-assistant-tools/_rtx/tests/test_codex_install.py skills/install-assistant-tools/_rtx/tests/test_claude_install.py skills/install-assistant-tools/_rtx/tests/test_codex_github_install.py skills/install-assistant-tools/_rtx/tests/test_claude_github_install.py skills/install-assistant-tools/_rtx/tests/test_complete_install_profiles.py .github/workflows/python-tests.yml tests/test_repository_test_checks.py
git commit -m "test: cover complete Famulus install profiles"
```

---

### Task 9: Publish one canonical workflow and reorganize Workstream 1

**Files:**
- Modify: `README.md`
- Modify: `docs/officina/installation.md`
- Modify: `skills/install-assistant-tools/SKILL.md`
- Modify: `skills/install-assistant-tools/blueprint.yaml`
- Modify: `skills/install-assistant-tools/_rtx/blueprint.yaml`
- Modify: `docs/plans/isolated-llm-testing.md`
- Modify: `docs/isolated-lm-testing.md`
- Modify: `docs/superpowers/plans/2026-08-11-isolated-lm-vm-foundation.md`
- Create: `references/installation/complete-install-commands-v1.json`
- Create: `references/installation/complete-install-commands-v1.schema.json`
- Test: `tests/test_docs_catalog.py`
- Test: `tests/test_docs_site.py`
- Test: `tests/validate_skill_runtime_doc_references.py`

**Interfaces:**
- Documents the exact fresh install, repair, native prerequisite, minimum/maximal profile, uninstall, purge, and verification commands.
- Produces `complete-install-commands-v1.json`, a closed, versioned catalog of command IDs, argv templates, required substitutions, expected exit codes, and output schemas; Plan B binds its digest.
- Links this Plan A before the VM acceptance Plan B in Workstream 1.

- [ ] **Step 1: Add failing executable documentation assertions**

Parse every published command and compare it with `--help` plus the relevant blueprint interface. Assert README contains `_phase_entry.py` for minimum setup and never describes direct scaffold as fresh installation.

Validate the command catalog against its JSON Schema. Require these IDs:
`codex-marketplace-add`, `codex-plugin-add`, `phase1-minimum`,
`phase1-maximal`, `dispatcher-help`, `invoke-skill-help`,
`llm-wakeup-help`, `lw-help`, `certify-get-weather`,
`drift-get-weather`, `manifest-uninstall`, `manifest-purge`,
`codex-plugin-remove`, `codex-marketplace-remove`, and
`installed-provenance`. Each argv is an array of literal tokens and named
substitution objects—never a shell string.

- [ ] **Step 2: Update public documentation and skill instructions**

Document the native credential prerequisite table, Ubuntu headless requirements (`dbus-daemon`, `gnome-keyring`), closed preflight errors, transaction retention table, complete profiles, direct repair, default uninstall, purge, and platform evidence boundary.

Create the command catalog from the canonical documented commands in this task,
then compute its SHA-256 when building the public-documentation bundle. The
catalog is authored and reviewed here, not inferred by the VM runner; executable
documentation tests keep it in parity with parsers and blueprints.

- [ ] **Step 3: Reorganize Workstream 1**

Put installer/security completion before immutable candidate injection and live package acceptance. Preserve the 2026-08-11 public-package failure as historical evidence. Leave the public-package checkbox open until Plan B tests the published artifact.

- [ ] **Step 4: Regenerate blueprints and generated docs**

Run: `dispatcher --caller-skill skill-certifier skill-maker._rtx.interface.sync-blueprints`

Review every generated file; stage only outputs caused by this plan.

- [ ] **Step 5: Run documentation, validator, and full gates**

Run: `python3 -m pytest -q tests/test_docs_catalog.py tests/test_docs_site.py tests/validate_skill_runtime_doc_references.py tests/validate_platform_neutral.py`

Run: `python3 repo_checks.py --suite validators --repository-view working`

Run: `python3 scripts/run-python-tests.py --suite precommit --verbose`

- [ ] **Step 6: Review against the design and commit**

Re-read `docs/superpowers/specs/2026-08-11-complete-famulus-install-acceptance-design.md`; account for every Plan A requirement and explicitly hand off VM-only requirements to Plan B.

```bash
git add README.md docs/officina/installation.md docs/plans/isolated-lm-testing.md docs/isolated-lm-testing.md docs/superpowers/plans/2026-08-11-isolated-lm-vm-foundation.md references/installation/complete-install-commands-v1.json references/installation/complete-install-commands-v1.schema.json skills/install-assistant-tools/SKILL.md skills/install-assistant-tools/blueprint.yaml skills/install-assistant-tools/_rtx/blueprint.yaml tests/test_docs_catalog.py tests/test_docs_site.py tests/validate_skill_runtime_doc_references.py
git commit -m "docs: publish complete Famulus installation workflow"
```

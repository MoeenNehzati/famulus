from __future__ import annotations

import inspect
import os
import shutil
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import officina.common.certificate_records as certificate_records
import officina.common.atomic_files as atomic_files
from officina.common.atomic_files import AtomicWriteError
from officina.common.certificate_records import (
    CertificateCleanupError,
    CertificateLogError,
    CertificateProvisioningError,
    CertificateStateConflict,
    StagedCertificateKey,
    abort_staged_certificate,
    canonical_certificate_envelope_bytes,
    canonical_certificate_payload_bytes,
    certificate_entry_hash,
    commit_staged_certificate,
    load_active_certificate_key_id,
    load_certificate_public_key,
    load_certificate_signing_key,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    provision_certificate_signing_material,
    rotate_certificate_signing_key,
    sign_certificate_payload,
    stage_certificate_signing_material,
    verify_certificate_envelope,
)
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.install_lock import InstallLock


class MemorySecretBackend:
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        self.values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.values.pop((namespace, key), None) is not None

    def snapshot(self) -> dict[tuple[str, str], str]:
        return dict(self.values)


class RetainingClearBackend(MemorySecretBackend):
    def clear(self, namespace: str, key: str) -> bool:
        return (namespace, key) in self.values


class StoreThenFailBackend(MemorySecretBackend):
    def __init__(self) -> None:
        super().__init__()
        self.submitted_secret: str | None = None

    def store(self, namespace: str, key: str, secret: str) -> None:
        super().store(namespace, key, secret)
        self.submitted_secret = secret
        raise RuntimeError("backend leaked " + secret)


def _state_paths(tmp_path: Path):
    return certificate_records.certificate_state_paths(
        platform="linux",
        home=tmp_path / "home",
        repo_root=tmp_path / "plugin-cache" / "famulus",
    )


def _copy_public_state(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def _certificate_payload(key_id: str, previous_entry_hash: str | None) -> dict[str, object]:
    return {
        "certificate_schema_version": 1,
        "subject": {"id": "demo-skill.source.gateway"},
        "node_hash": "sha256:" + "1" * 64,
        "key_id": key_id,
        "previous_entry_hash": previous_entry_hash,
    }


def test_certificate_record_owner_does_not_expose_legacy_hmac_authority() -> None:
    for name in (
        "RECORD_DIGEST_FIELD",
        "RECORD_HASH_FIELD",
        "AUTHENTICATION_FIELD",
        "HEALTH_MAC_DOMAIN",
        "HMAC_KEY_BYTES",
        "canonical_record_bytes",
        "compute_record_digest",
        "attach_record_digest",
        "record_digest_matches",
        "canonical_health_record_bytes",
        "compute_record_hash",
        "attach_record_hash",
        "attach_record_authentication",
        "record_authentication_matches",
        "load_or_create_hmac_key",
        "load_hmac_key",
    ):
        assert not hasattr(certificate_records, name)


def test_certificate_payload_and_envelope_canonicalization_is_stable() -> None:
    left = {"subject": {"version": 1, "id": "demo"}, "checks": []}
    right = {"checks": [], "subject": {"id": "demo", "version": 1}}
    envelope_left = {"payload": left, "signature": {"scheme": "ed25519", "value": "base64:AA=="}}
    envelope_right = {"signature": {"value": "base64:AA==", "scheme": "ed25519"}, "payload": right}

    assert canonical_certificate_payload_bytes(left) == canonical_certificate_payload_bytes(right)
    assert canonical_certificate_envelope_bytes(envelope_left) == canonical_certificate_envelope_bytes(
        envelope_right
    )
    assert certificate_entry_hash(envelope_left) == certificate_entry_hash(envelope_right)


@pytest.mark.parametrize(
    "value",
    [
        {"coverage": 0.5},
        {"nested": [{"ratio": float("nan")}]},
        {1: "non-string key"},
    ],
)
def test_certificate_canonicalization_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(TypeError):
        canonical_certificate_payload_bytes(value)  # type: ignore[arg-type]


def test_ed25519_key_lifecycle_uses_secret_store_and_windows_safe_public_filename(
    tmp_path: Path,
) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()

    created = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    loaded = load_certificate_signing_key(public_key_root, secret_backend=backend)

    assert loaded.key_id == created.key_id
    assert load_active_certificate_key_id(public_key_root) == created.key_id
    hexadecimal = created.key_id.removeprefix("sha256:")
    assert len(hexadecimal) == 64
    assert ":" not in hexadecimal
    assert (public_key_root / f"{hexadecimal}.pub").read_bytes().startswith(
        b"-----BEGIN PUBLIC KEY-----"
    )
    assert load_certificate_public_key(public_key_root, created.key_id) is not None
    assert len(backend.values) == 1
    assert next(iter(backend.values.values())).startswith("base64:")


def test_plugin_cache_replacement_preserves_certificate_identity(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    plugin_cache = tmp_path / "plugin-cache"
    plugin_cache.mkdir(parents=True)

    first = provision_certificate_signing_material(
        paths,
        secret_backend=backend,
    )
    shutil.rmtree(plugin_cache)
    plugin_cache.mkdir(parents=True)
    second = provision_certificate_signing_material(
        paths,
        secret_backend=backend,
    )

    assert second.key_id == first.key_id
    assert load_active_certificate_key_id(paths.public_key_root) == first.key_id
    assert load_certificate_signing_key(
        paths.public_key_root,
        secret_backend=backend,
    ).key_id == first.key_id
    if os.name == "posix":
        assert paths.public_key_root.stat().st_mode & 0o077 == 0
    stored_private = next(iter(backend.values.values())).encode("ascii")
    assert all(
        stored_private not in candidate.read_bytes()
        for candidate in tmp_path.rglob("*")
        if candidate.is_file()
    )


def test_staging_existing_valid_identity_is_idempotent(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    active = provision_certificate_signing_material(paths, secret_backend=backend)
    before = backend.snapshot()

    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    committed = commit_staged_certificate(paths, staged, secret_backend=backend)

    assert isinstance(staged, StagedCertificateKey)
    assert staged.key_id == active.key_id
    assert staged.public_key_path == (
        paths.public_key_root / f"{active.key_id.removeprefix('sha256:')}.pub"
    )
    assert staged.secret_target.endswith(active.key_id)
    assert not staged.created
    assert committed.key_id == active.key_id
    assert backend.snapshot() == before
    assert len(list(paths.public_key_root.glob("*.pub"))) == 1


def test_failure_after_private_secret_store_cleans_exact_target_without_leak(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = StoreThenFailBackend()

    with pytest.raises(CertificateProvisioningError) as captured:
        stage_certificate_signing_material(paths, secret_backend=backend)

    assert backend.submitted_secret is not None
    assert backend.submitted_secret not in str(captured.value)
    assert backend.snapshot() == {}
    assert not list(paths.public_key_root.glob("*.pub"))
    assert not paths.active_key_id.exists()


def test_post_public_creation_staging_failure_removes_exact_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    monkeypatch.setattr(
        certificate_records,
        "_bind_staged_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("failure after public creation")
        ),
    )

    with pytest.raises(CertificateProvisioningError) as captured:
        stage_certificate_signing_material(paths, secret_backend=backend)

    assert str(captured.value) == "certificate public-key staging failed"
    assert backend.snapshot() == {}
    assert not list(paths.public_key_root.glob("*.pub"))
    assert not paths.active_key_id.exists()


def test_selector_intent_journal_failure_aborts_exact_staged_pair(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)

    def journal_selector_intent() -> None:
        raise OSError("selector intent journal failed")

    with pytest.raises(OSError, match="selector intent journal failed"):
        try:
            journal_selector_intent()
        except BaseException:
            abort_staged_certificate(paths, staged, secret_backend=backend)
            raise

    assert backend.snapshot() == {}
    assert not staged.public_key_path.exists()
    assert not paths.active_key_id.exists()


def test_public_provisioner_aborts_failed_pre_selector_pair_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    active = provision_certificate_signing_material(paths, secret_backend=backend)
    before = backend.snapshot()
    staged = certificate_records._stage_generated_certificate_key(
        paths.public_key_root,
        prior_key_id=active.key_id,
        secret_backend=backend,
    )
    monkeypatch.setattr(
        certificate_records,
        "stage_certificate_signing_material",
        lambda *_args, **_kwargs: staged,
    )
    real_read = certificate_records.read_regular_file_bytes

    def corrupt_staged_public_read(
        path: Path,
        *,
        allowed_root: Path,
        allow_non_atomic: bool = False,
    ) -> bytes:
        if Path(path).absolute() == staged.public_key_path.absolute():
            return b"corrupt staged public key"
        return real_read(
            path,
            allowed_root=allowed_root,
            allow_non_atomic=allow_non_atomic,
        )

    monkeypatch.setattr(
        certificate_records,
        "read_regular_file_bytes",
        corrupt_staged_public_read,
    )

    with pytest.raises(
        CertificateProvisioningError,
        match="staged certificate pair verification failed",
    ):
        provision_certificate_signing_material(paths, secret_backend=backend)

    assert backend.snapshot() == before
    assert not staged.public_key_path.exists()
    assert paths.active_key_id.read_text(encoding="ascii") == active.key_id + "\n"
    assert load_certificate_signing_key(
        paths.public_key_root,
        secret_backend=backend,
    ).key_id == active.key_id


def test_transactional_apis_require_explicit_non_none_backend() -> None:
    paths = certificate_records.CertificateStatePaths(
        public_key_root=Path("public-keys"),
        active_key_id=Path("public-keys/active-key-id"),
    )
    staged = StagedCertificateKey(
        key_id="sha256:" + "0" * 64,
        public_key_path=Path("public-keys") / ("0" * 64 + ".pub"),
        secret_target="Famulus:skill-certifier:ed25519-private-key:sha256:" + "0" * 64,
        created=True,
    )

    for operation, args in (
        (stage_certificate_signing_material, (paths,)),
        (commit_staged_certificate, (paths, staged)),
        (abort_staged_certificate, (paths, staged)),
    ):
        assert "allow_non_atomic" not in inspect.signature(operation).parameters
        with pytest.raises(TypeError):
            inspect.signature(operation).bind(*args)
        with pytest.raises(TypeError, match="secret_backend"):
            operation(*args, secret_backend=None)


def test_commit_selector_replace_is_always_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    real_replace = certificate_records.atomic_replace_bytes
    calls: list[dict[str, object]] = []

    def capture_replace(*args, **kwargs) -> None:
        calls.append(dict(kwargs))
        real_replace(*args, **kwargs)

    monkeypatch.setattr(certificate_records, "atomic_replace_bytes", capture_replace)

    committed = commit_staged_certificate(paths, staged, secret_backend=backend)

    assert committed.key_id == staged.key_id
    assert calls == [
        {
            "allowed_root": paths.public_key_root,
            "mode": 0o600,
        }
    ]
    assert "allow_non_atomic" not in inspect.signature(
        commit_staged_certificate
    ).parameters


def test_commit_recovers_selector_write_that_completed_before_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    real_replace = certificate_records.atomic_replace_bytes

    def replace_then_raise(*args, **kwargs) -> None:
        real_replace(*args, **kwargs)
        raise OSError("selector-write-uncertain")

    monkeypatch.setattr(certificate_records, "atomic_replace_bytes", replace_then_raise)

    committed = commit_staged_certificate(paths, staged, secret_backend=backend)

    assert committed.key_id == staged.key_id
    assert load_active_certificate_key_id(paths.public_key_root) == staged.key_id
    assert staged.public_key_path.is_file()
    assert len(backend.values) == 1


def test_post_selector_verification_failure_retains_pair_for_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    real_load = certificate_records.load_certificate_signing_key

    def fail_active_verification(*args, **kwargs):
        raise ValueError("verification failed")

    monkeypatch.setattr(
        certificate_records,
        "load_certificate_signing_key",
        fail_active_verification,
    )
    with pytest.raises(CertificateProvisioningError, match="verification"):
        commit_staged_certificate(paths, staged, secret_backend=backend)

    assert paths.active_key_id.read_text() == staged.key_id + "\n"
    assert staged.public_key_path.is_file()
    assert len(backend.values) == 1

    abort_staged_certificate(paths, staged, secret_backend=backend)
    assert paths.active_key_id.read_text() == staged.key_id + "\n"
    assert staged.public_key_path.is_file()
    assert len(backend.values) == 1

    monkeypatch.setattr(certificate_records, "load_certificate_signing_key", real_load)
    resumed = commit_staged_certificate(paths, staged, secret_backend=backend)
    assert resumed.key_id == staged.key_id


def test_abort_fails_closed_on_malformed_selector_without_deleting_candidate(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    paths.active_key_id.write_bytes(b"malformed\n")
    before = backend.snapshot()

    with pytest.raises(CertificateStateConflict, match="selector"):
        abort_staged_certificate(paths, staged, secret_backend=backend)

    assert backend.snapshot() == before
    assert staged.public_key_path.is_file()
    assert paths.active_key_id.read_bytes() == b"malformed\n"


def test_abort_fails_closed_on_third_selector_without_deleting_either_pair(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    third_root = tmp_path / "third-public-keys"
    third_root.mkdir()
    third = load_or_create_certificate_signing_key(
        third_root,
        secret_backend=backend,
    )
    third_public = third_root / f"{third.key_id.removeprefix('sha256:')}.pub"
    target_public = paths.public_key_root / third_public.name
    shutil.copyfile(third_public, target_public)
    paths.active_key_id.write_text(third.key_id + "\n")
    before = backend.snapshot()

    with pytest.raises(CertificateStateConflict, match="selector"):
        abort_staged_certificate(paths, staged, secret_backend=backend)

    assert backend.snapshot() == before
    assert staged.public_key_path.is_file()
    assert target_public.is_file()
    assert load_active_certificate_key_id(paths.public_key_root) == third.key_id


def test_stage_commit_and_abort_require_same_backend_object(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    bound = MemorySecretBackend()
    replacement = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=bound)

    with pytest.raises(CertificateProvisioningError, match="backend"):
        commit_staged_certificate(paths, staged, secret_backend=replacement)
    with pytest.raises(CertificateProvisioningError, match="backend"):
        abort_staged_certificate(paths, staged, secret_backend=replacement)

    assert replacement.snapshot() == {}
    assert staged.public_key_path.is_file()
    assert len(bound.values) == 1
    abort_staged_certificate(paths, staged, secret_backend=bound)


def test_abort_cleanup_failure_is_typed_static_and_preserves_public_half(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = RetainingClearBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    secret = next(iter(backend.values.values()))

    with pytest.raises(CertificateCleanupError) as captured:
        abort_staged_certificate(paths, staged, secret_backend=backend)

    assert str(captured.value) == "certificate key cleanup failed"
    assert secret not in str(captured.value)
    assert staged.public_key_path.is_file()
    assert len(backend.values) == 1


def test_abort_refuses_replaced_public_candidate_identity(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    staged = stage_certificate_signing_material(paths, secret_backend=backend)
    staged.public_key_path.unlink()
    staged.public_key_path.write_bytes(b"replacement")

    with pytest.raises(CertificateCleanupError) as captured:
        abort_staged_certificate(paths, staged, secret_backend=backend)

    assert str(captured.value) == "certificate public-key cleanup failed"
    assert backend.snapshot() == {}
    assert staged.public_key_path.read_bytes() == b"replacement"


def test_rotation_failure_before_selector_replace_preserves_active_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    active = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    )
    before = backend.snapshot()

    monkeypatch.setattr(
        certificate_records,
        "atomic_replace_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(CertificateProvisioningError, match="selector"):
        rotate_certificate_signing_key(public_key_root, secret_backend=backend)

    assert load_active_certificate_key_id(public_key_root) == active.key_id
    assert backend.snapshot() == before
    assert len(list(public_key_root.glob("*.pub"))) == 1


def test_two_provisioners_under_home_lock_create_one_pair(tmp_path: Path) -> None:
    paths = _state_paths(tmp_path)
    home = tmp_path / "home"
    entered_store = threading.Event()
    release_store = threading.Event()

    class BlockingFirstStoreBackend(MemorySecretBackend):
        def __init__(self) -> None:
            super().__init__()
            self.first = True

        def store(self, namespace: str, key: str, secret: str) -> None:
            super().store(namespace, key, secret)
            if self.first:
                self.first = False
                entered_store.set()
                assert release_store.wait(timeout=2)

    backend = BlockingFirstStoreBackend()

    def provision() -> str:
        with InstallLock.for_home(home, timeout_seconds=2):
            return provision_certificate_signing_material(
                paths,
                secret_backend=backend,
            ).key_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provision)
        assert entered_store.wait(timeout=2)
        second = pool.submit(provision)
        assert not second.done()
        release_store.set()
        key_ids = {first.result(timeout=3), second.result(timeout=3)}

    assert len(key_ids) == 1
    assert len(backend.values) == 1
    assert len(list(paths.public_key_root.glob("*.pub"))) == 1
    assert load_active_certificate_key_id(paths.public_key_root) in key_ids


@pytest.mark.parametrize("stable_state", ["missing", "empty", "matching"])
def test_legacy_state_migrates_idempotently(
    tmp_path: Path,
    stable_state: str,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    paths.legacy_public_key_root.mkdir(parents=True)
    legacy_key = load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    if stable_state == "empty":
        paths.public_key_root.mkdir(parents=True)
    elif stable_state == "matching":
        _copy_public_state(paths.legacy_public_key_root, paths.public_key_root)

    certificate_records.migrate_legacy_certificate_state(
        tmp_path / "plugin-cache" / "famulus",
        paths,
        secret_backend=backend,
    )
    first = paths.active_key_id.read_bytes()
    certificate_records.migrate_legacy_certificate_state(
        tmp_path / "plugin-cache" / "famulus",
        paths,
        secret_backend=backend,
    )

    assert paths.active_key_id.read_bytes() == first
    assert load_certificate_signing_key(
        paths.public_key_root,
        secret_backend=backend,
    ).key_id == legacy_key.key_id


def test_conflicting_legacy_and_stable_state_fails_without_rotation(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    paths.legacy_public_key_root.mkdir(parents=True)
    paths.public_key_root.mkdir(parents=True)
    load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    stable_key = load_or_create_certificate_signing_key(
        paths.public_key_root,
        secret_backend=backend,
    )
    before = backend.snapshot()

    with pytest.raises(certificate_records.CertificateStateConflict):
        certificate_records.migrate_legacy_certificate_state(
            tmp_path / "plugin-cache" / "famulus",
            paths,
            secret_backend=backend,
        )

    assert backend.snapshot() == before
    assert load_active_certificate_key_id(paths.public_key_root) == stable_key.key_id


@pytest.mark.parametrize("linked_root", ["legacy", "stable"])
def test_legacy_migration_rejects_linked_state_roots_without_secret_changes(
    tmp_path: Path,
    linked_root: str,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    outside = tmp_path / "outside"
    outside.mkdir()
    if linked_root == "legacy":
        paths.legacy_public_key_root.parent.mkdir(parents=True)
        paths.legacy_public_key_root.symlink_to(outside, target_is_directory=True)
    else:
        paths.legacy_public_key_root.mkdir(parents=True)
        load_or_create_certificate_signing_key(
            paths.legacy_public_key_root,
            secret_backend=backend,
        )
        paths.public_key_root.parent.mkdir(parents=True)
        paths.public_key_root.symlink_to(outside, target_is_directory=True)
    before = backend.snapshot()

    with pytest.raises(certificate_records.CertificateStateConflict):
        certificate_records.migrate_legacy_certificate_state(
            tmp_path / "plugin-cache" / "famulus",
            paths,
            secret_backend=backend,
        )

    assert backend.snapshot() == before
    assert not list(outside.iterdir())


@pytest.mark.parametrize("linked_state", ["legacy", "stable"])
def test_legacy_migration_rejects_intermediate_linked_components(
    tmp_path: Path,
    linked_state: str,
) -> None:
    home = tmp_path / "home"
    repo_root = tmp_path / "plugin-cache" / "famulus"
    outside = tmp_path / "outside"
    backend = MemorySecretBackend()
    if linked_state == "legacy":
        outside_repo = outside / "famulus"
        outside_legacy = certificate_records.legacy_certificate_public_key_root(
            outside_repo
        )
        outside_legacy.mkdir(parents=True)
        load_or_create_certificate_signing_key(
            outside_legacy,
            secret_backend=backend,
        )
        (tmp_path / "plugin-cache").symlink_to(
            outside, target_is_directory=True
        )
    else:
        paths_without_link = certificate_records.certificate_state_paths(
            platform="linux",
            home=home,
            repo_root=repo_root,
        )
        assert paths_without_link.legacy_public_key_root is not None
        paths_without_link.legacy_public_key_root.mkdir(parents=True)
        load_or_create_certificate_signing_key(
            paths_without_link.legacy_public_key_root,
            secret_backend=backend,
        )
        outside_local = outside / "local"
        outside_stable = (
            outside_local / "share" / "famulus" / "certificates" / "public-keys"
        )
        _copy_public_state(
            paths_without_link.legacy_public_key_root,
            outside_stable,
        )
        home.mkdir()
        (home / ".local").symlink_to(outside_local, target_is_directory=True)
    paths = certificate_records.certificate_state_paths(
        platform="linux",
        home=home,
        repo_root=repo_root,
    )
    before = backend.snapshot()
    linked_root = (
        paths.legacy_public_key_root
        if linked_state == "legacy"
        else paths.public_key_root
    )
    assert linked_root is not None

    with pytest.raises(AtomicWriteError):
        atomic_files.read_regular_directory_entries(linked_root)
    with pytest.raises((certificate_records.CertificateStateConflict, AtomicWriteError)):
        certificate_records.migrate_legacy_certificate_state(
            repo_root,
            paths,
            secret_backend=backend,
        )

    assert backend.snapshot() == before


def test_legacy_migration_rejects_child_disappearing_after_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    paths.legacy_public_key_root.mkdir(parents=True)
    load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    before = backend.snapshot()
    real_open = atomic_files._secure_open

    def disappear_public_key(
        path: str | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if (
            dir_fd is not None
            and str(path).endswith(".pub")
            and not flags & os.O_DIRECTORY
        ):
            raise FileNotFoundError(path)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(atomic_files, "_secure_open", disappear_public_key)

    with pytest.raises(
        certificate_records.CertificateStateConflict,
        match="disappeared",
    ):
        certificate_records.migrate_legacy_certificate_state(
            tmp_path / "plugin-cache" / "famulus",
            paths,
            secret_backend=backend,
        )

    assert backend.snapshot() == before
    assert not paths.public_key_root.exists()


# famulus-skip: category=platform-contract; reason=requires native Win32 junction behavior; alternate=POSIX intermediate-symlink regression runs on non-Windows hosts
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
@pytest.mark.parametrize("linked_state", ["legacy", "stable"])
def test_legacy_migration_rejects_intermediate_windows_junctions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linked_state: str,
) -> None:
    home = tmp_path / "home"
    local_app_data = home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    repo_root = tmp_path / "plugin-cache" / "famulus"
    outside = tmp_path / "outside"
    backend = MemorySecretBackend()
    paths = certificate_records.certificate_state_paths(
        platform="win32",
        home=home,
        repo_root=repo_root,
    )
    assert paths.legacy_public_key_root is not None

    if linked_state == "legacy":
        outside_legacy = certificate_records.legacy_certificate_public_key_root(
            outside / "famulus"
        )
        outside_legacy.mkdir(parents=True)
        load_or_create_certificate_signing_key(
            outside_legacy,
            secret_backend=backend,
        )
        junction = repo_root.parent
        junction.parent.mkdir(parents=True)
        target = outside
    else:
        paths.legacy_public_key_root.mkdir(parents=True)
        load_or_create_certificate_signing_key(
            paths.legacy_public_key_root,
            secret_backend=backend,
        )
        outside_stable = outside / "Famulus" / "certificates" / "public-keys"
        _copy_public_state(paths.legacy_public_key_root, outside_stable)
        local_app_data.mkdir(parents=True)
        junction = local_app_data / "Famulus"
        target = outside / "Famulus"

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # famulus-skip: category=platform-contract; reason=junction creation is unavailable on this Windows host; alternate=POSIX intermediate-symlink regression runs on non-Windows hosts
        pytest.skip(f"Windows junction creation unavailable: {result.stderr}")

    linked_root = (
        paths.legacy_public_key_root
        if linked_state == "legacy"
        else paths.public_key_root
    )
    before = backend.snapshot()
    with pytest.raises(AtomicWriteError, match="reparse"):
        atomic_files.read_regular_directory_entries(linked_root)
    with pytest.raises(certificate_records.CertificateStateConflict):
        certificate_records.migrate_legacy_certificate_state(
            repo_root,
            paths,
            secret_backend=backend,
        )

    assert backend.snapshot() == before


def test_legacy_migration_removes_public_candidates_before_selector_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    paths.legacy_public_key_root.mkdir(parents=True)
    load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    real_create = certificate_records.atomic_create_bytes

    def fail_selector(path: Path, data: bytes, **kwargs: object) -> bool:
        if Path(path).name == "active-key-id":
            raise OSError("injected selector failure")
        return real_create(path, data, **kwargs)

    monkeypatch.setattr(certificate_records, "atomic_create_bytes", fail_selector)

    with pytest.raises(OSError, match="injected selector failure"):
        certificate_records.migrate_legacy_certificate_state(
            tmp_path / "plugin-cache" / "famulus",
            paths,
            secret_backend=backend,
        )

    assert not list(paths.public_key_root.iterdir())


def test_legacy_migration_does_not_remove_replaced_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    paths.legacy_public_key_root.mkdir(parents=True)
    load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    real_create = certificate_records.atomic_create_bytes_tracked
    replacement = b"replacement owned by another writer"

    def replace_candidate(path: Path, data: bytes, **kwargs: object):
        if Path(path).name == "active-key-id":
            raise OSError("injected selector failure")
        created = real_create(path, data, **kwargs)
        assert created is not None
        Path(path).unlink()
        Path(path).write_bytes(replacement)
        return created

    monkeypatch.setattr(
        certificate_records,
        "atomic_create_bytes_tracked",
        replace_candidate,
    )
    monkeypatch.setattr(
        certificate_records,
        "atomic_create_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected selector failure")
        ),
    )

    with pytest.raises(AtomicWriteError, match="changed"):
        certificate_records.migrate_legacy_certificate_state(
            tmp_path / "plugin-cache" / "famulus",
            paths,
            secret_backend=backend,
        )

    public_files = list(paths.public_key_root.glob("*.pub"))
    assert len(public_files) == 1
    assert public_files[0].read_bytes() == replacement


def test_legacy_migration_cleanup_uses_retained_parent_after_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    backend = MemorySecretBackend()
    paths.legacy_public_key_root.mkdir(parents=True)
    load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    real_create = certificate_records.atomic_create_bytes_tracked
    moved_root = paths.public_key_root.with_name("moved-public-keys")
    outside = tmp_path / "outside-cleanup"
    replacement = b"outside replacement"

    def swap_parent(path: Path, data: bytes, **kwargs: object):
        if Path(path).name == "active-key-id":
            raise OSError("injected selector failure")
        created = real_create(path, data, **kwargs)
        assert created is not None
        paths.public_key_root.rename(moved_root)
        outside.mkdir()
        (outside / Path(path).name).write_bytes(replacement)
        paths.public_key_root.symlink_to(outside, target_is_directory=True)
        return created

    monkeypatch.setattr(
        certificate_records,
        "atomic_create_bytes_tracked",
        swap_parent,
    )

    with pytest.raises(AtomicWriteError):
        certificate_records.migrate_legacy_certificate_state(
            tmp_path / "plugin-cache" / "famulus",
            paths,
            secret_backend=backend,
        )

    assert (outside / next(outside.iterdir()).name).read_bytes() == replacement
    assert not list(moved_root.glob("*.pub"))


def test_certificate_paths_are_stable_famulus_state_not_repository_state(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "plugin-cache" / "famulus"
    paths = certificate_records.certificate_state_paths(
        platform="linux",
        home=tmp_path / "home",
        repo_root=repo_root,
    )
    famulus = resolve_famulus_paths(platform="linux", home=tmp_path / "home")

    assert paths.public_key_root == famulus.certificate_public_key_root
    assert paths.active_key_id == famulus.certificate_public_key_root / "active-key-id"
    assert paths.legacy_public_key_root == certificate_records.legacy_certificate_public_key_root(repo_root)
    assert repo_root not in paths.public_key_root.parents


def test_ed25519_sign_verify_detects_payload_tamper_and_wrong_key(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_backend = MemorySecretBackend()
    second_backend = MemorySecretBackend()
    first = load_or_create_certificate_signing_key(first_root, secret_backend=first_backend)
    load_or_create_certificate_signing_key(second_root, secret_backend=second_backend)
    envelope = sign_certificate_payload(_certificate_payload(first.key_id, None), first)

    assert verify_certificate_envelope(envelope, first_root)
    assert not verify_certificate_envelope(envelope, second_root)

    tampered = {**envelope, "payload": {**envelope["payload"], "node_hash": "sha256:" + "2" * 64}}
    assert not verify_certificate_envelope(tampered, first_root)


def test_rotation_retains_old_public_verification_but_changes_active_key(tmp_path: Path) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    first = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    old_envelope = sign_certificate_payload(_certificate_payload(first.key_id, None), first)

    second = rotate_certificate_signing_key(public_key_root, secret_backend=backend)

    assert second.key_id != first.key_id
    assert load_active_certificate_key_id(public_key_root) == second.key_id
    assert verify_certificate_envelope(old_envelope, public_key_root)
    assert load_certificate_signing_key(public_key_root, secret_backend=backend).key_id == second.key_id
    assert len(list(public_key_root.glob("*.pub"))) == 2


def test_chained_certificate_jsonl_parses_and_verifies_rotated_history(tmp_path: Path) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    first_key = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    first = sign_certificate_payload(_certificate_payload(first_key.key_id, None), first_key)
    second_key = rotate_certificate_signing_key(public_key_root, secret_backend=backend)
    second = sign_certificate_payload(
        _certificate_payload(second_key.key_id, certificate_entry_hash(first)), second_key
    )
    log_bytes = (
        canonical_certificate_envelope_bytes(first)
        + b"\n"
        + canonical_certificate_envelope_bytes(second)
        + b"\n"
    )

    assert parse_certificate_log(log_bytes, public_key_root) == (first, second)


@pytest.mark.parametrize("failure", ["noncanonical", "missing-newline", "broken-chain", "tamper"])
def test_certificate_jsonl_rejects_suspect_log(
    tmp_path: Path,
    failure: str,
) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    key = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    first = sign_certificate_payload(_certificate_payload(key.key_id, None), key)
    previous_hash = certificate_entry_hash(first)
    second = sign_certificate_payload(_certificate_payload(key.key_id, previous_hash), key)
    first_bytes = canonical_certificate_envelope_bytes(first)
    second_bytes = canonical_certificate_envelope_bytes(second)
    if failure == "noncanonical":
        first_bytes = b" " + first_bytes
    elif failure == "missing-newline":
        with pytest.raises(CertificateLogError, match="final newline"):
            parse_certificate_log(first_bytes, public_key_root)
        return
    elif failure == "broken-chain":
        second = sign_certificate_payload(
            _certificate_payload(key.key_id, "sha256:" + "0" * 64), key
        )
        second_bytes = canonical_certificate_envelope_bytes(second)
    else:
        second = {
            **second,
            "payload": {**second["payload"], "node_hash": "sha256:" + "2" * 64},
        }
        second_bytes = canonical_certificate_envelope_bytes(second)

    with pytest.raises(CertificateLogError):
        parse_certificate_log(first_bytes + b"\n" + second_bytes + b"\n", public_key_root)


def test_certificate_jsonl_rejects_inactive_key_on_final_entry(tmp_path: Path) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    first_key = load_or_create_certificate_signing_key(public_key_root, secret_backend=backend)
    first = sign_certificate_payload(_certificate_payload(first_key.key_id, None), first_key)
    rotate_certificate_signing_key(public_key_root, secret_backend=backend)

    with pytest.raises(CertificateLogError, match="active key"):
        parse_certificate_log(
            canonical_certificate_envelope_bytes(first) + b"\n",
            public_key_root,
        )


def test_certificate_history_mode_verifies_inactive_retained_final_entry(
    tmp_path: Path,
) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    first_key = load_or_create_certificate_signing_key(
        public_key_root, secret_backend=backend
    )
    first = sign_certificate_payload(
        _certificate_payload(first_key.key_id, None), first_key
    )
    log = canonical_certificate_envelope_bytes(first) + b"\n"
    rotate_certificate_signing_key(public_key_root, secret_backend=backend)

    with pytest.raises(CertificateLogError, match="active key"):
        parse_certificate_log(log, public_key_root)
    assert parse_certificate_log(
        log,
        public_key_root,
        require_active_final=False,
    ) == (first,)


def test_certificate_key_lifecycle_keeps_fallback_outside_exact_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    create_flags: list[bool] = []
    replace_flags: list[bool] = []
    read_flags: list[bool] = []
    real_create = certificate_records.atomic_create_bytes
    real_replace = certificate_records.atomic_replace_bytes
    real_read = certificate_records.read_regular_file_bytes

    def capture_create(
        path: Path,
        data: bytes,
        *,
        allowed_root: Path,
        mode: int,
        allow_non_atomic: bool = False,
    ) -> bool:
        create_flags.append(allow_non_atomic)
        return real_create(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            allow_non_atomic=allow_non_atomic,
        )

    def capture_replace(
        path: Path,
        data: bytes,
        *,
        allowed_root: Path,
        mode: int,
        allow_non_atomic: bool = False,
    ) -> None:
        replace_flags.append(allow_non_atomic)
        real_replace(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            allow_non_atomic=allow_non_atomic,
        )

    def capture_read(
        path: Path,
        *,
        allowed_root: Path,
        allow_non_atomic: bool = False,
    ) -> bytes:
        read_flags.append(allow_non_atomic)
        return real_read(
            path,
            allowed_root=allowed_root,
            allow_non_atomic=allow_non_atomic,
        )

    monkeypatch.setattr(certificate_records, "atomic_create_bytes", capture_create)
    monkeypatch.setattr(certificate_records, "atomic_replace_bytes", capture_replace)
    monkeypatch.setattr(certificate_records, "read_regular_file_bytes", capture_read)

    first = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
        allow_non_atomic=True,
    )
    envelope = sign_certificate_payload(_certificate_payload(first.key_id, None), first)
    assert verify_certificate_envelope(
        envelope,
        public_key_root,
        allow_non_atomic=True,
    )
    assert parse_certificate_log(
        canonical_certificate_envelope_bytes(envelope) + b"\n",
        public_key_root,
        allow_non_atomic=True,
    ) == (envelope,)
    rotate_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
        allow_non_atomic=True,
    )

    assert create_flags and all(create_flags)
    assert replace_flags == [False]
    assert True in read_flags
    assert False in read_flags


def test_certificate_key_bootstrap_works_through_real_capability_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_key_root = tmp_path / "public-keys"
    public_key_root.mkdir()
    backend = MemorySecretBackend()
    monkeypatch.setattr(atomic_files.os, "supports_dir_fd", set())

    created = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
        allow_non_atomic=True,
    )
    loaded = load_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
        allow_non_atomic=True,
    )

    assert loaded.key_id == created.key_id
    assert load_active_certificate_key_id(
        public_key_root,
        allow_non_atomic=True,
    ) == created.key_id
    assert (public_key_root / "active-key-id").is_file()
    assert (public_key_root / f"{created.key_id.removeprefix('sha256:')}.pub").is_file()

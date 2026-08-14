from __future__ import annotations

import base64
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
    CertificateMutationResult,
    CertificatePreparationResult,
    CertificateRecoveryDisposition,
    CertificateStateConflict,
    StagedCertificateKey,
    abort_staged_certificate,
    abort_certificate_mutation,
    apply_certificate_mutation,
    canonical_certificate_envelope_bytes,
    canonical_certificate_payload_bytes,
    certificate_entry_hash,
    commit_staged_certificate,
    commit_certificate_mutation,
    load_active_certificate_key_id,
    load_certificate_public_key,
    load_certificate_signing_key,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    prepare_certificate_mutation,
    provision_certificate_signing_material,
    rotate_certificate_signing_key,
    sign_certificate_payload,
    stage_certificate_signing_material,
    recover_certificate_mutation,
    verify_certificate_envelope,
)
from officina.common.certificate_intents import CertificateMutationIntent
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.install_lock import InstallBusyError, InstallLock


class MemorySecretBackend:
    name = "memory"

    def __init__(
        self,
        values: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.values = {} if values is None else values

    def backend_identity(self) -> str:
        return f"{type(self).__module__}.{type(self).__name__}"

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


class NoOpStoreBackend(MemorySecretBackend):
    def store(self, namespace: str, key: str, secret: str) -> None:
        del namespace, key, secret


class WrongStoreBackend(MemorySecretBackend):
    def store(self, namespace: str, key: str, secret: str) -> None:
        del secret
        wrong = b"\x00" * 32
        self.values[(namespace, key)] = "base64:" + base64.b64encode(wrong).decode("ascii")


class RetainingWrongStoreBackend(WrongStoreBackend):
    def clear(self, namespace: str, key: str) -> bool:
        return (namespace, key) in self.values


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


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _candidate_path(paths: certificate_records.CertificateStatePaths, key_id: str) -> Path:
    return paths.public_key_root / (key_id.removeprefix("sha256:") + ".pub")


def _secret_reference(key_id: str) -> tuple[str, str]:
    return ("skill-certifier", f"ed25519-private-key:{key_id}")


def _persisted(intent: CertificateMutationIntent) -> CertificateMutationIntent:
    return CertificateMutationIntent.from_dict(intent.to_dict())


def _persisted_with_prior(
    intent: CertificateMutationIntent,
    prior_key_id: str,
) -> CertificateMutationIntent:
    payload = intent.to_dict()
    payload["prior_key_id"] = prior_key_id
    return CertificateMutationIntent.from_dict(payload)


def _prepare_create(
    tmp_path: Path,
    backend: MemorySecretBackend,
    *,
    transaction: str = "1" * 32,
) -> tuple[certificate_records.CertificateStatePaths, CertificateMutationIntent]:
    paths = _state_paths(tmp_path)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id=transaction,
        secret_backend=backend,
    )
    assert isinstance(prepared, CertificatePreparationResult)
    assert prepared.intent is not None
    assert prepared.intent.action == "create"
    return paths, prepared.intent


def _seed_legacy_with_history(
    tmp_path: Path,
    backend: MemorySecretBackend,
    *,
    retained_keys: int = 2,
) -> tuple[certificate_records.CertificateStatePaths, str]:
    paths = _state_paths(tmp_path)
    assert paths.legacy_public_key_root is not None
    paths.legacy_public_key_root.mkdir(parents=True)
    active = load_or_create_certificate_signing_key(
        paths.legacy_public_key_root,
        secret_backend=backend,
    )
    for _ in range(retained_keys - 1):
        active = rotate_certificate_signing_key(
            paths.legacy_public_key_root,
            secret_backend=backend,
        )
    return paths, active.key_id


def test_certificate_mutation_public_result_shapes_are_closed() -> None:
    assert tuple(CertificatePreparationResult.__dataclass_fields__) == ("key_id", "intent")
    assert tuple(CertificateMutationResult.__dataclass_fields__) == ("key_id", "disposition")
    assert tuple(item.value for item in CertificateRecoveryDisposition) == (
        "abandoned",
        "aborted",
        "staged",
        "committed",
    )


def test_prepare_create_is_nonmutating_and_apply_stages_only_under_install_lock(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths = _state_paths(tmp_path)
    before = _tree_bytes(tmp_path)

    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="1" * 32,
        secret_backend=backend,
    )

    assert prepared.intent is not None
    assert prepared.key_id == prepared.intent.active_key_id
    assert prepared.intent.action == "create"
    assert prepared.intent.prior_key_id is None
    assert prepared.intent.backend_identity == backend.backend_identity()
    assert _tree_bytes(tmp_path) == before
    assert backend.snapshot() == {}

    with InstallLock.for_home(tmp_path / "home"):
        staged = apply_certificate_mutation(
            paths,
            prepared.intent,
            secret_backend=backend,
        )

    assert staged == CertificateMutationResult(
        key_id=prepared.key_id,
        disposition=CertificateRecoveryDisposition.STAGED,
    )
    assert not paths.active_key_id.exists()
    assert _candidate_path(paths, prepared.key_id).is_file()
    assert set(backend.values) == {_secret_reference(prepared.key_id)}


def test_prepare_reuses_valid_active_identity_without_intent_or_mutation(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    active = provision_certificate_signing_material(paths, secret_backend=backend)
    before_files = _tree_bytes(tmp_path)
    before_secrets = backend.snapshot()

    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="2" * 32,
        secret_backend=backend,
    )

    assert prepared == CertificatePreparationResult(key_id=active.key_id, intent=None)
    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secrets


def test_prepare_legacy_copy_is_nonmutating_and_retains_every_public_key(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, active_key_id = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    before_files = _tree_bytes(tmp_path)
    before_secrets = backend.snapshot()

    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="3" * 32,
        secret_backend=backend,
    )

    assert prepared.intent is not None
    assert prepared.key_id == active_key_id
    assert prepared.intent.action == "copy_legacy"
    assert prepared.intent.prior_key_id is None
    assert len(prepared.intent.public_files) == 3
    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secrets

    staged = apply_certificate_mutation(paths, prepared.intent, secret_backend=backend)
    assert staged.disposition is CertificateRecoveryDisposition.STAGED
    assert not paths.active_key_id.exists()
    assert len(list(paths.public_key_root.glob("*.pub"))) == 3
    assert backend.snapshot() == before_secrets

    committed = commit_certificate_mutation(paths, prepared.intent, secret_backend=backend)
    assert committed.disposition is CertificateRecoveryDisposition.COMMITTED
    assert load_active_certificate_key_id(paths.public_key_root) == active_key_id
    assert backend.snapshot() == before_secrets


def test_prepare_regenerates_a_generated_secret_target_collision_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    first = certificate_records.Ed25519PrivateKey.generate()
    second = certificate_records.Ed25519PrivateKey.generate()
    first_id = certificate_records.certificate_key_id(first.public_key())
    backend.values[_secret_reference(first_id)] = "reserved"
    generated = iter((first, second))
    monkeypatch.setattr(
        certificate_records.Ed25519PrivateKey,
        "generate",
        lambda: next(generated),
    )
    before = backend.snapshot()

    prepared = prepare_certificate_mutation(
        _state_paths(tmp_path),
        transaction_id="4" * 32,
        secret_backend=backend,
    )

    assert prepared.key_id == certificate_records.certificate_key_id(second.public_key())
    assert backend.snapshot() == before
    assert _tree_bytes(tmp_path) == {}


def test_prepare_regenerates_intent_and_quarantine_identifier_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = "b" * 32
    identifiers = iter(
        (
            transaction_id,
            "c" * 32,
            "c" * 32,
            "d" * 32,
        )
    )
    monkeypatch.setattr(
        certificate_records.secrets,
        "token_hex",
        lambda _size: next(identifiers),
    )

    prepared = prepare_certificate_mutation(
        _state_paths(tmp_path),
        transaction_id=transaction_id,
        secret_backend=MemorySecretBackend(),
    )

    assert prepared.intent is not None
    identifiers_used = {
        transaction_id,
        prepared.intent.intent_id,
        *(record.quarantine_id for record in prepared.intent.public_files),
    }
    assert len(identifiers_used) == 3
    assert prepared.intent.intent_id == "c" * 32
    assert prepared.intent.public_files[0].quarantine_id == "d" * 32


def test_prepare_identifier_collision_exhaustion_is_static_and_nonmutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = "e" * 32
    monkeypatch.setattr(
        certificate_records.secrets,
        "token_hex",
        lambda _size: transaction_id,
    )
    backend = MemorySecretBackend()

    with pytest.raises(
        CertificateProvisioningError,
        match="^certificate intent identifier allocation failed$",
    ):
        prepare_certificate_mutation(
            _state_paths(tmp_path),
            transaction_id=transaction_id,
            secret_backend=backend,
        )

    assert backend.snapshot() == {}
    assert _tree_bytes(tmp_path) == {}


@pytest.mark.parametrize("failure", ["backend", "selector", "legacy-conflict"])
def test_prepare_failures_do_not_mutate_certificate_or_secret_state(
    tmp_path: Path,
    failure: str,
) -> None:
    paths = _state_paths(tmp_path)
    backend = MemorySecretBackend()
    if failure == "backend":
        class FailingLookupBackend(MemorySecretBackend):
            def lookup(self, namespace: str, key: str) -> str | None:
                raise RuntimeError("private-canary")

        backend = FailingLookupBackend()
    elif failure == "selector":
        paths.public_key_root.mkdir(parents=True)
        paths.active_key_id.write_bytes(b"malformed\n")
    else:
        paths, _ = _seed_legacy_with_history(tmp_path, backend)
        paths.public_key_root.mkdir(parents=True)
        paths.active_key_id.write_bytes(("sha256:" + "f" * 64 + "\n").encode("ascii"))
    before_files = _tree_bytes(tmp_path)
    before_secrets = backend.snapshot()

    with pytest.raises((CertificateProvisioningError, CertificateStateConflict)) as captured:
        prepare_certificate_mutation(
            paths,
            transaction_id="5" * 32,
            secret_backend=backend,
        )

    assert "private-canary" not in str(captured.value)
    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secrets


def test_live_mutations_require_exact_intent_and_backend_objects_before_writes(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    reconstructed = _persisted(intent)
    other_backend = MemorySecretBackend(backend.values)

    for operation in (apply_certificate_mutation, commit_certificate_mutation, abort_certificate_mutation):
        with pytest.raises(CertificateProvisioningError, match="binding"):
            operation(paths, reconstructed, secret_backend=backend)
        with pytest.raises(CertificateProvisioningError, match="backend identity changed"):
            operation(paths, intent, secret_backend=other_backend)

    assert backend.snapshot() == {}
    assert _tree_bytes(tmp_path) == {}


def test_live_create_apply_abort_clears_only_exact_verified_candidate(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)

    aborted = abort_certificate_mutation(paths, intent, secret_backend=backend)

    assert aborted == CertificateMutationResult(
        key_id=intent.active_key_id,
        disposition=CertificateRecoveryDisposition.ABORTED,
    )
    assert backend.snapshot() == {}
    assert not paths.public_key_root.exists() or not list(paths.public_key_root.iterdir())


@pytest.mark.parametrize(
    ("backend_type", "expected_error"),
    [
        (NoOpStoreBackend, CertificateProvisioningError),
        (WrongStoreBackend, CertificateProvisioningError),
        (RetainingWrongStoreBackend, CertificateCleanupError),
    ],
)
def test_create_apply_verifies_stored_secret_before_publication_and_closes_cleanup(
    tmp_path: Path,
    backend_type: type[MemorySecretBackend],
    expected_error: type[BaseException],
) -> None:
    backend = backend_type()
    paths, intent = _prepare_create(tmp_path, backend)

    with pytest.raises(expected_error) as captured:
        apply_certificate_mutation(paths, intent, secret_backend=backend)

    assert "base64:" not in str(captured.value)
    assert not list(paths.public_key_root.glob("*.pub"))
    if backend_type is RetainingWrongStoreBackend:
        assert backend.snapshot()
    else:
        assert backend.snapshot() == {}


def test_commit_revalidates_selector_immediately_before_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secret = backend.snapshot()
    before_public = _candidate_path(paths, intent.active_key_id).read_bytes()
    third = "sha256:" + "f" * 64

    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate
    drifted = False

    def drift_then_revalidate(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        nonlocal drifted
        if not drifted:
            drifted = True
            paths.active_key_id.write_bytes((third + "\n").encode("ascii"))
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        drift_then_revalidate,
    )

    with pytest.raises(CertificateStateConflict):
        commit_certificate_mutation(paths, intent, secret_backend=backend)

    assert paths.active_key_id.read_text(encoding="ascii") == third + "\n"
    assert backend.snapshot() == before_secret
    assert _candidate_path(paths, intent.active_key_id).read_bytes() == before_public


def test_abort_revalidates_selector_immediately_before_secret_or_public_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secret = backend.snapshot()
    before_public = _candidate_path(paths, intent.active_key_id).read_bytes()
    third = "sha256:" + "f" * 64

    def drift_then_revalidate(
        root: Path,
        supplied: CertificateMutationIntent,
        expected: str,
    ) -> None:
        paths.active_key_id.write_bytes((third + "\n").encode("ascii"))
        if certificate_records._intent_selector_state(root, supplied) != expected:
            raise CertificateStateConflict("certificate selector conflicts with intent")

    monkeypatch.setattr(
        certificate_records,
        "_require_intent_selector_state",
        drift_then_revalidate,
        raising=False,
    )

    with pytest.raises(CertificateStateConflict):
        abort_certificate_mutation(paths, intent, secret_backend=backend)

    assert paths.active_key_id.read_text(encoding="ascii") == third + "\n"
    assert backend.snapshot() == before_secret
    assert _candidate_path(paths, intent.active_key_id).read_bytes() == before_public


def test_caller_held_install_lock_excludes_cooperative_certificate_mutator(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    home = tmp_path / "home"
    paths = _state_paths(tmp_path)

    with InstallLock.for_home(home):
        with pytest.raises(InstallBusyError):
            with InstallLock.for_home(home, timeout_seconds=0):
                raise AssertionError("second cooperative mutator acquired the home lock")
        prepared = prepare_certificate_mutation(
            paths,
            transaction_id="a" * 32,
            secret_backend=backend,
        )
        assert prepared.intent is not None
        apply_certificate_mutation(paths, prepared.intent, secret_backend=backend)
        abort_certificate_mutation(paths, prepared.intent, secret_backend=backend)


def test_live_abort_before_apply_is_abandoned_and_nonmutating(tmp_path: Path) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)

    result = abort_certificate_mutation(paths, intent, secret_backend=backend)

    assert result.disposition is CertificateRecoveryDisposition.ABANDONED
    assert backend.snapshot() == {}
    assert _tree_bytes(tmp_path) == {}


def test_live_abort_after_partial_legacy_apply_removes_all_created_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="8" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    before_secrets = backend.snapshot()
    real_create = certificate_records.atomic_create_bytes_tracked
    calls = 0

    def fail_second_copy(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("copy failed")
        return real_create(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(certificate_records, "atomic_create_bytes_tracked", fail_second_copy)
    with pytest.raises(CertificateProvisioningError):
        apply_certificate_mutation(paths, intent, secret_backend=backend)
    monkeypatch.undo()

    result = abort_certificate_mutation(paths, intent, secret_backend=backend)

    assert result.disposition is CertificateRecoveryDisposition.ABORTED
    assert backend.snapshot() == before_secrets
    assert not list(paths.public_key_root.glob("*.pub"))


def test_prepare_normalizes_backend_failure_while_validating_existing_state(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    seed = MemorySecretBackend()
    provision_certificate_signing_material(paths, secret_backend=seed)

    class FailingExistingBackend(MemorySecretBackend):
        def lookup(self, namespace: str, key: str) -> str | None:
            raise RuntimeError("private-canary-from-backend")

    before = _tree_bytes(tmp_path)
    with pytest.raises(CertificateProvisioningError) as captured:
        prepare_certificate_mutation(
            paths,
            transaction_id="9" * 32,
            secret_backend=FailingExistingBackend(seed.values),
        )

    assert str(captured.value) == "certificate mutation preparation failed"
    assert "private-canary" not in str(captured.value)
    assert _tree_bytes(tmp_path) == before


def test_prepare_normalizes_dynamic_state_conflict_without_path_or_errno(
    tmp_path: Path,
) -> None:
    paths = _state_paths(tmp_path)
    paths.public_key_root.mkdir(parents=True)
    unexpected = paths.public_key_root / "unexpected-private-canary"
    unexpected.write_bytes(b"do not expose this payload")

    with pytest.raises(CertificateStateConflict) as captured:
        prepare_certificate_mutation(
            paths,
            transaction_id="f" * 32,
            secret_backend=MemorySecretBackend(),
        )

    assert str(captured.value) == "certificate mutation preparation failed"
    assert str(paths.public_key_root) not in str(captured.value)
    assert "unexpected-private-canary" not in str(captured.value)


def test_live_create_commit_is_idempotent_after_selector_replacement(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)

    first = commit_certificate_mutation(paths, intent, secret_backend=backend)
    second = commit_certificate_mutation(paths, intent, secret_backend=backend)

    assert first.disposition is CertificateRecoveryDisposition.COMMITTED
    assert second == first
    assert load_certificate_signing_key(
        paths.public_key_root,
        secret_backend=backend,
    ).key_id == intent.active_key_id


@pytest.mark.parametrize("boundary", ["secret", "public", "staged", "selector", "verified"])
def test_create_restart_recovery_covers_every_mutation_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)

    if boundary == "secret":
        real_store = certificate_records.secret_store.store

        def crash_after_store(*args: object, **kwargs: object) -> None:
            real_store(*args, **kwargs)  # type: ignore[arg-type]
            raise SystemExit("crash after secret")

        monkeypatch.setattr(certificate_records.secret_store, "store", crash_after_store)
        with pytest.raises(SystemExit):
            apply_certificate_mutation(paths, intent, secret_backend=backend)
    elif boundary == "public":
        real_create = certificate_records.atomic_create_bytes_tracked

        def crash_after_public(*args: object, **kwargs: object):
            created = real_create(*args, **kwargs)  # type: ignore[arg-type]
            raise SystemExit("crash after public")

        monkeypatch.setattr(certificate_records, "atomic_create_bytes_tracked", crash_after_public)
        with pytest.raises(SystemExit):
            apply_certificate_mutation(paths, intent, secret_backend=backend)
    else:
        apply_certificate_mutation(paths, intent, secret_backend=backend)
        if boundary == "selector":
            real_replace = atomic_files._secure_replace
            stage = f".famulus-staged-{intent.intent_id}"

            def crash_after_selector(
                parent_fd: int,
                source: str,
                destination: str,
            ) -> None:
                real_replace(parent_fd, source, destination)
                if source == stage:
                    raise SystemExit("crash after selector")

            monkeypatch.setattr(atomic_files, "_secure_replace", crash_after_selector)
            with pytest.raises(SystemExit):
                commit_certificate_mutation(paths, intent, secret_backend=backend)
        elif boundary == "verified":
            real_load = certificate_records.load_certificate_signing_key
            calls = 0

            def crash_post_selector(*args: object, **kwargs: object):
                nonlocal calls
                calls += 1
                result = real_load(*args, **kwargs)  # type: ignore[arg-type]
                if paths.active_key_id.exists():
                    raise SystemExit("crash after verification")
                return result

            monkeypatch.setattr(certificate_records, "load_certificate_signing_key", crash_post_selector)
            with pytest.raises(SystemExit):
                commit_certificate_mutation(paths, intent, secret_backend=backend)

    monkeypatch.undo()
    recovered_backend = MemorySecretBackend(backend.values)
    directive = "commit" if boundary in {"selector", "verified"} else "abort"
    recovered = recover_certificate_mutation(
        paths,
        _persisted(intent),
        directive,
        secret_backend=recovered_backend,
    )
    expected = (
        CertificateRecoveryDisposition.COMMITTED
        if boundary in {"selector", "verified"}
        else CertificateRecoveryDisposition.ABORTED
    )
    assert recovered.disposition is expected
    if expected is CertificateRecoveryDisposition.ABORTED:
        assert backend.snapshot() == {}
        assert not list(paths.public_key_root.glob("*.pub"))
    else:
        assert load_certificate_signing_key(
            paths.public_key_root,
            secret_backend=recovered_backend,
        ).key_id == intent.active_key_id


@pytest.mark.parametrize("copied_before_crash", [1, 2, 3])
def test_legacy_restart_aborts_after_each_copied_public_file_without_clearing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    copied_before_crash: int,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="6" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    before_secrets = backend.snapshot()
    real_create = certificate_records.atomic_create_bytes_tracked
    calls = 0

    def crash_after_selected_copy(*args: object, **kwargs: object):
        nonlocal calls
        created = real_create(*args, **kwargs)  # type: ignore[arg-type]
        calls += 1
        if calls == copied_before_crash:
            raise SystemExit("crash after copied public")
        return created

    monkeypatch.setattr(certificate_records, "atomic_create_bytes_tracked", crash_after_selected_copy)
    with pytest.raises(SystemExit):
        apply_certificate_mutation(paths, intent, secret_backend=backend)

    recovered = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "abort",
        secret_backend=MemorySecretBackend(backend.values),
    )
    assert recovered.disposition is CertificateRecoveryDisposition.ABORTED
    assert backend.snapshot() == before_secrets
    assert not list(paths.public_key_root.glob("*.pub"))
    assert not list(paths.public_key_root.glob(".famulus-quarantine-*"))


def test_legacy_restart_commits_complete_stage_with_new_same_identity_adapter(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, active_key_id = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="a" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    before_secrets = backend.snapshot()
    apply_certificate_mutation(paths, intent, secret_backend=backend)

    result = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "commit",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert result == CertificateMutationResult(
        key_id=active_key_id,
        disposition=CertificateRecoveryDisposition.COMMITTED,
    )
    assert backend.snapshot() == before_secrets
    assert load_active_certificate_key_id(paths.public_key_root) == active_key_id


def test_recovery_rejects_unjournaled_third_public_file_before_cleanup(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    third = paths.public_key_root / ("f" * 64 + ".pub")
    third.write_bytes(b"unjournaled public state")
    before_files = _tree_bytes(tmp_path)
    before_secrets = backend.snapshot()

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secrets


# famulus-skip: category=platform-contract; reason=requires a native POSIX FIFO entry to prove unexpected payloads are never opened; alternate=bounded native Windows name-enumeration unit coverage exercises the no-payload branch
@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO non-read regression")
def test_recovery_rejects_unexpected_name_before_opening_or_reading_its_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    unexpected = paths.public_key_root / "unexpected-private-canary"
    os.mkfifo(unexpected)
    real_read = certificate_records.read_regular_file_bytes
    reads: list[str] = []

    def record_expected_reads(*args: object, **kwargs: object) -> bytes:
        reads.append(Path(args[0]).name)
        return real_read(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(certificate_records, "read_regular_file_bytes", record_expected_reads)
    monkeypatch.setattr(
        certificate_records,
        "read_regular_directory_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("payload-reading inventory was used")
        ),
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert unexpected.name not in reads


def test_recovery_rejects_unexpected_entry_added_after_initial_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secret = backend.snapshot()
    candidate = _candidate_path(paths, intent.active_key_id)
    before_public = candidate.read_bytes()
    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate
    injected = False

    def add_unexpected_then_revalidate(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            (paths.public_key_root / "unexpected").write_bytes(
                b"private-canary-must-not-be-read"
            )
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        add_unexpected_then_revalidate,
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secret
    assert candidate.read_bytes() == before_public
    assert (paths.public_key_root / "unexpected").read_bytes().startswith(b"private-canary")


# famulus-skip: category=platform-contract; reason=renaming a retained open directory is the POSIX root-swap regression fixture; alternate=Windows retained-root identity is covered through its native-handle unit contract and platform CI
@pytest.mark.skipif(os.name != "posix", reason="POSIX retained-root swap regression")
def test_recovery_rejects_root_swap_after_initial_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secret = backend.snapshot()
    displaced = paths.public_key_root.with_name("displaced-public-keys")
    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate
    injected = False

    def swap_root_then_revalidate(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            paths.public_key_root.rename(displaced)
            shutil.copytree(displaced, paths.public_key_root)
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        swap_root_then_revalidate,
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secret
    assert _candidate_path(paths, intent.active_key_id).is_file()
    assert (displaced / _candidate_path(paths, intent.active_key_id).name).is_file()


@pytest.mark.parametrize("oversized", ["selector", "public"])
def test_recovery_rejects_oversized_expected_payloads(
    tmp_path: Path,
    oversized: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    candidate = _candidate_path(paths, intent.active_key_id)
    if oversized == "selector":
        paths.active_key_id.write_bytes(b"x" * 4096)
    else:
        candidate.write_bytes(candidate.read_bytes() + b"x" * 4096)
    before_files = _tree_bytes(tmp_path)
    before_secret = backend.snapshot()

    def reject_unbounded_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("recovery used the unbounded path reader")

    monkeypatch.setattr(
        certificate_records,
        "read_regular_file_bytes",
        reject_unbounded_read,
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secret


# famulus-skip: category=platform-contract; reason=os.fork provides real abrupt-child-death boundaries through the live POSIX commit path; alternate=Windows deterministic build, publish, rename, and discard functions have direct unit contracts and native platform CI
@pytest.mark.skipif(os.name != "posix", reason="POSIX abrupt selector-transaction death")
@pytest.mark.parametrize(
    "boundary",
    [
        "after-build-create",
        "mid-build-write",
        "after-build-fsync",
        "after-stage-publish",
        "after-selector-rename",
    ],
)
def test_recovery_resumes_real_child_death_from_live_selector_commit(
    tmp_path: Path,
    boundary: str,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    intended = (intent.active_key_id + "\n").encode("ascii")
    build = paths.public_key_root / f".famulus-build-{intent.intent_id}"
    staging = paths.public_key_root / f".famulus-staged-{intent.intent_id}"

    child = os.fork()
    if child == 0:
        real_open_build = atomic_files._open_inventory_build
        real_write = atomic_files.os.write
        real_publish = atomic_files._secure_rename_noreplace
        real_replace = atomic_files._secure_replace

        def create_then_die(parent_fd: int, name: str, mode: int) -> int:
            descriptor = real_open_build(parent_fd, name, mode)
            if boundary == "after-build-create" and name == build.name:
                os._exit(73)
            return descriptor

        def write_then_die(descriptor: int, data: bytes) -> int:
            if boundary == "mid-build-write" and build.exists():
                metadata = os.fstat(descriptor)
                build_metadata = build.stat()
                if (metadata.st_dev, metadata.st_ino) == (
                    build_metadata.st_dev,
                    build_metadata.st_ino,
                ):
                    real_write(descriptor, data[: max(1, len(data) // 2)])
                    os._exit(73)
            return real_write(descriptor, data)

        def die_at_publish(parent_fd: int, source: str, destination: str) -> None:
            if source == build.name and boundary == "after-build-fsync":
                os._exit(73)
            real_publish(parent_fd, source, destination)
            if source == build.name and boundary == "after-stage-publish":
                os._exit(73)

        def die_at_boundary(parent_fd: int, source: str, destination: str) -> None:
            if source == staging.name and boundary == "after-selector-rename":
                real_replace(parent_fd, source, destination)
                os._exit(73)
            real_replace(parent_fd, source, destination)

        atomic_files._open_inventory_build = create_then_die
        atomic_files.os.write = write_then_die
        atomic_files._secure_rename_noreplace = die_at_publish
        atomic_files._secure_replace = die_at_boundary
        try:
            commit_certificate_mutation(
                paths,
                intent,
                secret_backend=backend,
            )
        except BaseException:
            os._exit(74)
        os._exit(75)

    _pid, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    if boundary == "after-build-create":
        assert build.read_bytes() == b""
        assert not paths.active_key_id.exists()
    elif boundary == "mid-build-write":
        partial = build.read_bytes()
        assert 0 < len(partial) < len(intended)
        assert not paths.active_key_id.exists()
    elif boundary == "after-build-fsync":
        assert build.read_bytes() == intended
        assert not staging.exists()
        assert not paths.active_key_id.exists()
    elif boundary == "after-stage-publish":
        assert not build.exists()
        assert staging.read_bytes() == intended
        assert not paths.active_key_id.exists()
    else:
        assert not build.exists()
        assert not staging.exists()
        assert paths.active_key_id.read_bytes() == intended

    recovered = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "commit",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert recovered.disposition is CertificateRecoveryDisposition.COMMITTED
    assert paths.active_key_id.read_bytes() == intended
    assert not build.exists()
    assert not staging.exists()


def test_recovery_adopts_exact_staged_selector_when_canonical_is_intended(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    intended = (intent.active_key_id + "\n").encode("ascii")
    paths.active_key_id.write_bytes(intended)
    staging = paths.public_key_root / f".famulus-staged-{intent.intent_id}"
    staging.write_bytes(intended)

    recovered = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "commit",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert recovered.disposition is CertificateRecoveryDisposition.COMMITTED
    assert paths.active_key_id.read_bytes() == intended
    assert not staging.exists()


@pytest.mark.parametrize("directive", ["commit", "abort"])
def test_recovery_handles_partial_private_selector_build(
    tmp_path: Path,
    directive: str,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    build = paths.public_key_root / f".famulus-build-{intent.intent_id}"
    stage = paths.public_key_root / f".famulus-staged-{intent.intent_id}"
    build.write_bytes(b"partial selector")

    recovered = recover_certificate_mutation(
        paths,
        _persisted(intent),
        directive,
        secret_backend=MemorySecretBackend(backend.values),
    )

    expected = (
        CertificateRecoveryDisposition.COMMITTED
        if directive == "commit"
        else CertificateRecoveryDisposition.ABORTED
    )
    assert recovered.disposition is expected
    assert not build.exists()
    assert not stage.exists()
    if directive == "commit":
        assert paths.active_key_id.read_text(encoding="ascii") == intent.active_key_id + "\n"
    else:
        assert not list(paths.public_key_root.iterdir())


def test_recovery_rejects_private_selector_build_stage_ambiguity(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    intended = (intent.active_key_id + "\n").encode("ascii")
    build = paths.public_key_root / f".famulus-build-{intent.intent_id}"
    stage = paths.public_key_root / f".famulus-staged-{intent.intent_id}"
    build.write_bytes(intended)
    stage.write_bytes(intended)
    before_secret = backend.snapshot()

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "commit",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert build.read_bytes() == intended
    assert stage.read_bytes() == intended
    assert backend.snapshot() == before_secret


@pytest.mark.parametrize(
    ("private_entry", "private_state"),
    [
        ("staged", "mismatch"),
        ("staged", "symlink"),
        ("build", "symlink"),
        pytest.param(
            "staged",
            "special",
            # famulus-skip: category=platform-contract; reason=mkfifo supplies the native special-entry recovery fixture; alternate=Windows no-reparse regular-file validation has native unit and platform-CI coverage
            marks=pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO fixture"),
        ),
        pytest.param(
            "build",
            "special",
            # famulus-skip: category=platform-contract; reason=mkfifo supplies the native special-entry recovery fixture; alternate=Windows no-reparse regular-file validation has native unit and platform-CI coverage
            marks=pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO fixture"),
        ),
    ],
)
def test_recovery_rejects_invalid_deterministic_selector_private_entry(
    tmp_path: Path,
    private_entry: str,
    private_state: str,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    private = paths.public_key_root / f".famulus-{private_entry}-{intent.intent_id}"
    if private_state == "mismatch":
        private.write_bytes(b"third selector")
    elif private_state == "symlink":
        private.symlink_to(_candidate_path(paths, intent.active_key_id))
    else:
        os.mkfifo(private)
    before_secret = backend.snapshot()
    before_public = _candidate_path(paths, intent.active_key_id).read_bytes()

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "commit",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secret
    assert _candidate_path(paths, intent.active_key_id).read_bytes() == before_public
    assert os.path.lexists(private)


def test_abort_recovery_discards_exact_staged_selector_before_pair_cleanup(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    staging = paths.public_key_root / f".famulus-staged-{intent.intent_id}"
    staging.write_bytes((intent.active_key_id + "\n").encode("ascii"))

    recovered = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "abort",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert recovered.disposition is CertificateRecoveryDisposition.ABORTED
    assert backend.snapshot() == {}
    assert not list(paths.public_key_root.iterdir())


def test_recovery_selector_drift_before_secret_clear_preserves_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    prior = "sha256:" + "a" * 64
    third = "sha256:" + "b" * 64
    restart_intent = _persisted_with_prior(intent, prior)
    paths.active_key_id.write_text(prior + "\n", encoding="ascii")
    before_secret = backend.snapshot()
    candidate = _candidate_path(paths, intent.active_key_id)
    before_public = candidate.read_bytes()
    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate

    def drift_then_revalidate(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        paths.active_key_id.write_text(third + "\n", encoding="ascii")
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        drift_then_revalidate,
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            restart_intent,
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secret
    assert candidate.read_bytes() == before_public
    assert paths.active_key_id.read_text(encoding="ascii") == third + "\n"


def test_recovery_selector_drift_before_selector_replace_preserves_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    prior = "sha256:" + "a" * 64
    third = "sha256:" + "b" * 64
    restart_intent = _persisted_with_prior(intent, prior)
    paths.active_key_id.write_text(prior + "\n", encoding="ascii")
    before_secret = backend.snapshot()
    candidate = _candidate_path(paths, intent.active_key_id)
    before_public = candidate.read_bytes()
    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate

    def drift_then_revalidate(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        paths.active_key_id.write_text(third + "\n", encoding="ascii")
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        drift_then_revalidate,
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            restart_intent,
            "commit",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secret
    assert candidate.read_bytes() == before_public
    assert paths.active_key_id.read_text(encoding="ascii") == third + "\n"


@pytest.mark.parametrize("boundary", ["relocation", "disposal"])
@pytest.mark.parametrize("file_index", [1, 2, 3])
def test_legacy_recovery_selector_drift_before_every_public_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    file_index: int,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="9" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    prior = "sha256:" + "a" * 64
    third = "sha256:" + "b" * 64
    restart_intent = _persisted_with_prior(intent, prior)
    paths.active_key_id.write_text(prior + "\n", encoding="ascii")
    before_secrets = backend.snapshot()
    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate
    calls = 0
    target_call = file_index if boundary == "relocation" else 3 + file_index

    def drift_at_boundary(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == target_call:
            paths.active_key_id.write_text(third + "\n", encoding="ascii")
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        drift_at_boundary,
    )

    with pytest.raises(CertificateCleanupError):
        recover_certificate_mutation(
            paths,
            restart_intent,
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    canonical = list(paths.public_key_root.glob("*.pub"))
    quarantined = list(paths.public_key_root.glob(".famulus-quarantine-*"))
    if boundary == "relocation":
        assert len(canonical) == 4 - file_index
        assert len(quarantined) == file_index - 1
    else:
        assert canonical == []
        assert len(quarantined) == 4 - file_index
    assert backend.snapshot() == before_secrets
    assert paths.active_key_id.read_text(encoding="ascii") == third + "\n"


def test_recovery_fails_closed_when_intended_selector_disappears_after_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    paths.active_key_id.write_bytes((intent.active_key_id + "\n").encode("ascii"))
    before_secret = backend.snapshot()
    before_public = _candidate_path(paths, intent.active_key_id).read_bytes()
    real_revalidate = atomic_files.RetainedBoundedDirectoryInventory.revalidate
    injected = False

    def disappear_then_revalidate(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            paths.active_key_id.unlink()
        real_revalidate(inventory)

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "revalidate",
        disappear_then_revalidate,
    )

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "commit",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secret
    assert _candidate_path(paths, intent.active_key_id).read_bytes() == before_public


def test_legacy_recovery_fails_closed_when_candidate_disappears_after_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="1" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secrets = backend.snapshot()
    first = _candidate_path(paths, intent.public_files[0].key_id)
    real_track = atomic_files.RetainedBoundedDirectoryInventory.track_existing_regular_file
    observed = 0

    def disappear_after_complete_observation(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
        canonical_name: str,
        expected_bytes: bytes,
        *,
        quarantine_id: str,
    ) -> atomic_files.TrackedExistingFile:
        nonlocal observed
        authority = real_track(
            inventory,
            canonical_name,
            expected_bytes,
            quarantine_id=quarantine_id,
        )
        observed += 1
        if observed == len(intent.public_files):
            first.unlink()
        return authority

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "track_existing_regular_file",
        disappear_after_complete_observation,
    )

    with pytest.raises(CertificateCleanupError):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert backend.snapshot() == before_secrets
    assert not first.exists()


def test_create_recovery_resumes_after_crash_immediately_after_relocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    real_relocate = atomic_files.TrackedExistingFile.relocate
    crashed = False

    def relocate_then_crash(authority: atomic_files.TrackedExistingFile) -> None:
        nonlocal crashed
        real_relocate(authority)
        if not crashed:
            crashed = True
            raise SystemExit("crash after relocation")

    monkeypatch.setattr(atomic_files.TrackedExistingFile, "relocate", relocate_then_crash)
    with pytest.raises(CertificateCleanupError):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )
    assert list(paths.public_key_root.glob(".famulus-quarantine-*"))
    monkeypatch.undo()

    resumed = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "abort",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert resumed.disposition is CertificateRecoveryDisposition.ABORTED
    assert backend.snapshot() == {}
    assert not list(paths.public_key_root.iterdir())


@pytest.mark.parametrize("disposal_before_crash", [1, 2, 3])
def test_legacy_recovery_resumes_after_crash_after_each_disposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disposal_before_crash: int,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="2" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secrets = backend.snapshot()
    real_dispose = atomic_files.TrackedExistingFile.dispose
    disposed = 0

    def dispose_then_crash(authority: atomic_files.TrackedExistingFile) -> None:
        nonlocal disposed
        real_dispose(authority)
        disposed += 1
        if disposed == disposal_before_crash:
            raise SystemExit("crash after disposal")

    monkeypatch.setattr(atomic_files.TrackedExistingFile, "dispose", dispose_then_crash)
    with pytest.raises(CertificateCleanupError):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )
    monkeypatch.undo()

    resumed = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "abort",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert resumed.disposition in {
        CertificateRecoveryDisposition.ABORTED,
        CertificateRecoveryDisposition.ABANDONED,
    }
    assert backend.snapshot() == before_secrets
    assert not list(paths.public_key_root.iterdir())


def test_legacy_recovery_resumes_mixed_canonical_and_quarantine_candidates(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="3" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_secrets = backend.snapshot()
    first = intent.public_files[0]
    first_path = _candidate_path(paths, first.key_id)
    authority = atomic_files.track_existing_regular_file(
        first_path,
        first_path.read_bytes(),
        quarantine_id=first.quarantine_id,
        allowed_root=paths.public_key_root,
    )
    authority.relocate()
    authority.release()

    result = recover_certificate_mutation(
        paths,
        _persisted(intent),
        "abort",
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert result.disposition is CertificateRecoveryDisposition.ABORTED
    assert backend.snapshot() == before_secrets
    assert not list(paths.public_key_root.iterdir())


@pytest.mark.parametrize(
    ("physical", "selector", "directive", "expected"),
    [
        ("none", "prior", "abort", CertificateRecoveryDisposition.ABANDONED),
        ("secret", "prior", "abort", CertificateRecoveryDisposition.ABORTED),
        ("public", "prior", "abort", CertificateRecoveryDisposition.ABORTED),
        ("complete", "prior", "abort", CertificateRecoveryDisposition.ABORTED),
        ("complete", "prior", "commit", CertificateRecoveryDisposition.COMMITTED),
        ("complete", "intended", "abort", CertificateRecoveryDisposition.COMMITTED),
        ("complete", "intended", "commit", CertificateRecoveryDisposition.COMMITTED),
    ],
)
def test_create_recovery_success_matrix(
    tmp_path: Path,
    physical: str,
    selector: str,
    directive: str,
    expected: CertificateRecoveryDisposition,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    if physical != "none":
        apply_certificate_mutation(paths, intent, secret_backend=backend)
        if physical == "secret":
            _candidate_path(paths, intent.active_key_id).unlink()
        elif physical == "public":
            backend.clear(*_secret_reference(intent.active_key_id))
    if selector == "intended":
        paths.active_key_id.write_bytes((intent.active_key_id + "\n").encode("ascii"))

    result = recover_certificate_mutation(
        paths,
        _persisted(intent),
        directive,
        secret_backend=MemorySecretBackend(backend.values),
    )

    assert result.disposition is expected


@pytest.mark.parametrize(
    ("physical", "selector", "directive"),
    [
        ("secret-mismatch", "prior", "abort"),
        ("public-mismatch", "prior", "abort"),
        ("secret", "prior", "commit"),
        ("public", "prior", "commit"),
        ("complete", "malformed", "abort"),
        ("complete", "third", "abort"),
        ("complete", "malformed", "commit"),
        ("complete", "third", "commit"),
    ],
)
def test_create_recovery_conflict_matrix_preserves_potentially_active_material(
    tmp_path: Path,
    physical: str,
    selector: str,
    directive: str,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    public_path = _candidate_path(paths, intent.active_key_id)
    if physical == "secret-mismatch":
        wrong = certificate_records.Ed25519PrivateKey.generate().private_bytes(
            encoding=certificate_records.serialization.Encoding.Raw,
            format=certificate_records.serialization.PrivateFormat.Raw,
            encryption_algorithm=certificate_records.serialization.NoEncryption(),
        )
        backend.values[_secret_reference(intent.active_key_id)] = (
            "base64:" + base64.b64encode(wrong).decode("ascii")
        )
    elif physical == "public-mismatch":
        public_path.write_bytes(b"third public state")
    elif physical == "secret":
        public_path.unlink()
    elif physical == "public":
        backend.clear(*_secret_reference(intent.active_key_id))
    if selector == "malformed":
        paths.active_key_id.write_bytes(b"malformed\n")
    elif selector == "third":
        paths.active_key_id.write_bytes(("sha256:" + "f" * 64 + "\n").encode("ascii"))
    before_files = _tree_bytes(tmp_path)
    before_secrets = backend.snapshot()

    with pytest.raises((CertificateProvisioningError, CertificateStateConflict)):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            directive,
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secrets


def test_recovery_requires_same_concrete_backend_identity_before_observation_or_mutation(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before_files = _tree_bytes(tmp_path)
    before_secrets = backend.snapshot()

    with pytest.raises(CertificateProvisioningError, match="backend identity changed"):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=RetainingClearBackend(backend.values),
        )

    assert _tree_bytes(tmp_path) == before_files
    assert backend.snapshot() == before_secrets


def test_legacy_abort_prevalidates_complete_candidate_set_before_any_quarantine(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, _active = _seed_legacy_with_history(tmp_path, backend, retained_keys=3)
    prepared = prepare_certificate_mutation(
        paths,
        transaction_id="7" * 32,
        secret_backend=backend,
    )
    assert prepared.intent is not None
    intent = prepared.intent
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    corrupt = _candidate_path(paths, intent.public_files[-1].key_id)
    corrupt.write_bytes(b"third public state")
    before = _tree_bytes(tmp_path)

    with pytest.raises(CertificateStateConflict):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert _tree_bytes(tmp_path) == before
    assert not list(paths.public_key_root.glob(".famulus-quarantine-*"))


def test_recovery_refuses_replaced_exact_file_identity_and_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    public_path = _candidate_path(paths, intent.active_key_id)
    expected = public_path.read_bytes()
    real_track = atomic_files.RetainedBoundedDirectoryInventory.track_existing_regular_file

    def replace_after_tracking(
        inventory: atomic_files.RetainedBoundedDirectoryInventory,
        canonical_name: str,
        expected_bytes: bytes,
        *,
        quarantine_id: str,
    ) -> atomic_files.TrackedExistingFile:
        tracked = real_track(
            inventory,
            canonical_name,
            expected_bytes,
            quarantine_id=quarantine_id,
        )
        public_path.unlink()
        public_path.write_bytes(expected)
        return tracked

    monkeypatch.setattr(
        atomic_files.RetainedBoundedDirectoryInventory,
        "track_existing_regular_file",
        replace_after_tracking,
    )

    with pytest.raises((AtomicWriteError, CertificateCleanupError)):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )

    assert public_path.read_bytes() == expected


def test_create_recovery_proves_secret_absence_after_clear(
    tmp_path: Path,
) -> None:
    shared: dict[tuple[str, str], str] = {}
    backend = RetainingClearBackend(shared)
    paths, intent = _prepare_create(tmp_path, backend)
    apply_certificate_mutation(paths, intent, secret_backend=backend)
    before = _tree_bytes(tmp_path)

    with pytest.raises(CertificateCleanupError):
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=RetainingClearBackend(shared),
        )

    assert _tree_bytes(tmp_path) == before
    assert shared


def test_public_results_and_errors_never_expose_private_material(
    tmp_path: Path,
) -> None:
    backend = MemorySecretBackend()
    paths, intent = _prepare_create(tmp_path, backend)
    staged = apply_certificate_mutation(paths, intent, secret_backend=backend)
    private_canary = next(iter(backend.values.values()))
    encoded_forms = {
        private_canary,
        base64.b64encode(private_canary.encode("utf-8")).decode("ascii"),
        private_canary.encode("utf-8").hex(),
    }

    public_text = repr((intent, staged, intent.to_dict()))
    assert all(canary not in public_text for canary in encoded_forms)
    _candidate_path(paths, intent.active_key_id).write_bytes(b"conflict")
    with pytest.raises(CertificateStateConflict) as captured:
        recover_certificate_mutation(
            paths,
            _persisted(intent),
            "abort",
            secret_backend=MemorySecretBackend(backend.values),
        )
    assert all(canary not in str(captured.value) for canary in encoded_forms)


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

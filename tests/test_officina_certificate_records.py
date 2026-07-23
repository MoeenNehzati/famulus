from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import officina.common.certificate_records as certificate_records
import officina.common.atomic_files as atomic_files
from officina.common.certificate_records import (
    CertificateLogError,
    canonical_certificate_envelope_bytes,
    canonical_certificate_payload_bytes,
    certificate_entry_hash,
    certificate_public_key_root,
    load_active_certificate_key_id,
    load_certificate_public_key,
    load_certificate_signing_key,
    load_or_create_certificate_signing_key,
    parse_certificate_log,
    provision_certificate_signing_material,
    rotate_certificate_signing_key,
    sign_certificate_payload,
    verify_certificate_envelope,
)


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


def test_certificate_signing_material_provisioning_is_idempotent_and_verified(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    certifier_root = skills_root / "skill-certifier"
    certifier_root.mkdir(parents=True)
    if os.name == "posix":
        skills_root.chmod(0o755)
        certifier_root.chmod(0o755)
    backend = MemorySecretBackend()

    first = provision_certificate_signing_material(
        tmp_path,
        secret_backend=backend,
    )
    second = provision_certificate_signing_material(
        tmp_path,
        secret_backend=backend,
    )

    assert second.key_id == first.key_id
    public_key_root = certificate_public_key_root(tmp_path)
    assert load_active_certificate_key_id(public_key_root) == first.key_id
    assert load_certificate_signing_key(
        public_key_root,
        secret_backend=backend,
    ).key_id == first.key_id
    if os.name == "posix":
        assert public_key_root.stat().st_mode & 0o077 == 0
        assert stat.S_IMODE(skills_root.stat().st_mode) == 0o755
        assert stat.S_IMODE(certifier_root.stat().st_mode) == 0o755


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


def test_certificate_key_lifecycle_propagates_explicit_non_atomic_fallback(
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
    assert replace_flags == [True]
    assert read_flags and all(read_flags)


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

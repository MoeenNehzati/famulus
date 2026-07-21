from __future__ import annotations

import os
from pathlib import Path

import pytest

import officina.common.audit_records as audit_records
import officina.common.atomic_files as atomic_files
from officina.common.atomic_files import AtomicWriteError
from officina.common.audit_records import (
    CertificateLogError,
    attach_record_authentication,
    attach_record_hash,
    attach_record_digest,
    canonical_certificate_envelope_bytes,
    canonical_certificate_payload_bytes,
    canonical_health_record_bytes,
    certificate_entry_hash,
    compute_record_digest,
    load_active_certificate_key_id,
    load_certificate_public_key,
    load_certificate_signing_key,
    load_hmac_key,
    load_or_create_certificate_signing_key,
    load_or_create_hmac_key,
    parse_certificate_log,
    record_authentication_matches,
    record_digest_matches,
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


def test_record_digest_ignores_existing_digest_field() -> None:
    record = {"skill": "demo-skill", "checks": {"semantic": {"passed": True}}}
    signed = attach_record_digest(record)

    assert signed["record_digest"] == compute_record_digest(signed)
    assert record_digest_matches(signed)


def test_record_digest_changes_when_record_content_changes() -> None:
    signed = attach_record_digest({"skill": "demo-skill", "checks": {"semantic": {"passed": True}}})

    signed["checks"]["semantic"]["passed"] = False

    assert not record_digest_matches(signed)


def test_health_record_canonicalization_is_order_independent() -> None:
    left = {"subject": {"version": 1, "id": "demo-skill"}, "checks": []}
    right = {"checks": [], "subject": {"id": "demo-skill", "version": 1}}

    assert canonical_health_record_bytes(left) == canonical_health_record_bytes(right)


def test_health_record_canonicalization_rejects_floats() -> None:
    with pytest.raises(TypeError, match="floating-point"):
        canonical_health_record_bytes({"coverage": 0.5})


def test_health_record_canonicalization_rejects_float_nested_in_tuple() -> None:
    with pytest.raises(TypeError, match="floating-point"):
        canonical_health_record_bytes({"coverage": ({"ratio": (1, 0.5)},)})


def test_manual_edit_with_recomputed_record_hash_still_fails_mac() -> None:
    key = bytes(range(32))
    authenticated = attach_record_authentication(
        {"subject": {"id": "demo-skill"}, "checks": [{"id": "schema", "passed": True}]},
        key,
    )
    tampered = {
        **authenticated,
        "checks": [{"id": "schema", "passed": False}],
    }
    tampered = attach_record_hash(tampered)

    assert not record_authentication_matches(tampered, key)


def test_authentication_rejects_wrong_key() -> None:
    authenticated = attach_record_authentication({"subject": {"id": "demo-skill"}}, b"a" * 32)

    assert record_authentication_matches(authenticated, b"a" * 32)
    assert not record_authentication_matches(authenticated, b"b" * 32)


def test_record_hash_authenticates_source_commit_and_input_paths() -> None:
    payload = {
        "subject": {"id": "demo-skill"},
        "hashes": {"certified_health_hash": "sha256:" + "1" * 64},
        "source": {
            "vcs": "git",
            "commit": "a" * 40,
            "input_paths": ["skills/demo-skill/blueprint.yaml"],
        },
    }
    first = attach_record_authentication(payload, b"a" * 32)
    second = attach_record_authentication(
        {
            **payload,
            "source": {**payload["source"], "commit": "b" * 40},
        },
        b"a" * 32,
    )
    third = attach_record_authentication(
        {
            **payload,
            "source": {
                **payload["source"],
                "input_paths": [
                    "skills/demo-skill/blueprint.yaml",
                    "skills/demo-skill/SKILL.md",
                ],
            },
        },
        b"a" * 32,
    )

    assert len({first["record_hash"], second["record_hash"], third["record_hash"]}) == 3


def test_hmac_key_is_created_once_with_private_posix_mode(tmp_path) -> None:
    path = tmp_path / ".health-authentication-key"

    first = load_or_create_hmac_key(path, allowed_root=tmp_path)
    second = load_or_create_hmac_key(path, allowed_root=tmp_path)

    assert len(first) == 32
    assert second == first
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_existing_hmac_key_must_be_exactly_32_bytes(tmp_path) -> None:
    path = tmp_path / ".health-authentication-key"
    path.write_bytes(b"short")

    with pytest.raises(ValueError, match="exactly 32 bytes"):
        load_or_create_hmac_key(path, allowed_root=tmp_path)


def test_interrupted_hmac_key_creation_leaves_no_short_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".health-authentication-key"

    def interrupt(*args: object, **kwargs: object) -> bool:
        raise OSError("injected interruption")

    monkeypatch.setattr(audit_records, "atomic_create_bytes", interrupt)

    with pytest.raises(OSError, match="injected interruption"):
        load_or_create_hmac_key(path, allowed_root=tmp_path)

    assert not path.exists()


def test_hmac_key_creation_loads_concurrent_winner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".health-authentication-key"
    winner = b"w" * 32

    def lose_race(
        destination, data: bytes, *, allowed_root, mode: int
    ) -> bool:
        assert destination == path
        assert len(data) == 32
        assert allowed_root == tmp_path
        assert mode == 0o600
        path.write_bytes(winner)
        return False

    monkeypatch.setattr(audit_records, "atomic_create_bytes", lose_race)

    assert load_or_create_hmac_key(path, allowed_root=tmp_path) == winner


def test_hmac_key_creation_rejects_malformed_concurrent_winner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".health-authentication-key"

    def lose_race(
        destination, data: bytes, *, allowed_root, mode: int
    ) -> bool:
        assert destination == path
        assert len(data) == 32
        assert allowed_root == tmp_path
        assert mode == 0o600
        path.write_bytes(b"short")
        return False

    monkeypatch.setattr(audit_records, "atomic_create_bytes", lose_race)

    with pytest.raises(ValueError, match="exactly 32 bytes"):
        load_or_create_hmac_key(path, allowed_root=tmp_path)


def test_read_only_hmac_key_loader_never_creates_missing_key(tmp_path) -> None:
    path = tmp_path / ".health-authentication-key"

    with pytest.raises(FileNotFoundError):
        load_hmac_key(path, allowed_root=tmp_path)

    assert not path.exists()


def test_read_only_hmac_key_loader_validates_size(tmp_path) -> None:
    path = tmp_path / ".health-authentication-key"
    path.write_bytes(b"k" * 32)

    assert load_hmac_key(path, allowed_root=tmp_path) == b"k" * 32

    path.write_bytes(b"short")
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        load_hmac_key(path, allowed_root=tmp_path)


def test_existing_hmac_key_rejects_final_symlink(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-key"
    outside.write_bytes(b"x" * 32)
    path = tmp_path / ".health-authentication-key"
    path.symlink_to(outside)

    with pytest.raises(AtomicWriteError, match="symbolic link"):
        load_hmac_key(path, allowed_root=tmp_path)


def test_existing_hmac_key_read_is_stable_across_final_replacement(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".health-authentication-key"
    original = b"a" * 32
    path.write_bytes(original)
    displaced = tmp_path / "displaced-key"
    real_open = atomic_files._secure_open

    def replace_after_open(target, flags: int, mode: int = 0o777, *, dir_fd=None) -> int:
        descriptor = real_open(target, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and target == path.name:
            path.rename(displaced)
            path.write_bytes(b"b" * 32)
        return descriptor

    monkeypatch.setattr(atomic_files, "_secure_open", replace_after_open)

    assert load_hmac_key(path, allowed_root=tmp_path) == original
    assert path.read_bytes() == b"b" * 32


def test_existing_hmac_key_read_is_stable_across_parent_replacement(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_root = tmp_path / "allowed"
    parent = allowed_root / "keys"
    parent.mkdir(parents=True)
    path = parent / ".health-authentication-key"
    original = b"a" * 32
    path.write_bytes(original)
    displaced = allowed_root / "displaced-keys"
    real_open = atomic_files._secure_open

    def replace_after_open(target, flags: int, mode: int = 0o777, *, dir_fd=None) -> int:
        descriptor = real_open(target, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and target == parent.name:
            parent.rename(displaced)
            parent.mkdir()
            (parent / path.name).write_bytes(b"b" * 32)
        return descriptor

    monkeypatch.setattr(atomic_files, "_secure_open", replace_after_open)

    assert load_hmac_key(path, allowed_root=allowed_root) == original
    assert path.read_bytes() == b"b" * 32


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
    real_create = audit_records.atomic_create_bytes
    real_replace = audit_records.atomic_replace_bytes
    real_read = audit_records.read_regular_file_bytes

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

    monkeypatch.setattr(audit_records, "atomic_create_bytes", capture_create)
    monkeypatch.setattr(audit_records, "atomic_replace_bytes", capture_replace)
    monkeypatch.setattr(audit_records, "read_regular_file_bytes", capture_read)

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

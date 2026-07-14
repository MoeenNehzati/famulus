from __future__ import annotations

import os

import pytest

import officina.common.audit_records as audit_records
import officina.common.atomic_files as atomic_files
from officina.common.atomic_files import AtomicWriteError
from officina.common.audit_records import (
    attach_record_authentication,
    attach_record_hash,
    attach_record_digest,
    canonical_health_record_bytes,
    compute_record_digest,
    load_hmac_key,
    load_or_create_hmac_key,
    record_authentication_matches,
    record_digest_matches,
)


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

"""Helpers for local skill audit records."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Any

from .atomic_files import atomic_create_bytes, read_regular_file_bytes


RECORD_DIGEST_FIELD = "record_digest"
RECORD_HASH_FIELD = "record_hash"
AUTHENTICATION_FIELD = "authentication"
HEALTH_MAC_DOMAIN = b"famulus-health-record-v1\0"
HMAC_KEY_BYTES = 32


def canonical_record_bytes(record: dict[str, Any]) -> bytes:
    """Return stable JSON bytes for record integrity checks."""

    unsigned = {key: value for key, value in record.items() if key != RECORD_DIGEST_FIELD}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return canonical.encode("utf-8")


def compute_record_digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_record_bytes(record)).hexdigest()


def attach_record_digest(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result[RECORD_DIGEST_FIELD] = compute_record_digest(result)
    return result


def record_digest_matches(record: dict[str, Any]) -> bool:
    digest = record.get(RECORD_DIGEST_FIELD)
    return isinstance(digest, str) and digest == compute_record_digest(record)


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise TypeError(f"{path}: floating-point values are not allowed in health records")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: health record object keys must be strings")
            _reject_floats(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_floats(child, f"{path}[{index}]")


def canonical_health_record_bytes(record: dict[str, Any]) -> bytes:
    """Return canonical authenticated payload bytes for a health record."""

    payload = {
        key: value
        for key, value in record.items()
        if key not in {RECORD_HASH_FIELD, AUTHENTICATION_FIELD}
    }
    _reject_floats(payload)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return canonical.encode("utf-8")


def compute_record_hash(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_health_record_bytes(record)).hexdigest()


def attach_record_hash(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result[RECORD_HASH_FIELD] = compute_record_hash(result)
    return result


def _key_id(key: bytes) -> str:
    return "sha256:" + hashlib.sha256(key).hexdigest()[:16]


def _mac_bytes(record_hash: str, key: bytes) -> bytes:
    prefix, separator, hexadecimal = record_hash.partition(":")
    if prefix != "sha256" or not separator or len(hexadecimal) != 64:
        raise ValueError("record_hash must be a sha256 hash")
    try:
        hash_bytes = bytes.fromhex(hexadecimal)
    except ValueError as exc:
        raise ValueError("record_hash must be a sha256 hash") from exc
    return hmac.digest(key, HEALTH_MAC_DOMAIN + hash_bytes, "sha256")


def attach_record_authentication(record: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Attach a canonical record hash and HMAC-SHA-256 authentication envelope."""

    if len(key) != HMAC_KEY_BYTES:
        raise ValueError("HMAC key must be exactly 32 bytes")
    result = attach_record_hash(record)
    mac = base64.b64encode(_mac_bytes(result[RECORD_HASH_FIELD], key)).decode("ascii")
    result[AUTHENTICATION_FIELD] = {
        "scheme": "hmac-sha256",
        "key_id": _key_id(key),
        "mac": "base64:" + mac,
    }
    return result


def record_authentication_matches(record: dict[str, Any], key: bytes) -> bool:
    """Return whether both the canonical hash and HMAC envelope verify."""

    if len(key) != HMAC_KEY_BYTES:
        return False
    record_hash = record.get(RECORD_HASH_FIELD)
    authentication = record.get(AUTHENTICATION_FIELD)
    if not isinstance(record_hash, str) or not isinstance(authentication, dict):
        return False
    if record_hash != compute_record_hash(record):
        return False
    if (
        authentication.get("scheme") != "hmac-sha256"
        or authentication.get("key_id") != _key_id(key)
    ):
        return False
    encoded_mac = authentication.get("mac")
    if not isinstance(encoded_mac, str) or not encoded_mac.startswith("base64:"):
        return False
    try:
        actual = base64.b64decode(encoded_mac.removeprefix("base64:"), validate=True)
        expected = _mac_bytes(record_hash, key)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def load_or_create_hmac_key(path: Path, *, allowed_root: Path) -> bytes:
    """Load a 32-byte local key, creating it exclusively with mode 0600."""

    path = Path(path)
    try:
        return load_hmac_key(path, allowed_root=allowed_root)
    except FileNotFoundError:
        candidate = secrets.token_bytes(HMAC_KEY_BYTES)
        atomic_create_bytes(path, candidate, allowed_root=allowed_root, mode=0o600)
        return load_hmac_key(path, allowed_root=allowed_root)


def load_hmac_key(path: Path, *, allowed_root: Path) -> bytes:
    """Load an existing HMAC key without creating or modifying it."""

    path = Path(path)
    return _validate_hmac_key(
        path,
        read_regular_file_bytes(path, allowed_root=allowed_root),
    )


def _validate_hmac_key(path: Path, key: bytes) -> bytes:
    if len(key) != HMAC_KEY_BYTES:
        raise ValueError(f"{path}: HMAC key must be exactly 32 bytes")
    return key

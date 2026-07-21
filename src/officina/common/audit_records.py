"""Helpers for local skill audit records."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import secret_store
from .atomic_files import (
    AtomicWriteError,
    atomic_create_bytes,
    atomic_replace_bytes,
    read_regular_file_bytes,
)


RECORD_DIGEST_FIELD = "record_digest"
RECORD_HASH_FIELD = "record_hash"
AUTHENTICATION_FIELD = "authentication"
HEALTH_MAC_DOMAIN = b"famulus-health-record-v1\0"
HMAC_KEY_BYTES = 32
CERTIFIER_SECRET_NAMESPACE = "skill-certifier"
CERTIFICATE_SIGNATURE_SCHEME = "ed25519"
ACTIVE_KEY_ID_NAME = "active-key-id"
ED25519_PRIVATE_KEY_BYTES = 32


class CertificateLogError(ValueError):
    """Raised when a signed certificate log is malformed or suspect."""


@dataclass(frozen=True)
class CertificateSigningKey:
    """Active certifier key material loaded through the shared secret store."""

    key_id: str
    signer: Ed25519PrivateKey


def _validate_certificate_json(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise TypeError(f"{path}: floating-point values are not allowed in certificates")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_certificate_json(child, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: certificate object keys must be strings")
            _validate_certificate_json(child, f"{path}.{key}")
        return
    raise TypeError(f"{path}: unsupported certificate JSON value {type(value).__name__}")


def _canonical_certificate_bytes(value: Mapping[str, Any]) -> bytes:
    _validate_certificate_json(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_certificate_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the canonical bytes covered by an Ed25519 certificate signature."""

    if not isinstance(payload, Mapping):
        raise TypeError("certificate payload must be a mapping")
    return _canonical_certificate_bytes(payload)


def canonical_certificate_envelope_bytes(envelope: Mapping[str, Any]) -> bytes:
    """Return canonical bytes for one complete certificate log entry."""

    if not isinstance(envelope, Mapping):
        raise TypeError("certificate envelope must be a mapping")
    return _canonical_certificate_bytes(envelope)


def certificate_entry_hash(envelope: Mapping[str, Any]) -> str:
    """Hash one complete canonical envelope, including its signature."""

    return "sha256:" + hashlib.sha256(
        canonical_certificate_envelope_bytes(envelope)
    ).hexdigest()


def _raw_public_key(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def certificate_key_id(public_key: Ed25519PublicKey) -> str:
    """Return the full certificate key ID over raw Ed25519 public bytes."""

    return "sha256:" + hashlib.sha256(_raw_public_key(public_key)).hexdigest()


def _key_id_hex(key_id: str) -> str:
    prefix, separator, hexadecimal = key_id.partition(":")
    if (
        prefix != "sha256"
        or not separator
        or len(hexadecimal) != 64
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise ValueError("certificate key_id must be sha256:<64 lowercase hex>")
    return hexadecimal


def _public_key_path(public_key_root: Path, key_id: str) -> Path:
    return Path(public_key_root) / f"{_key_id_hex(key_id)}.pub"


def _private_key_secret_name(key_id: str) -> str:
    _key_id_hex(key_id)
    return f"ed25519-private-key:{key_id}"


def load_active_certificate_key_id(
    public_key_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> str:
    """Read the complete active key ID from the public verification root."""

    root = Path(public_key_root)
    raw = read_regular_file_bytes(
        root / ACTIVE_KEY_ID_NAME,
        allowed_root=root,
        allow_non_atomic=allow_non_atomic,
    )
    try:
        key_id = raw.decode("ascii").removesuffix("\n")
    except UnicodeDecodeError as exc:
        raise ValueError("active-key-id must contain ASCII") from exc
    if raw != (key_id + "\n").encode("ascii"):
        raise ValueError("active-key-id must contain one key ID followed by newline")
    _key_id_hex(key_id)
    return key_id


def load_certificate_public_key(
    public_key_root: Path,
    key_id: str,
    *,
    allow_non_atomic: bool = False,
) -> Ed25519PublicKey:
    """Load and validate one retained public key without private-key access."""

    root = Path(public_key_root)
    pem = read_regular_file_bytes(
        _public_key_path(root, key_id),
        allowed_root=root,
        allow_non_atomic=allow_non_atomic,
    )
    try:
        public_key = serialization.load_pem_public_key(pem)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key_id}: invalid public key PEM") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError(f"{key_id}: public key must be Ed25519")
    if certificate_key_id(public_key) != key_id:
        raise ValueError(f"{key_id}: public key bytes do not match key ID")
    return public_key


def _decode_private_key(secret: str, key_id: str) -> Ed25519PrivateKey:
    if not isinstance(secret, str) or not secret.startswith("base64:"):
        raise ValueError(f"{key_id}: stored private key must be base64 encoded")
    try:
        raw = base64.b64decode(secret.removeprefix("base64:"), validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key_id}: stored private key must be valid base64") from exc
    if len(raw) != ED25519_PRIVATE_KEY_BYTES:
        raise ValueError(
            f"{key_id}: Ed25519 private key must be exactly {ED25519_PRIVATE_KEY_BYTES} bytes"
        )
    private_key = Ed25519PrivateKey.from_private_bytes(raw)
    if certificate_key_id(private_key.public_key()) != key_id:
        raise ValueError(f"{key_id}: private key does not match active key ID")
    return private_key


def load_certificate_signing_key(
    public_key_root: Path,
    *,
    secret_backend: secret_store.SecretBackend | None = None,
    allow_non_atomic: bool = False,
) -> CertificateSigningKey:
    """Load the active private key through ``secret_store`` and verify its public pair."""

    key_id = load_active_certificate_key_id(
        public_key_root,
        allow_non_atomic=allow_non_atomic,
    )
    load_certificate_public_key(
        public_key_root,
        key_id,
        allow_non_atomic=allow_non_atomic,
    )
    secret = secret_store.require(
        CERTIFIER_SECRET_NAMESPACE,
        _private_key_secret_name(key_id),
        backend=secret_backend,
    )
    private_key = _decode_private_key(secret, key_id)
    return CertificateSigningKey(key_id=key_id, signer=private_key)


def _store_generated_certificate_key(
    public_key_root: Path,
    *,
    secret_backend: secret_store.SecretBackend | None,
    allow_non_atomic: bool = False,
) -> CertificateSigningKey:
    root = Path(public_key_root)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    key_id = certificate_key_id(public_key)
    raw_private = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    secret_name = _private_key_secret_name(key_id)
    secret_store.store(
        CERTIFIER_SECRET_NAMESPACE,
        secret_name,
        "base64:" + base64.b64encode(raw_private).decode("ascii"),
        backend=secret_backend,
    )
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    try:
        created = atomic_create_bytes(
            _public_key_path(root, key_id),
            pem,
            allowed_root=root,
            mode=0o600,
            allow_non_atomic=allow_non_atomic,
        )
        if not created:
            existing = read_regular_file_bytes(
                _public_key_path(root, key_id),
                allowed_root=root,
                allow_non_atomic=allow_non_atomic,
            )
            if existing != pem:
                raise AtomicWriteError(f"{key_id}: public key collision")
    except BaseException:
        secret_store.clear(
            CERTIFIER_SECRET_NAMESPACE,
            secret_name,
            backend=secret_backend,
        )
        raise
    return CertificateSigningKey(key_id=key_id, signer=private_key)


def load_or_create_certificate_signing_key(
    public_key_root: Path,
    *,
    secret_backend: secret_store.SecretBackend | None = None,
    allow_non_atomic: bool = False,
) -> CertificateSigningKey:
    """Load the active key, or create the initial private/public pair once."""

    root = Path(public_key_root)
    try:
        return load_certificate_signing_key(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
    except FileNotFoundError:
        candidate = _store_generated_certificate_key(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
        created = atomic_create_bytes(
            root / ACTIVE_KEY_ID_NAME,
            (candidate.key_id + "\n").encode("ascii"),
            allowed_root=root,
            mode=0o600,
            allow_non_atomic=allow_non_atomic,
        )
        if created:
            return candidate
        return load_certificate_signing_key(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )


def rotate_certificate_signing_key(
    public_key_root: Path,
    *,
    secret_backend: secret_store.SecretBackend | None = None,
    allow_non_atomic: bool = False,
) -> CertificateSigningKey:
    """Create a new key pair, retain old public keys, and switch the selector."""

    root = Path(public_key_root)
    candidate = _store_generated_certificate_key(
        root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    atomic_replace_bytes(
        root / ACTIVE_KEY_ID_NAME,
        (candidate.key_id + "\n").encode("ascii"),
        allowed_root=root,
        mode=0o600,
        allow_non_atomic=allow_non_atomic,
    )
    loaded = load_certificate_signing_key(
        root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    if loaded.key_id != candidate.key_id:
        raise ValueError("active certificate key changed during rotation")
    return loaded


def sign_certificate_payload(
    payload: Mapping[str, Any],
    signing_key: CertificateSigningKey,
) -> dict[str, Any]:
    """Sign canonical payload bytes and return the closed certificate envelope."""

    if payload.get("key_id") != signing_key.key_id:
        raise ValueError("certificate payload key_id does not match signing key")
    payload_copy = dict(payload)
    signature = signing_key.signer.sign(
        canonical_certificate_payload_bytes(payload_copy)
    )
    return {
        "payload": payload_copy,
        "signature": {
            "scheme": CERTIFICATE_SIGNATURE_SCHEME,
            "value": "base64:" + base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_certificate_envelope(
    envelope: Mapping[str, Any],
    public_key_root: Path,
    *,
    allow_non_atomic: bool = False,
) -> bool:
    """Verify one envelope through retained public material only."""

    payload = envelope.get("payload") if isinstance(envelope, Mapping) else None
    signature = envelope.get("signature") if isinstance(envelope, Mapping) else None
    if not isinstance(payload, Mapping) or not isinstance(signature, Mapping):
        return False
    if set(envelope) != {"payload", "signature"}:
        return False
    if set(signature) != {"scheme", "value"}:
        return False
    if signature.get("scheme") != CERTIFICATE_SIGNATURE_SCHEME:
        return False
    key_id = payload.get("key_id")
    encoded = signature.get("value")
    if not isinstance(key_id, str) or not isinstance(encoded, str) or not encoded.startswith("base64:"):
        return False
    try:
        signature_bytes = base64.b64decode(
            encoded.removeprefix("base64:"), validate=True
        )
        public_key = load_certificate_public_key(
            public_key_root,
            key_id,
            allow_non_atomic=allow_non_atomic,
        )
        public_key.verify(
            signature_bytes,
            canonical_certificate_payload_bytes(payload),
        )
    except (FileNotFoundError, InvalidSignature, OSError, TypeError, ValueError):
        return False
    return True


def parse_certificate_log(
    data: bytes,
    public_key_root: Path,
    *,
    require_active_final: bool = True,
    allow_non_atomic: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Parse one canonical signed chain, optionally enforcing current final key."""

    if not isinstance(data, bytes):
        raise TypeError("certificate log data must be bytes")
    if not data.endswith(b"\n"):
        raise CertificateLogError("certificate log is missing its final newline")
    entries: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for line_number, line in enumerate(data.split(b"\n")[:-1], start=1):
        if not line:
            continue
        try:
            parsed = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CertificateLogError(
                f"certificate log line {line_number} is malformed"
            ) from exc
        if not isinstance(parsed, dict):
            raise CertificateLogError(
                f"certificate log line {line_number} must be an envelope"
            )
        try:
            canonical = canonical_certificate_envelope_bytes(parsed)
        except (TypeError, ValueError) as exc:
            raise CertificateLogError(
                f"certificate log line {line_number} is not canonical"
            ) from exc
        if line != canonical:
            raise CertificateLogError(
                f"certificate log line {line_number} is not canonical"
            )
        if not verify_certificate_envelope(
            parsed,
            public_key_root,
            allow_non_atomic=allow_non_atomic,
        ):
            raise CertificateLogError(
                f"certificate log line {line_number} has an invalid signature"
            )
        payload = parsed.get("payload")
        if not isinstance(payload, Mapping) or payload.get("previous_entry_hash") != previous_hash:
            raise CertificateLogError(
                f"certificate log line {line_number} has a broken history chain"
            )
        entries.append(parsed)
        previous_hash = certificate_entry_hash(parsed)
    if not entries:
        raise CertificateLogError("certificate log has no entries")
    if require_active_final:
        active_key_id = load_active_certificate_key_id(
            public_key_root,
            allow_non_atomic=allow_non_atomic,
        )
        final_payload = entries[-1]["payload"]
        if final_payload.get("key_id") != active_key_id:
            raise CertificateLogError(
                "certificate log final entry is not signed by the active key"
            )
    return tuple(entries)


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

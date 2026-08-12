"""Canonical append-only Ed25519 certificate records."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
import stat
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
    ensure_secure_directory,
    read_regular_file_bytes,
)
from .famulus_paths import resolve_famulus_paths


CERTIFIER_SECRET_NAMESPACE = "skill-certifier"
CERTIFICATE_SIGNATURE_SCHEME = "ed25519"
ACTIVE_KEY_ID_NAME = "active-key-id"
ED25519_PRIVATE_KEY_BYTES = 32


class CertificateLogError(ValueError):
    """Raised when a signed certificate log is malformed or suspect."""


class CertificateStateConflict(ValueError):
    """Raised when legacy and durable certificate identities cannot be reconciled."""


@dataclass(frozen=True)
class CertificateStatePaths:
    """Durable certificate paths plus the optional one-time legacy source."""

    public_key_root: Path
    active_key_id: Path
    legacy_public_key_root: Path | None = None

    @property
    def root(self) -> Path:
        """Return the stable certificate lifecycle root."""

        return self.public_key_root.parent


def legacy_certificate_public_key_root(repo_root: Path) -> Path:
    """Return the retired repository-local key root for one-time migration."""

    return (
        Path(repo_root).absolute()
        / "skills"
        / "skill-certifier"
        / ".certificates"
        / "public-keys"
    )


def certificate_public_key_root(repo_root: Path) -> Path:
    """Compatibility alias for legacy repair and migration callers."""

    return legacy_certificate_public_key_root(repo_root)


def certificate_state_paths(
    *,
    platform: str,
    home: Path,
    repo_root: Path | None = None,
) -> CertificateStatePaths:
    """Resolve certificate identity below durable per-home Famulus data."""

    famulus = resolve_famulus_paths(platform=platform, home=Path(home))
    return CertificateStatePaths(
        public_key_root=famulus.certificate_public_key_root,
        active_key_id=famulus.certificate_public_key_root / ACTIVE_KEY_ID_NAME,
        legacy_public_key_root=(
            legacy_certificate_public_key_root(repo_root)
            if repo_root is not None
            else None
        ),
    )


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


def provision_certificate_signing_material(
    paths: CertificateStatePaths,
    *,
    secret_backend: secret_store.SecretBackend | None = None,
    allow_non_atomic: bool = False,
) -> CertificateSigningKey:
    """Provision and verify the durable cooperative certifier key pair."""

    if not isinstance(paths, CertificateStatePaths):
        raise TypeError("paths must be CertificateStatePaths")
    public_key_root = Path(paths.public_key_root).absolute()
    if Path(paths.active_key_id).absolute() != public_key_root / ACTIVE_KEY_ID_NAME:
        raise ValueError("active_key_id must belong to public_key_root")
    ensure_secure_directory(public_key_root)
    metadata = public_key_root.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"unsafe certificate key directory: {public_key_root}")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        public_key_root.chmod(0o700)
        metadata = public_key_root.lstat()
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError(
                f"certificate key directory is not user-private: {public_key_root}"
            )
    created = load_or_create_certificate_signing_key(
        public_key_root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    verified = load_certificate_signing_key(
        public_key_root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    if verified.key_id != created.key_id:
        raise ValueError("certificate signing material failed post-provision verification")
    return verified


def _validated_public_state(
    public_key_root: Path,
    *,
    secret_backend: secret_store.SecretBackend | None,
    allow_non_atomic: bool,
) -> tuple[str, dict[str, bytes]] | None:
    """Read one complete public state without following directory or file links."""

    root = Path(public_key_root).absolute()
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CertificateStateConflict(f"unsafe certificate state root: {root}")
    try:
        entries = tuple(root.iterdir())
        if not entries:
            return None
        key_id = load_active_certificate_key_id(
            root,
            allow_non_atomic=allow_non_atomic,
        )
        load_certificate_signing_key(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
        retained: dict[str, bytes] = {}
        for candidate in entries:
            candidate_metadata = candidate.lstat()
            if candidate.name == ACTIVE_KEY_ID_NAME:
                if not stat.S_ISREG(candidate_metadata.st_mode):
                    raise CertificateStateConflict(
                        f"unsafe certificate selector: {candidate}"
                    )
                continue
            if not candidate.name.endswith(".pub"):
                raise CertificateStateConflict(
                    f"unexpected certificate state entry: {candidate.name}"
                )
            if stat.S_ISLNK(candidate_metadata.st_mode) or not stat.S_ISREG(
                candidate_metadata.st_mode
            ):
                raise CertificateStateConflict(
                    f"unsafe certificate public key: {candidate}"
                )
            retained[candidate.name] = read_regular_file_bytes(
                candidate,
                allowed_root=root,
                allow_non_atomic=allow_non_atomic,
            )
            load_certificate_public_key(
                root,
                "sha256:" + candidate.stem,
                allow_non_atomic=allow_non_atomic,
            )
        if f"{_key_id_hex(key_id)}.pub" not in retained:
            raise CertificateStateConflict("active certificate public key is missing")
        return key_id, retained
    except CertificateStateConflict:
        raise
    except (AtomicWriteError, OSError, TypeError, ValueError) as exc:
        raise CertificateStateConflict(
            f"invalid certificate state at {root}: {exc}"
        ) from exc


def migrate_legacy_certificate_state(
    repo_root: Path,
    paths: CertificateStatePaths,
    *,
    secret_backend: secret_store.SecretBackend | None,
    allow_non_atomic: bool = False,
) -> None:
    """Move one matching legacy identity into durable state without rotation.

    The installer caller owns the per-home ``InstallLock``.  This function
    validates both sides before writing, publishes retained public keys first,
    and commits the durable identity by creating ``active-key-id`` last.
    """

    expected_legacy = legacy_certificate_public_key_root(repo_root)
    if (
        paths.legacy_public_key_root is not None
        and Path(paths.legacy_public_key_root).absolute() != expected_legacy
    ):
        raise CertificateStateConflict(
            "legacy certificate root does not match repository root"
        )
    stable_root = Path(paths.public_key_root).absolute()
    if Path(paths.active_key_id).absolute() != stable_root / ACTIVE_KEY_ID_NAME:
        raise CertificateStateConflict("active_key_id must belong to public_key_root")
    legacy_state = _validated_public_state(
        expected_legacy,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    stable_state = _validated_public_state(
        stable_root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    if legacy_state is None:
        return
    if stable_state is not None:
        if stable_state != legacy_state:
            raise CertificateStateConflict(
                "legacy and durable certificate identities conflict"
            )
        return

    key_id, retained = legacy_state
    ensure_secure_directory(stable_root)
    created: list[Path] = []
    try:
        for name, payload in sorted(retained.items()):
            destination = stable_root / name
            if atomic_create_bytes(
                destination,
                payload,
                allowed_root=stable_root,
                mode=0o600,
                allow_non_atomic=allow_non_atomic,
            ):
                created.append(destination)
            else:
                existing = read_regular_file_bytes(
                    destination,
                    allowed_root=stable_root,
                    allow_non_atomic=allow_non_atomic,
                )
                if existing != payload:
                    raise CertificateStateConflict(
                        f"durable public key conflicts during migration: {name}"
                    )
        if not atomic_create_bytes(
            paths.active_key_id,
            (key_id + "\n").encode("ascii"),
            allowed_root=stable_root,
            mode=0o600,
            allow_non_atomic=allow_non_atomic,
        ):
            raise CertificateStateConflict(
                "durable certificate selector appeared during migration"
            )
    except BaseException:
        for candidate in reversed(created):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
        raise
    migrated = _validated_public_state(
        stable_root,
        secret_backend=secret_backend,
        allow_non_atomic=allow_non_atomic,
    )
    if migrated != legacy_state:
        raise CertificateStateConflict(
            "durable certificate state failed post-migration verification"
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

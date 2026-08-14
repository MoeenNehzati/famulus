"""Canonical append-only Ed25519 certificate records."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
import secrets
import stat
import weakref
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
    RetainedBoundedDirectoryInventory,
    TrackedExistingFile,
    TrackedFileCreation,
    TrackedFileLocation,
    atomic_create_bytes,
    atomic_create_bytes_tracked,
    atomic_replace_bytes,
    build_file_name,
    ensure_secure_directory,
    read_regular_directory_entries,
    read_regular_file_bytes,
    retain_bounded_directory_inventory,
    staged_file_name,
    track_existing_regular_file,
)
from .certificate_intents import (
    CERTIFICATE_INTENT_SCHEMA_VERSION,
    CertificateMutationIntent,
    CertificatePublicFileIntent,
)
from .famulus_paths import resolve_famulus_paths


CERTIFIER_SECRET_NAMESPACE = "skill-certifier"
CERTIFICATE_SIGNATURE_SCHEME = "ed25519"
ACTIVE_KEY_ID_NAME = "active-key-id"
ED25519_PRIVATE_KEY_BYTES = 32
_ACTIVE_SELECTOR_READ_LIMIT = len("sha256:") + 64 + len("\n") + 1


class CertificateLogError(ValueError):
    """Raised when a signed certificate log is malformed or suspect."""


class CertificateStateConflict(ValueError):
    """Raised when legacy and durable certificate identities cannot be reconciled."""


class CertificateProvisioningError(RuntimeError):
    """Raised with static text when transactional key provisioning cannot finish."""


class CertificateCleanupError(CertificateProvisioningError):
    """Raised with static text when staged secret or public cleanup is unproven."""


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


@dataclass(frozen=True)
class StagedCertificateKey:
    """One validated active identity or one exact inactive candidate pair.

    All fields are safe to journal.  Process-local bindings retain
    the exact backend object and filesystem cleanup authority so commit and
    abort cannot silently reselect a backend or unlink another file identity.
    """

    key_id: str
    public_key_path: Path
    secret_target: str
    created: bool


@dataclass(frozen=True)
class CertificatePreparationResult:
    """Return a verified reusable identity or one secret-free mutation intent."""

    key_id: str
    intent: CertificateMutationIntent | None


class CertificateRecoveryDisposition(str, Enum):
    """Closed restart disposition for one certificate mutation intent."""

    ABANDONED = "abandoned"
    ABORTED = "aborted"
    STAGED = "staged"
    COMMITTED = "committed"


@dataclass(frozen=True)
class CertificateMutationResult:
    """Return the intended key and its closed physical-state disposition."""

    key_id: str
    disposition: CertificateRecoveryDisposition


@dataclass
class _StagedCertificateBinding:
    staged_ref: weakref.ReferenceType[StagedCertificateKey]
    secret_backend: secret_store.SecretBackend
    public_creation: TrackedFileCreation | None
    prior_key_id: str | None
    cleanup_complete: bool = False


_STAGED_CERTIFICATE_BINDINGS: dict[int, _StagedCertificateBinding] = {}


@dataclass
class _CertificateMutationBinding:
    """Retain live-only secret bytes and exact mutation authority after prepare."""

    intent_ref: weakref.ReferenceType[CertificateMutationIntent]
    secret_backend: secret_store.SecretBackend
    public_payloads: dict[str, bytes]
    encoded_private: str | None
    public_creations: dict[str, TrackedFileCreation]


_CERTIFICATE_MUTATION_BINDINGS: dict[int, _CertificateMutationBinding] = {}


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
    return _active_key_id_from_bytes(raw)


def _active_key_id_from_bytes(raw: bytes) -> str:
    """Validate and decode one complete active-key selector."""

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
    return _public_key_from_bytes(pem, key_id)


def _public_key_from_bytes(
    pem: bytes,
    key_id: str,
) -> Ed25519PublicKey:
    """Validate one retained PEM against its filename-derived key ID."""

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


def _validated_transaction_paths(paths: CertificateStatePaths) -> Path:
    if not isinstance(paths, CertificateStatePaths):
        raise TypeError("paths must be CertificateStatePaths")
    public_key_root = Path(paths.public_key_root).absolute()
    if Path(paths.active_key_id).absolute() != public_key_root / ACTIVE_KEY_ID_NAME:
        raise ValueError("active_key_id must belong to public_key_root")
    return public_key_root


def _resolve_secret_backend(
    backend: secret_store.SecretBackend | None,
) -> secret_store.SecretBackend:
    """Select a backend once; later transaction steps reuse this exact object."""

    if backend is not None:
        return backend
    return secret_store.KeyringSecretBackend()


def _bind_staged_key(
    staged: StagedCertificateKey,
    *,
    secret_backend: secret_store.SecretBackend,
    public_creation: TrackedFileCreation | None,
    prior_key_id: str | None,
) -> StagedCertificateKey:
    staged_id = id(staged)

    def release_binding(
        _reference: weakref.ReferenceType[StagedCertificateKey],
    ) -> None:
        abandoned = _STAGED_CERTIFICATE_BINDINGS.get(staged_id)
        if abandoned is None or abandoned.staged_ref is not _reference:
            return
        _STAGED_CERTIFICATE_BINDINGS.pop(staged_id, None)
        if abandoned.public_creation is not None:
            abandoned.public_creation.release()

    _STAGED_CERTIFICATE_BINDINGS[staged_id] = _StagedCertificateBinding(
        staged_ref=weakref.ref(staged, release_binding),
        secret_backend=secret_backend,
        public_creation=public_creation,
        prior_key_id=prior_key_id,
    )
    return staged


def _staged_binding(staged: StagedCertificateKey) -> _StagedCertificateBinding:
    binding = _STAGED_CERTIFICATE_BINDINGS.get(id(staged))
    if binding is None or binding.staged_ref() is not staged:
        raise CertificateProvisioningError(
            "staged certificate transaction binding is unavailable"
        )
    return binding


def _staged_secret_target(key_id: str) -> str:
    return secret_store.target_name(
        CERTIFIER_SECRET_NAMESPACE,
        _private_key_secret_name(key_id),
    )


def _require_bound_backend(
    staged: StagedCertificateKey,
    supplied: secret_store.SecretBackend,
) -> secret_store.SecretBackend:
    if not isinstance(staged, StagedCertificateKey):
        raise TypeError("staged must be StagedCertificateKey")
    if supplied is None:
        raise TypeError("secret_backend must be an explicit backend object")
    bound = _staged_binding(staged).secret_backend
    if supplied is not bound:
        raise CertificateProvisioningError(
            "certificate transaction backend identity changed"
        )
    return bound


def _validate_staged_identity(
    public_key_root: Path,
    staged: StagedCertificateKey,
) -> None:
    expected_public = _public_key_path(public_key_root, staged.key_id).absolute()
    if Path(staged.public_key_path).absolute() != expected_public:
        raise CertificateProvisioningError("staged certificate identity is invalid")
    if staged.secret_target != _staged_secret_target(staged.key_id):
        raise CertificateProvisioningError("staged certificate identity is invalid")


def _clear_staged_secret(
    key_id: str,
    *,
    secret_backend: secret_store.SecretBackend,
) -> None:
    secret_name = _private_key_secret_name(key_id)
    try:
        secret_store.clear(
            CERTIFIER_SECRET_NAMESPACE,
            secret_name,
            backend=secret_backend,
        )
        remaining = secret_store.lookup(
            CERTIFIER_SECRET_NAMESPACE,
            secret_name,
            backend=secret_backend,
        )
    except BaseException:
        raise CertificateCleanupError("certificate key cleanup failed") from None
    if remaining is not None:
        raise CertificateCleanupError("certificate key cleanup failed")


def _selector_key_id(
    public_key_root: Path,
    *,
    allow_non_atomic: bool,
) -> str | None:
    try:
        return load_active_certificate_key_id(
            public_key_root,
            allow_non_atomic=allow_non_atomic,
        )
    except FileNotFoundError:
        return None
    except (AtomicWriteError, OSError, TypeError, ValueError):
        raise CertificateStateConflict("certificate selector is malformed") from None


def _selector_state(
    public_key_root: Path,
    staged: StagedCertificateKey,
    *,
    allow_non_atomic: bool,
) -> str:
    selected = _selector_key_id(
        public_key_root,
        allow_non_atomic=allow_non_atomic,
    )
    if selected == staged.key_id:
        return "staged"
    if selected == _staged_binding(staged).prior_key_id:
        return "prior"
    return "third"


def _load_staged_pair(
    public_key_root: Path,
    staged: StagedCertificateKey,
    *,
    secret_backend: secret_store.SecretBackend,
    allow_non_atomic: bool,
) -> CertificateSigningKey:
    try:
        load_certificate_public_key(
            public_key_root,
            staged.key_id,
            allow_non_atomic=allow_non_atomic,
        )
        encoded = secret_store.require(
            CERTIFIER_SECRET_NAMESPACE,
            _private_key_secret_name(staged.key_id),
            backend=secret_backend,
        )
        signer = _decode_private_key(encoded, staged.key_id)
    except BaseException:
        raise CertificateProvisioningError(
            "staged certificate pair verification failed"
        ) from None
    return CertificateSigningKey(key_id=staged.key_id, signer=signer)


def _load_committed_staged_pair(
    public_key_root: Path,
    staged: StagedCertificateKey,
    *,
    secret_backend: secret_store.SecretBackend,
    allow_non_atomic: bool,
) -> CertificateSigningKey:
    try:
        loaded = load_certificate_signing_key(
            public_key_root,
            secret_backend=secret_backend,
            allow_non_atomic=allow_non_atomic,
        )
    except BaseException:
        raise CertificateProvisioningError(
            "certificate post-commit verification failed"
        ) from None
    if loaded.key_id != staged.key_id:
        raise CertificateProvisioningError(
            "certificate post-commit verification failed"
        )
    return loaded


def _release_staged_public(staged: StagedCertificateKey) -> None:
    tracked = _staged_binding(staged).public_creation
    if tracked is not None:
        tracked.release()


def _stage_generated_certificate_key(
    public_key_root: Path,
    *,
    prior_key_id: str | None,
    secret_backend: secret_store.SecretBackend,
) -> StagedCertificateKey:
    for _attempt in range(16):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        key_id = certificate_key_id(public_key)
        secret_name = _private_key_secret_name(key_id)
        try:
            if secret_store.lookup(
                CERTIFIER_SECRET_NAMESPACE,
                secret_name,
                backend=secret_backend,
            ) is not None:
                continue
        except BaseException:
            raise CertificateProvisioningError(
                "certificate secret target validation failed"
            ) from None

        raw_private = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        encoded_private = "base64:" + base64.b64encode(raw_private).decode("ascii")
        try:
            secret_store.store(
                CERTIFIER_SECRET_NAMESPACE,
                secret_name,
                encoded_private,
                backend=secret_backend,
            )
        except BaseException:
            _clear_staged_secret(key_id, secret_backend=secret_backend)
            raise CertificateProvisioningError(
                "certificate secret storage failed"
            ) from None

        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        try:
            tracked = atomic_create_bytes_tracked(
                _public_key_path(public_key_root, key_id),
                pem,
                allowed_root=public_key_root,
                mode=0o600,
            )
        except BaseException:
            _clear_staged_secret(key_id, secret_backend=secret_backend)
            raise CertificateProvisioningError(
                "certificate public-key staging failed"
            ) from None
        if tracked is None:
            _clear_staged_secret(key_id, secret_backend=secret_backend)
            continue

        try:
            staged = StagedCertificateKey(
                key_id=key_id,
                public_key_path=_public_key_path(public_key_root, key_id),
                secret_target=_staged_secret_target(key_id),
                created=True,
            )
            return _bind_staged_key(
                staged,
                secret_backend=secret_backend,
                public_creation=tracked,
                prior_key_id=prior_key_id,
            )
        except BaseException:
            _clear_staged_secret(key_id, secret_backend=secret_backend)
            try:
                tracked.remove()
            except BaseException:
                raise CertificateCleanupError(
                    "certificate public-key cleanup failed"
                ) from None
            raise CertificateProvisioningError(
                "certificate public-key staging failed"
            ) from None
    raise CertificateProvisioningError(
        "could not allocate unique certificate signing material"
    )


def stage_certificate_signing_material(
    paths: CertificateStatePaths,
    *,
    secret_backend: secret_store.SecretBackend,
) -> StagedCertificateKey:
    """Validate the active identity or create one inactive exact key pair.

    The caller owns the per-home ``InstallLock`` for the complete transaction.
    This operation never creates or replaces the active selector.
    """

    if secret_backend is None:
        raise TypeError("secret_backend must be an explicit backend object")
    public_key_root = _validated_transaction_paths(paths)
    ensure_secure_directory(public_key_root)
    backend = secret_backend
    try:
        active = load_certificate_signing_key(
            public_key_root,
            secret_backend=backend,
            allow_non_atomic=False,
        )
    except FileNotFoundError:
        if _selector_key_id(
            public_key_root,
            allow_non_atomic=False,
        ) is not None:
            raise CertificateStateConflict(
                "active certificate state is incomplete"
            ) from None
    else:
        staged = StagedCertificateKey(
            key_id=active.key_id,
            public_key_path=_public_key_path(public_key_root, active.key_id),
            secret_target=_staged_secret_target(active.key_id),
            created=False,
        )
        return _bind_staged_key(
            staged,
            secret_backend=backend,
            public_creation=None,
            prior_key_id=active.key_id,
        )
    return _stage_generated_certificate_key(
        public_key_root,
        prior_key_id=None,
        secret_backend=backend,
    )


def abort_staged_certificate(
    paths: CertificateStatePaths,
    staged: StagedCertificateKey,
    *,
    secret_backend: secret_store.SecretBackend,
) -> None:
    """Remove only an inactive exact candidate; retain committed/ambiguous state."""

    public_key_root = _validated_transaction_paths(paths)
    backend = _require_bound_backend(staged, secret_backend)
    _validate_staged_identity(public_key_root, staged)
    state = _selector_state(
        public_key_root,
        staged,
        allow_non_atomic=False,
    )
    if state == "staged":
        _release_staged_public(staged)
        return
    if state != "prior":
        raise CertificateStateConflict(
            "certificate selector is neither prior nor staged"
        )
    binding = _staged_binding(staged)
    if not staged.created or binding.cleanup_complete:
        return

    _clear_staged_secret(staged.key_id, secret_backend=backend)
    tracked = binding.public_creation
    if tracked is None:
        raise CertificateCleanupError(
            "certificate public-key cleanup failed"
        )
    try:
        tracked.remove()
    except BaseException:
        raise CertificateCleanupError(
            "certificate public-key cleanup failed"
        ) from None
    binding.cleanup_complete = True


def commit_staged_certificate(
    paths: CertificateStatePaths,
    staged: StagedCertificateKey,
    *,
    secret_backend: secret_store.SecretBackend,
) -> CertificateSigningKey:
    """Commit or resume one staged identity without rolling back ambiguity."""

    public_key_root = _validated_transaction_paths(paths)
    backend = _require_bound_backend(staged, secret_backend)
    _validate_staged_identity(public_key_root, staged)
    state = _selector_state(
        public_key_root,
        staged,
        allow_non_atomic=False,
    )
    if state == "staged":
        loaded = _load_committed_staged_pair(
            public_key_root,
            staged,
            secret_backend=backend,
            allow_non_atomic=False,
        )
        _release_staged_public(staged)
        return loaded
    if state != "prior":
        raise CertificateStateConflict(
            "certificate selector is neither prior nor staged"
        )
    if not staged.created:
        raise CertificateStateConflict("active certificate selector changed")

    _load_staged_pair(
        public_key_root,
        staged,
        secret_backend=backend,
        allow_non_atomic=False,
    )
    try:
        atomic_replace_bytes(
            paths.active_key_id,
            (staged.key_id + "\n").encode("ascii"),
            allowed_root=public_key_root,
            mode=0o600,
        )
    except BaseException:
        state = _selector_state(
            public_key_root,
            staged,
            allow_non_atomic=False,
        )
        if state == "staged":
            loaded = _load_committed_staged_pair(
                public_key_root,
                staged,
                secret_backend=backend,
                allow_non_atomic=False,
            )
            _release_staged_public(staged)
            return loaded
        if state == "prior":
            abort_staged_certificate(
                paths,
                staged,
                secret_backend=backend,
            )
            raise CertificateProvisioningError(
                "certificate selector commit failed"
            ) from None
        raise CertificateStateConflict(
            "certificate selector is neither prior nor staged"
        ) from None

    loaded = _load_committed_staged_pair(
        public_key_root,
        staged,
        secret_backend=backend,
        allow_non_atomic=False,
    )
    _release_staged_public(staged)
    return loaded


def provision_certificate_signing_material(
    paths: CertificateStatePaths,
    *,
    secret_backend: secret_store.SecretBackend | None = None,
    allow_non_atomic: bool = False,
) -> CertificateSigningKey:
    """Provision and verify the durable cooperative certifier key pair."""

    public_key_root = _validated_transaction_paths(paths)
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
    backend = _resolve_secret_backend(secret_backend)
    staged = stage_certificate_signing_material(
        paths,
        secret_backend=backend,
    )
    try:
        return commit_staged_certificate(
            paths,
            staged,
            secret_backend=backend,
        )
    except BaseException:
        abort_staged_certificate(
            paths,
            staged,
            secret_backend=backend,
        )
        raise


def _validated_public_state(
    public_key_root: Path,
    *,
    secret_backend: secret_store.SecretBackend | None,
    allow_non_atomic: bool,
) -> tuple[str, dict[str, bytes]] | None:
    """Read one complete public state without following directory or file links."""

    root = Path(public_key_root).absolute()
    try:
        try:
            entries = read_regular_directory_entries(root)
        except FileNotFoundError:
            return None
        if not entries:
            return None
        by_name = {entry.name: entry.data for entry in entries}
        selector = by_name.pop(ACTIVE_KEY_ID_NAME, None)
        if selector is None:
            raise CertificateStateConflict("active certificate selector is missing")
        key_id = _active_key_id_from_bytes(selector)
        retained: dict[str, bytes] = {}
        for name, payload in by_name.items():
            if not name.endswith(".pub"):
                raise CertificateStateConflict(
                    f"unexpected certificate state entry: {name}"
                )
            retained[name] = payload
            _public_key_from_bytes(payload, "sha256:" + Path(name).stem)
        if f"{_key_id_hex(key_id)}.pub" not in retained:
            raise CertificateStateConflict("active certificate public key is missing")
        secret = secret_store.require(
            CERTIFIER_SECRET_NAMESPACE,
            _private_key_secret_name(key_id),
            backend=secret_backend,
        )
        _decode_private_key(secret, key_id)
        return key_id, retained
    except CertificateStateConflict:
        raise
    except (AtomicWriteError, OSError, TypeError, ValueError) as exc:
        raise CertificateStateConflict(
            f"invalid certificate state at {root}: {exc}"
        ) from exc


def _certificate_backend_identity(
    backend: secret_store.SecretBackend,
) -> str:
    """Return one bounded concrete identity without trusting persisted text."""

    if backend is None:
        raise TypeError("secret_backend must be an explicit backend object")
    try:
        identity_method = getattr(backend, "backend_identity", None)
        if callable(identity_method):
            identity = identity_method()
        else:
            backend_type = type(backend)
            identity = f"{backend_type.__module__}.{backend_type.__name__}"
        if not isinstance(identity, str):
            raise TypeError
        identity.encode("ascii")
    except BaseException:
        raise CertificateProvisioningError(
            "certificate backend identity is unavailable"
        ) from None
    return identity


def _bind_certificate_intent(
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
    public_payloads: dict[str, bytes],
    encoded_private: str | None,
) -> CertificateMutationIntent:
    intent_id = id(intent)

    def release_binding(
        reference: weakref.ReferenceType[CertificateMutationIntent],
    ) -> None:
        abandoned = _CERTIFICATE_MUTATION_BINDINGS.get(intent_id)
        if abandoned is None or abandoned.intent_ref is not reference:
            return
        _CERTIFICATE_MUTATION_BINDINGS.pop(intent_id, None)
        for tracked in abandoned.public_creations.values():
            tracked.release()

    _CERTIFICATE_MUTATION_BINDINGS[intent_id] = _CertificateMutationBinding(
        intent_ref=weakref.ref(intent, release_binding),
        secret_backend=secret_backend,
        public_payloads=dict(public_payloads),
        encoded_private=encoded_private,
        public_creations={},
    )
    return intent


def _live_certificate_binding(
    intent: CertificateMutationIntent,
    supplied_backend: secret_store.SecretBackend,
) -> _CertificateMutationBinding:
    if not isinstance(intent, CertificateMutationIntent):
        raise TypeError("intent must be a CertificateMutationIntent")
    binding = _CERTIFICATE_MUTATION_BINDINGS.get(id(intent))
    if binding is None or binding.intent_ref() is not intent:
        raise CertificateProvisioningError(
            "certificate mutation intent binding is unavailable"
        )
    if supplied_backend is not binding.secret_backend:
        raise CertificateProvisioningError(
            "certificate transaction backend identity changed"
        )
    return binding


def _fresh_intent_identifier(excluded: set[str]) -> str:
    for _attempt in range(16):
        candidate = secrets.token_hex(16)
        if candidate not in excluded:
            excluded.add(candidate)
            return candidate
    raise CertificateProvisioningError(
        "certificate intent identifier allocation failed"
    )


def _public_file_intent(
    key_id: str,
    payload: bytes,
    *,
    quarantine_id: str,
) -> CertificatePublicFileIntent:
    return CertificatePublicFileIntent(
        key_id=key_id,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        quarantine_id=quarantine_id,
    )


def _new_certificate_intent(
    *,
    transaction_id: str,
    action: str,
    backend_identity: str,
    active_key_id: str,
    prior_key_id: str | None,
    public_payloads: dict[str, bytes],
) -> CertificateMutationIntent:
    identifiers = {transaction_id}
    intent_id = _fresh_intent_identifier(identifiers)
    public_files = tuple(
        _public_file_intent(
            key_id,
            public_payloads[key_id],
            quarantine_id=_fresh_intent_identifier(identifiers),
        )
        for key_id in sorted(public_payloads)
    )
    return CertificateMutationIntent(
        schema_version=CERTIFICATE_INTENT_SCHEMA_VERSION,
        transaction_id=transaction_id,
        intent_id=intent_id,
        action=action,  # type: ignore[arg-type]
        backend_identity=backend_identity,
        active_key_id=active_key_id,
        prior_key_id=prior_key_id,
        public_files=public_files,
        secret_target=_staged_secret_target(active_key_id),
    )


def _public_payloads_from_retained(retained: dict[str, bytes]) -> dict[str, bytes]:
    return {
        "sha256:" + Path(name).stem: payload
        for name, payload in retained.items()
    }


def prepare_certificate_mutation(
    paths: CertificateStatePaths,
    *,
    transaction_id: str,
    secret_backend: secret_store.SecretBackend,
) -> CertificatePreparationResult:
    """Prepare one secret-free intent without mutating any owned sink.

    The caller must hold the per-home ``InstallLock`` from this read through the
    eventual apply, commit, abort, or recovery decision.  That serialization
    precondition excludes every cooperative selector writer; this API cannot
    conditionally mutate a separate secret store and filesystem, and does not
    protect against an uncooperative concurrent selector writer outside the
    held lock.  Paths are supplied by the capability-derived
    ``certificate_state_paths`` result, never by an intent or journal.
    """

    root = _validated_transaction_paths(paths)
    backend_identity = _certificate_backend_identity(secret_backend)
    try:
        stable_state = _validated_public_state(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=False,
        )
        legacy_state = None
        if paths.legacy_public_key_root is not None:
            legacy_state = _validated_public_state(
                paths.legacy_public_key_root,
                secret_backend=secret_backend,
                allow_non_atomic=False,
            )
    except CertificateStateConflict:
        raise CertificateStateConflict(
            "certificate mutation preparation failed"
        ) from None
    except Exception:
        raise CertificateProvisioningError(
            "certificate mutation preparation failed"
        ) from None
    if stable_state is not None:
        if legacy_state is not None and legacy_state != stable_state:
            raise CertificateStateConflict(
                "legacy and durable certificate identities conflict"
            )
        return CertificatePreparationResult(key_id=stable_state[0], intent=None)
    if legacy_state is not None:
        key_id, retained = legacy_state
        payloads = _public_payloads_from_retained(retained)
        intent = _new_certificate_intent(
            transaction_id=transaction_id,
            action="copy_legacy",
            backend_identity=backend_identity,
            active_key_id=key_id,
            prior_key_id=None,
            public_payloads=payloads,
        )
        return CertificatePreparationResult(
            key_id=key_id,
            intent=_bind_certificate_intent(
                intent,
                secret_backend=secret_backend,
                public_payloads=payloads,
                encoded_private=None,
            ),
        )

    for _attempt in range(16):
        private_key = Ed25519PrivateKey.generate()
        key_id = certificate_key_id(private_key.public_key())
        secret_name = _private_key_secret_name(key_id)
        try:
            if secret_store.lookup(
                CERTIFIER_SECRET_NAMESPACE,
                secret_name,
                backend=secret_backend,
            ) is not None:
                continue
            if root.exists():
                try:
                    read_regular_file_bytes(
                        _public_key_path(root, key_id),
                        allowed_root=root,
                        allow_non_atomic=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    continue
        except Exception:
            raise CertificateProvisioningError(
                "certificate mutation preparation failed"
            ) from None
        raw_private = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        encoded_private = "base64:" + base64.b64encode(raw_private).decode("ascii")
        pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        payloads = {key_id: pem}
        intent = _new_certificate_intent(
            transaction_id=transaction_id,
            action="create",
            backend_identity=backend_identity,
            active_key_id=key_id,
            prior_key_id=None,
            public_payloads=payloads,
        )
        return CertificatePreparationResult(
            key_id=key_id,
            intent=_bind_certificate_intent(
                intent,
                secret_backend=secret_backend,
                public_payloads=payloads,
                encoded_private=encoded_private,
            ),
        )
    raise CertificateProvisioningError(
        "could not allocate unique certificate signing material"
    )


def _intent_selector_state(
    root: Path,
    intent: CertificateMutationIntent,
) -> str:
    selected = (
        _selector_key_id(root, allow_non_atomic=False)
        if root.exists()
        else None
    )
    if selected == intent.active_key_id:
        return "intended"
    if selected == intent.prior_key_id:
        return "prior"
    return "third"


def _require_intent_selector_state(
    root: Path,
    intent: CertificateMutationIntent,
    expected: str,
) -> None:
    """Revalidate the exact selector state at one mutation boundary."""

    if _intent_selector_state(root, intent) != expected:
        raise CertificateStateConflict("certificate selector conflicts with intent")


def _validate_intent_public_payload(
    record: CertificatePublicFileIntent,
    payload: bytes,
) -> None:
    if len(payload) != record.size or hashlib.sha256(payload).hexdigest() != record.sha256:
        raise CertificateStateConflict("certificate public state conflicts with intent")
    try:
        _public_key_from_bytes(payload, record.key_id)
    except (TypeError, ValueError):
        raise CertificateStateConflict(
            "certificate public state conflicts with intent"
        ) from None


def _verify_intent_secret(
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> str | None:
    try:
        encoded = secret_store.lookup(
            CERTIFIER_SECRET_NAMESPACE,
            _private_key_secret_name(intent.active_key_id),
            backend=secret_backend,
        )
    except BaseException:
        raise CertificateProvisioningError(
            "certificate secret observation failed"
        ) from None
    if encoded is None:
        return None
    try:
        _decode_private_key(encoded, intent.active_key_id)
    except (TypeError, ValueError):
        raise CertificateStateConflict(
            "certificate secret conflicts with intent"
        ) from None
    return encoded


def _verify_applied_create_secret(
    root: Path,
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> None:
    """Verify store readback or remove the unacknowledgeable exact target."""

    try:
        encoded = secret_store.lookup(
            CERTIFIER_SECRET_NAMESPACE,
            _private_key_secret_name(intent.active_key_id),
            backend=secret_backend,
        )
        if encoded is None:
            raise ValueError
        _decode_private_key(encoded, intent.active_key_id)
    except Exception:
        _require_intent_selector_state(root, intent, "prior")
        _clear_staged_secret(
            intent.active_key_id,
            secret_backend=secret_backend,
        )
        raise CertificateProvisioningError(
            "certificate secret staging verification failed"
        ) from None


def apply_certificate_mutation(
    paths: CertificateStatePaths,
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> CertificateMutationResult:
    """Apply one exact live intent after its caller has durably ACKed it.

    The caller must retain the per-home ``InstallLock`` so no cooperative
    selector writer can enter between each immediate selector revalidation and
    its secret-store or public-file mutation.  Cross-store conditional mutation
    is unavailable, so an uncooperative writer outside that lock is excluded.
    """

    root = _validated_transaction_paths(paths)
    binding = _live_certificate_binding(intent, secret_backend)
    _require_intent_selector_state(root, intent, "prior")
    ensure_secure_directory(root)
    if intent.action == "create":
        if binding.encoded_private is None:
            raise CertificateProvisioningError(
                "certificate mutation intent binding is unavailable"
            )
        try:
            existing = secret_store.lookup(
                CERTIFIER_SECRET_NAMESPACE,
                _private_key_secret_name(intent.active_key_id),
                backend=secret_backend,
            )
            if existing is None:
                _require_intent_selector_state(root, intent, "prior")
                secret_store.store(
                    CERTIFIER_SECRET_NAMESPACE,
                    _private_key_secret_name(intent.active_key_id),
                    binding.encoded_private,
                    backend=secret_backend,
                )
            else:
                _decode_private_key(existing, intent.active_key_id)
        except CertificateStateConflict:
            raise
        except (TypeError, ValueError):
            raise CertificateStateConflict(
                "certificate secret conflicts with intent"
            ) from None
        except Exception:
            raise CertificateProvisioningError(
                "certificate secret staging failed"
            ) from None
        _verify_applied_create_secret(
            root,
            intent,
            secret_backend=secret_backend,
        )
    else:
        if _verify_intent_secret(intent, secret_backend=secret_backend) is None:
            raise CertificateStateConflict("legacy certificate secret is missing")

    for record in intent.public_files:
        payload = binding.public_payloads.get(record.key_id)
        if payload is None:
            raise CertificateProvisioningError(
                "certificate mutation intent binding is unavailable"
            )
        _validate_intent_public_payload(record, payload)
        _require_intent_selector_state(root, intent, "prior")
        try:
            tracked = atomic_create_bytes_tracked(
                _public_key_path(root, record.key_id),
                payload,
                allowed_root=root,
                mode=0o600,
            )
        except Exception:
            raise CertificateProvisioningError(
                "certificate public staging failed"
            ) from None
        if tracked is None:
            raise CertificateStateConflict(
                "certificate public state conflicts with intent"
            )
        binding.public_creations[record.key_id] = tracked
    return CertificateMutationResult(
        key_id=intent.active_key_id,
        disposition=CertificateRecoveryDisposition.STAGED,
    )


def _release_live_public(binding: _CertificateMutationBinding) -> None:
    for tracked in binding.public_creations.values():
        tracked.release()


def _live_public_authorities(
    root: Path,
    intent: CertificateMutationIntent,
    binding: _CertificateMutationBinding,
    *,
    allow_absent: bool = False,
) -> list[TrackedExistingFile]:
    authorities: list[TrackedExistingFile] = []
    try:
        for record in intent.public_files:
            if allow_absent and not root.exists():
                continue
            try:
                payload = read_regular_file_bytes(
                    _public_key_path(root, record.key_id),
                    allowed_root=root,
                    allow_non_atomic=False,
                )
            except FileNotFoundError:
                if allow_absent:
                    continue
                raise
            _validate_intent_public_payload(record, payload)
            authority = track_existing_regular_file(
                _public_key_path(root, record.key_id),
                payload,
                quarantine_id=record.quarantine_id,
                allowed_root=root,
            )
            tracked = binding.public_creations.get(record.key_id)
            if tracked is not None and authority.identity != tracked.identity:
                raise CertificateStateConflict(
                    "certificate public identity conflicts with live mutation"
                )
            authorities.append(authority)
    except BaseException:
        for authority in authorities:
            authority.release()
        raise
    return authorities


def _verify_committed_intent(
    root: Path,
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> None:
    try:
        active = load_certificate_signing_key(
            root,
            secret_backend=secret_backend,
            allow_non_atomic=False,
        )
    except Exception:
        raise CertificateProvisioningError(
            "certificate post-commit verification failed"
        ) from None
    if active.key_id != intent.active_key_id:
        raise CertificateProvisioningError(
            "certificate post-commit verification failed"
        )


def commit_certificate_mutation(
    paths: CertificateStatePaths,
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> CertificateMutationResult:
    """Commit or resume one exact live intent through the active selector.

    The caller-held per-home ``InstallLock`` excludes cooperative selector
    writers.  The selector is revalidated immediately before replacement, but
    no protection is claimed against an uncooperative writer outside that lock.
    """

    root = _validated_transaction_paths(paths)
    binding = _live_certificate_binding(intent, secret_backend)
    authorities = _live_public_authorities(root, intent, binding)
    inventory: RetainedBoundedDirectoryInventory | None = None
    try:
        if _verify_intent_secret(intent, secret_backend=secret_backend) is None:
            raise CertificateStateConflict("certificate secret is missing")
        inventory = _retain_recovery_inventory(root, intent)
        if inventory is None:
            raise CertificateStateConflict("certificate public state conflicts with intent")
        names = set(inventory.names)
        _require_allowed_recovery_names(names, root, intent)
        selector = _selector_from_inventory(inventory, names, intent)
        transaction = _selector_transaction_from_inventory(inventory, names, intent)
        if selector in {"malformed", "third"} or transaction in {
            "ambiguous",
            "invalid",
            "mismatched",
        }:
            raise CertificateStateConflict("certificate selector conflicts with intent")
        if selector == "prior" or transaction != "absent":
            _require_inventory_selector_state(inventory, intent, selector)
            inventory.replace_regular_file(
                ACTIVE_KEY_ID_NAME,
                (intent.active_key_id + "\n").encode("ascii"),
                mode=0o600,
                staging_capability=intent.intent_id,
            )
        _verify_inventory_selector_committed(inventory, intent)
        _verify_committed_intent(
            root,
            intent,
            secret_backend=secret_backend,
        )
    finally:
        if inventory is not None:
            inventory.release()
        for authority in authorities:
            authority.release()
    _release_live_public(binding)
    return CertificateMutationResult(
        key_id=intent.active_key_id,
        disposition=CertificateRecoveryDisposition.COMMITTED,
    )


def abort_certificate_mutation(
    paths: CertificateStatePaths,
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> CertificateMutationResult:
    """Abort one exact live pre-selector intent through retained authority.

    The caller-held per-home ``InstallLock`` excludes cooperative selector
    writers across the secret and public cleanup sequence.  Every mutation has
    an immediate selector revalidation; cross-store conditional cleanup cannot
    exclude an uncooperative writer outside the held lock.
    """

    root = _validated_transaction_paths(paths)
    binding = _live_certificate_binding(intent, secret_backend)
    state = _intent_selector_state(root, intent)
    if state == "intended":
        _verify_committed_intent(root, intent, secret_backend=secret_backend)
        _release_live_public(binding)
        return CertificateMutationResult(
            key_id=intent.active_key_id,
            disposition=CertificateRecoveryDisposition.COMMITTED,
        )
    if state != "prior":
        raise CertificateStateConflict("certificate selector conflicts with intent")
    secret = _verify_intent_secret(intent, secret_backend=secret_backend)
    authorities = _live_public_authorities(
        root,
        intent,
        binding,
        allow_absent=True,
    )
    for authority in authorities:
        authority.release()
    had_effect = bool(authorities) or (intent.action == "create" and secret is not None)
    if intent.action == "create" and secret is not None:
        _require_intent_selector_state(root, intent, "prior")
        _clear_staged_secret(intent.active_key_id, secret_backend=secret_backend)
    try:
        for tracked in binding.public_creations.values():
            _require_intent_selector_state(root, intent, "prior")
            tracked.remove()
    except BaseException:
        raise CertificateCleanupError("certificate public-key cleanup failed") from None
    return CertificateMutationResult(
        key_id=intent.active_key_id,
        disposition=(
            CertificateRecoveryDisposition.ABORTED
            if had_effect
            else CertificateRecoveryDisposition.ABANDONED
        ),
    )


@dataclass
class _RecoveryObservation:
    selector: str
    selector_transaction: str
    secret: str | None
    public: list[TrackedExistingFile | None]
    inventory: RetainedBoundedDirectoryInventory | None


def _retain_recovery_inventory(
    root: Path,
    intent: CertificateMutationIntent,
) -> RetainedBoundedDirectoryInventory | None:
    try:
        return retain_bounded_directory_inventory(
            root,
            max_entries=2 * len(intent.public_files) + 3,
            max_name_bytes=128,
        )
    except FileNotFoundError:
        return None
    except (AtomicWriteError, OSError, TypeError, ValueError):
        raise CertificateStateConflict(
            "certificate public state conflicts with intent"
        ) from None


def _selector_from_inventory(
    inventory: RetainedBoundedDirectoryInventory | None,
    names: set[str],
    intent: CertificateMutationIntent,
) -> str:
    if ACTIVE_KEY_ID_NAME not in names:
        selected = None
    else:
        try:
            if inventory is None:
                return "malformed"
            raw = inventory.read_regular_file(
                ACTIVE_KEY_ID_NAME,
                maximum_bytes=_ACTIVE_SELECTOR_READ_LIMIT,
            )
            selected = _active_key_id_from_bytes(raw.data)
        except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
            return "malformed"
    if selected == intent.active_key_id:
        return "intended"
    if selected == intent.prior_key_id:
        return "prior"
    return "third"


def _selector_transaction_from_inventory(
    inventory: RetainedBoundedDirectoryInventory | None,
    names: set[str],
    intent: CertificateMutationIntent,
) -> str:
    build_name = build_file_name(intent.intent_id)
    staging_name = staged_file_name(intent.intent_id)
    build_present = build_name in names
    stage_present = staging_name in names
    if build_present and stage_present:
        return "ambiguous"
    if not build_present and not stage_present:
        return "absent"
    if inventory is None:
        return "invalid"
    if build_present:
        try:
            inventory.read_regular_file(
                build_name,
                maximum_bytes=_ACTIVE_SELECTOR_READ_LIMIT,
            )
        except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
            return "invalid"
        return "build"
    try:
        raw = inventory.read_regular_file(
            staging_name,
            maximum_bytes=_ACTIVE_SELECTOR_READ_LIMIT,
        ).data
        if _active_key_id_from_bytes(raw) != intent.active_key_id:
            return "mismatched"
    except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
        return "invalid"
    return "stage"


def _allowed_recovery_names(root: Path, intent: CertificateMutationIntent) -> set[str]:
    allowed_names = {
        ACTIVE_KEY_ID_NAME,
        build_file_name(intent.intent_id),
        staged_file_name(intent.intent_id),
    }
    for record in intent.public_files:
        allowed_names.add(_public_key_path(root, record.key_id).name)
        allowed_names.add(f".famulus-quarantine-{record.quarantine_id}")
    return allowed_names


def _require_allowed_recovery_names(
    names: set[str],
    root: Path,
    intent: CertificateMutationIntent,
) -> None:
    if names - _allowed_recovery_names(root, intent):
        raise CertificateStateConflict("certificate public state conflicts with intent")


def _observe_recovery_state(
    root: Path,
    intent: CertificateMutationIntent,
    *,
    secret_backend: secret_store.SecretBackend,
) -> _RecoveryObservation:
    inventory = _retain_recovery_inventory(root, intent)
    authorities: list[TrackedExistingFile | None] = []
    try:
        names = set(() if inventory is None else inventory.names)
        _require_allowed_recovery_names(names, root, intent)
        selector = _selector_from_inventory(inventory, names, intent)
        if selector in {"malformed", "third"}:
            raise CertificateStateConflict("certificate selector conflicts with intent")
        selector_transaction = _selector_transaction_from_inventory(
            inventory, names, intent
        )
        if selector_transaction not in {"absent", "build", "stage"}:
            raise CertificateStateConflict("certificate selector conflicts with intent")
        secret = _verify_intent_secret(intent, secret_backend=secret_backend)
        if intent.action == "copy_legacy" and secret is None:
            raise CertificateStateConflict("legacy certificate secret is missing")
        for record in intent.public_files:
            canonical_name = _public_key_path(root, record.key_id).name
            quarantine_name = f".famulus-quarantine-{record.quarantine_id}"
            present_names = [
                name
                for name in (canonical_name, quarantine_name)
                if name in names
            ]
            if not present_names:
                authorities.append(None)
                continue
            if len(present_names) != 1:
                raise CertificateStateConflict(
                    "certificate public state conflicts with intent"
                )
            try:
                if inventory is None:
                    raise AtomicWriteError("retained directory inventory is missing")
                payload = inventory.read_regular_file(
                    present_names[0],
                    maximum_bytes=record.size + 1,
                ).data
            except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
                raise CertificateStateConflict(
                    "certificate public state conflicts with intent"
                ) from None
            _validate_intent_public_payload(record, payload)
            authorities.append(
                inventory.track_existing_regular_file(
                    canonical_name,
                    payload,
                    quarantine_id=record.quarantine_id,
                )
            )
    except BaseException:
        for authority in authorities:
            if authority is not None:
                authority.release()
        if inventory is not None:
            inventory.release()
        raise
    return _RecoveryObservation(
        selector=selector,
        selector_transaction=selector_transaction,
        secret=secret,
        public=authorities,
        inventory=inventory,
    )


def _release_recovery_observation(observation: _RecoveryObservation) -> None:
    for authority in observation.public:
        if authority is not None:
            authority.release()
    if observation.inventory is not None:
        observation.inventory.release()


def _require_recovery_selector_state(
    observation: _RecoveryObservation,
    intent: CertificateMutationIntent,
    expected: str,
) -> None:
    """Revalidate one complete retained recovery snapshot and selector."""

    inventory = observation.inventory
    if inventory is None:
        raise CertificateStateConflict("certificate public state conflicts with intent")
    try:
        inventory.revalidate()
        names = set(inventory.names)
        selector = _selector_from_inventory(inventory, names, intent)
        transaction = _selector_transaction_from_inventory(inventory, names, intent)
    except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
        raise CertificateStateConflict(
            "certificate public state conflicts with intent"
        ) from None
    if selector != expected:
        raise CertificateStateConflict("certificate selector conflicts with intent")
    if transaction not in {"absent", "build", "stage"}:
        raise CertificateStateConflict("certificate selector conflicts with intent")


def _require_inventory_selector_state(
    inventory: RetainedBoundedDirectoryInventory,
    intent: CertificateMutationIntent,
    expected: str,
) -> None:
    """Revalidate a retained live selector snapshot before mutation."""

    try:
        inventory.revalidate()
        names = set(inventory.names)
        selector = _selector_from_inventory(inventory, names, intent)
        transaction = _selector_transaction_from_inventory(inventory, names, intent)
    except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
        raise CertificateStateConflict("certificate selector conflicts with intent") from None
    if selector != expected or transaction not in {"absent", "build", "stage"}:
        raise CertificateStateConflict("certificate selector conflicts with intent")


def _verify_inventory_selector_committed(
    inventory: RetainedBoundedDirectoryInventory,
    intent: CertificateMutationIntent,
) -> None:
    inventory.revalidate()
    names = set(inventory.names)
    if _selector_from_inventory(inventory, names, intent) != "intended":
        raise CertificateStateConflict("certificate selector conflicts with intent")
    if _selector_transaction_from_inventory(inventory, names, intent) != "absent":
        raise CertificateStateConflict("certificate selector conflicts with intent")


def _verify_recovery_committed(
    observation: _RecoveryObservation,
    intent: CertificateMutationIntent,
) -> None:
    """Verify committed state only through the retained bounded inventory."""

    inventory = observation.inventory
    if inventory is None or observation.secret is None:
        raise CertificateStateConflict("committed certificate state is incomplete")
    try:
        inventory.revalidate()
        names = set(inventory.names)
        if _selector_from_inventory(inventory, names, intent) != "intended":
            raise CertificateStateConflict("certificate selector conflicts with intent")
        _verify_inventory_selector_committed(inventory, intent)
        for record in intent.public_files:
            name = _public_key_path(Path("."), record.key_id).name
            payload = inventory.read_regular_file(
                name,
                maximum_bytes=record.size + 1,
            ).data
            _validate_intent_public_payload(record, payload)
    except CertificateStateConflict:
        raise
    except (AtomicWriteError, FileNotFoundError, OSError, TypeError, ValueError):
        raise CertificateStateConflict("committed certificate state is incomplete") from None


def recover_certificate_mutation(
    paths: CertificateStatePaths,
    intent: CertificateMutationIntent,
    directive: str,
    *,
    secret_backend: secret_store.SecretBackend,
) -> CertificateMutationResult:
    """Recover one persisted intent using newly audited live authority.

    The caller must hold the per-home ``InstallLock`` across observation and
    every recovery mutation.  Immediate selector revalidation and that lock
    exclude cooperative selector writers; separate secret-store and filesystem
    APIs cannot protect against an uncooperative writer outside the held lock.
    One bounded retained-directory authority binds the observed root, complete
    name set, bounded selector/public reads, and every relative filesystem
    mutation; each mutation boundary revalidates that root, name set, and
    selector before proceeding.
    Selector replacement uses the intent ID for deterministic private build and
    published-stage names. Recovery may rewrite an interrupted build, but only
    an exact published stage can advance to the active selector.
    Only ``abort`` and ``commit`` are accepted; journal state selects the
    directive outside this mutation owner.
    """

    if not isinstance(intent, CertificateMutationIntent):
        raise TypeError("intent must be a CertificateMutationIntent")
    if directive not in {"abort", "commit"}:
        raise ValueError("certificate recovery directive is invalid")
    if _certificate_backend_identity(secret_backend) != intent.backend_identity:
        raise CertificateProvisioningError(
            "certificate transaction backend identity changed"
        )
    root = _validated_transaction_paths(paths)
    observation = _observe_recovery_state(
        root,
        intent,
        secret_backend=secret_backend,
    )
    present = [authority for authority in observation.public if authority is not None]
    if observation.selector == "intended":
        try:
            if len(present) != len(intent.public_files) or observation.secret is None:
                raise CertificateStateConflict(
                    "committed certificate state is incomplete"
                )
            if any(
                authority.location is not TrackedFileLocation.CANONICAL
                for authority in present
            ):
                raise CertificateStateConflict(
                    "committed certificate state is incomplete"
                )
            if observation.selector_transaction != "absent":
                _require_recovery_selector_state(observation, intent, "intended")
                if observation.inventory is None:
                    raise CertificateStateConflict(
                        "certificate public state conflicts with intent"
                    )
                observation.inventory.replace_regular_file(
                    ACTIVE_KEY_ID_NAME,
                    (intent.active_key_id + "\n").encode("ascii"),
                    mode=0o600,
                    staging_capability=intent.intent_id,
                )
            _verify_recovery_committed(observation, intent)
        finally:
            _release_recovery_observation(observation)
        return CertificateMutationResult(
            key_id=intent.active_key_id,
            disposition=CertificateRecoveryDisposition.COMMITTED,
        )

    if directive == "commit":
        try:
            if len(present) != len(intent.public_files) or observation.secret is None:
                raise CertificateStateConflict(
                    "staged certificate state is incomplete"
                )
            if any(
                authority.location is not TrackedFileLocation.CANONICAL
                for authority in present
            ):
                raise CertificateStateConflict(
                    "staged certificate state is incomplete"
                )
            _require_recovery_selector_state(observation, intent, "prior")
            if observation.inventory is None:
                raise CertificateStateConflict(
                    "certificate public state conflicts with intent"
                )
            observation.inventory.replace_regular_file(
                ACTIVE_KEY_ID_NAME,
                (intent.active_key_id + "\n").encode("ascii"),
                mode=0o600,
                staging_capability=intent.intent_id,
            )
            _verify_recovery_committed(observation, intent)
        finally:
            _release_recovery_observation(observation)
        return CertificateMutationResult(
            key_id=intent.active_key_id,
            disposition=CertificateRecoveryDisposition.COMMITTED,
        )

    had_effect = observation.selector_transaction != "absent" or bool(present) or (
        intent.action == "create" and observation.secret is not None
    )
    if not had_effect:
        _release_recovery_observation(observation)
        return CertificateMutationResult(
            key_id=intent.active_key_id,
            disposition=CertificateRecoveryDisposition.ABANDONED,
        )
    if observation.selector_transaction != "absent":
        try:
            _require_recovery_selector_state(observation, intent, "prior")
            if observation.inventory is None:
                raise CertificateStateConflict(
                    "certificate public state conflicts with intent"
                )
            observation.inventory.discard_selector_transaction(
                ACTIVE_KEY_ID_NAME,
                (intent.active_key_id + "\n").encode("ascii"),
                staging_capability=intent.intent_id,
            )
        except BaseException:
            _release_recovery_observation(observation)
            raise
    if intent.action == "create" and observation.secret is not None:
        try:
            _require_recovery_selector_state(observation, intent, "prior")
            _clear_staged_secret(intent.active_key_id, secret_backend=secret_backend)
        except BaseException:
            _release_recovery_observation(observation)
            raise
    try:
        for authority in present:
            if authority.location is TrackedFileLocation.CANONICAL:
                _require_recovery_selector_state(observation, intent, "prior")
                authority.relocate()
        for authority in present:
            _require_recovery_selector_state(observation, intent, "prior")
            authority.dispose()
    except BaseException:
        _release_recovery_observation(observation)
        raise CertificateCleanupError("certificate public-key cleanup failed") from None
    _release_recovery_observation(observation)
    return CertificateMutationResult(
        key_id=intent.active_key_id,
        disposition=CertificateRecoveryDisposition.ABORTED,
    )


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
    created: list[TrackedFileCreation] = []
    try:
        for name, payload in sorted(retained.items()):
            destination = stable_root / name
            tracked = atomic_create_bytes_tracked(
                destination,
                payload,
                allowed_root=stable_root,
                mode=0o600,
            )
            if tracked is not None:
                created.append(tracked)
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
    except BaseException as exc:
        cleanup_error: BaseException | None = None
        for candidate in reversed(created):
            try:
                candidate.remove()
            except BaseException as removal_error:
                if cleanup_error is None:
                    cleanup_error = removal_error
        if cleanup_error is not None:
            raise cleanup_error from exc
        raise
    else:
        for candidate in created:
            candidate.release()
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
    """Create and commit a new pair through the separately serialized rotation API."""

    root = Path(public_key_root).absolute()
    ensure_secure_directory(root)
    backend = _resolve_secret_backend(secret_backend)
    prior_key_id = _selector_key_id(root, allow_non_atomic=allow_non_atomic)
    if prior_key_id is not None:
        try:
            active = load_certificate_signing_key(
                root,
                secret_backend=backend,
                allow_non_atomic=allow_non_atomic,
            )
        except BaseException:
            raise CertificateProvisioningError(
                "active certificate verification failed before rotation"
            ) from None
        if active.key_id != prior_key_id:
            raise CertificateProvisioningError(
                "active certificate verification failed before rotation"
            )
    paths = CertificateStatePaths(
        public_key_root=root,
        active_key_id=root / ACTIVE_KEY_ID_NAME,
    )
    staged = _stage_generated_certificate_key(
        root,
        prior_key_id=prior_key_id,
        secret_backend=backend,
    )
    return commit_staged_certificate(
        paths,
        staged,
        secret_backend=backend,
    )


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

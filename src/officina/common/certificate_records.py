"""Canonical append-only Ed25519 certificate records."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
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
    TrackedFileCreation,
    atomic_create_bytes,
    atomic_create_bytes_tracked,
    atomic_replace_bytes,
    ensure_secure_directory,
    read_regular_directory_entries,
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


@dataclass
class _StagedCertificateBinding:
    staged_ref: weakref.ReferenceType[StagedCertificateKey]
    secret_backend: secret_store.SecretBackend
    public_creation: TrackedFileCreation | None
    prior_key_id: str | None
    cleanup_complete: bool = False


_STAGED_CERTIFICATE_BINDINGS: dict[int, _StagedCertificateBinding] = {}


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

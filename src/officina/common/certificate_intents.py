"""Secret-free, canonical certificate-mutation intent records.

The installer persists these lightweight records before authorizing a retained
credential worker to mutate certificate state.  This module intentionally uses
only the Python standard library so journal inspection never imports private-key
or native credential dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal


CERTIFICATE_INTENT_SCHEMA_VERSION = 1
MAX_CERTIFICATE_INTENT_BYTES = 16_384
MAX_CERTIFICATE_PUBLIC_FILES = 64
MAX_CERTIFICATE_PUBLIC_FILE_BYTES = 65_536
MAX_TRANSACTION_ID_BYTES = 32
MAX_BACKEND_IDENTITY_BYTES = 255
MAX_SECRET_TARGET_BYTES = 255

_IDENTIFIER_PATTERN = re.compile(r"[0-9a-f]{32}")
_KEY_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_BACKEND_IDENTITY_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
)
_PUBLIC_FILE_FIELDS = {"key_id", "size", "sha256", "quarantine_id"}
_INTENT_FIELDS = {
    "schema_version",
    "transaction_id",
    "intent_id",
    "action",
    "backend_identity",
    "active_key_id",
    "prior_key_id",
    "public_files",
    "secret_target",
}


def _require_string_pattern(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str],
    maximum_bytes: int | None = None,
) -> str:
    """Return one bounded canonical ASCII string or raise a static error."""

    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{field} is invalid") from None
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        raise ValueError(f"{field} is invalid")
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _require_key_id(value: object, *, field: str) -> str:
    return _require_string_pattern(value, field=field, pattern=_KEY_ID_PATTERN)


def _canonical_secret_target(key_id: str) -> str:
    return f"Famulus:skill-certifier:ed25519-private-key:{key_id}"


@dataclass(frozen=True)
class CertificatePublicFileIntent:
    """Expected public bytes for one bounded certificate-key file."""

    key_id: str
    size: int
    sha256: str
    quarantine_id: str

    def __post_init__(self) -> None:
        _require_key_id(self.key_id, field="public_files.key_id")
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or not 1 <= self.size <= MAX_CERTIFICATE_PUBLIC_FILE_BYTES
        ):
            raise ValueError("public_files.size is invalid")
        _require_string_pattern(
            self.sha256,
            field="public_files.sha256",
            pattern=_SHA256_PATTERN,
        )
        _require_string_pattern(
            self.quarantine_id,
            field="public_files.quarantine_id",
            pattern=_IDENTIFIER_PATTERN,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible public-file record."""

        return {
            "key_id": self.key_id,
            "size": self.size,
            "sha256": self.sha256,
            "quarantine_id": self.quarantine_id,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "CertificatePublicFileIntent":
        """Parse one closed public-file object without accepting extensions."""

        if not isinstance(payload, dict) or set(payload) != _PUBLIC_FILE_FIELDS:
            raise ValueError("public file intent fields are incomplete or unknown")
        return cls(
            key_id=payload["key_id"],  # type: ignore[arg-type]
            size=payload["size"],  # type: ignore[arg-type]
            sha256=payload["sha256"],  # type: ignore[arg-type]
            quarantine_id=payload["quarantine_id"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class CertificateMutationIntent:
    """One bounded, canonical, secret-free certificate mutation plan."""

    schema_version: int
    transaction_id: str
    intent_id: str
    action: Literal["create", "copy_legacy"]
    backend_identity: str
    active_key_id: str
    prior_key_id: str | None
    public_files: tuple[CertificatePublicFileIntent, ...]
    secret_target: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != CERTIFICATE_INTENT_SCHEMA_VERSION
        ):
            raise ValueError("schema_version must be 1")
        _require_string_pattern(
            self.transaction_id,
            field="transaction_id",
            pattern=_IDENTIFIER_PATTERN,
            maximum_bytes=MAX_TRANSACTION_ID_BYTES,
        )
        _require_string_pattern(
            self.intent_id,
            field="intent_id",
            pattern=_IDENTIFIER_PATTERN,
        )
        if self.action not in {"create", "copy_legacy"}:
            raise ValueError("action is invalid")
        _require_string_pattern(
            self.backend_identity,
            field="backend_identity",
            pattern=_BACKEND_IDENTITY_PATTERN,
            maximum_bytes=MAX_BACKEND_IDENTITY_BYTES,
        )
        _require_key_id(self.active_key_id, field="active_key_id")
        if self.prior_key_id is not None:
            _require_key_id(self.prior_key_id, field="prior_key_id")
        if not isinstance(self.public_files, tuple):
            raise ValueError("public_files must be a tuple")
        if not 1 <= len(self.public_files) <= MAX_CERTIFICATE_PUBLIC_FILES:
            raise ValueError("public_files must contain 1 to at most 64 entries")
        if not all(
            isinstance(item, CertificatePublicFileIntent)
            for item in self.public_files
        ):
            raise ValueError("public_files entries are invalid")
        key_ids = tuple(item.key_id for item in self.public_files)
        if len(set(key_ids)) != len(key_ids):
            raise ValueError("public_files contains a duplicate key_id")
        if key_ids != tuple(sorted(key_ids)):
            raise ValueError("public_files must be strictly sorted by key_id")
        quarantine_ids = tuple(item.quarantine_id for item in self.public_files)
        if len(set(quarantine_ids)) != len(quarantine_ids):
            raise ValueError("public_files contains a duplicate quarantine_id")
        if self.active_key_id not in key_ids:
            raise ValueError("active_key_id must identify one public_files entry")
        expected_target = _canonical_secret_target(self.active_key_id)
        try:
            encoded_target = self.secret_target.encode("ascii")
        except (AttributeError, UnicodeEncodeError):
            raise ValueError("secret_target is invalid") from None
        if (
            len(encoded_target) > MAX_SECRET_TARGET_BYTES
            or self.secret_target != expected_target
        ):
            raise ValueError("secret_target is invalid")
        if len(_encode_intent_dict(self._to_dict_unchecked())) > MAX_CERTIFICATE_INTENT_BYTES:
            raise ValueError("certificate intent encoding is too large")

    def _to_dict_unchecked(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "intent_id": self.intent_id,
            "action": self.action,
            "backend_identity": self.backend_identity,
            "active_key_id": self.active_key_id,
            "prior_key_id": self.prior_key_id,
            "public_files": [item.to_dict() for item in self.public_files],
            "secret_target": self.secret_target,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible intent object."""

        return self._to_dict_unchecked()

    @classmethod
    def from_dict(cls, payload: object) -> "CertificateMutationIntent":
        """Parse one closed intent object and revalidate every nested field."""

        if not isinstance(payload, dict) or set(payload) != _INTENT_FIELDS:
            raise ValueError("certificate intent fields are incomplete or unknown")
        public_files = payload["public_files"]
        if not isinstance(public_files, list):
            raise ValueError("public_files must be a JSON array")
        return cls(
            schema_version=payload["schema_version"],  # type: ignore[arg-type]
            transaction_id=payload["transaction_id"],  # type: ignore[arg-type]
            intent_id=payload["intent_id"],  # type: ignore[arg-type]
            action=payload["action"],  # type: ignore[arg-type]
            backend_identity=payload["backend_identity"],  # type: ignore[arg-type]
            active_key_id=payload["active_key_id"],  # type: ignore[arg-type]
            prior_key_id=payload["prior_key_id"],  # type: ignore[arg-type]
            public_files=tuple(
                CertificatePublicFileIntent.from_dict(item)
                for item in public_files
            ),
            secret_target=payload["secret_target"],  # type: ignore[arg-type]
        )


def _encode_intent_dict(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_certificate_intent_bytes(intent: CertificateMutationIntent) -> bytes:
    """Return the bounded canonical UTF-8 JSON used by journals and ACKs."""

    if not isinstance(intent, CertificateMutationIntent):
        raise TypeError("intent must be a CertificateMutationIntent")
    encoded = _encode_intent_dict(intent.to_dict())
    if len(encoded) > MAX_CERTIFICATE_INTENT_BYTES:
        raise ValueError("certificate intent encoding is too large")
    return encoded


def certificate_intent_digest(intent: CertificateMutationIntent) -> str:
    """Bind an ACK to one complete canonical intent with an algorithm tag."""

    return "sha256:" + hashlib.sha256(
        canonical_certificate_intent_bytes(intent)
    ).hexdigest()


def _closed_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("certificate intent JSON contains a duplicate field")
        result[key] = value
    return result


def parse_certificate_intent_bytes(encoded: bytes) -> CertificateMutationIntent:
    """Parse one bounded UTF-8 JSON intent while detecting duplicate fields."""

    if not isinstance(encoded, bytes):
        raise TypeError("encoded certificate intent must be bytes")
    if not encoded or len(encoded) > MAX_CERTIFICATE_INTENT_BYTES:
        raise ValueError("encoded certificate intent size is invalid")
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_closed_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("encoded certificate intent is invalid") from None
    return CertificateMutationIntent.from_dict(payload)

from __future__ import annotations

import base64
import importlib
import json

import pytest


KEY_A = "sha256:" + "a" * 64
KEY_B = "sha256:" + "b" * 64
DIGEST_A = "c" * 64
TRANSACTION_A = "fedcba9876543210fedcba9876543210"
QUARANTINE_A = "11111111111111111111111111111111"
QUARANTINE_B = "22222222222222222222222222222222"
SECRET_TARGET_A = (
    "Famulus:skill-certifier:ed25519-private-key:" + KEY_A
)


def _intents():
    return importlib.import_module("officina.common.certificate_intents")


def _public_file(
    *,
    key_id: str = KEY_A,
    size: int = 32,
    sha256: str = DIGEST_A,
    quarantine_id: str = QUARANTINE_A,
):
    api = _intents()
    return api.CertificatePublicFileIntent(
        key_id=key_id,
        size=size,
        sha256=sha256,
        quarantine_id=quarantine_id,
    )


def test_public_file_intent_requires_canonical_quarantine_capability() -> None:
    api = _intents()

    intent = api.CertificatePublicFileIntent(
        key_id=KEY_A,
        size=32,
        sha256=DIGEST_A,
        quarantine_id=QUARANTINE_A,
    )

    assert intent.quarantine_id == QUARANTINE_A


def _intent(**changes: object):
    api = _intents()
    values: dict[str, object] = {
        "schema_version": 1,
        "transaction_id": TRANSACTION_A,
        "intent_id": "0123456789abcdef0123456789abcdef",
        "action": "create",
        "backend_identity": "keyring.backends.SecretService.Keyring",
        "active_key_id": KEY_A,
        "prior_key_id": None,
        "public_files": (_public_file(),),
        "secret_target": SECRET_TARGET_A,
    }
    values.update(changes)
    return api.CertificateMutationIntent(**values)


def _intent_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "transaction_id": TRANSACTION_A,
        "intent_id": "0123456789abcdef0123456789abcdef",
        "action": "create",
        "backend_identity": "keyring.backends.SecretService.Keyring",
        "active_key_id": KEY_A,
        "prior_key_id": None,
        "public_files": [{
            "key_id": KEY_A,
            "size": 32,
            "sha256": DIGEST_A,
            "quarantine_id": QUARANTINE_A,
        }],
        "secret_target": SECRET_TARGET_A,
    }


def test_certificate_intent_has_exact_canonical_fields_and_digest() -> None:
    api = _intents()
    intent = _intent()
    canonical = (
        b'{"action":"create","active_key_id":"sha256:'
        + b"a" * 64
        + b'","backend_identity":"keyring.backends.SecretService.Keyring",'
        + b'"intent_id":"0123456789abcdef0123456789abcdef",'
        + b'"prior_key_id":null,"public_files":[{"key_id":"sha256:'
        + b"a" * 64
        + b'","quarantine_id":"11111111111111111111111111111111","sha256":"'
        + b"c" * 64
        + b'","size":32}],"schema_version":1,"secret_target":"Famulus:'
        + b'skill-certifier:ed25519-private-key:sha256:'
        + b"a" * 64
        + b'","transaction_id":"fedcba9876543210fedcba9876543210"}'
    )

    assert intent.to_dict() == _intent_dict()
    assert api.canonical_certificate_intent_bytes(intent) == canonical
    assert api.certificate_intent_digest(intent) == (
        "sha256:5e48d90e2c9328f98c9526e630c5cdfa60859395ef26a5717b8120401db6fafc"
    )
    assert api.CertificateMutationIntent.from_dict(intent.to_dict()) == intent
    assert api.parse_certificate_intent_bytes(canonical) == intent


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"transaction_id": ""}, "transaction_id"),
        ({"transaction_id": "transaction-001"}, "transaction_id"),
        ({"transaction_id": "A" * 32}, "transaction_id"),
        ({"transaction_id": "a" * 31}, "transaction_id"),
        ({"intent_id": "A" * 32}, "intent_id"),
        ({"intent_id": "a" * 31}, "intent_id"),
        ({"action": "delete"}, "action"),
        ({"backend_identity": "Keyring"}, "backend_identity"),
        ({"backend_identity": "keyring. Backend"}, "backend_identity"),
        ({"backend_identity": "a." + "b" * 254}, "backend_identity"),
        ({"active_key_id": "sha256:" + "A" * 64}, "active_key_id"),
        ({"prior_key_id": "sha256:" + "0" * 63}, "prior_key_id"),
        ({"public_files": ()}, "public_files"),
        ({"secret_target": "Famulus:wrong"}, "secret_target"),
    ],
)
def test_certificate_intent_rejects_noncanonical_top_level_values(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _intent(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"key_id": "sha256:" + "f" * 63}, "key_id"),
        ({"size": 0}, "size"),
        ({"size": 65537}, "size"),
        ({"size": True}, "size"),
        ({"sha256": "F" * 64}, "sha256"),
        ({"sha256": "f" * 63}, "sha256"),
        ({"quarantine_id": "A" * 32}, "quarantine_id"),
        ({"quarantine_id": "a" * 31}, "quarantine_id"),
        ({"quarantine_id": "PRIVATE-CERTIFICATE-KEY-CANARY"}, "quarantine_id"),
    ],
)
def test_public_file_intent_rejects_noncanonical_values(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _public_file(**changes)


def test_certificate_intent_rejects_unsorted_duplicate_and_excess_public_files() -> None:
    first = _public_file(key_id=KEY_A)
    second = _public_file(
        key_id=KEY_B,
        sha256="d" * 64,
        quarantine_id=QUARANTINE_B,
    )

    with pytest.raises(ValueError, match="strictly sorted"):
        _intent(public_files=(second, first))
    with pytest.raises(ValueError, match="duplicate"):
        _intent(public_files=(first, first))
    with pytest.raises(ValueError, match="quarantine_id"):
        _intent(
            public_files=(
                first,
                _public_file(key_id=KEY_B, sha256="d" * 64),
            )
        )
    with pytest.raises(ValueError, match="at most 64"):
        _intent(public_files=tuple(first for _index in range(65)))


@pytest.mark.parametrize("action", ["create", "copy_legacy"])
def test_certificate_intent_accepts_only_closed_actions(action: str) -> None:
    assert _intent(action=action).action == action


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("intent_id"),
        lambda payload: payload.__setitem__("unknown", "field"),
        lambda payload: payload["public_files"][0].pop("size"),
        lambda payload: payload["public_files"][0].__setitem__("unknown", 1),
        lambda payload: payload.__setitem__("public_files", "not-an-array"),
        lambda payload: payload.__setitem__("prior_key_id", False),
    ],
)
def test_certificate_intent_from_dict_rejects_malformed_or_unknown_fields(
    mutation,
) -> None:
    api = _intents()
    payload = _intent_dict()
    mutation(payload)

    with pytest.raises((TypeError, ValueError)):
        api.CertificateMutationIntent.from_dict(payload)


def test_certificate_intent_parser_rejects_duplicate_json_fields() -> None:
    api = _intents()
    encoded = api.canonical_certificate_intent_bytes(_intent())
    duplicate = encoded.replace(
        b'{"action":"create",',
        b'{"action":"create","action":"create",',
        1,
    )

    with pytest.raises(ValueError, match="duplicate"):
        api.parse_certificate_intent_bytes(duplicate)


@pytest.mark.parametrize(
    "encoded",
    [
        b"[]",
        b"{not-json}",
        b'{"schema_version":1} trailing',
        b"\xff",
        b" " * 16385,
    ],
)
def test_certificate_intent_parser_rejects_malformed_or_oversized_bytes(
    encoded: bytes,
) -> None:
    api = _intents()
    with pytest.raises((TypeError, ValueError)):
        api.parse_certificate_intent_bytes(encoded)


def test_certificate_intent_rejects_encoded_private_secret_canaries() -> None:
    private = b"PRIVATE-CERTIFICATE-KEY-CANARY"
    encoded_forms = (
        private.decode("ascii"),
        base64.b64encode(private).decode("ascii"),
        private.hex(),
        json.dumps(private.decode("ascii"))[1:-1],
    )

    for canary in encoded_forms:
        with pytest.raises(ValueError) as captured:
            _intent(secret_target=canary)
        assert canary not in str(captured.value)


def test_certificate_intent_rejects_raw_private_canary_in_identity_fields() -> None:
    canary = "PRIVATE-CERTIFICATE-KEY-CANARY"
    for changes in (
        {"transaction_id": canary},
        {"backend_identity": f"keyring.backends.{canary}.Keyring"},
    ):
        with pytest.raises(ValueError) as captured:
            _intent(**changes)
        assert canary not in str(captured.value)


def test_certificate_intent_encoded_size_is_bounded() -> None:
    api = _intents()
    files = tuple(
        _public_file(
            key_id="sha256:" + f"{index:064x}",
            sha256=f"{index + 1:064x}",
            quarantine_id=f"{index + 1:032x}",
        )
        for index in range(64)
    )
    intent = _intent(
        backend_identity="a." + "b" * 253,
        active_key_id=files[0].key_id,
        public_files=files,
        secret_target=(
            "Famulus:skill-certifier:ed25519-private-key:" + files[0].key_id
        ),
    )

    assert len(api.canonical_certificate_intent_bytes(intent)) <= 16384


def test_certificate_intent_requires_active_public_file() -> None:
    with pytest.raises(ValueError, match="active_key_id"):
        _intent(
            active_key_id=KEY_B,
            secret_target=(
                "Famulus:skill-certifier:ed25519-private-key:" + KEY_B
            ),
        )

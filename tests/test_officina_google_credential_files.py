from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

import pytest
import yaml

from officina.credentials.google import (
    GoogleCredentialError,
    SERVICE_SCOPES,
    create_credential_file,
    load_credential_file,
    refresh_access_token_from_file,
)
from officina.credentials.secret_store import SecretNotFoundError


class FakeSecretBackend:
    def __init__(self) -> None:
        self.stored: list[tuple[str, str, str]] = []

    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored.append((namespace, key, value))

    def lookup(self, namespace: str, key: str) -> str | None:
        for stored_namespace, stored_key, value in reversed(self.stored):
            if stored_namespace == namespace and stored_key == key:
                return value
        return None

    def clear(self, namespace: str, key: str) -> bool:
        return False


CREATED_AT = datetime(2026, 8, 10, 14, 52, 10, tzinfo=UTC)
GRANTED_SCOPES = frozenset(
    {
        "openid",
        "email",
        *SERVICE_SCOPES["calendar"],
        *SERVICE_SCOPES["drive"],
    }
)
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def secret_backend() -> FakeSecretBackend:
    """Provide an isolated mutable secret store for each test."""
    return FakeSecretBackend()


@pytest.fixture
def credential_factory(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
) -> Callable[..., object]:
    """Create descriptors with shared valid defaults in the test's temp root."""

    def create_file(
        *,
        now: datetime = CREATED_AT,
        unique_id: str = "a1b2c3d4",
        refresh_token: str = "raw-refresh-token",
    ) -> object:
        return create_credential_file(
            subject="google-subject",
            account="person@example.com",
            client_id="client-id",
            token_uri="https://oauth2.googleapis.com/token",
            granted_services=("calendar", "drive"),
            granted_scopes=GRANTED_SCOPES,
            refresh_token=refresh_token,
            home=tmp_path,
            platform="linux",
            now=now,
            unique_id=unique_id,
            secret_backend=secret_backend,
        )

    return create_file


@pytest.fixture(scope="module")
def common_blueprint() -> dict[str, object]:
    """Parse the immutable blueprint once for this test module."""
    return yaml.safe_load(
        (
            REPO_ROOT
            / "src/officina/credentials/blueprints/google.yaml"
        ).read_text(encoding="utf-8")
    )


def test_same_account_authorizations_create_distinct_secret_free_files(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    credential_factory: Callable[..., object],
) -> None:
    """Replacing file creation with an account-keyed write would destroy history."""
    first = credential_factory()
    first_bytes = first.path.read_bytes()
    second = credential_factory(unique_id="e5f6a7b8")

    expected_dir = tmp_path / ".config" / "famulus" / "connect-google" / "credentials"
    assert first.path == expected_dir / "2026-08-10T14-52-10Z-a1b2c3d4.json"
    assert second.path == expected_dir / "2026-08-10T14-52-10Z-e5f6a7b8.json"
    assert first.path.is_absolute()
    assert first.path.read_bytes() == first_bytes

    payload = json.loads(first.path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "created_at": "2026-08-10T14:52:10Z",
        "subject": "google-subject",
        "account": "person@example.com",
        "client_id": "client-id",
        "token_uri": "https://oauth2.googleapis.com/token",
        "granted_services": ["calendar", "drive"],
        "granted_scopes": sorted(GRANTED_SCOPES),
        "client_secret_ref": "oauth-client:client-id:client-secret",
        "refresh_token_ref": (
            "credential-file:2026-08-10T14-52-10Z-a1b2c3d4:refresh-token"
        ),
    }
    rendered = first.path.read_text(encoding="utf-8")
    assert "raw-refresh-token" not in rendered
    assert "access_token" not in rendered
    assert secret_backend.lookup(
        "connect-google", payload["refresh_token_ref"]
    ) == "raw-refresh-token"


def test_equal_short_ids_at_different_times_have_distinct_secret_references(
    secret_backend: FakeSecretBackend,
    credential_factory: Callable[..., object],
) -> None:
    """Keying the secret by the short ID alone would mutate an older descriptor."""
    first = credential_factory()
    second = credential_factory(now=CREATED_AT + timedelta(seconds=1))

    assert first.refresh_token_ref != second.refresh_token_ref
    assert secret_backend.lookup(
        "connect-google", first.refresh_token_ref
    ) == "raw-refresh-token"
    assert secret_backend.lookup(
        "connect-google", second.refresh_token_ref
    ) == "raw-refresh-token"


def test_filename_collision_changes_neither_file_nor_secret(
    secret_backend: FakeSecretBackend,
    credential_factory: Callable[..., object],
) -> None:
    """Storing the secret before exclusive creation would corrupt the old file's token."""
    first = credential_factory()
    first_bytes = first.path.read_bytes()
    stored_before = list(secret_backend.stored)

    with pytest.raises(GoogleCredentialError, match="already exists"):
        credential_factory(refresh_token="replacement-token")

    assert first.path.read_bytes() == first_bytes
    assert secret_backend.stored == stored_before


def test_load_credential_file_round_trips_and_resolves_absolute_path(
    credential_factory: Callable[..., object],
) -> None:
    created = credential_factory()

    loaded = load_credential_file(created.path)

    assert loaded == created
    assert loaded.path.is_absolute()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.pop("account"), "fields"),
        (lambda payload: payload.update(schema_version=2), "schema_version"),
        (lambda payload: payload.update(granted_scopes="openid"), "granted_scopes"),
        (lambda payload: payload.update(unexpected=True), "fields"),
    ],
)
def test_load_credential_file_rejects_malformed_schema(
    credential_factory: Callable[..., object], mutation, message
) -> None:
    created = credential_factory()
    payload = json.loads(created.path.read_text(encoding="utf-8"))
    mutation(payload)
    created.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GoogleCredentialError, match=message):
        load_credential_file(created.path)


def test_load_credential_file_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(GoogleCredentialError, match="does not exist"):
        load_credential_file(tmp_path / "missing.json")


def test_refresh_from_file_checks_scope_before_network(
    secret_backend: FakeSecretBackend,
    credential_factory: Callable[..., object],
) -> None:
    created = credential_factory()

    def forbidden_urlopen(*_args, **_kwargs):
        raise AssertionError("network must not run for an ungranted scope")

    with pytest.raises(GoogleCredentialError, match="lacks required scopes"):
        refresh_access_token_from_file(
            created.path,
            required_scopes=SERVICE_SCOPES["gmail"],
            urlopen=forbidden_urlopen,
            secret_backend=secret_backend,
        )


def test_refresh_from_file_uses_recorded_secret_references(
    secret_backend: FakeSecretBackend,
    credential_factory: Callable[..., object],
) -> None:
    created = credential_factory()
    secret_backend.store(
        "connect-google",
        "oauth-client:client-id:client-secret",
        "raw-client-secret",
    )
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size: int = -1) -> bytes:
            data = json.dumps({"access_token": "fresh-access-token"}).encode()
            return data if size < 0 else data[:size]

    def fake_urlopen(request, *, timeout=30.0):
        assert timeout == 30.0
        requests.append(request)
        return FakeResponse()

    token = refresh_access_token_from_file(
        created.path,
        required_scopes=SERVICE_SCOPES["drive"],
        urlopen=fake_urlopen,
        secret_backend=secret_backend,
    )

    assert token == "fresh-access-token"
    assert len(requests) == 1
    body = requests[0].data.decode("ascii")
    assert "refresh_token=raw-refresh-token" in body
    assert "client_secret=raw-client-secret" in body


@pytest.mark.parametrize(
    "missing_reference",
    ["client_secret_ref", "refresh_token_ref"],
)
def test_refresh_from_file_rejects_missing_recorded_secret_before_network(
    secret_backend: FakeSecretBackend,
    credential_factory: Callable[..., object],
    missing_reference: str,
) -> None:
    created = credential_factory()
    secret_backend.store(
        "connect-google",
        "oauth-client:client-id:client-secret",
        "raw-client-secret",
    )
    missing_key = getattr(created, missing_reference)
    secret_backend.stored[:] = [
        item for item in secret_backend.stored if item[1] != missing_key
    ]

    with pytest.raises(SecretNotFoundError, match="no secret stored"):
        refresh_access_token_from_file(
            created.path,
            required_scopes=SERVICE_SCOPES["drive"],
            urlopen=lambda *_args, **_kwargs: pytest.fail(
                "token endpoint must not run with a missing secret reference"
            ),
            secret_backend=secret_backend,
        )


def test_common_blueprint_declares_descriptor_operations_and_io(
    common_blueprint: dict[str, object],
) -> None:
    """Omitting the contract would leave working code outside graph ownership."""
    interface = common_blueprint["interfaces"][
        "credentials.source.google.interface.python-api"
    ]["contract"]
    operations = {
        item["value"]
        for item in interface["arguments"]["operation"]["type"]["values"]
    }
    assert {
        "create-credential-file",
        "load-credential-file",
        "refresh-access-token-from-file",
    } <= operations
    assert "store-credential" not in operations

    direct_io = interface["direct_io"]
    assert "credential-descriptor-file" in {
        item["id"] for item in direct_io["reads"]
    }
    assert "credential-descriptor-file" in {
        item["id"] for item in direct_io["writes"]
    }

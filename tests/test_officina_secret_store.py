from __future__ import annotations

import os
import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common import secret_store  # noqa: E402


class MemoryBackend:
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, secret: str) -> None:
        self.values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.values.pop((namespace, key), None) is not None


class UsableBackend:
    priority = 1


class FailBackend:
    __module__ = "keyring.backends.fail"
    priority = 0


class NullBackend:
    __module__ = "keyring.backends.null"
    priority = 1


class ZeroPriorityBackend:
    __module__ = "custom.backend"
    priority = 0


class FakeKeyring:
    def __init__(self, backend=None) -> None:
        self.backend = backend or UsableBackend()
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    def get_keyring(self):
        return self.backend

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append(("set", service, username))
        self.values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        self.values.pop((service, username))


class KeyringError(Exception):
    pass


class PasswordDeleteError(KeyringError):
    pass


def install_fake_keyring(monkeypatch: pytest.MonkeyPatch, fake: FakeKeyring) -> None:
    keyring_module = types.ModuleType("keyring")
    keyring_module.__path__ = []
    keyring_module.get_keyring = fake.get_keyring
    keyring_module.set_password = fake.set_password
    keyring_module.get_password = fake.get_password
    keyring_module.delete_password = fake.delete_password

    errors_module = types.ModuleType("keyring.errors")
    errors_module.KeyringError = KeyringError
    errors_module.PasswordDeleteError = PasswordDeleteError
    keyring_module.errors = errors_module

    monkeypatch.setitem(sys.modules, "keyring", keyring_module)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors_module)


def test_store_lookup_require_and_clear_with_injected_backend() -> None:
    backend = MemoryBackend()

    secret_store.store("email-client", "personal:imap", "app-password", backend=backend)

    assert secret_store.lookup("email-client", "personal:imap", backend=backend) == "app-password"
    assert secret_store.require("email-client", "personal:imap", backend=backend) == "app-password"
    assert secret_store.clear("email-client", "personal:imap", backend=backend)
    assert secret_store.lookup("email-client", "personal:imap", backend=backend) is None


@pytest.mark.parametrize(
    ("namespace", "key", "message"),
    [
        ("", "key", "namespace"),
        ("namespace", "", "key"),
        ("namespace\x00", "key", "namespace"),
        ("namespace", "key\x00", "key"),
    ],
)
def test_reference_parts_are_validated(namespace: str, key: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        secret_store.lookup(namespace, key, backend=MemoryBackend())


def test_secret_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="secret"):
        secret_store.store("namespace", "key", "", backend=MemoryBackend())


def test_require_raises_for_missing_secret() -> None:
    with pytest.raises(secret_store.SecretNotFoundError):
        secret_store.require("namespace", "key", backend=MemoryBackend())


def test_target_name_has_project_prefix() -> None:
    assert secret_store.target_name("email-client", "personal:imap") == "Famulus:email-client:personal:imap"


def test_keyring_backend_uses_canonical_service_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeKeyring()
    install_fake_keyring(monkeypatch, fake)

    secret_store.store("email-client", "personal:imap", "s3cret")

    assert fake.values[("Famulus:email-client", "personal:imap")] == "s3cret"
    assert secret_store.require("email-client", "personal:imap") == "s3cret"
    assert secret_store.clear("email-client", "personal:imap")
    assert secret_store.lookup("email-client", "personal:imap") is None
    assert fake.calls[0] == ("set", "Famulus:email-client", "personal:imap")


def test_keyring_backend_reports_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setitem(sys.modules, "keyring.errors", None)

    with pytest.raises(secret_store.SecretStoreUnavailable, match="keyring package"):
        secret_store.lookup("email-client", "personal:imap")


@pytest.mark.parametrize("backend", [FailBackend(), NullBackend(), ZeroPriorityBackend()])
def test_keyring_backend_rejects_unusable_backend(monkeypatch: pytest.MonkeyPatch, backend) -> None:
    install_fake_keyring(monkeypatch, FakeKeyring(backend=backend))

    with pytest.raises(secret_store.SecretStoreUnavailable, match="usable keyring backend"):
        secret_store.lookup("email-client", "personal:imap")


def test_keyring_errors_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeKeyring()
    install_fake_keyring(monkeypatch, fake)

    def fail_set_password(service: str, username: str, password: str) -> None:
        raise KeyringError("backend refused")

    sys.modules["keyring"].set_password = fail_set_password

    with pytest.raises(secret_store.SecretStoreError, match="backend refused"):
        secret_store.store("email-client", "personal:imap", "s3cret")


def test_default_backend_native_roundtrip_when_available() -> None:
    namespace = "officina-test"
    key = f"native:{uuid.uuid4()}"
    secret = f"secret:{uuid.uuid4()}"
    require_native = os.environ.get("FAMULUS_REQUIRE_NATIVE_KEYRING") == "1"

    try:
        secret_store.store(namespace, key, secret)
    except secret_store.SecretStoreUnavailable as exc:
        if require_native:
            pytest.fail(f"native keyring backend required but unavailable: {exc}")
        # famulus-skip: category=native-backend-unavailable; reason=generic CI may not provide a host keyring; alternate=fake keyring backend tests cover the shared contract
        pytest.skip(f"native keyring backend unavailable: {exc}")

    try:
        assert secret_store.require(namespace, key) == secret
    finally:
        secret_store.clear(namespace, key)

    assert secret_store.lookup(namespace, key) is None

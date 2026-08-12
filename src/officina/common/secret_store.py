"""Host-neutral API for storing small local secrets.

This module owns the public contract for first-party skill code. The default
implementation delegates to the third-party ``keyring`` package, which in turn
uses the host credential store when one is available.
"""
from __future__ import annotations

import importlib
from importlib import metadata
from typing import Protocol

from officina.common.native_keyring_linux_osx_windows import (
    NATIVE_BACKENDS,
    current_platform_name,
)


PINNED_KEYRING_VERSION = "25.6.0"
class SecretStoreError(RuntimeError):
    """Base error for credential-store failures."""


class SecretStoreUnavailable(SecretStoreError):
    """Raised when the current host has no usable credential backend."""


class SecretStoreUnsupportedBackend(SecretStoreUnavailable):
    """Raised when keyring selected a backend outside the audited allowlist."""


class SecretStoreLocked(SecretStoreError):
    """Raised when the selected native credential store is locked."""


class SecretNotFoundError(SecretStoreError):
    """Raised when a requested secret does not exist."""


class SecretBackend(Protocol):
    """Backend contract implemented by secret-store adapters."""

    name: str

    def store(self, namespace: str, key: str, secret: str) -> None:
        """Store ``secret`` under ``namespace`` and ``key``."""

    def lookup(self, namespace: str, key: str) -> str | None:
        """Return a stored secret, or None if it is absent."""

    def clear(self, namespace: str, key: str) -> bool:
        """Clear a stored secret. Return True if a secret was removed."""


def store(namespace: str, key: str, secret: str, backend: SecretBackend | None = None) -> None:
    """Store a secret through the selected host backend."""
    _validate_reference(namespace, key)
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret must be a non-empty string")
    _backend(backend).store(namespace, key, secret)


def lookup(namespace: str, key: str, backend: SecretBackend | None = None) -> str | None:
    """Look up a secret through the selected host backend."""
    _validate_reference(namespace, key)
    return _backend(backend).lookup(namespace, key)


def require(namespace: str, key: str, backend: SecretBackend | None = None) -> str:
    """Look up a secret and raise if it is missing."""
    secret = lookup(namespace, key, backend=backend)
    if secret is None:
        raise SecretNotFoundError(f"no secret stored for {namespace}:{key}")
    return secret


def clear(namespace: str, key: str, backend: SecretBackend | None = None) -> bool:
    """Remove a secret through the selected host backend."""
    _validate_reference(namespace, key)
    return _backend(backend).clear(namespace, key)


def target_name(namespace: str, key: str) -> str:
    """Return the canonical human-readable target name for a secret reference."""
    _validate_reference(namespace, key)
    return f"Famulus:{namespace}:{key}"


def _backend(backend: SecretBackend | None) -> SecretBackend:
    if backend is not None:
        return backend

    return KeyringSecretBackend()


def _validate_reference(namespace: str, key: str) -> None:
    for label, value in (("namespace", namespace), ("key", key)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a non-empty string")
        if "\x00" in value:
            raise ValueError(f"{label} must not contain NUL bytes")


class KeyringSecretBackend:
    """Secret backend backed by the Python ``keyring`` package."""

    name = "keyring"

    def __init__(self) -> None:
        self._validated_backend: object | None = None

    def backend_identity(self) -> str:
        """Return the audited concrete backend identity without reading a secret."""
        return native_backend_identity(self._selected_backend())

    def store(self, namespace: str, key: str, secret: str) -> None:
        selected = self._selected_backend()
        try:
            selected.set_password(_service_name(namespace), key, secret)
        except self._keyring_error_classes() as exc:
            self._raise_normalized_error("store", namespace, key, exc)

    def lookup(self, namespace: str, key: str) -> str | None:
        selected = self._selected_backend()
        try:
            return selected.get_password(_service_name(namespace), key)
        except self._keyring_error_classes() as exc:
            self._raise_normalized_error("read", namespace, key, exc)

    def clear(self, namespace: str, key: str) -> bool:
        if self.lookup(namespace, key) is None:
            return False

        selected = self._selected_backend()
        try:
            selected.delete_password(_service_name(namespace), key)
            return True
        except self._password_delete_error_class():
            return False
        except self._keyring_error_classes() as exc:
            self._raise_normalized_error("clear", namespace, key, exc)

    def _keyring_module(self):
        try:
            import keyring
        except ModuleNotFoundError:
            raise SecretStoreUnavailable("the keyring package is not installed") from None

        return keyring

    def _selected_backend(self):
        if self._validated_backend is not None:
            return self._validated_backend
        module = self._keyring_module()
        _require_pinned_keyring_version()
        try:
            selected = module.get_keyring()
        except self._keyring_error_classes():
            raise SecretStoreUnavailable("no usable keyring backend") from None
        native_backend_identity(selected)
        self._validated_backend = selected
        return selected

    def _keyring_error_classes(self) -> tuple[type[Exception], ...]:
        try:
            import keyring.errors
        except ModuleNotFoundError:
            return (Exception,)
        return (keyring.errors.KeyringError,)

    def _password_delete_error_class(self) -> type[Exception]:
        try:
            import keyring.errors
        except ModuleNotFoundError:
            return Exception
        return keyring.errors.PasswordDeleteError

    def _raise_normalized_error(
        self,
        operation: str,
        namespace: str,
        key: str,
        exc: BaseException,
    ) -> None:
        """Raise a static public error without retaining backend-controlled text."""
        error_name = type(exc).__name__.lower()
        message = f"could not {operation} secret for {target_name(namespace, key)}"
        if error_name in {"keyringlocked", "lockedexception"}:
            raise SecretStoreLocked(message) from None
        if error_name in {
            "initerror",
            "nokeyringerror",
            "secretservicenotavailableexception",
            "dbuserror",
            "dbusexception",
            "serviceunknown",
            "connectionerror",
        }:
            raise SecretStoreUnavailable("the native credential service is unavailable") from None
        raise SecretStoreError(message) from None


def _service_name(namespace: str) -> str:
    return f"Famulus:{namespace}"


def native_backend_identity(
    backend: object,
    *,
    platform_name: str | None = None,
    package_version: str | None = None,
) -> str:
    """Validate and return one concrete audited native keyring class name.

    Validation is deliberately independent of backend priority. Alternate,
    chained, custom, Null, Fail, and test backends remain unsupported even when
    they advertise a positive priority or successfully round-trip a value.
    """
    actual_version = package_version
    if actual_version is None:
        actual_version = _keyring_package_version()
    if actual_version != PINNED_KEYRING_VERSION:
        raise SecretStoreUnsupportedBackend(
            "the selected keyring package version is not audited"
        )

    selected_platform = current_platform_name() if platform_name is None else platform_name
    backend_type = type(backend)
    identity = f"{backend_type.__module__}.{backend_type.__name__}"
    if identity not in NATIVE_BACKENDS.get(selected_platform, set()):
        raise SecretStoreUnsupportedBackend(
            "no usable keyring backend; the selected backend is not audited native"
        )
    module_name, _, class_name = identity.rpartition(".")
    try:
        canonical_type = getattr(importlib.import_module(module_name), class_name)
    except (AttributeError, ImportError, ModuleNotFoundError):
        raise SecretStoreUnsupportedBackend(
            "no usable keyring backend; the audited native class is unavailable"
        ) from None
    if type(backend) is not canonical_type:
        raise SecretStoreUnsupportedBackend(
            "no usable keyring backend; the selected class identity is not canonical"
        )
    return identity


def _keyring_package_version() -> str:
    try:
        return metadata.version("keyring")
    except metadata.PackageNotFoundError:
        raise SecretStoreUnavailable("the keyring package is not installed") from None


def _require_pinned_keyring_version() -> None:
    if _keyring_package_version() != PINNED_KEYRING_VERSION:
        raise SecretStoreUnsupportedBackend(
            "the selected keyring package version is not audited"
        )

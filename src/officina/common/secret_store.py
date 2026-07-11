"""Host-neutral API for storing small local secrets.

This module owns the public contract for first-party skill code. The default
implementation delegates to the third-party ``keyring`` package, which in turn
uses the host credential store when one is available.
"""
from __future__ import annotations

from typing import Protocol


class SecretStoreError(RuntimeError):
    """Base error for credential-store failures."""


class SecretStoreUnavailable(SecretStoreError):
    """Raised when the current host has no usable credential backend."""


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

    def store(self, namespace: str, key: str, secret: str) -> None:
        module = self._keyring_module()
        try:
            module.set_password(_service_name(namespace), key, secret)
        except self._keyring_error_classes() as exc:
            raise SecretStoreError(f"could not store secret for {target_name(namespace, key)}: {exc}") from exc

    def lookup(self, namespace: str, key: str) -> str | None:
        module = self._keyring_module()
        try:
            return module.get_password(_service_name(namespace), key)
        except self._keyring_error_classes() as exc:
            raise SecretStoreError(f"could not read secret for {target_name(namespace, key)}: {exc}") from exc

    def clear(self, namespace: str, key: str) -> bool:
        if self.lookup(namespace, key) is None:
            return False

        module = self._keyring_module()
        try:
            module.delete_password(_service_name(namespace), key)
            return True
        except self._password_delete_error_class():
            return False
        except self._keyring_error_classes() as exc:
            raise SecretStoreError(f"could not clear secret for {target_name(namespace, key)}: {exc}") from exc

    def _keyring_module(self):
        try:
            import keyring
        except ModuleNotFoundError as exc:
            raise SecretStoreUnavailable("the keyring package is not installed") from exc

        self._ensure_usable_backend(keyring)
        return keyring

    def _ensure_usable_backend(self, module) -> None:
        try:
            backend = module.get_keyring()
        except self._keyring_error_classes() as exc:
            raise SecretStoreUnavailable(f"no usable keyring backend: {exc}") from exc

        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}".lower()
        if ".fail." in backend_name or ".null." in backend_name:
            raise SecretStoreUnavailable(f"no usable keyring backend: {backend}")

        priority = getattr(backend, "priority", None)
        if isinstance(priority, (int, float)) and priority <= 0:
            raise SecretStoreUnavailable(f"no usable keyring backend: {backend}")

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


def _service_name(namespace: str) -> str:
    return f"Famulus:{namespace}"

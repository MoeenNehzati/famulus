"""Structured dispatcher failures: every raise site produces a typed error
with a stable machine-readable code and safe context, never raw credentials
or tracebacks in the payload. Every class here remains an InvocationError
subclass so existing `except InvocationError` handlers (including
script_dispatcher's re-export) keep working unchanged.

`InvocationError` itself lives here (not in `core.py`) because it has no
dependency on anything in `core.py`, and `core.py` needs to import it (and
the typed subclasses below) to raise them -- defining it here lets that be
one ordinary top-of-file import in `core.py`, with no import cycle."""
from __future__ import annotations

SCHEMA_VERSION = 1


class InvocationError(Exception):
    """Raised when a dispatcher request is invalid."""


class DispatcherError(InvocationError):
    """Base of every structured dispatcher failure.

    Carries a stable machine-readable `code` plus safe `caller_module_id`/
    `target_module_id` context. `as_payload()` renders a flat, JSON-safe
    dict suitable for `--error-format json`; it never includes argv, stdin,
    environment values, credentials, or tracebacks.
    """

    code = "dispatcher.error"

    def __init__(
        self,
        message: str,
        *,
        caller_module_id: str = "",
        target_module_id: str = "",
    ) -> None:
        super().__init__(message)
        self.caller_module_id = caller_module_id
        self.target_module_id = target_module_id

    def as_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "code": self.code,
            "caller_module_id": self.caller_module_id,
            "target_module_id": self.target_module_id,
            "message": str(self),
        }


class InvalidRequestError(DispatcherError):
    """The caller-supplied request shape itself is malformed."""

    code = "dispatcher.invalid_request"


class BlueprintInvalidError(DispatcherError):
    """A blueprint YAML file or the repository blueprint graph is invalid."""

    code = "dispatcher.blueprint_invalid"


class ModuleNotCallableError(DispatcherError):
    """The requested target resolves to a module id, not a callable export."""

    code = "dispatcher.module_not_callable"


class CallerNotFoundError(DispatcherError):
    """The declared caller module does not exist in the blueprint graph."""

    code = "dispatcher.caller_not_found"


class InterfaceUseUndeclaredError(DispatcherError):
    """The caller does not declare use of the exact interface/version."""

    code = "dispatcher.interface_use_undeclared"


class ExportAccessMissingError(DispatcherError):
    """The target export is missing its `access` declaration."""

    code = "dispatcher.export_access_missing"


class UnauthorizedCallerError(DispatcherError):
    """The caller is not an allowed caller of the target interface."""

    code = "dispatcher.unauthorized_caller"

    def __init__(
        self,
        *,
        caller_module_id: str,
        target_module_id: str,
        interface_id: str,
    ) -> None:
        self.interface_id = interface_id
        super().__init__(
            f"{caller_module_id} is not an allowed caller of {interface_id}",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
        )

    def as_payload(self) -> dict:
        payload = super().as_payload()
        payload["interface_id"] = self.interface_id
        return payload


class CertificationRejectedError(DispatcherError):
    """Certification review rejected the resolved export."""

    code = "dispatcher.certification_rejected"


class ResolutionFailedError(DispatcherError):
    """Export resolution or invocation compilation failed."""

    code = "dispatcher.resolution_failed"


class UnsupportedLanguageError(DispatcherError):
    """The target's process binding declares an unsupported gateway language."""

    code = "dispatcher.unsupported_language"


class RuntimeMisconfiguredError(DispatcherError):
    """The target's Python process binding is missing a gateway or entry."""

    code = "dispatcher.runtime_misconfigured"


class GatewayOutsideModuleError(DispatcherError):
    """The resolved gateway path escapes its owning module."""

    code = "dispatcher.gateway_outside_module"


class RuntimeInvalidError(DispatcherError):
    """The Python runtime could not be built for the resolved source."""

    code = "dispatcher.runtime_invalid"


class LaunchFailedError(DispatcherError):
    """The resolved command failed to launch as a subprocess."""

    code = "dispatcher.launch_failed"


class InterfaceNotFoundError(DispatcherError):
    """No export matches the requested target interface id."""

    code = "dispatcher.interface_not_found"

    def __init__(
        self,
        *,
        caller_module_id: str,
        target_module_id: str,
        interface_id: str,
    ) -> None:
        self.interface_id = interface_id
        super().__init__(
            f"interface not found: {interface_id} (requested by {caller_module_id})",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
        )

    def as_payload(self) -> dict:
        payload = super().as_payload()
        payload["interface_id"] = self.interface_id
        return payload


__all__ = [
    "SCHEMA_VERSION",
    "InvocationError",
    "DispatcherError",
    "InvalidRequestError",
    "BlueprintInvalidError",
    "ModuleNotCallableError",
    "CallerNotFoundError",
    "InterfaceUseUndeclaredError",
    "ExportAccessMissingError",
    "UnauthorizedCallerError",
    "CertificationRejectedError",
    "ResolutionFailedError",
    "UnsupportedLanguageError",
    "RuntimeMisconfiguredError",
    "GatewayOutsideModuleError",
    "RuntimeInvalidError",
    "LaunchFailedError",
    "InterfaceNotFoundError",
]

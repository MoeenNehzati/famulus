"""Public installation API aggregation."""

from importlib import import_module

from .context import (
    build_development_environment,
    DevelopmentBoundaryError,
    InstallationContext,
    InvalidInstallationContextError,
    installation_context_home_fields,
    load_or_create_development_installation_id,
    resolve_installation_context,
    resolve_stable_roots,
    validate_development_boundaries,
)
from .assistant_access import AssistantAccessBoundaryError, resolve_assistant_access_roots
from .managed_runtime import deployed_resolver_trusted_roots
from .runtime_pointer import (
    InstalledContextRecord,
    RuntimePointer,
    RuntimePointerError,
    _require_contained_or_trusted,
    activate_release,
    load_current_pointer,
    load_installed_context_record,
)

__all__ = [
    "AssistantAccessBoundaryError",
    "DevelopmentBoundaryError",
    "DiagnosticCheck",
    "DiagnosticReport",
    "InstalledContextRecord",
    "InstallationContext",
    "InvalidInstallationContextError",
    "RuntimePointer",
    "RuntimePointerError",
    "activate_release",
    "build_development_environment",
    "deployed_resolver_trusted_roots",
    "diagnose_installation",
    "installation_context_home_fields",
    "load_current_pointer",
    "load_installed_context_record",
    "load_or_create_development_installation_id",
    "resolve_installation_context",
    "resolve_assistant_access_roots",
    "render_diagnostic_json",
    "render_diagnostic_text",
    "resolve_stable_roots",
    "validate_development_boundaries",
]

_LAZY_EXPORTS = {
}

for _doctor_name in (
    "DiagnosticCheck",
    "DiagnosticReport",
    "diagnose_installation",
    "render_diagnostic_json",
    "render_diagnostic_text",
):
    _LAZY_EXPORTS[_doctor_name] = (".doctor", _doctor_name)


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value

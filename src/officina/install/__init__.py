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
    "ActivationError",
    "DevelopmentBoundaryError",
    "DiagnosticCheck",
    "DiagnosticReport",
    "InstalledContextRecord",
    "InstallationContext",
    "InvalidInstallationContextError",
    "RuntimePointer",
    "RuntimePointerError",
    "activate_release",
    "build_interactive_environment",
    "build_development_environment",
    "development_activation_main",
    "deployed_resolver_trusted_roots",
    "diagnose_installation",
    "install_development_activation",
    "installation_context_home_fields",
    "load_current_pointer",
    "load_installed_context_record",
    "load_or_create_development_installation_id",
    "resolve_installation_context",
    "render_diagnostic_json",
    "render_diagnostic_text",
    "resolve_stable_roots",
    "validate_development_boundaries",
    "verify_managed_commands",
]

_LAZY_EXPORTS = {
    "ActivationError": (".development_activation", "ActivationError"),
    "build_interactive_environment": (
        ".development_activation",
        "build_interactive_environment",
    ),
    "development_activation_main": (".development_activation", "main"),
    "install_development_activation": (
        ".development_activation",
        "install_development_activation",
    ),
    "verify_managed_commands": (
        ".development_activation",
        "verify_managed_commands",
    ),
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

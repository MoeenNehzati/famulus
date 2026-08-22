"""Public installation API aggregation."""

from .context import (
    DevelopmentBoundaryError,
    InstallationContext,
    InvalidInstallationContextError,
    installation_context_home_fields,
    load_or_create_development_installation_id,
    resolve_installation_context,
    resolve_stable_roots,
    validate_development_boundaries,
)
from .development_activation import (
    ActivationError,
    build_interactive_environment,
    install_development_activation,
    main as development_activation_main,
    verify_managed_commands,
)
from .doctor import (
    DiagnosticCheck,
    DiagnosticReport,
    diagnose_installation,
    render_diagnostic_json,
    render_diagnostic_text,
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

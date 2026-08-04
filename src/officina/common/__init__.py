"""Shared first-party packages and certification infrastructure.

Import concrete helpers from their owning submodules. Legacy root-level exports
remain available lazily so importing one common submodule does not execute
unrelated docstring or visualization stacks.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_EXPORTS = {
    "AuthorizationRequest": (".blueprint_authorization", "AuthorizationRequest"),
    "AuthorizationResult": (".blueprint_authorization", "AuthorizationResult"),
    "resolve_interface_authorization": (
        ".blueprint_authorization",
        "resolve_interface_authorization",
    ),
    "BlueprintDocument": (".blueprint_inventory", "BlueprintDocument"),
    "BlueprintInventoryError": (".blueprint_inventory", "BlueprintInventoryError"),
    "BlueprintInventoryIssue": (".blueprint_inventory", "BlueprintInventoryIssue"),
    "BlueprintInventoryResult": (".blueprint_inventory", "BlueprintInventoryResult"),
    "collect_blueprints": (".blueprint_inventory", "collect_blueprints"),
    "iter_blueprints": (".blueprint_inventory", "iter_blueprints"),
    "ParserIssue": (".docstring", "ParserIssue"),
    "PipelineSpec": (".docstring", "PipelineSpec"),
    "FunctionSpec": (".docstring", "FunctionSpec"),
    "CallableDocstringSchema": (".docstring", "CallableDocstringSchema"),
    "OwnershipConfig": (".docstring", "OwnershipConfig"),
    "ModuleOwnershipConfig": (".docstring", "ModuleOwnershipConfig"),
    "DocstringSchema": (".docstring", "DocstringSchema"),
    "PipelineDocstringSchema": (".docstring", "PipelineDocstringSchema"),
    "ModuleDocstringSchema": (".docstring", "ModuleDocstringSchema"),
    "check": (".docstring", "check"),
    "check_graph_docstring": (".docstring", "check_graph_docstring"),
    "check_pipeline_docstring": (".docstring", "check_pipeline_docstring"),
    "parse_function_graphs": (".docstring", "parse_function_graphs"),
    "parse_graph_block": (".docstring", "parse_graph_block"),
    "parse_ownership_reference": (".docstring", "parse_ownership_reference"),
    "parse_ownable_registry": (".docstring", "parse_ownable_registry"),
    "parse_pipeline": (".docstring", "parse_pipeline"),
    "resolve_docstring_schema_path": (".docstring", "resolve_docstring_schema_path"),
    "load_docstring_schema": (".docstring", "load_docstring_schema"),
    "validate_edge_expression": (".docstring", "validate_edge_expression"),
    "validate_pipeline_docstring": (".docstring", "validate_pipeline_docstring"),
    "build_docstring_graph": (".visualization", "build_docstring_graph"),
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load a compatibility export only when a caller requests it."""

    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy compatibility names to interactive discovery."""

    return sorted({*globals(), *__all__})

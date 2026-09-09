"""Structured dispatcher failures: every raise site produces a typed error
with a stable machine-readable code and safe context, never raw credentials
or tracebacks in the payload. Every class here remains an InvocationError
subclass so existing Officina Dispatcher `except InvocationError` handlers
keep working unchanged.

`InvocationError` itself lives here (not in `core.py`) because it has no
dependency on anything in `core.py`, and `core.py` needs to import it (and
the typed subclasses below) to raise them -- defining it here lets that be
one ordinary top-of-file import in `core.py`, with no import cycle."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import AbstractSet, Mapping

SCHEMA_VERSION = 1


def _allowed_values(**fields: AbstractSet[object]) -> Mapping[str, frozenset[object]]:
    return MappingProxyType({name: frozenset(values) for name, values in fields.items()})


_RUNNER_PRIVATE_OPTIONS = frozenset(
    {
        "--source-fd",
        "--diagnostic-writer",
        "--package-file",
        "--package-snapshot",
        "--package-snapshot-sha256",
        "--logical-package",
        "--logical-entrypoint",
        "--physical-package-prefix",
        "--runtime-caller-module-id",
        "--runtime-caller-source-id",
        "--runtime-repo-root",
        "--runtime-repository-config",
        "--confined-module-root",
    }
)


@dataclass(frozen=True)
class ErrorSpec:
    """One closed, safe public dispatcher-error contract."""

    code: str
    message: str
    context_fields: frozenset[str] = frozenset()
    payload_fields: frozenset[str] = frozenset()
    identity_fields: frozenset[str] = frozenset()
    allowed_context_values: Mapping[str, frozenset[object]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    context_value_templates: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    allow_cause: bool = False
    allow_external_cause: bool = False
    allowed_clues: frozenset[str] = frozenset()


@dataclass(frozen=True, init=False)
class ReducedCause:
    """A registry-validated cross-boundary cause with no nested envelope."""

    code: str
    message: str
    context: Mapping[str, object]
    clues: tuple[str, ...] = ()

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ReducedCause must be constructed by its validated factory")

    @classmethod
    def _from_allowed(
        cls,
        code: str,
        message: str,
        *,
        clues: tuple[str, ...] = (),
        allowed_messages: Mapping[str, AbstractSet[str]],
        allowed_clues: Mapping[str, str],
    ) -> ReducedCause:
        """Validate one diagnosis against an external owner's closed policy."""

        if message not in allowed_messages.get(code, ()):
            raise ValueError("unregistered external cause")
        allowed_clue = allowed_clues.get(code)
        if clues not in ((), (allowed_clue,) if allowed_clue is not None else ()):
            raise ValueError("unregistered external cause clue")
        cause = object.__new__(cls)
        object.__setattr__(cause, "code", code)
        object.__setattr__(cause, "message", message)
        object.__setattr__(cause, "context", MappingProxyType({}))
        object.__setattr__(cause, "clues", tuple(clues))
        return cause

    def __str__(self) -> str:
        return self.message

    def as_payload(self) -> dict[str, object]:
        payload = {"code": self.code, "message": self.message, **self.context}
        if self.clues:
            payload["clues"] = list(self.clues)
        return payload


DISPATCHER_ERROR_SPECS: Mapping[str, ErrorSpec] = MappingProxyType(
    {
        "D01": ErrorSpec(
            code="dispatcher.blueprint_schema_mismatch",
            message=(
                "Dispatcher metadata trace requires blueprint graph schema 6; "
                "received schema {schema_version}."
            ),
            context_fields=frozenset({"schema_version"}),
            payload_fields=frozenset({"schema_version"}),
        ),
        "D02": ErrorSpec(
            code="dispatcher.caller_not_found",
            message="The dispatcher caller module does not exist.",
        ),
        "D03": ErrorSpec(
            code="dispatcher.interface_use_undeclared",
            message=(
                "Caller module `{caller_module_id}` does not declare use of "
                "`{interface_id}`."
            ),
            context_fields=frozenset({"caller_module_id", "interface_id"}),
            payload_fields=frozenset({"interface_id"}),
        ),
        "D04": ErrorSpec(
            code="dispatcher.unauthorized_caller",
            message="Caller `{caller_module_id}` is not allowed to invoke `{interface_id}`.",
            context_fields=frozenset({"caller_module_id", "interface_id", "gate"}),
            payload_fields=frozenset({"interface_id", "gate"}),
            allowed_context_values=_allowed_values(
                gate={"namespace-route", "namespace-interface", "terminal-export"}
            ),
        ),
        "D05": ErrorSpec(
            code="dispatcher.invalid_request",
            message="`caller_skill` must be a nonempty string.",
        ),
        "D06": ErrorSpec(
            code="dispatcher.repository_config_missing",
            message=(
                "The dispatcher invocation did not supply the required "
                "repository configuration path."
            ),
        ),
        "D07": ErrorSpec(
            code="dispatcher.repository_config_invalid",
            message="The dispatcher could not load the supplied repository configuration.",
        ),
        "D08": ErrorSpec(
            code="dispatcher.python_target_missing",
            message="Resolved route `{interface_id}` has no Python process target.",
            context_fields=frozenset({"interface_id"}),
            payload_fields=frozenset({"interface_id"}),
        ),
        "D09": ErrorSpec(
            code="dispatcher.python_identity_missing",
            message=(
                "Resolved Python target for `{interface_id}` lacks a logical "
                "package or entry point."
            ),
            context_fields=frozenset({"interface_id"}),
            payload_fields=frozenset({"interface_id"}),
        ),
        "D10": ErrorSpec(
            code="dispatcher.launch_failed",
            message="The dispatcher could not start `{interface_id}`.",
            context_fields=frozenset({"interface_id"}),
            payload_fields=frozenset({"interface_id"}),
        ),
        "D11": ErrorSpec(
            code="dispatcher.invalid_module_id",
            message="The dispatcher request contains an invalid module ID.",
        ),
        "D12": ErrorSpec(
            code="dispatcher.invalid_interface_id",
            message="The dispatcher request contains an invalid interface ID.",
        ),
        "D13": ErrorSpec(
            code="dispatcher.module_not_found",
            message="Module not found: `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D14": ErrorSpec(
            code="dispatcher.module_ambiguous",
            message="Module `{module_id}` is present in multiple configured roots.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D15": ErrorSpec(
            code="dispatcher.unsafe_blueprint_path",
            message="The dispatcher could not inspect the blueprint path for module `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D16": ErrorSpec(
            code="dispatcher.unsafe_blueprint_path",
            message="The blueprint path for module `{module_id}` contains a symbolic link.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D17": ErrorSpec(
            code="dispatcher.unsafe_blueprint_path",
            message="The blueprint for module `{module_id}` is not a regular file.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D18": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="The blueprint for module `{module_id}` could not be read as YAML.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D19": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="The blueprint for module `{module_id}` must be a mapping.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D20": ErrorSpec(
            code="dispatcher.blueprint_schema_mismatch",
            message=(
                "The blueprint for module `{module_id}` must be a schema-6 "
                "module blueprint."
            ),
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D21": ErrorSpec(
            code="dispatcher.blueprint_identity_mismatch",
            message="Blueprint identity does not match registered module `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D22": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="Blueprint version must be a positive integer for module `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D23": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="Blueprint field `{field}` must be a mapping for module `{module_id}`.",
            context_fields=frozenset({"field", "module_id"}),
            payload_fields=frozenset({"field", "module_id"}),
        ),
        "D24": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="Blueprint child registrations are invalid for module `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D25": ErrorSpec(
            code="dispatcher.module_not_found",
            message="Registered module blueprint not found: `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D26": ErrorSpec(
            code="dispatcher.child_unregistered",
            message="Module `{parent_module_id}` does not register child `{child_name}`.",
            context_fields=frozenset({"parent_module_id", "child_name"}),
            payload_fields=frozenset({"parent_module_id", "child_name"}),
        ),
        "D27": ErrorSpec(
            code="dispatcher.invalid_caller_reference",
            message="Relative caller reference is invalid: it {reason}.",
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={"has no local suffix", "escapes its registration root"}
            ),
        ),
        "D28": ErrorSpec(
            code="dispatcher.access_invalid",
            message="Access declaration is missing or invalid for {access_kind} `{interface_id}`.",
            context_fields=frozenset({"access_kind", "interface_id"}),
            payload_fields=frozenset({"access_kind", "interface_id"}),
            allowed_context_values=_allowed_values(
                access_kind={"namespace-route", "namespace-interface", "terminal-export"}
            ),
        ),
        "D29": ErrorSpec(
            code="dispatcher.unsafe_blueprint_path",
            message="The source `{field_name}` is not a safe module-relative path.",
            context_fields=frozenset({"field_name"}),
            payload_fields=frozenset({"field_name"}),
        ),
        "D30": ErrorSpec(
            code="dispatcher.source_not_found",
            message="The dispatcher could not inspect the source path for module `{module_id}`.",
            context_fields=frozenset({"module_id"}),
            payload_fields=frozenset({"module_id"}),
        ),
        "D31": ErrorSpec(
            code="dispatcher.unsafe_blueprint_path",
            message="The source for module `{module_id}` {reason}.",
            context_fields=frozenset({"module_id", "reason"}),
            payload_fields=frozenset({"module_id", "reason"}),
            allowed_context_values=_allowed_values(
                reason={
                    "has a path containing a symbolic link",
                    "is not a regular file",
                }
            ),
        ),
        "D32": ErrorSpec(
            code="dispatcher.source_locator_invalid",
            message="The source locator has {reason}.",
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={"an invalid shape", "an unsupported base"}
            ),
        ),
        "D33": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="The source blueprint could not be read as YAML.",
        ),
        "D34": ErrorSpec(
            code="dispatcher.blueprint_schema_mismatch",
            message="Direct dispatch requires a schema-6 behavioral source blueprint.",
        ),
        "D35": ErrorSpec(
            code="dispatcher.blueprint_identity_mismatch",
            message="Source blueprint identity does not match its registered source.",
        ),
        "D36": ErrorSpec(
            code="dispatcher.blueprint_malformed",
            message="The source blueprint has {reason}.",
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={"no gateway declaration", "an invalid version"}
            ),
        ),
        "D37": ErrorSpec(
            code="dispatcher.host_caller_invalid",
            message=(
                "Host caller `{caller_module_id}` is not a discoverable "
                "top-level skill."
            ),
            context_fields=frozenset({"caller_module_id"}),
        ),
        "D38": ErrorSpec(
            code="dispatcher.interface_not_found",
            message="Interface `{interface_id}` {reason}.",
            context_fields=frozenset(
                {"interface_id", "reason", "target_module_id"}
            ),
            payload_fields=frozenset({"interface_id", "reason"}),
            context_value_templates=MappingProxyType(
                {
                    "reason": frozenset(
                        {"was not found", "is not owned by {target_module_id}"}
                    )
                }
            ),
        ),
        "D39": ErrorSpec(
            code="dispatcher.source_interface_invalid",
            message="Source-interface declaration for `{interface_id}` {reason}.",
            context_fields=frozenset({"interface_id", "reason"}),
            payload_fields=frozenset({"interface_id", "reason"}),
            allowed_context_values=_allowed_values(
                reason={
                    "is invalid",
                    "is absent",
                    "has the wrong owner",
                    "has an invalid version",
                }
            ),
        ),
        "D40": ErrorSpec(
            code="dispatcher.interface_version_mismatch",
            message=(
                "Version mismatch for `{interface_id}`: requested "
                "{requested_version}, available {available_version}."
            ),
            context_fields=frozenset(
                {"interface_id", "requested_version", "available_version"}
            ),
            payload_fields=frozenset(
                {"interface_id", "requested_version", "available_version"}
            ),
        ),
        "D41": ErrorSpec(
            code="dispatcher.namespace_route_missing",
            message="Namespace export `{route_owner_module_id}->{child_segment}` is missing.",
            context_fields=frozenset({"route_owner_module_id", "child_segment"}),
            payload_fields=frozenset({"route_owner_module_id", "child_segment"}),
        ),
        "D42": ErrorSpec(
            code="dispatcher.namespace_version_mismatch",
            message="Namespace version does not match child `{child_module_id}`.",
            context_fields=frozenset({"child_module_id"}),
            payload_fields=frozenset({"child_module_id"}),
        ),
        "D43": ErrorSpec(
            code="dispatcher.namespace_surface_excludes_interface",
            message="Namespace surface excludes `{interface_id}@{interface_version}`.",
            context_fields=frozenset({"interface_id", "interface_version"}),
            payload_fields=frozenset({"interface_id", "interface_version"}),
        ),
        "D44": ErrorSpec(
            code="dispatcher.access_invalid",
            message=(
                "Namespace route `{route_owner_module_id}` has invalid "
                "`interface_access`."
            ),
            context_fields=frozenset({"route_owner_module_id"}),
            payload_fields=frozenset({"route_owner_module_id"}),
        ),
        "D45": ErrorSpec(
            code="dispatcher.resolution_failed",
            message="The dispatcher could not compile arguments for `{interface_id}`: {detail}.",
            context_fields=frozenset({"interface_id", "detail"}),
            payload_fields=frozenset({"interface_id"}),
            identity_fields=frozenset({"caller_module_id", "target_module_id"}),
        ),
        "D46": ErrorSpec(
            code="dispatcher.resolution_failed",
            message="The dispatcher could not construct the Python target for `{interface_id}`.",
            context_fields=frozenset({"interface_id"}),
            payload_fields=frozenset({"interface_id"}),
            identity_fields=frozenset({"caller_module_id", "target_module_id"}),
        ),
        "D47": ErrorSpec(
            code="dispatcher.invalid_request",
            message=(
                "Target must be a fully qualified "
                "`<module>.interface.<name>` export."
            ),
        ),
        "D48": ErrorSpec(
            code="dispatcher.invalid_request",
            message=(
                "The dispatcher CLI request does not match the declared "
                "command-line signature."
            ),
        ),
        "D49": ErrorSpec(
            code="dispatcher.mcp_python_unsupported",
            message=(
                "Famulus MCP startup requires Python 3.11 or newer; running "
                "{major}.{minor}."
            ),
            context_fields=frozenset({"major", "minor"}),
            payload_fields=frozenset({"major", "minor"}),
        ),
        "D50": ErrorSpec(
            code="dispatcher.mcp_persistence_invalid",
            message="Famulus MCP startup rejected an unsafe plugin-data {kind}.",
            context_fields=frozenset({"kind"}),
            payload_fields=frozenset({"kind"}),
        ),
        "D51": ErrorSpec(
            code="dispatcher.mcp_persistence_invalid",
            message="Famulus MCP startup could not initialize plugin persistence.",
        ),
        "D52": ErrorSpec(
            code="dispatcher.mcp_package_unavailable",
            message=(
                "Famulus MCP startup could not import its declared server "
                "package `{module_name}`."
            ),
            context_fields=frozenset({"module_name"}),
            payload_fields=frozenset({"module_name"}),
        ),
        "D53": ErrorSpec(
            code="dispatcher.mcp_server_failed",
            message="Famulus MCP server initialization or execution failed.",
        ),
        "D54": ErrorSpec(
            code="dispatcher.invalid_request",
            message="MCP ordered arguments require `positionals=[]`.",
        ),
        "D55": ErrorSpec(
            code="dispatcher.invalid_request",
            message=(
                "MCP compact option values must be strings or `true`, not lists."
            ),
        ),
        "D56": ErrorSpec(
            code="dispatcher.manager_invocation_failed",
            message=(
                "The dispatcher could not obtain a valid `{operation}` result "
                "from the setup manager."
            ),
            context_fields=frozenset({"operation"}),
            allow_cause=True,
        ),
        "D57": ErrorSpec(
            code="dispatcher.manager_response_malformed",
            message="The setup manager returned no valid JSON object for `{operation}`.",
            context_fields=frozenset({"operation"}),
        ),
        "D58": ErrorSpec(
            code="dispatcher.manager_response_invalid",
            message=(
                "The setup manager `{operation}` response is invalid for that "
                "operation or process status."
            ),
            context_fields=frozenset({"operation"}),
        ),
        "D59": ErrorSpec(
            code="dispatcher.manager_response_invalid",
            message=(
                "The setup manager `status` response contains an invalid "
                "`pending_stack`."
            ),
        ),
        "D60": ErrorSpec(
            code="dispatcher.manager_response_invalid",
            message=(
                "The setup manager `authorize` response did not confirm "
                "`state=ready` and `resume_original=true`."
            ),
        ),
        "D61": ErrorSpec(
            code="dispatcher.manager_response_invalid",
            message=(
                "The setup manager `status` result `setup_required` lacked a "
                "nonempty root or pending stack."
            ),
        ),
        "D62": ErrorSpec(
            code="dispatcher.manager_response_invalid",
            message=(
                "The setup manager `status` result `setup_busy` lacked a "
                "nonempty `flow_id`."
            ),
        ),
        "D63": ErrorSpec(
            code="dispatcher.manager_response_invalid",
            message="The setup manager `status` response contains an unsupported code.",
        ),
        "D64": ErrorSpec(
            code="dispatcher.manager_operation_failed",
            message="The setup manager `{operation}` failed: {setup_error}",
            context_fields=frozenset(
                {"operation", "setup_error", "setup_error_code"}
            ),
            payload_fields=frozenset({"setup_error_code"}),
            allow_cause=True,
            allow_external_cause=True,
            allowed_clues=frozenset(
                {
                    "Another setup-manager process may have updated the ledger concurrently.",
                    "Another setup-manager operation may have changed the managed state concurrently.",
                }
            ),
        ),
        "D65": ErrorSpec(
            code="dispatcher.invalid_request",
            message=(
                "MCP request field `setup_flow_id` cannot be used with `dry_run` "
                "or a setup-manager target."
            ),
        ),
        "D66": ErrorSpec(
            code="dispatcher.setup_projection_unavailable",
            message=(
                "MCP preflight could not evaluate the managed-setup projection "
                "for `{interface_id}`."
            ),
            context_fields=frozenset({"interface_id"}),
            payload_fields=frozenset({"interface_id"}),
            allow_cause=True,
        ),
        "D68": ErrorSpec(
            code="dispatcher.error",
            message="The dispatcher request failed at an unclassified invocation boundary.",
        ),
        "D69": ErrorSpec(
            code="dispatcher.execution_timeout",
            message="The dispatched process for `{interface_id}` exceeded its timeout; completion is unknown.",
            context_fields=frozenset({"interface_id", "timeout"}),
            payload_fields=frozenset({"interface_id", "timeout"}),
        ),
        "D70": ErrorSpec(
            code="dispatcher.checked_process_failed",
            message=(
                "The dispatched process for `{interface_id}` returned nonzero "
                "process status {returncode}; checked execution treated the result as an error."
            ),
            context_fields=frozenset({"interface_id", "returncode"}),
            payload_fields=frozenset({"interface_id", "returncode"}),
        ),
        "D71": ErrorSpec(
            code="dispatcher.output_decode_failed",
            message="The dispatcher could not decode captured output from `{interface_id}` as text.",
            context_fields=frozenset({"interface_id"}),
            payload_fields=frozenset({"interface_id"}),
        ),
        "R01": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message="Python interface runner requires a gateway path and process entry.",
        ),
        "R02": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner option `{option}` is missing required "
                "arguments."
            ),
            context_fields=frozenset({"option"}),
            payload_fields=frozenset({"option"}),
            allowed_context_values=_allowed_values(option=_RUNNER_PRIVATE_OPTIONS),
        ),
        "R03": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner option `{option}` was supplied more "
                "than once."
            ),
            context_fields=frozenset({"option"}),
            payload_fields=frozenset({"option"}),
            allowed_context_values=_allowed_values(option=_RUNNER_PRIVATE_OPTIONS),
        ),
        "R04": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner option `{option}` requires an integer "
                "descriptor."
            ),
            context_fields=frozenset({"option"}),
            payload_fields=frozenset({"option"}),
            allowed_context_values=_allowed_values(
                option={"--source-fd", "--diagnostic-writer", "--package-file"}
            ),
        ),
        "R05": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner requires package snapshot path and "
                "SHA-256 together."
            ),
        ),
        "R06": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner requires logical package and entry "
                "point together."
            ),
        ),
        "R07": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner physical package prefix is invalid "
                "for this request."
            ),
        ),
        "R08": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message=(
                "Python interface runner cannot combine package snapshot and "
                "descriptor transports."
            ),
        ),
        "R09": ErrorSpec(
            code="dispatcher.runner_request_invalid",
            message="Python interface runner confined-root request is inconsistent.",
        ),
        "R10": ErrorSpec(
            code="dispatcher.runner_target_invalid",
            message=(
                "Python interface runner received invalid gateway or "
                "process-entry metadata."
            ),
        ),
        "R11": ErrorSpec(
            code="dispatcher.runner_source_invalid",
            message="Python interface runner received an invalid bound package source.",
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={
                    "path-invalid",
                    "source-unreadable",
                    "outside-physical-root",
                    "duplicate-module",
                }
            ),
        ),
        "R12": ErrorSpec(
            code="dispatcher.runner_snapshot_invalid",
            message="Python interface runner rejected its package snapshot: {reason}.",
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={
                    "the expected digest is invalid",
                    "the snapshot file could not be read safely",
                    "the snapshot digest does not match",
                    "the snapshot payload is invalid",
                }
            ),
        ),
        "R13": ErrorSpec(
            code="dispatcher.runner_source_invalid",
            message=(
                "Python interface runner could not load the gateway source "
                "within its validated package boundary."
            ),
        ),
        "R14": ErrorSpec(
            code="dispatcher.runner_import_rejected",
            message="Python interface runner rejected a confined import: {reason}.",
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={
                    "the module is outside the validated package",
                    "the import escaped the confined module root",
                    "the import path could not be inspected",
                    "the import path contains a symbolic link",
                    "the module name is invalid",
                    "the module is ambiguous",
                    "the import source could not be read safely",
                    "the gateway mutated the import search path",
                }
            ),
        ),
        "R15": ErrorSpec(
            code="dispatcher.runner_import_failed",
            message="Python interface runner could not load the resolved gateway module.",
        ),
        "R16": ErrorSpec(
            code="dispatcher.runner_interface_invalid",
            message=(
                "Resolved Python gateway has an invalid machine-interface "
                "entry: {reason}."
            ),
            context_fields=frozenset({"reason"}),
            payload_fields=frozenset({"reason"}),
            allowed_context_values=_allowed_values(
                reason={"the entry is absent", "the entry has the wrong type"}
            ),
        ),
        "R17": ErrorSpec(
            code="dispatcher.invalid_request",
            message=(
                "The Python interface request does not match the declared "
                "interface signature."
            ),
        ),
        "R18": ErrorSpec(
            code="dispatcher.runner_route_smoke_failed",
            message="Python machine-interface route-smoke execution failed.",
        ),
        "R19": ErrorSpec(
            code="dispatcher.runner_execution_failed",
            message=(
                "Python machine-interface execution failed before returning "
                "an exit code; completion is unknown."
            ),
        ),
        "R20": ErrorSpec(
            code="dispatcher.runner_interface_invalid",
            message="Python machine interface returned an unsupported result type.",
        ),
        "R21": ErrorSpec(
            code="dispatcher.runner_source_invalid",
            message=(
                "Python interface runner could not construct a validated "
                "package-source snapshot."
            ),
        ),
        "R22": ErrorSpec(
            code="dispatcher.runner_target_invalid",
            message=(
                "Python interface runner received a confined gateway without "
                "a logical entry point."
            ),
        ),
        "R23": ErrorSpec(
            code="dispatcher.runner_interface_initialization_failed",
            message=(
                "Python interface constructor failed before request handling began."
            ),
        ),
        "R24": ErrorSpec(
            code="dispatcher.runner_interface_initialization_failed",
            message=(
                "Python machine interface did not provide a valid argument parser."
            ),
        ),
        "R25": ErrorSpec(
            code="dispatcher.runner_request_validation_failed",
            message=(
                "Python machine interface failed while validating request arguments."
            ),
        ),
    }
)


class SetupBlocked(BaseException):
    def __init__(self, status, call_path, lifecycle=None):
        self.status = status
        self.call_path = tuple(call_path)
        self.lifecycle = lifecycle


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
        raise TypeError("DispatcherError must be constructed from a registered spec")

    @classmethod
    def from_spec(
        cls,
        entry_id: str,
        *,
        caller_module_id: str = "",
        target_module_id: str = "",
        cause: DispatcherError | ReducedCause | None = None,
        clues: tuple[str, ...] = (),
        **context: object,
    ) -> DispatcherError:
        spec = DISPATCHER_ERROR_SPECS[entry_id]
        unsupported = set(context) - spec.context_fields
        if unsupported:
            raise ValueError(
                "unsupported context for "
                f"{entry_id}: {', '.join(sorted(unsupported))}"
            )
        format_context = dict(context)
        if "caller_module_id" in spec.context_fields:
            format_context["caller_module_id"] = caller_module_id
        if "target_module_id" in spec.context_fields:
            format_context["target_module_id"] = target_module_id
        identities = {
            "caller_module_id": caller_module_id,
            "target_module_id": target_module_id,
        }
        for field_name in spec.context_fields | spec.identity_fields:
            if field_name in identities and not isinstance(identities[field_name], str):
                raise ValueError(
                    f"{entry_id} received an invalid value type for {field_name}"
                )
        missing = spec.context_fields - format_context.keys()
        if missing:
            raise ValueError(
                f"missing context for {entry_id}: {', '.join(sorted(missing))}"
            )
        integer_fields = {
            "available_version", "interface_version", "major", "minor",
            "returncode", "schema_version",
        }
        for field_name, value in format_context.items():
            valid = (
                (type(value) is int or field_name == "requested_version" and value == "invalid")
                if field_name == "requested_version"
                else type(value) is int
                if field_name in integer_fields
                else type(value) in {int, float} and value >= 0
                if field_name == "timeout"
                else isinstance(value, str) and bool(value)
            )
            if not valid:
                raise ValueError(
                    f"{entry_id} received an invalid value type for {field_name}"
                )
        for field_name, allowed_values in spec.allowed_context_values.items():
            if field_name in context and context[field_name] not in allowed_values:
                raise ValueError(
                    f"{entry_id} received an unregistered value for {field_name}"
                )
        template_context = {**format_context, **identities}
        for field_name, templates in spec.context_value_templates.items():
            allowed_values = {
                template.format(**template_context) for template in templates
            }
            if context.get(field_name) not in allowed_values:
                raise ValueError(
                    f"{entry_id} received an unregistered value for {field_name}"
                )
        if cause is not None:
            if not spec.allow_cause:
                raise ValueError(f"{entry_id} does not permit a cause")
            if not isinstance(cause, (DispatcherError, ReducedCause)):
                raise ValueError(f"{entry_id} requires a registered cause")
            if isinstance(cause, ReducedCause) and not spec.allow_external_cause:
                raise ValueError(f"{entry_id} does not permit an external cause")
            if isinstance(cause, DispatcherError) and cause._entry_id not in DISPATCHER_ERROR_SPECS:
                raise ValueError(f"{entry_id} requires a registered cause")
        if any(not isinstance(clue, str) for clue in clues):
            raise ValueError(f"{entry_id} clues must be strings")
        unsupported_clues = set(clues) - spec.allowed_clues
        if unsupported_clues:
            raise ValueError(f"{entry_id} received an unregistered clue")
        try:
            message = spec.message.format(**format_context)
        except KeyError as exc:
            raise ValueError(f"missing context for {entry_id}: {exc.args[0]}") from exc
        error = cls.__new__(cls)
        InvocationError.__init__(error, message)
        error.caller_module_id = caller_module_id
        error.target_module_id = target_module_id
        error._entry_id = None
        error._spec_context = None
        error.cause = None
        error.clues = ()
        error.code = spec.code
        error._entry_id = entry_id
        error._spec_context = format_context
        error.cause = cause
        error.clues = tuple(clues)
        return error

    def as_payload(self) -> dict:
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "code": self.code,
            "message": str(self),
        }
        if self._spec_context is None:
            payload["caller_module_id"] = self.caller_module_id
            payload["target_module_id"] = self.target_module_id
        elif self._entry_id is not None:
            spec = DISPATCHER_ERROR_SPECS[self._entry_id]
            allowed_identities = spec.context_fields | spec.identity_fields
            if "caller_module_id" in allowed_identities and self.caller_module_id:
                payload["caller_module_id"] = self.caller_module_id
            if "target_module_id" in allowed_identities and self.target_module_id:
                payload["target_module_id"] = self.target_module_id
            for field in spec.payload_fields:
                payload[field] = self._spec_context[field]
        if self.cause is not None:
            reduced = self.cause.as_payload()
            reduced.pop("schema_version", None)
            reduced.pop("cause", None)
            reduced.pop("recovery", None)
            payload["cause"] = reduced
        if self.clues:
            payload["clues"] = list(self.clues)
        return payload


def render_dispatcher_error(error: InvocationError) -> tuple[str, ...]:
    """Render one structured error without changing its semantics."""

    lines = (f"error: {error}",)
    if not isinstance(error, DispatcherError):
        return lines
    if error.cause is not None:
        lines += (f"Cause: {error.cause}",)
    if error.clues:
        lines += ("Possible clues:", *(f"- {clue}" for clue in error.clues))
    return lines


class InvalidRequestError(DispatcherError):
    """The caller-supplied request shape itself is malformed."""

    code = "dispatcher.invalid_request"


class BlueprintInvalidError(DispatcherError):
    """A blueprint YAML file or the repository blueprint graph is invalid."""

    code = "dispatcher.blueprint_invalid"


class DirectBlueprintError(DispatcherError):
    """Direct route lookup found invalid or ambiguous relevant state."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("DirectBlueprintError must be constructed from a registered spec")


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

__all__ = [
    "SCHEMA_VERSION",
    "ErrorSpec",
    "ReducedCause",
    "DISPATCHER_ERROR_SPECS",
    "render_dispatcher_error",
    "InvocationError",
    "DispatcherError",
    "InvalidRequestError",
    "BlueprintInvalidError",
    "DirectBlueprintError",
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

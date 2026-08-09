"""Small route-local data models used by the live v6 dispatcher path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from officina.common.blueprint_authorization import AuthorizationResult
    from officina.runtime.python_machine_interface import PythonProcessTarget


@dataclass(frozen=True)
class DirectBlueprintNode:
    """Behavioral source fields needed by the process-binding compiler."""

    node_id: str
    node_type: str
    version: int
    module_root: Path
    blueprint_path: Path
    gateway_path: Path
    declaration: Mapping[str, object]


@dataclass(frozen=True)
class DirectInterfaceExport:
    """Callable export fields needed by the process-binding compiler."""

    interface_id: str
    version: int
    local_name: str
    module_node_id: str
    declaration: Mapping[str, Any]
    source_node_id: str | None = None
    source_interface_id: str | None = None
    export_declaration: Mapping[str, Any] | None = None
    terminal_interface_id: str | None = None
    terminal_module_node_id: str | None = None


@dataclass(frozen=True)
class InvocationDiagnostic:
    """One warning attached to an otherwise valid invocation."""

    severity: str
    code: str
    message: str
    subject: str | None = None

    def __post_init__(self) -> None:
        if self.severity != "warning":
            raise ValueError("resolved invocation diagnostics must be warnings")
        if not self.code or not self.message:
            raise ValueError("invocation diagnostics require code and message")

    def as_payload(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.subject is not None:
            payload["subject"] = self.subject
        return payload


@dataclass(frozen=True)
class ResolvedInvocationMetadata:
    """Descriptor-free result of direct authorization and compilation."""

    caller_module_id: str
    target_module_id: str
    script_interface: str
    target: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool
    python_target: PythonProcessTarget | None = None
    caller_source_id: str | None = None
    terminal_module_id: str | None = None
    implementing_source_id: str | None = None
    authorization: AuthorizationResult | None = None
    schema_version: int = 6
    diagnostics: tuple[InvocationDiagnostic, ...] = ()

    @property
    def caller_skill(self) -> str:
        return self.caller_module_id

    @property
    def target_skill(self) -> str:
        return self.target_module_id

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "script_interface": self.script_interface,
            "target": self.target,
            "pattern": self.pattern,
            "cwd": str(self.cwd),
            "command": list(self.command),
            "stdin": self.stdin,
            "python_target": None,
            "caller_module_id": self.caller_module_id,
            "caller_source_id": self.caller_source_id,
            "target_module_id": self.target_module_id,
            "terminal_module_id": self.terminal_module_id,
            "implementing_source_id": self.implementing_source_id,
        }
        if self.python_target is not None:
            payload["python_target"] = {
                "gateway_path": self.python_target.gateway_path.as_posix(),
                "process_entry": self.python_target.process_entry,
                **(
                    {
                        "logical_package": self.python_target.logical_package,
                        "logical_entrypoint": self.python_target.logical_entrypoint,
                    }
                    if self.python_target.logical_package is not None
                    and self.python_target.logical_entrypoint is not None
                    else {}
                ),
            }
        if self.diagnostics:
            payload["warnings"] = [item.as_payload() for item in self.diagnostics]
        return payload


__all__ = [
    "DirectBlueprintNode",
    "DirectInterfaceExport",
    "InvocationDiagnostic",
    "ResolvedInvocationMetadata",
]

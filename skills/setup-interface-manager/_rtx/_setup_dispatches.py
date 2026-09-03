"""Finite dispatch declarations for the setup lifecycle manager.

Release Task 4 intentionally has no production managed target.  Later tasks may
add reviewed entries to ``PRODUCTION_BINDINGS`` only after their exact setup,
teardown, and verifier exports exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from officina.runtime.python_machine_interface import DispatchCall


GETTER_KEY = "setup-status-path"
GETTER_CALL = DispatchCall(
    caller_module_id="setup-interface-manager._rtx",
    target_module_id="common",
    interface="famulus-paths-get",
    smoke_args=("setup-status",),
)


@dataclass(frozen=True)
class ManagedArgument:
    """One finite JSON-to-argv projection declared for a managed setup action."""

    name: str
    position: int | None = None
    option: str | None = None
    required: bool = False

    def __post_init__(self) -> None:
        if not self.name or (self.position is None) == (self.option is None):
            raise ValueError("managed arguments need one positional or option projection")
        if self.position is not None and self.position < 0:
            raise ValueError("managed positional indexes must be nonnegative")
        if self.option is not None and not self.option.startswith("-"):
            raise ValueError("managed options must be explicit argv flags")


@dataclass(frozen=True)
class ManagedInterfaceBinding:
    """The complete, immutable dispatch boundary for one managed setup export."""

    setup_interface: str
    setup_version: int
    setup_kind: Literal["markdown", "python"]
    setup_dispatch_key: str
    setup_instructions: str
    setup_verifier_interface: str
    setup_verifier_version: int
    setup_verifier_dispatch_key: str
    teardown_interface: str
    teardown_version: int
    teardown_dispatch_key: str
    teardown_instructions: str
    teardown_verifier_interface: str
    teardown_verifier_version: int
    teardown_verifier_dispatch_key: str
    arguments: tuple[ManagedArgument, ...] = ()

    def __post_init__(self) -> None:
        identifiers = (
            self.setup_interface,
            self.setup_dispatch_key,
            self.setup_verifier_interface,
            self.setup_verifier_dispatch_key,
            self.teardown_interface,
            self.teardown_dispatch_key,
            self.teardown_verifier_interface,
            self.teardown_verifier_dispatch_key,
        )
        if any(not value for value in identifiers):
            raise ValueError("managed dispatch identifiers must be nonempty")
        if self.setup_kind not in {"markdown", "python"}:
            raise ValueError("managed setup kind must be markdown or python")
        if self.setup_version < 1 or self.teardown_version < 1:
            raise ValueError("managed action versions must be positive")
        if self.setup_verifier_version < 1 or self.teardown_verifier_version < 1:
            raise ValueError("managed verifier versions must be positive")
        names = [argument.name for argument in self.arguments]
        positions = [
            argument.position
            for argument in self.arguments
            if argument.position is not None
        ]
        if len(names) != len(set(names)) or len(positions) != len(set(positions)):
            raise ValueError("managed argument names and positions must be unique")
        if positions and sorted(positions) != list(range(max(positions) + 1)):
            raise ValueError("managed positional arguments must be contiguous")


_WAKEUP_SETUP = "llm-wakeup._rtx.interface.setup"
_WAKEUP = ManagedInterfaceBinding(
    setup_interface=_WAKEUP_SETUP,
    setup_version=1,
    setup_kind="python",
    setup_dispatch_key="wakeup-setup",
    setup_instructions=(
        "Install the feature-owned wakeup commands and the due-delivery "
        "registration from the selected interpreter and plugin root."
    ),
    setup_verifier_interface="llm-wakeup._rtx.interface.setup-status",
    setup_verifier_version=1,
    setup_verifier_dispatch_key="wakeup-setup-status",
    teardown_interface="llm-wakeup._rtx.interface.teardown",
    teardown_version=1,
    teardown_dispatch_key="wakeup-teardown",
    teardown_instructions=(
        "Remove the feature-owned wakeup commands and the due-delivery "
        "registration owned by this feature."
    ),
    teardown_verifier_interface="llm-wakeup._rtx.interface.teardown-status",
    teardown_verifier_version=1,
    teardown_verifier_dispatch_key="wakeup-teardown-status",
)


def _wakeup_call(interface: str) -> DispatchCall:
    """Return one reviewed argument-free dispatch into the wakeup feature."""

    return DispatchCall(
        caller_module_id="setup-interface-manager._rtx",
        target_module_id="llm-wakeup._rtx",
        interface=interface,
        smoke_args=(),
    )


PRODUCTION_BINDINGS: Mapping[str, ManagedInterfaceBinding] = MappingProxyType(
    {_WAKEUP_SETUP: _WAKEUP}
)
PRODUCTION_ACTION_CALLS: Mapping[str, DispatchCall] = MappingProxyType(
    {
        "wakeup-setup": _wakeup_call("setup"),
        "wakeup-setup-status": _wakeup_call("setup-status"),
        "wakeup-teardown": _wakeup_call("teardown"),
        "wakeup-teardown-status": _wakeup_call("teardown-status"),
    }
)


def production_dispatches(
    *,
    bindings: Mapping[str, ManagedInterfaceBinding] = PRODUCTION_BINDINGS,
    action_calls: Mapping[str, DispatchCall] = PRODUCTION_ACTION_CALLS,
) -> Mapping[str, DispatchCall]:
    """Return the getter plus only statically reviewed production action routes."""
    dispatches = {GETTER_KEY: GETTER_CALL}
    required_action_keys = {
        key
        for binding in bindings.values()
        for key in (
            binding.setup_dispatch_key,
            binding.setup_verifier_dispatch_key,
            binding.teardown_dispatch_key,
            binding.teardown_verifier_dispatch_key,
        )
    }
    if set(action_calls) != required_action_keys:
        missing = sorted(required_action_keys - set(action_calls))
        extra = sorted(set(action_calls) - required_action_keys)
        raise ValueError(
            "finite production action dispatches do not match bindings: "
            f"missing={missing}, extra={extra}"
        )
    runtime_action_keys = {
        key
        for binding in bindings.values()
        for key in (
            binding.setup_verifier_dispatch_key,
            binding.teardown_verifier_dispatch_key,
            *(
                (binding.setup_dispatch_key, binding.teardown_dispatch_key)
                if binding.setup_kind == "python"
                else ()
            ),
        )
    }
    dispatches.update({key: action_calls[key] for key in runtime_action_keys})
    return MappingProxyType(dispatches)


PRODUCTION_DISPATCHES = production_dispatches()


__all__ = [
    "GETTER_CALL",
    "GETTER_KEY",
    "ManagedArgument",
    "ManagedInterfaceBinding",
    "PRODUCTION_ACTION_CALLS",
    "PRODUCTION_BINDINGS",
    "PRODUCTION_DISPATCHES",
    "production_dispatches",
]

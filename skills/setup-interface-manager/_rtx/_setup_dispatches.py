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
    setup_verifier_interface: str | None
    setup_verifier_version: int | None
    setup_verifier_dispatch_key: str | None
    teardown_interface: str | None
    teardown_version: int | None
    teardown_dispatch_key: str | None
    teardown_instructions: str | None
    teardown_verifier_interface: str | None
    teardown_verifier_version: int | None
    teardown_verifier_dispatch_key: str | None
    arguments: tuple[ManagedArgument, ...] = ()

    def __post_init__(self) -> None:
        # Required setup identifiers
        if not self.setup_interface:
            raise ValueError("managed setup interface must be nonempty")

        # Setup verifier: all present or all absent
        setup_verifier_fields = (
            self.setup_verifier_interface,
            self.setup_verifier_version,
            self.setup_verifier_dispatch_key,
        )
        if not all(setup_verifier_fields) and any(setup_verifier_fields):
            raise ValueError("setup verifier must have interface, version, and dispatch-key all present or all absent")

        # Teardown: interface and version both present or both absent
        if (self.teardown_interface is None) != (self.teardown_version is None):
            raise ValueError("teardown interface and version must be both present or both absent")

        # Teardown verifier: all present or all absent
        teardown_verifier_fields = (
            self.teardown_verifier_interface,
            self.teardown_verifier_version,
            self.teardown_verifier_dispatch_key,
        )
        if not all(teardown_verifier_fields) and any(teardown_verifier_fields):
            raise ValueError("teardown verifier must have interface, version, and dispatch-key all present or all absent")

        # If teardown is absent, verifier and action must also be absent
        if self.teardown_interface is None:
            if self.teardown_dispatch_key is not None or any(teardown_verifier_fields):
                raise ValueError("when teardown is absent, dispatch key and verifier must also be absent")

        # Setup kind validation
        if self.setup_kind not in {"markdown", "python"}:
            raise ValueError("managed setup kind must be markdown or python")

        # Version validation
        if self.setup_version < 1:
            raise ValueError("managed action versions must be positive")
        if self.teardown_version is not None and self.teardown_version < 1:
            raise ValueError("managed action versions must be positive")
        if self.setup_verifier_version is not None and self.setup_verifier_version < 1:
            raise ValueError("managed verifier versions must be positive")
        if self.teardown_verifier_version is not None and self.teardown_verifier_version < 1:
            raise ValueError("managed verifier versions must be positive")

        # Markdown setup: instructions must be nonempty
        if self.setup_kind == "markdown" and not self.setup_instructions:
            raise ValueError("markdown setup instructions must be nonempty")

        # Python setup: action dispatch key must be nonempty
        if self.setup_kind == "python" and not self.setup_dispatch_key:
            raise ValueError("python setup action dispatch key must be nonempty")

        # Teardown instructions: if teardown is markdown, instructions must be nonempty
        if self.teardown_interface is not None and self.setup_kind == "markdown":
            if not self.teardown_instructions:
                raise ValueError("markdown teardown instructions must be nonempty")

        # Teardown action constraints depend on kind
        if self.teardown_interface is not None:
            if self.setup_kind == "python":
                if not self.teardown_dispatch_key:
                    raise ValueError("python teardown action dispatch key must be nonempty")
            elif self.setup_kind == "markdown":
                if self.teardown_dispatch_key is not None:
                    raise ValueError("markdown teardown action dispatch key must be absent")

        # Argument validation
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

_CONNECT_GOOGLE_SETUP = "connect-google.interface.setup"
_CONNECT_GOOGLE = ManagedInterfaceBinding(
    setup_interface=_CONNECT_GOOGLE_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "Follow the connect-google skill's setup gateway to grant access to Google services."
    ),
    setup_verifier_interface=None,
    setup_verifier_version=None,
    setup_verifier_dispatch_key=None,
    teardown_interface=None,
    teardown_version=None,
    teardown_dispatch_key=None,
    teardown_instructions=None,
    teardown_verifier_interface=None,
    teardown_verifier_version=None,
    teardown_verifier_dispatch_key=None,
)

_ONLINE_CALENDAR_SETUP = "online-calendar.interface.setup"
_ONLINE_CALENDAR = ManagedInterfaceBinding(
    setup_interface=_ONLINE_CALENDAR_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "Follow the online-calendar skill's setup gateway to connect your calendar provider."
    ),
    setup_verifier_interface=None,
    setup_verifier_version=None,
    setup_verifier_dispatch_key=None,
    teardown_interface=None,
    teardown_version=None,
    teardown_dispatch_key=None,
    teardown_instructions=None,
    teardown_verifier_interface=None,
    teardown_verifier_version=None,
    teardown_verifier_dispatch_key=None,
)

_CLOUD_FILES_SETUP = "cloud-files.interface.setup"
_CLOUD_FILES = ManagedInterfaceBinding(
    setup_interface=_CLOUD_FILES_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "Follow the cloud-files skill's setup gateway to configure cloud storage access."
    ),
    setup_verifier_interface=None,
    setup_verifier_version=None,
    setup_verifier_dispatch_key=None,
    teardown_interface=None,
    teardown_version=None,
    teardown_dispatch_key=None,
    teardown_instructions=None,
    teardown_verifier_interface=None,
    teardown_verifier_version=None,
    teardown_verifier_dispatch_key=None,
)

_LIST_MANAGER_SETUP = "list-manager.interface.setup"
_LIST_MANAGER = ManagedInterfaceBinding(
    setup_interface=_LIST_MANAGER_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "Follow the list-manager skill's setup gateway to initialize your task list."
    ),
    setup_verifier_interface=None,
    setup_verifier_version=None,
    setup_verifier_dispatch_key=None,
    teardown_interface=None,
    teardown_version=None,
    teardown_dispatch_key=None,
    teardown_instructions=None,
    teardown_verifier_interface=None,
    teardown_verifier_version=None,
    teardown_verifier_dispatch_key=None,
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
    {
        _CONNECT_GOOGLE_SETUP: _CONNECT_GOOGLE,
        _ONLINE_CALENDAR_SETUP: _ONLINE_CALENDAR,
        _CLOUD_FILES_SETUP: _CLOUD_FILES,
        _LIST_MANAGER_SETUP: _LIST_MANAGER,
        _WAKEUP_SETUP: _WAKEUP,
    }
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
        if key is not None and key != ""
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
        if key is not None and key != ""
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

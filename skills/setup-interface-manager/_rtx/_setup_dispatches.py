"""Finite dispatch declarations for the setup lifecycle manager.

All public .interface.setup exports are automatically managed. Reviewed entries
in ``PRODUCTION_BINDINGS`` declare exact finite bindings for each canonical
setup's optional verifiers and optional teardown.
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
    helper_allowlist: tuple[tuple[str, int], ...] = ()

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

        # Helper allowlist validation
        for interface, version in self.helper_allowlist:
            if not interface or not isinstance(interface, str):
                raise ValueError("helper allowlist interface names must be nonempty strings")
            if not isinstance(version, int) or version < 1:
                raise ValueError("helper allowlist versions must be positive integers")
        allowlist_set = set(self.helper_allowlist)
        if len(allowlist_set) != len(self.helper_allowlist):
            raise ValueError("helper allowlist must not contain duplicates")


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
        "# Connect Google setup\n\n"
        "Use the current `run-markdown` flow id as `setup_flow_id` on every executable\n"
        "Famulus interface call below.\n\n"
        "1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for\n"
        "   owner `connect-google` and exact declaration `[\"keyring\"]`. Stop on failure.\n"
        "2. Call `connect-google._rtx.interface.shared-credential`. If it succeeds and\n"
        "   grants Drive, Calendar, and Gmail, setup is complete.\n"
        "3. Otherwise call `connect-google._rtx.interface.client-status`.\n"
        "4. If the Desktop OAuth client is absent, follow the existing Connect Google\n"
        "   client-creation instructions and install the downloaded JSON with\n"
        "   `connect-google._rtx.interface.install-client`.\n"
        "5. Call `connect-google._rtx.interface.authorize-services` with exactly\n"
        "   `--services drive,calendar,gmail`. Require all three grants.\n"
        "6. Call `connect-google._rtx.interface.select-shared-credential` with the returned\n"
        "   `credential_file`.\n"
        "7. Re-run `shared-credential` and require the same file plus all three grants.\n\n"
        "Settle only after step 2 or step 7 succeeds.\n"
        "Do not call Calendar, Cloud Files, or Email Client binders.\n"
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
    helper_allowlist=(
        ("connect-google._rtx.interface.shared-credential", 1),
        ("connect-google._rtx.interface.client-status", 1),
        ("connect-google._rtx.interface.install-client", 1),
        ("connect-google._rtx.interface.authorize-services", 1),
        ("connect-google._rtx.interface.select-shared-credential", 1),
    ),
)

_ONLINE_CALENDAR_SETUP = "online-calendar.interface.setup"
_ONLINE_CALENDAR = ManagedInterfaceBinding(
    setup_interface=_ONLINE_CALENDAR_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "# Online Calendar setup\n\n"
        "`connect-google.interface.setup` has already completed. Use the current\n"
        "`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.\n\n"
        "1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for\n"
        "   owner `online-calendar` and exact declaration `[\"keyring\"]`. Stop on failure.\n"
        "2. Call `connect-google._rtx.interface.shared-credential`.\n"
        "3. Call `online-calendar._rtx.interface.use-google-credential-file\n"
        "   --credential-file <credential_file> --home <current home>`.\n"
        "4. Require `bound: true` and `verified: true`.\n\n"
        "Settle only after step 4 succeeds. The binder already performs the live Calendar probe.\n"
        "Do not run OAuth or add another Calendar probe.\n"
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
    helper_allowlist=(
        ("connect-google._rtx.interface.shared-credential", 1),
        ("online-calendar._rtx.interface.use-google-credential-file", 1),
    ),
)

_CLOUD_FILES_SETUP = "cloud-files.interface.setup"
_CLOUD_FILES = ManagedInterfaceBinding(
    setup_interface=_CLOUD_FILES_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "# Cloud Files setup\n\n"
        "`connect-google.interface.setup` has already completed. Use the current\n"
        "`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.\n\n"
        "1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for\n"
        "   owner `cloud-files` and exact declaration `[\"keyring\"]`. Stop on failure.\n"
        "2. Call `connect-google._rtx.interface.shared-credential`.\n"
        "3. Call `cloud-files._rtx.interface.use-google-credential-file\n"
        "   --credential-file <credential_file> --home <current home>`; require\n"
        "   `bound: true` and `verified: true`.\n"
        "4. Call `cloud-files._rtx.interface.write-config` with\n"
        "   `--remote-llm-root assistant` and the current home.\n"
        "5. Call `cloud-files._rtx.interface.ensure-assistant-root`; require\n"
        "   `{\"exists\": true, \"root\": \"assistant\"}`.\n\n"
        "Settle only after steps 3 and 5 succeed.\n"
        "Do not run OAuth or create List Manager lists.\n"
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
    helper_allowlist=(
        ("connect-google._rtx.interface.shared-credential", 1),
        ("cloud-files._rtx.interface.use-google-credential-file", 1),
        ("cloud-files._rtx.interface.write-config", 1),
        ("cloud-files._rtx.interface.ensure-assistant-root", 1),
    ),
)

_EMAIL_CLIENT_SETUP = "email-client.interface.setup"
_EMAIL_CLIENT = ManagedInterfaceBinding(
    setup_interface=_EMAIL_CLIENT_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "# Email Client setup\n\n"
        "`connect-google.interface.setup` has already completed. Use the current\n"
        "`run-markdown` flow id as `setup_flow_id` on every executable Famulus call below.\n\n"
        "1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for\n"
        "   owner `email-client` and exact declaration `[\"keyring\"]`. Stop on failure.\n"
        "2. Call `connect-google._rtx.interface.shared-credential`; retain its file and email.\n"
        "3. Call `email-client._rtx.interface.accounts-list`.\n"
        "4. Reuse the unique exact-email match. If none exists, ask for a nickname, then call\n"
        "   `email-client._rtx.interface.accounts-add --email <email> --nickname <nickname> --auth gmail-oauth`.\n"
        "   If multiple matches exist, ask which nickname to use.\n"
        "5. Call `email-client._rtx.interface.accounts-use-google-credential-file\n"
        "   --nickname <nickname> --credential-file <credential_file> --home <current home>`;\n"
        "   require `bound: true` and `verified: true`.\n"
        "6. Run `email-client._rtx.interface.live-smoke -a <nickname> --imap --smtp-auth`;\n"
        "   require both checks to succeed.\n\n"
        "Settle only after steps 5 and 6 succeed.\n"
        "Do not use `--send-self` or `accounts-setup-oauth`.\n"
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
    helper_allowlist=(
        ("connect-google._rtx.interface.shared-credential", 1),
        ("email-client._rtx.interface.accounts-list", 1),
        ("email-client._rtx.interface.accounts-add", 1),
        ("email-client._rtx.interface.accounts-use-google-credential-file", 1),
        ("email-client._rtx.interface.live-smoke", 1),
    ),
)

_LIST_MANAGER_SETUP = "list-manager.interface.setup"
_LIST_MANAGER = ManagedInterfaceBinding(
    setup_interface=_LIST_MANAGER_SETUP,
    setup_version=1,
    setup_kind="markdown",
    setup_dispatch_key="",
    setup_instructions=(
        "# List Manager setup\n\n"
        "`cloud-files.interface.setup` has already completed. Use the current `run-markdown`\n"
        "flow id as `setup_flow_id` on every executable Famulus call below.\n\n"
        "1. Follow `bootstrap-dispatcher-runtime.interface.repair-selected-packages` for\n"
        "   owner `list-manager` and exact declaration `[\"dateparser\", \"keyring\", \"rich\"]`.\n"
        "   Stop on failure.\n"
        "2. Call `cloud-files._rtx.interface.lists-exists lists/todo.yaml` and\n"
        "   `cloud-files._rtx.interface.lists-exists lists/triage.yaml`.\n"
        "3. Only for an `exists: false` result, initialize exactly that missing list:\n"
        "   `list-manager._rtx.interface.cloud-init todo --cloud --schema todo` or\n"
        "   `list-manager._rtx.interface.cloud-init triage --cloud --schema triage`.\n"
        "4. Validate both with `list-manager._rtx.interface.cloud-read todo --cloud` and\n"
        "   `list-manager._rtx.interface.cloud-read triage --cloud`.\n\n"
        "Settle only after both lists exist and validate.\n"
        "Never overwrite an existing list; an existence/read error is a failure, not absence.\n"
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
    helper_allowlist=(
        ("cloud-files._rtx.interface.lists-exists", 1),
        ("list-manager._rtx.interface.cloud-init", 1),
        ("list-manager._rtx.interface.cloud-read", 1),
    ),
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
        _EMAIL_CLIENT_SETUP: _EMAIL_CLIENT,
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

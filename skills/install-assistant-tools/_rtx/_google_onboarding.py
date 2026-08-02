#!/usr/bin/env python3
"""Self-contained, script-owned Google onboarding step run after core
install completes (managed-runtime candidate build + scaffold, both of
which must already have succeeded before this runs -- see _phase_entry.py).

This step never imports connect-google/cloud-files/g-calendar/email-client
Python directly: every cross-skill call goes through the shared dispatcher
launcher (``dispatcher --caller-skill install-assistant-tools <interface>
...``), exactly like every other skill-to-skill call in this repo. That
means the interfaces it calls (``connect-google.interface.client-status``,
``connect-google.interface.authorize-services``,
``cloud-files.interface.use-google-credential``,
``g-calendar.interface.use-google-credential``,
``email-client.interface.accounts-use-google-credential``) must list
``install-assistant-tools`` in their export's ``allowed_callers`` and this
skill's source blueprint must declare ``uses_interfaces`` for each of them
-- both sides of the dispatcher's access-control check in
``officina.dispatcher.core._resolve_export_dispatch``.

Does NOT depend on any ``InstallSelections`` wizard type -- that contract
was never built. Service selection is either passed in explicitly
(non-interactive / programmatic callers, including _phase_entry.py today)
or prompted for interactively, matching the same stdin_isatty pattern
already used by cloud-files/_rtx/_ensure_oauth.py.

Design note -- gmail deferral: email-client is multi-account, so binding a
Gmail credential requires an account nickname
(``email-client.interface.accounts-use-google-credential --nickname ...``).
At fresh-install time there is normally no email account configured yet to
bind to. Rather than block the whole onboarding step (the credential is
still valid and useful once an account exists) or invent a nickname, gmail
is left in ``granted_services`` (it *was* authorized at the connect-google
credential level) but reported separately in ``deferred_services`` when no
``gmail_nickname`` was supplied, and the accounts-use-google-credential
dispatch call is skipped for it. The caller can bind it later once an
account nickname exists.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Google services this step knows how to authorize. Keys match
# officina.common.google_credentials.SERVICE_SCOPES exactly (the shared
# source of truth for valid service names, enforced by connect-google's
# authorize-services interface itself).
_SERVICE_MODULES: dict[str, str] = {
    "drive": "cloud-files",
    "calendar": "g-calendar",
    # "gmail" is handled specially below -- email-client's binder interface
    # takes a different argument shape (nickname + credential-id) because
    # it is multi-account.
}
_KNOWN_SERVICES = frozenset(_SERVICE_MODULES) | {"gmail"}

CALLER_SKILL = "install-assistant-tools"


@dataclass(frozen=True)
class OnboardingCapabilityResult:
    """Outcome of one run_google_onboarding() call.

    Never carries a credential's secret value -- only the opaque
    credential_id connect-google's authorize-services issued, plus which
    requested services ended up granted / denied / deferred.
    """

    status: str  # "completed" | "partial" | "skipped" | "needs_selection" | "failed"
    credential_id: str | None = None
    granted_services: tuple[str, ...] = ()
    denied_services: tuple[str, ...] = ()
    # Granted at the connect-google credential level but not yet bound to a
    # concrete per-skill config (currently: gmail with no account nickname
    # available yet).
    deferred_services: tuple[str, ...] = ()
    # Services that were granted and attempted, and successfully bound to
    # their owning skill.
    bound_services: tuple[str, ...] = ()
    # Services that were granted but whose per-skill binding dispatch call
    # raised unexpectedly. Each entry is (service, error message) -- never a
    # raw exception object, so this stays JSON/log friendly and can't smuggle
    # secrets any more than the rest of this result can.
    failed_services: tuple[tuple[str, str], ...] = ()
    detail: str | None = None


def dispatcher_launcher_path(bin_dir: Path) -> Path:
    """Platform-correct path to the generated ``dispatcher`` launcher.

    Mirrors the naming install-assistant-tools' own launcher installers use
    (skills/install-assistant-tools/_rtx/_install_launcher/*): ``dispatcher``
    everywhere except Windows, which gets ``dispatcher.bat``.
    """
    if sys.platform.startswith("win"):
        return Path(bin_dir) / "dispatcher.bat"
    return Path(bin_dir) / "dispatcher"


def run_google_onboarding(
    services,
    *,
    dispatcher_path: Path,
    home: Path,
    gmail_nickname: str | None = None,
    dry_run: bool = False,
    stdin_isatty: bool | None = None,
) -> OnboardingCapabilityResult:
    """Authorize the requested Google services and bind each to its owning
    skill, entirely through the dispatcher. Never raises on a partial or
    failed *service* outcome -- callers (namely _phase_entry.py) must not
    have their overall install fail because Google onboarding was
    incomplete. Only truly unexpected dispatcher failures raise, and even
    those are caught and converted to a "failed" result by _phase_entry.py.
    """
    if not services:
        if stdin_isatty:
            services = _prompt_for_services()
            if not services:
                return OnboardingCapabilityResult(status="skipped", detail="no services selected")
        else:
            return OnboardingCapabilityResult(status="needs_selection")

    # Dedupe while preserving first-seen order; reject unknown service names
    # up front rather than letting the dispatcher reject them opaquely.
    seen: dict[str, None] = {}
    for service in services:
        seen.setdefault(service, None)
    services = tuple(seen)
    unknown = [service for service in services if service not in _KNOWN_SERVICES]
    if unknown:
        raise ValueError(f"unknown Google service(s): {', '.join(unknown)}")

    if dry_run:
        return OnboardingCapabilityResult(status="skipped", detail="dry-run")

    status = _dispatch(dispatcher_path, "connect-google.interface.client-status", home=home)
    if status.get("status") != "valid":
        # No canonical OAuth client configured yet. This is the expected
        # state on a fresh machine before the user (or a future wizard) has
        # run connect-google's client setup -- not a failure of this
        # installer step, and it must not block the rest of the install.
        return OnboardingCapabilityResult(status="skipped", detail=f"client status: {status.get('status')}")

    auth_result = _dispatch(
        dispatcher_path,
        "connect-google.interface.authorize-services",
        "--services", ",".join(services),
        home=home,
    )
    credential_id = auth_result["credential_id"]
    granted = tuple(auth_result.get("granted_services", ()))
    denied = tuple(auth_result.get("denied_services", ()))

    bound: list[str] = []
    deferred: list[str] = []
    failed: list[tuple[str, str]] = []
    for service in granted:
        try:
            if service == "gmail":
                if not gmail_nickname:
                    # See module docstring: no account to bind to yet. The
                    # credential itself is still granted and usable later.
                    deferred.append(service)
                    continue
                _dispatch(
                    dispatcher_path,
                    "email-client.interface.accounts-use-google-credential",
                    "--nickname", gmail_nickname,
                    "--credential-id", credential_id,
                    home=home,
                )
            else:
                module = _SERVICE_MODULES[service]
                _dispatch(
                    dispatcher_path,
                    f"{module}.interface.use-google-credential",
                    "--credential-id", credential_id,
                    home=home,
                )
        except Exception as exc:  # noqa: BLE001 - a per-service binding
            # failure must not abort binding of the remaining granted
            # services, nor lose the partial-success info already gathered
            # in `bound` for services processed earlier in this loop.
            failed.append((service, str(exc)))
            continue
        bound.append(service)

    status_value = "completed" if not denied and not deferred and not failed else "partial"
    return OnboardingCapabilityResult(
        status=status_value,
        credential_id=credential_id,
        granted_services=granted,
        denied_services=denied,
        deferred_services=tuple(deferred),
        bound_services=tuple(bound),
        failed_services=tuple(failed),
    )


def _dispatch(dispatcher_path: Path, interface: str, *args: str, home: Path) -> dict:
    """Invoke one dispatcher-routed interface and return its parsed JSON
    stdout (or {} for interfaces that emit no output on success, e.g. the
    use-google-credential binders). Never returns or logs raw stdout/stderr
    text -- only the parsed JSON object, so an unexpected extra field a
    downstream interface might print can never smuggle a secret past this
    boundary into a result object or a log line.
    """
    argv = [
        str(dispatcher_path), "--caller-skill", CALLER_SKILL, interface,
        *args, "--home", str(home),
    ]
    completed = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="strict", cwd=str(home),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{interface} failed (exit {completed.returncode})")
    stdout = completed.stdout.strip()
    return json.loads(stdout) if stdout else {}


def _prompt_for_services() -> tuple[str, ...]:
    raise NotImplementedError(
        "interactive Google-service prompting ships alongside the installer wizard; "
        "callers running with a tty today should collect services themselves and "
        "pass them explicitly to run_google_onboarding()."
    )

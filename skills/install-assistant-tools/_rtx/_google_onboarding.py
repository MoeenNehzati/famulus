#!/usr/bin/env python3
"""Self-contained, script-owned Google onboarding step run after core
install completes (managed-runtime candidate build + scaffold, both of
which must already have succeeded before this runs -- see _phase_entry.py).

This step never imports another skill's Python. It checks the canonical client
through the dispatcher, then invokes connect-google's single coordinator. The
coordinator owns authorization and all fixed service-binding dispatches, so the
installer never handles a credential ID or performs an LLM-mediated handoff.

Does NOT depend on any ``InstallSelections`` wizard type -- that contract
was never built. Service selection is either passed in explicitly
(non-interactive / programmatic callers, including _phase_entry.py today)
or prompted for interactively, matching the same stdin_isatty pattern
already used by cloud-files/_rtx/_ensure_oauth.py.

When Gmail is granted without a nickname, the coordinator reports the stable
``missing-gmail-nickname`` incomplete code. This adapter maps that one case to
the installer's existing ``deferred_services`` result.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_KNOWN_SERVICES = frozenset({"drive", "calendar", "gmail"})

CALLER_SKILL = "install-assistant-tools"


@dataclass(frozen=True)
class OnboardingCapabilityResult:
    """Outcome of one run_google_onboarding() call.

    Never carries a credential's secret value -- only the absolute descriptor
    path returned by connect-google, plus per-service status.
    """

    status: str  # "completed" | "partial" | "skipped" | "needs_selection" | "failed"
    credential_file: str | None = None
    granted_services: tuple[str, ...] = ()
    denied_services: tuple[str, ...] = ()
    # Granted at the connect-google credential level but not yet bound to a
    # concrete per-skill config (currently: gmail with no account nickname
    # available yet).
    deferred_services: tuple[str, ...] = ()
    # Services that were granted and attempted, and successfully bound to
    # their owning skill.
    bound_services: tuple[str, ...] = ()
    verified_services: tuple[str, ...] = ()
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

    status = _dispatch(dispatcher_path, "connect-google._rtx.interface.client-status", home=home)
    if status.get("status") != "valid":
        # No canonical OAuth client configured yet. This is the expected
        # state on a fresh machine before the user (or a future wizard) has
        # run connect-google's client setup -- not a failure of this
        # installer step, and it must not block the rest of the install.
        return OnboardingCapabilityResult(status="skipped", detail=f"client status: {status.get('status')}")

    coordinator_args = ["--services", ",".join(services)]
    if gmail_nickname:
        coordinator_args.extend(("--gmail-nickname", gmail_nickname))
    coordinator_result = _dispatch(
        dispatcher_path,
        "connect-google._rtx.interface.connect-services",
        *coordinator_args,
        home=home,
    )
    credential_file = coordinator_result.get("credential_file")
    credential_file = credential_file if isinstance(credential_file, str) else None
    granted = tuple(coordinator_result.get("granted_services", ()))
    denied = tuple(coordinator_result.get("denied_services", ()))
    bound = tuple(coordinator_result.get("bound_services", ()))
    verified = tuple(coordinator_result.get("verified_services", ()))
    incomplete = coordinator_result.get("incomplete_services", {})
    incomplete = incomplete if isinstance(incomplete, dict) else {}
    deferred = tuple(
        service
        for service, error in incomplete.items()
        if isinstance(error, dict) and error.get("code") == "missing-gmail-nickname"
    )
    failed = tuple(
        (service, str(error.get("message", error.get("code", "binding failed"))))
        for service, error in incomplete.items()
        if service not in deferred and isinstance(error, dict)
    )
    complete = coordinator_result.get("complete") is True
    if complete:
        status_value = "completed"
    elif granted or denied or bound or deferred or failed:
        status_value = "partial"
    else:
        status_value = "failed"
    error = coordinator_result.get("error")
    detail = (
        str(error.get("message"))
        if isinstance(error, dict) and error.get("message")
        else None
    )
    return OnboardingCapabilityResult(
        status=status_value,
        credential_file=credential_file,
        granted_services=granted,
        denied_services=denied,
        deferred_services=deferred,
        bound_services=bound,
        verified_services=verified,
        failed_services=failed,
        detail=detail,
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
    stdout = completed.stdout.strip()
    payload = json.loads(stdout) if stdout else {}
    if completed.returncode != 0:
        if interface == "connect-google._rtx.interface.connect-services" and (
            isinstance(payload, dict) and payload.get("complete") is False
        ):
            return payload
        raise RuntimeError(f"{interface} failed (exit {completed.returncode})")
    return payload


def _prompt_for_services() -> tuple[str, ...]:
    raise NotImplementedError(
        "interactive Google-service prompting ships alongside the installer wizard; "
        "callers running with a tty today should collect services themselves and "
        "pass them explicitly to run_google_onboarding()."
    )

#!/usr/bin/env python3
"""Authorize Google services once and bind their credential descriptor path.

This source owns only orchestration. OAuth consent and descriptor creation stay
with ``_loopback_oauth``; Calendar, Drive, and Gmail each validate, probe, and
persist their own binding through declared dispatcher interfaces.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from officina.runtime.python_machine_interface import (
    DispatchCall,
    PythonMachineInterface,
)


SERVICE_DISPATCHES = {
    "calendar": DispatchCall(
        caller_module_id="connect-google._rtx",
        target_module_id="g-calendar._rtx",
        interface="use-google-credential-file",
        smoke_args=(
            "--credential-file",
            "/tmp/famulus-route-smoke-google-credential.json",
            "--home",
            "/tmp",
        ),
    ),
    "drive": DispatchCall(
        caller_module_id="connect-google._rtx",
        target_module_id="cloud-files._rtx",
        interface="use-google-credential-file",
        smoke_args=(
            "--credential-file",
            "/tmp/famulus-route-smoke-google-credential.json",
            "--home",
            "/tmp",
        ),
    ),
    "gmail": DispatchCall(
        caller_module_id="connect-google._rtx",
        target_module_id="email-client._rtx",
        interface="accounts-use-google-credential-file",
        smoke_args=(
            "--nickname",
            "route-smoke",
            "--credential-file",
            "/tmp/famulus-route-smoke-google-credential.json",
            "--home",
            "/tmp",
        ),
    ),
}


def _binding_args(
    service: str,
    *,
    credential_file: Path,
    home: Path,
    gmail_nickname: str | None,
    allow_account_change: frozenset[str],
) -> list[str]:
    args: list[str] = []
    if service == "gmail":
        if not gmail_nickname:
            raise ValueError("Gmail binding requires an account nickname")
        args.extend(("--nickname", gmail_nickname))
    args.extend(
        (
            "--credential-file",
            str(credential_file),
            "--home",
            str(home),
        )
    )
    if service in allow_account_change:
        args.append("--allow-account-change")
    return args


def _binding_error(service: str, result) -> dict[str, str]:
    try:
        payload = json.loads(result.stdout or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, str) and code and isinstance(message, str) and message:
            return {"code": code, "message": message}
    return {
        "code": "binding-dispatch-failed",
        "message": f"{service} binding exited with status {result.returncode}",
    }


def bind_credential_file(
    *,
    credential_file: Path,
    requested_services: Sequence[str],
    granted_services: Sequence[str],
    home: Path,
    gmail_nickname: str | None,
    allow_account_change: Sequence[str],
    dispatch: Callable,
) -> dict:
    """Bind selected granted services and aggregate their declared JSON results."""
    from officina.common.google_credentials import normalize_services

    requested = normalize_services(tuple(requested_services))
    granted_set = set(granted_services)
    granted = tuple(service for service in requested if service in granted_set)
    denied = tuple(service for service in requested if service not in granted_set)
    allowed_changes = frozenset(allow_account_change)
    unknown_changes = allowed_changes - set(requested)
    if unknown_changes:
        raise ValueError(
            f"account-change approval names unrequested services: {sorted(unknown_changes)}"
        )

    path = Path(credential_file)
    bound: list[str] = []
    verified: list[str] = []
    incomplete: dict[str, dict[str, str]] = {}
    for service in granted:
        if service == "gmail" and not gmail_nickname:
            incomplete[service] = {
                "code": "missing-gmail-nickname",
                "message": "Gmail was granted but no target account nickname was supplied",
            }
            continue
        try:
            result = dispatch(
                service,
                args=_binding_args(
                    service,
                    credential_file=path,
                    home=home,
                    gmail_nickname=gmail_nickname,
                    allow_account_change=allowed_changes,
                ),
                capture_output=True,
                text=True,
            )
        except Exception as exc:  # noqa: BLE001 - preserve other service attempts
            incomplete[service] = {
                "code": "binding-dispatch-failed",
                "message": str(exc),
            }
            continue

        if result.returncode != 0:
            incomplete[service] = _binding_error(service, result)
            continue
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict) or not (
            payload.get("service") == service
            and payload.get("credential_file") == str(path)
            and payload.get("bound") is True
            and payload.get("verified") is True
        ):
            incomplete[service] = {
                "code": "invalid-binding-result",
                "message": f"{service} returned an invalid binding result",
            }
            continue
        bound.append(service)
        verified.append(service)

    return {
        "schema_version": 1,
        "credential_file": str(path),
        "requested_services": list(requested),
        "granted_services": list(granted),
        "denied_services": list(denied),
        "bound_services": bound,
        "verified_services": verified,
        "incomplete_services": incomplete,
        "complete": not denied and not incomplete and tuple(bound) == requested,
    }


def connect_services(
    *,
    services: Sequence[str],
    home: Path,
    account_hint: str | None,
    gmail_nickname: str | None,
    allow_account_change: Sequence[str],
    dispatch: Callable,
    browser_enabled: bool = True,
    callback_port: int = 0,
    authorize: Callable | None = None,
) -> dict:
    """Authorize once, then bind the returned descriptor to granted services."""
    if authorize is None:
        from _loopback_oauth import authorize_services

        authorize = authorize_services
    result = authorize(
        services,
        home=Path(home),
        account_hint=account_hint,
        browser_enabled=browser_enabled,
        callback_port=callback_port,
    )
    return bind_credential_file(
        credential_file=Path(result.credential_file),
        requested_services=result.requested_services,
        granted_services=result.granted_services,
        home=Path(home),
        gmail_nickname=gmail_nickname,
        allow_account_change=allow_account_change,
        dispatch=dispatch,
    )


def _comma_separated(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


class _CoordinatorInterface(PythonMachineInterface):
    dispatches = SERVICE_DISPATCHES

    def _add_binding_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--services", required=True, help="comma-separated service list")
        parser.add_argument("--home", type=Path, required=True)
        parser.add_argument("--gmail-nickname")
        parser.add_argument(
            "--allow-account-change",
            default="",
            help="comma-separated services whose existing account binding may change",
        )


class ConnectServicesInterface(_CoordinatorInterface):
    prog = "connect-services"
    description = "Authorize selected Google services and bind one descriptor path."

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        self._add_binding_arguments(parser)
        parser.add_argument("--account-hint")
        parser.add_argument("--no-open-browser", action="store_true")
        parser.add_argument("--callback-port", type=int, default=0)
        return parser

    def run(self, args: argparse.Namespace) -> int:
        try:
            payload = connect_services(
                services=_comma_separated(args.services),
                home=args.home,
                account_hint=args.account_hint,
                gmail_nickname=args.gmail_nickname,
                allow_account_change=_comma_separated(args.allow_account_change),
                dispatch=self.dispatch,
                browser_enabled=not args.no_open_browser,
                callback_port=args.callback_port,
            )
        except Exception as exc:  # noqa: BLE001 - stable machine failure boundary
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "complete": False,
                        "error": {"code": "authorization-failed", "message": str(exc)},
                    }
                )
            )
            return 1
        print(json.dumps(payload))
        return 0 if payload["complete"] else 1


class BindCredentialFileInterface(_CoordinatorInterface):
    prog = "bind-credential-file"
    description = "Retry service bindings using an existing descriptor path."

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        self._add_binding_arguments(parser)
        parser.add_argument("--credential-file", type=Path, required=True)
        return parser

    def run(self, args: argparse.Namespace) -> int:
        try:
            from officina.common.google_credentials import load_credential_file

            ref = load_credential_file(args.credential_file)
            requested = _comma_separated(args.services)
            payload = bind_credential_file(
                credential_file=ref.path,
                requested_services=requested,
                granted_services=tuple(
                    service for service in requested if service in ref.granted_services
                ),
                home=args.home,
                gmail_nickname=args.gmail_nickname,
                allow_account_change=_comma_separated(args.allow_account_change),
                dispatch=self.dispatch,
            )
        except Exception as exc:  # noqa: BLE001 - stable machine failure boundary
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "credential_file": str(args.credential_file),
                        "complete": False,
                        "error": {"code": "binding-retry-failed", "message": str(exc)},
                    }
                )
            )
            return 1
        print(json.dumps(payload))
        return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit("run through the declared dispatcher interfaces")

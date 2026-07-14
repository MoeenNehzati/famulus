#!/usr/bin/env python3
"""Validate, inspect, and install a canonical Google Desktop OAuth client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from officina.common.oauth_json import OAuthJsonError, write_oauth_json
from officina.runtime.python_machine_interface import PythonArgvMachineInterface


FORBIDDEN_KEYS = {"access_token", "refresh_token"}
REQUIRED_INSTALLED_FIELDS = {
    "client_id",
    "client_secret",
    "auth_uri",
    "token_uri",
    "redirect_uris",
}


class ClientConfigError(ValueError):
    """Raised when a client file is unsafe or unsupported."""


def canonical_client_path(home: Path) -> Path:
    return Path(home) / ".config" / "connect-google" / "client.json"


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in FORBIDDEN_KEYS
            or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _require_nonempty_string(installed: dict[str, object], field: str) -> None:
    value = installed.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ClientConfigError(f"installed.{field} must be a non-empty string")


def validate_client_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ClientConfigError("client JSON must be an object")
    if "web" in payload:
        raise ClientConfigError("web OAuth clients are unsupported; create a Desktop client")
    if _contains_forbidden_key(payload):
        raise ClientConfigError("client JSON contains a token credential field")

    installed = payload.get("installed")
    if not isinstance(installed, dict):
        raise ClientConfigError("client JSON must contain an installed object")
    missing = sorted(REQUIRED_INSTALLED_FIELDS - set(installed))
    if missing:
        fields = ", ".join(f"installed.{field}" for field in missing)
        raise ClientConfigError(f"required fields are missing: {fields}")
    for field in ("client_id", "client_secret", "auth_uri", "token_uri"):
        _require_nonempty_string(installed, field)
    redirect_uris = installed.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris or not all(
        isinstance(uri, str) and uri.strip() for uri in redirect_uris
    ):
        raise ClientConfigError("installed.redirect_uris must be a non-empty string list")

    try:
        normalized = json.loads(json.dumps(payload))
    except (TypeError, ValueError) as exc:
        raise ClientConfigError("client JSON contains unsupported values") from exc
    return normalized


def _load_client(path: Path) -> dict[str, object]:
    try:
        payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClientConfigError("client file is not valid JSON") from exc
    except OSError as exc:
        raise ClientConfigError(f"cannot read client file: {exc}") from exc
    return validate_client_payload(payload)


def _result(status: str, client_type: str, path: Path) -> dict[str, str]:
    return {"status": status, "client_type": client_type, "path": str(path)}


def client_status(home: Path) -> dict[str, str]:
    path = canonical_client_path(home)
    if not path.exists() and not path.is_symlink():
        return _result("missing", "none", path)
    if path.is_symlink():
        return _result("invalid", "unknown", path)
    try:
        _load_client(path)
    except ClientConfigError:
        return _result("invalid", "unknown", path)
    return _result("valid", "desktop", path)


def install_client(source: Path, home: Path, replace: bool) -> dict[str, str]:
    payload = _load_client(Path(source))
    destination = canonical_client_path(home)
    existed = destination.exists() or destination.is_symlink()
    current = None

    if existed:
        try:
            current = _load_client(destination)
        except ClientConfigError:
            current = None
        if current == payload:
            return _result("unchanged", "desktop", destination)
        if not replace:
            raise ClientConfigError(
                "a different or invalid canonical client already exists; use --replace"
            )

    try:
        write_oauth_json(destination, payload)
    except OAuthJsonError as exc:
        raise ClientConfigError(str(exc)) from exc
    status = "replaced" if existed else "installed"
    return _result(status, "desktop", destination)


def run_client_status(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="client-status")
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args(argv)
    print(json.dumps(client_status(args.home)))
    return 0


def run_install_client(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="install-client")
    parser.add_argument("--from-json", required=True, type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args(argv)
    try:
        result = install_client(args.from_json, args.home, args.replace)
    except ClientConfigError as exc:
        parser.error(str(exc))
    print(json.dumps(result))
    return 0


class ClientStatusInterface(PythonArgvMachineInterface):
    prog = "client-status"

    def run(self, argv: list[str]) -> int:
        return run_client_status(argv)


class InstallClientInterface(PythonArgvMachineInterface):
    prog = "install-client"

    def run(self, argv: list[str]) -> int:
        return run_install_client(argv)


if __name__ == "__main__":
    raise SystemExit(run_client_status(sys.argv[1:]))

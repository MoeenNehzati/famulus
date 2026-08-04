#!/usr/bin/env python3
"""Validate, inspect, and install a canonical Google Desktop OAuth client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


FORBIDDEN_KEYS = {"access_token", "refresh_token"}
REQUIRED_INSTALLED_FIELDS = {
    "client_id",
    "auth_uri",
    "token_uri",
    "redirect_uris",
}


class ClientConfigError(ValueError):
    """Raised when a client file is unsafe or unsupported."""


def canonical_client_path(home: Path) -> Path:
    from officina.common.google_credentials import canonical_client_path as _canonical_client_path

    return _canonical_client_path(home=Path(home), platform=sys.platform)


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


def _require_client_secret_or_ref(installed: dict[str, object]) -> None:
    # A freshly downloaded client (legacy discovery of cloud-files/g-calendar
    # client.json, or a file the user is about to install) carries a plaintext
    # client_secret. Once google_credentials.install_client has run, the
    # canonical client.json instead carries client_secret_ref (the secret
    # itself lives in the OS secret store). Re-validating the already-installed
    # canonical file (client-status) must accept the ref form; validating a
    # not-yet-installed source file must accept the plaintext form. Both forms
    # are mutually exclusive: a payload should never carry both.
    has_secret = isinstance(installed.get("client_secret"), str) and bool(installed["client_secret"].strip())
    has_ref = isinstance(installed.get("client_secret_ref"), str) and bool(installed["client_secret_ref"].strip())
    if has_secret and has_ref:
        raise ClientConfigError("installed must not contain both client_secret and client_secret_ref")
    if not has_secret and not has_ref:
        raise ClientConfigError("installed.client_secret or installed.client_secret_ref must be a non-empty string")


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
    for field in ("client_id", "auth_uri", "token_uri"):
        _require_nonempty_string(installed, field)
    _require_client_secret_or_ref(installed)
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


def _result(status: str, client_type: str, path: Path) -> dict[str, object]:
    return {"status": status, "client_type": client_type, "path": str(path)}


def _legacy_candidates(home: Path) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    candidates: list[dict[str, str]] = []
    payloads: list[dict[str, object]] = []
    for service in ("cloud-files", "g-calendar"):
        path = Path(home) / ".config" / service / "client.json"
        if path.is_symlink():
            continue
        try:
            payload = _load_client(path)
        except ClientConfigError:
            continue
        candidates.append({"service": service, "path": str(path)})
        payloads.append(payload)
    return candidates, payloads


def client_status(home: Path) -> dict[str, object]:
    path = canonical_client_path(home)
    if not path.exists() and not path.is_symlink():
        result = _result("missing", "none", path)
    elif path.is_symlink():
        result = _result("invalid", "unknown", path)
    else:
        try:
            _load_client(path)
        except ClientConfigError:
            result = _result("invalid", "unknown", path)
        else:
            return _result("valid", "desktop", path)

    candidates, payloads = _legacy_candidates(home)
    if candidates:
        result["legacy_candidates"] = candidates
    if len(payloads) > 1:
        result["legacy_candidates_match"] = all(
            payload == payloads[0] for payload in payloads[1:]
        )
    return result


def install_client(source: Path, home: Path, replace: bool, secret_backend=None) -> dict[str, object]:
    from officina.common.google_credentials import GoogleCredentialError, install_client as _install_client

    payload = _load_client(Path(source))
    try:
        result = _install_client(
            payload, home=Path(home), platform=sys.platform, replace=replace, secret_backend=secret_backend
        )
    except GoogleCredentialError as exc:
        raise ClientConfigError(str(exc)) from exc
    destination = canonical_client_path(home)
    return _result(result["status"], "desktop", destination)


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

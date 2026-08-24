#!/usr/bin/env python3
"""
ensure_oauth.py — Check online-calendar OAuth status and guide setup if needed.

Relocated from install-assistant-tools' shared Google-OAuth chooser — see
cloud-files/_rtx/_ensure_oauth.py for the sibling implementation and the
rationale (each service owns its own guidance and setup flow now).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

CONFIG_DIR_NAME = "online-calendar"
LABEL = "Google Calendar (online-calendar)"
CALENDAR_PROBE_URL = (
    "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"
)


class CredentialFileBindingError(RuntimeError):
    """Stable service-owned failure returned to the Google coordinator."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def log(msg: str = "") -> None:
    print(msg, flush=True)


def client_setup_lines(home: Path) -> list[str]:
    client_json = home / ".config" / CONFIG_DIR_NAME / "client.json"
    return [
        f"{LABEL} OAuth client setup still needed.",
        "  In Google Cloud Console, create or download an OAuth client JSON for a Desktop app.",
        f"  Save that file as: {client_json}",
        '  If the app stays in Google OAuth "Testing", Google may require repeated re-authorization after about 7 days.',
        '  If you do not want repeated re-authorization, use Google Cloud OAuth -> Audience and click "Publish app" / move it to "In production".',
    ]


def run(*, home: Path, dry_run: bool, stdin_isatty: bool | None = None) -> str:
    credentials_path = home / ".config" / CONFIG_DIR_NAME / "credentials.json"
    if credentials_path.exists():
        return "already_configured"

    client_json = home / ".config" / CONFIG_DIR_NAME / "client.json"
    setup_lines = client_setup_lines(home)

    if dry_run:
        if client_json.exists():
            log(f"Would run online-calendar OAuth setup: {sys.executable} setup_oauth.py")
            return "would_run"
        for line in setup_lines:
            log(line)
        return "needs_client_json"

    if not client_json.exists():
        for line in setup_lines:
            log(line)
        if stdin_isatty is None:
            stdin_isatty = sys.stdin.isatty()
        if not stdin_isatty:
            log("  online-calendar OAuth skipped for now: client.json is still missing.")
            return "needs_client_json"
        reply = input(
            f"Press Enter after saving {client_json.name} to launch browser authorization, "
            "or type 'skip' to continue without it: "
        ).strip().lower()
        if reply == "skip":
            log("  online-calendar OAuth skipped.")
            return "skipped"
        if not client_json.exists():
            log("  online-calendar OAuth skipped: client.json is still missing.")
            return "needs_client_json"

    log("Launching Google Calendar browser authorization...")
    script = Path(__file__).resolve().parent / "_oauth_bootstrap.py"
    result = subprocess.run([sys.executable, str(script)])
    if result.returncode == 0:
        return "configured"
    log(f"Warning: online-calendar OAuth setup exited {result.returncode}.")
    return "failed"


def _config_paths(home: Path) -> tuple[Path, Path]:
    config_dir = home / ".config" / CONFIG_DIR_NAME
    return config_dir, config_dir / "config.json"


def _read_existing_config(config_path: Path) -> dict[str, object]:
    if not os.path.lexists(config_path):
        return {}
    if not config_path.is_file():
        raise CredentialFileBindingError(
            "invalid-service-config", f"{config_path} exists but is not a regular file"
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialFileBindingError(
            "invalid-service-config", f"could not read {config_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CredentialFileBindingError(
            "invalid-service-config", f"{config_path} must contain a JSON object"
        )
    return payload


def _merge_and_write_config(
    home: Path, *, patch: dict[str, object], dry_run: bool = False, dry_run_message: str | None = None
) -> None:
    """Merge ``patch`` onto the existing config.json and write it back.

    Starting from ``dict(existing)`` (rather than an explicit allow-list of
    fields) means any field this module doesn't yet know about survives a
    rewrite, and any future second config-writer added to this file
    automatically inherits the same merge-not-replace behavior. cloud-files'
    _ensure_oauth.py hit a bug where two config-writing functions used
    inconsistent merge strategies — one rebuilt its payload from an
    allow-list, silently dropping fields the other wrote — before both were
    routed through a single shared helper. This module routes every
    config.json write through here from the start so that bug class can't
    recur here either.
    """
    config_dir, config_path = _config_paths(home)
    existing = _read_existing_config(config_path)

    payload = dict(existing)
    payload.update(patch)

    if dry_run:
        log(dry_run_message or f"Would write online-calendar config {config_path}")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def use_google_credential(*, credential_id: str, home: Path, platform: str = sys.platform) -> None:
    """Bind online-calendar to a shared connect-google credential.

    Validate Calendar scope, then store only the opaque ``credential_id`` in
    online-calendar's config. Secrets remain in the shared credential store.
    """
    from officina.credentials.google import SERVICE_SCOPES, GoogleCredentialError, load_credential

    try:
        ref = load_credential(credential_id, home=home, platform=platform)
        if not SERVICE_SCOPES["calendar"] <= ref.granted_scopes:
            raise GoogleCredentialError(f"credential {credential_id} lacks Calendar scope")
    except GoogleCredentialError as exc:
        raise SystemExit(str(exc)) from exc

    _merge_and_write_config(home, patch={"credential_id": credential_id})


def _existing_binding_subject(
    config: dict[str, object], *, home: Path, platform: str
) -> tuple[bool, str | None]:
    """Return whether prior OAuth state exists and its stable subject when provable."""
    from officina.credentials.google import (
        GoogleCredentialError,
        load_credential,
        load_credential_file,
    )

    if "credential_file" in config:
        value = config["credential_file"]
        if not isinstance(value, str) or not value.strip():
            return True, None
        try:
            return True, load_credential_file(Path(value)).subject
        except GoogleCredentialError:
            return True, None
    if "credential_id" in config:
        value = config["credential_id"]
        if not isinstance(value, str) or not value.strip():
            return True, None
        try:
            return True, load_credential(
                value.strip(), home=home, platform=platform
            ).subject
        except GoogleCredentialError:
            return True, None
    return (home / ".config" / CONFIG_DIR_NAME / "credentials.json").exists(), None


def use_google_credential_file(
    *,
    credential_file: Path,
    home: Path,
    allow_account_change: bool = False,
    platform: str = sys.platform,
    urlopen: Callable = urllib.request.urlopen,
) -> dict[str, object]:
    """Validate, live-probe, then persist one Calendar descriptor path.

    The descriptor is loaded before any network call, prior identity is compared
    before replacement, and ``config.json`` is not changed unless both refresh
    and the Calendar-owned probe succeed.
    """
    from officina.credentials.google import (
        GoogleCredentialError,
        SERVICE_SCOPES,
        load_credential_file,
        refresh_access_token_from_file,
    )

    path = Path(credential_file).expanduser().resolve()
    try:
        ref = load_credential_file(path)
    except GoogleCredentialError as exc:
        raise CredentialFileBindingError("invalid-credential-file", str(exc)) from exc
    if not SERVICE_SCOPES["calendar"] <= ref.granted_scopes:
        raise CredentialFileBindingError(
            "insufficient-scope", f"credential file {path} lacks Calendar scope"
        )

    _config_dir, config_path = _config_paths(home)
    config = _read_existing_config(config_path)
    has_prior_state, prior_subject = _existing_binding_subject(
        config, home=home, platform=platform
    )
    if has_prior_state and prior_subject != ref.subject and not allow_account_change:
        raise CredentialFileBindingError(
            "account-change-confirmation-required",
            "existing Calendar credential identity differs or cannot be established",
        )

    try:
        token = refresh_access_token_from_file(
            path,
            required_scopes=SERVICE_SCOPES["calendar"],
            urlopen=urlopen,
        )
        request = urllib.request.Request(
            CALENDAR_PROBE_URL,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urlopen(request, timeout=30) as response:
            response.read()
    except Exception as exc:
        raise CredentialFileBindingError("live-check-failed", str(exc)) from exc

    _merge_and_write_config(home, patch={"credential_file": str(path)})
    return {
        "service": "calendar",
        "credential_file": str(path),
        "account": ref.account,
        "bound": True,
        "verified": True,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    oauth_p = sub.add_parser("ensure-oauth")
    oauth_p.add_argument("--home", metavar="DIR", required=True)
    oauth_p.add_argument("--dry-run", action="store_true")

    use_cred_p = sub.add_parser("use-google-credential")
    use_cred_p.add_argument("--credential-id", metavar="ID", required=True)
    use_cred_p.add_argument("--home", metavar="DIR", required=True)

    use_file_p = sub.add_parser("use-google-credential-file")
    use_file_p.add_argument("--credential-file", metavar="PATH", required=True)
    use_file_p.add_argument("--home", metavar="DIR", required=True)
    use_file_p.add_argument("--allow-account-change", action="store_true")

    return parser.parse_args(argv)


class Interface(PythonArgvMachineInterface):
    prog = "ensure_oauth.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "ensure-oauth":
        status = run(home=Path(args.home), dry_run=args.dry_run)
        log(f"Status: {status}")
    elif args.command == "use-google-credential":
        use_google_credential(credential_id=args.credential_id, home=Path(args.home))
        log("Status: configured")
    elif args.command == "use-google-credential-file":
        path = Path(args.credential_file).expanduser().resolve()
        try:
            result = use_google_credential_file(
                credential_file=path,
                home=Path(args.home),
                allow_account_change=args.allow_account_change,
            )
        except CredentialFileBindingError as exc:
            print(
                json.dumps(
                    {
                        "service": "calendar",
                        "credential_file": str(path),
                        "bound": False,
                        "verified": False,
                        "error": {"code": exc.code, "message": str(exc)},
                    }
                )
            )
            return 1
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

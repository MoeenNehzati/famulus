#!/usr/bin/env python3
"""
ensure_oauth.py — Check cloud-files OAuth status and guide setup if needed.

Relocated from install-assistant-tools' shared Google-OAuth chooser: each
service now owns its own guidance text and setup flow instead of a shared
script batching cloud-files and g-calendar together. This wraps
setup_oauth.py (the actual token exchange) with the "is this already
configured, and if not, what does the user need to do" checks that used to
live in the installer, plus writing ~/.config/cloud-files/config.json
(also relocated from the installer).
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
from officina.configuration.configured_schema import load_configuration, validate_configuration

CONFIG_DIR_NAME = "cloud-files"
LABEL = "Google Drive (cloud-files)"
DRIVE_PROBE_URL = (
    "https://www.googleapis.com/drive/v3/files?pageSize=1&fields=files(id)"
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
            log(f"Would run cloud-files OAuth setup: {sys.executable} setup_oauth.py")
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
            log("  Cloud-files OAuth skipped for now: client.json is still missing.")
            return "needs_client_json"
        reply = input(
            f"Press Enter after saving {client_json.name} to launch browser authorization, "
            "or type 'skip' to continue without it: "
        ).strip().lower()
        if reply == "skip":
            log("  Cloud-files OAuth skipped.")
            return "skipped"
        if not client_json.exists():
            log("  Cloud-files OAuth skipped: client.json is still missing.")
            return "needs_client_json"

    log("Launching Google Drive browser authorization...")
    script = Path(__file__).resolve().parent / "_oauth_bootstrap.py"
    result = subprocess.run([sys.executable, str(script)])
    if result.returncode == 0:
        return "configured"
    log(f"Warning: cloud-files OAuth setup exited {result.returncode}.")
    return "failed"


def normalize_llm_root(root: str) -> str:
    raw = root.strip()
    if not raw:
        return ""
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"invalid remote_llm_root: {root}")
        parts.append(part)
    return "/".join(parts) if parts else ""


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
    return load_configuration(config_path)


def _merge_and_write_config(
    home: Path, *, patch: dict[str, object], dry_run: bool = False, dry_run_message: str | None = None
) -> None:
    """Merge ``patch`` onto the existing config.json and write it back.

    Starting from ``dict(existing)`` (rather than an explicit allow-list of
    fields) means any field neither function currently knows about — such as
    ``credential_id`` written by :func:`use_google_credential` — survives a
    rewrite by :func:`write_config`, and vice versa. Both config-writing
    functions in this module route through here so that invariant can't
    silently regress as more fields get added.
    """
    config_dir, config_path = _config_paths(home)
    existing = _read_existing_config(config_path)

    payload = dict(existing)
    payload.update(patch)
    validate_configuration(payload, document_name=str(config_path))

    if dry_run:
        log(dry_run_message or f"Would write cloud-files config {config_path}")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_config(home: Path, *, remote_llm_root: str, dry_run: bool) -> None:
    _, config_path = _config_paths(home)
    existing = _read_existing_config(config_path)

    try:
        normalized_llm_root = normalize_llm_root(remote_llm_root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    patch: dict[str, object] = {
        "remote_llm_root": normalized_llm_root,
        "timeout_seconds": int(existing.get("timeout_seconds", 45)),
    }

    _merge_and_write_config(
        home, patch=patch, dry_run=dry_run, dry_run_message=f"Would write cloud-files config {config_path}"
    )


def use_google_credential(*, credential_id: str, home: Path, platform: str = sys.platform) -> None:
    """Bind cloud-files to a shared connect-google credential.

    Validates the credential grants Drive scope *before* writing anything, then
    stores only the opaque ``credential_id`` in cloud-files' own config.json —
    never the client secret or refresh token, which stay in
    officina.credentials.google' registry/secret store.
    """
    from officina.credentials.google import SERVICE_SCOPES, GoogleCredentialError, load_credential

    try:
        ref = load_credential(credential_id, home=home, platform=platform)
        if not SERVICE_SCOPES["drive"] <= ref.granted_scopes:
            raise GoogleCredentialError(f"credential {credential_id} lacks Drive scope")
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
    credentials_value = config.get("credentials_path")
    credentials_path = (
        Path(credentials_value).expanduser()
        if isinstance(credentials_value, str) and credentials_value.strip()
        else home / ".config" / CONFIG_DIR_NAME / "credentials.json"
    )
    return credentials_path.exists(), None


def use_google_credential_file(
    *,
    credential_file: Path,
    home: Path,
    allow_account_change: bool = False,
    platform: str = sys.platform,
    urlopen: Callable = urllib.request.urlopen,
) -> dict[str, object]:
    """Validate, live-probe, then persist one Drive descriptor path."""
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
    if not SERVICE_SCOPES["drive"] <= ref.granted_scopes:
        raise CredentialFileBindingError(
            "insufficient-scope", f"credential file {path} lacks Drive scope"
        )

    _config_dir, config_path = _config_paths(home)
    try:
        config = _read_existing_config(config_path)
    except (OSError, ValueError) as exc:
        raise CredentialFileBindingError(
            "invalid-service-config", f"could not read {config_path}: {exc}"
        ) from exc
    has_prior_state, prior_subject = _existing_binding_subject(
        config, home=home, platform=platform
    )
    if has_prior_state and prior_subject != ref.subject and not allow_account_change:
        raise CredentialFileBindingError(
            "account-change-confirmation-required",
            "existing Drive credential identity differs or cannot be established",
        )

    try:
        token = refresh_access_token_from_file(
            path,
            required_scopes=SERVICE_SCOPES["drive"],
            urlopen=urlopen,
        )
        request = urllib.request.Request(
            DRIVE_PROBE_URL,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urlopen(request, timeout=45) as response:
            response.read()
    except Exception as exc:
        raise CredentialFileBindingError("live-check-failed", str(exc)) from exc

    _merge_and_write_config(home, patch={"credential_file": str(path)})
    return {
        "service": "drive",
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

    config_p = sub.add_parser("write-config")
    config_p.add_argument("--home", metavar="DIR", required=True)
    config_p.add_argument("--remote-llm-root", default="assistant/")
    config_p.add_argument("--dry-run", action="store_true")

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
    elif args.command == "write-config":
        write_config(Path(args.home), remote_llm_root=args.remote_llm_root, dry_run=args.dry_run)
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
                        "service": "drive",
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

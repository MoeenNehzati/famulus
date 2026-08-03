#!/usr/bin/env python3
"""
ensure_oauth.py — Check g-calendar OAuth status and guide setup if needed.

Relocated from install-assistant-tools' shared Google-OAuth chooser — see
cloud-files/_rtx/_ensure_oauth.py for the sibling implementation and the
rationale (each service owns its own guidance and setup flow now).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

CONFIG_DIR_NAME = "g-calendar"
LABEL = "Google Calendar (g-calendar)"


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
            log(f"Would run g-calendar OAuth setup: {sys.executable} setup_oauth.py")
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
            log("  g-calendar OAuth skipped for now: client.json is still missing.")
            return "needs_client_json"
        reply = input(
            f"Press Enter after saving {client_json.name} to launch browser authorization, "
            "or type 'skip' to continue without it: "
        ).strip().lower()
        if reply == "skip":
            log("  g-calendar OAuth skipped.")
            return "skipped"
        if not client_json.exists():
            log("  g-calendar OAuth skipped: client.json is still missing.")
            return "needs_client_json"

    log("Launching Google Calendar browser authorization...")
    script = Path(__file__).resolve().parent / "_oauth_bootstrap.py"
    result = subprocess.run([sys.executable, str(script)])
    if result.returncode == 0:
        return "configured"
    log(f"Warning: g-calendar OAuth setup exited {result.returncode}.")
    return "failed"


def _config_paths(home: Path) -> tuple[Path, Path]:
    config_dir = home / ".config" / CONFIG_DIR_NAME
    return config_dir, config_dir / "config.json"


def _read_existing_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


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
        log(dry_run_message or f"Would write g-calendar config {config_path}")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def use_google_credential(*, credential_id: str, home: Path, platform: str = sys.platform) -> None:
    """Bind g-calendar to a shared connect-google credential.

    Validates the credential grants Calendar scope *before* writing
    anything, then stores only the opaque ``credential_id`` in g-calendar's
    own config.json — never the client secret or refresh token, which stay
    in officina.common.google_credentials' registry/secret store.
    """
    from officina.common.google_credentials import SERVICE_SCOPES, GoogleCredentialError, load_credential

    try:
        ref = load_credential(credential_id, home=home, platform=platform)
        if not SERVICE_SCOPES["calendar"] <= ref.granted_scopes:
            raise GoogleCredentialError(f"credential {credential_id} lacks Calendar scope")
    except GoogleCredentialError as exc:
        raise SystemExit(str(exc)) from exc

    _merge_and_write_config(home, patch={"credential_id": credential_id})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    oauth_p = sub.add_parser("ensure-oauth")
    oauth_p.add_argument("--home", metavar="DIR", required=True)
    oauth_p.add_argument("--dry-run", action="store_true")

    use_cred_p = sub.add_parser("use-google-credential")
    use_cred_p.add_argument("--credential-id", metavar="ID", required=True)
    use_cred_p.add_argument("--home", metavar="DIR", required=True)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

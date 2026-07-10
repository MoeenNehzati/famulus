#!/usr/bin/env python3
"""
ensure_oauth.py — Check g-calendar OAuth status and guide setup if needed.

Relocated from install-assistant-tools' shared Google-OAuth chooser — see
cloud-files/_rtx/_ensure_oauth.py for the sibling implementation and the
rationale (each service owns its own guidance and setup flow now).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", metavar="DIR", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    status = run(home=Path(args.home), dry_run=args.dry_run)
    log(f"Status: {status}")


if __name__ == "__main__":
    main()

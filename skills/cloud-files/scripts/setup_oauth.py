#!/usr/bin/env python3
"""One-time OAuth2 setup for the cloud-files skill.

Two files live at ~/.config/cloud-files/ (both mode 600, never git-tracked):
  client.json      — original Google Cloud Console OAuth client JSON
                     (client_id + client_secret). Kept permanently; never
                     overwritten by this script. Source of truth for re-auth.
  credentials.json — working credentials written by this script
                     (client_id + client_secret + refresh_token). Overwritten
                     on every run. Used by cloud-files to mint access tokens.

Usage:
    setup_oauth.py                              # reads ~/.config/cloud-files/client.json
    setup_oauth.py --from-json /path/to/client_secret_*.json
    setup_oauth.py --client-id ID --client-secret SECRET [--port 8765]
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/drive"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CLIENT_PATH = Path.home() / ".config" / "cloud-files" / "client.json"
CREDS_PATH = Path.home() / ".config" / "cloud-files" / "credentials.json"


def load_client_credentials(
    *,
    from_json: Path | None,
    client_id: str | None,
    client_secret: str | None,
) -> tuple[str, str]:
    if from_json is not None:
        payload = json.loads(from_json.read_text(encoding="utf-8"))
        section = payload.get("installed", payload.get("web", {}))
        loaded_client_id = section.get("client_id")
        loaded_client_secret = section.get("client_secret")
        if not loaded_client_id or not loaded_client_secret:
            raise SystemExit(f"No client_id/client_secret found in {from_json}")
        return str(loaded_client_id), str(loaded_client_secret)

    if client_id and client_secret:
        return client_id, client_secret

    raise SystemExit(
        "either --from-json, or both --client-id and --client-secret, are required"
    )


def build_auth_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    parser.add_argument(
        "--from-json",
        help=f"path to OAuth client JSON (default: {CLIENT_PATH})",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    from_json = Path(args.from_json).expanduser() if args.from_json else None
    if from_json is None and not (args.client_id and args.client_secret):
        if CLIENT_PATH.exists():
            from_json = CLIENT_PATH
        else:
            parser.error(
                f"no client credentials found: place the Google client JSON at "
                f"{CLIENT_PATH}, or pass --from-json / --client-id + --client-secret"
            )

    client_id, client_secret = load_client_credentials(
        from_json=from_json,
        client_id=args.client_id,
        client_secret=args.client_secret,
    )

    redirect_uri = f"http://localhost:{args.port}"
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
    )

    code_holder: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()

            if qs.get("state", [""])[0] != state:
                code_holder["error"] = "state_mismatch"
                self.wfile.write(b"<html><body>State mismatch - aborting.</body></html>")
                return

            if "error" in qs:
                code_holder["error"] = qs["error"][0]
                self.wfile.write(b"<html><body>Authorization denied. You can close this tab.</body></html>")
                return

            code = qs.get("code", [None])[0]
            if code is not None:
                code_holder["code"] = code
            self.wfile.write(b"<html><body>Authorization complete. You can close this tab.</body></html>")

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.HTTPServer(("localhost", args.port), Handler)

    print("Opening browser for Google Drive authorization...")
    print(f"If it doesn't open automatically, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server.handle_request()

    if code_holder.get("error"):
        raise SystemExit(f"Authorization failed: {code_holder['error']}")

    code = code_holder.get("code")
    if not code:
        raise SystemExit("No authorization code received.")

    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(request) as response:
        token_data = json.load(response)

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "No refresh_token in the response. Google omits it if you've already "
            "granted this app access before without revoking it - revoke access at "
            "https://myaccount.google.com/permissions and re-run this script."
        )

    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    creds = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    CREDS_PATH.write_text(json.dumps(creds, indent=2) + "\n", encoding="utf-8")
    os.chmod(CREDS_PATH, 0o600)
    print(f"Saved credentials to {CREDS_PATH}")


if __name__ == "__main__":
    main()

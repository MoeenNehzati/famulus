#!/usr/bin/env python3
"""One-time OAuth2 setup for the g-calendar skill.

Two files live at ~/.config/g-calendar/ (both mode 600, never git-tracked):
  client.json      — original Google Cloud Console OAuth client JSON
                     (client_id + client_secret). Kept permanently; never
                     overwritten by this script. Source of truth for re-auth.
  credentials.json — working credentials written by this script
                     (client_id + client_secret + refresh_token). Overwritten
                     on every run. Used by gcal.py to mint access tokens.

Usage:
    setup_oauth.py                              # reads ~/.config/g-calendar/client.json
    setup_oauth.py --from-json /path/to/client_secret_*.json
    setup_oauth.py --client-id ID --client-secret SECRET [--port 8765]

Re-running this is safe (e.g. if the refresh token is later revoked or
expires because the OAuth consent screen is in "Testing" status). If you do
not want repeated re-authorization in that case, publish the app / move it to
"In production" from Google Cloud OAuth -> Audience first.
"""
import argparse
import http.server
import json
import os
import secrets
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from officina.common.oauth_json import write_oauth_json
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SCOPE = "https://www.googleapis.com/auth/calendar"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_VERIFY_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"
CLIENT_PATH = os.path.expanduser("~/.config/g-calendar/client.json")
CREDS_PATH = os.path.expanduser("~/.config/g-calendar/credentials.json")


def verify_access_token(access_token: str) -> None:
    request = urllib.request.Request(
        CALENDAR_VERIFY_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request) as response:
        json.load(response)


def persist_verified_credentials(
    token_data: dict[str, object],
    client_id: str,
    client_secret: str,
    credentials_path: Path | str = CREDS_PATH,
) -> None:
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "No refresh_token in the response. Google omits it if you've already "
            "granted this app access before without revoking it - revoke access at "
            "https://myaccount.google.com/permissions and re-run this script."
        )
    access_token = token_data.get("access_token")
    if not access_token:
        raise SystemExit("No access_token in the response; credentials were not changed.")

    verify_access_token(str(access_token))
    write_oauth_json(
        Path(credentials_path),
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": str(refresh_token),
        },
    )


class Interface(PythonArgvMachineInterface):
    prog = "setup_oauth.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    parser.add_argument("--from-json", help=f"path to OAuth client JSON (default: {CLIENT_PATH})")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if not args.from_json and not (args.client_id and args.client_secret):
        if os.path.exists(CLIENT_PATH):
            args.from_json = CLIENT_PATH
        else:
            parser.error(
                f"no client credentials found: place the Google client JSON at "
                f"{CLIENT_PATH}, or pass --from-json / --client-id + --client-secret"
            )

    if args.from_json:
        with open(args.from_json) as f:
            client_data = json.load(f)
        section = client_data.get("installed", client_data.get("web", {}))
        client_id = section.get("client_id")
        client_secret = section.get("client_secret")
        if not client_id or not client_secret:
            raise SystemExit(f"No client_id/client_secret found in {args.from_json}")
    elif args.client_id and args.client_secret:
        client_id = args.client_id
        client_secret = args.client_secret
    else:
        parser.error("either --from-json, or both --client-id and --client-secret, are required")

    redirect_uri = f"http://localhost:{args.port}"
    state = secrets.token_urlsafe(16)

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    code_holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()

            if qs.get("state", [""])[0] != state:
                self.wfile.write(b"<html><body>State mismatch - aborting.</body></html>")
                return

            if "error" in qs:
                code_holder["error"] = qs["error"][0]
                self.wfile.write(b"<html><body>Authorization denied. You can close this tab.</body></html>")
                return

            code_holder["code"] = qs.get("code", [None])[0]
            self.wfile.write(b"<html><body>Authorization complete. You can close this tab.</body></html>")

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("localhost", args.port), Handler)

    print("Opening browser for Google authorization...")
    print('Tip: if the OAuth consent screen is still in "Testing", Google may require repeated re-authorization after about 7 days.')
    print('If you do not want that, publish the app / move it to "In production" from Google Cloud OAuth -> Audience before completing auth.')
    print(f"If it doesn't open automatically, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server.handle_request()

    if code_holder.get("error"):
        raise SystemExit(f"Authorization failed: {code_holder['error']}")

    code = code_holder.get("code")
    if not code:
        raise SystemExit("No authorization code received.")

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        token_data = json.load(resp)

    persist_verified_credentials(token_data, client_id, client_secret)

    print(f"Saved credentials to {CREDS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

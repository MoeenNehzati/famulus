#!/usr/bin/env python3
"""Account registry for email-client: nickname -> {email, IMAP/SMTP settings}.

Lives at ~/.config/email-client/accounts.json — deliberately OUTSIDE the
skills git repo (which may go public). Passwords are never stored here; they
stay in the host credential store via officina.common.secret_store.

Subcommands:
  list                                        -> JSON {nickname: {email, display_name}}
  add     --nickname N --email E [--display-name D]
          [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P] [--starttls]
  update  --nickname N [--email E] [--display-name D]
          [--imap-host H] [--imap-port P] [--smtp-host H] [--smtp-port P]
  remove  --nickname N [--purge-credentials]
  set-password --nickname N --purpose imap|smtp   (secret read from stdin)
  setup-oauth --nickname N --client-config PATH    (Gmail OAuth loopback setup)
  resolve --nickname N                        -> JSON full record (for other scripts)
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from officina.common import secret_store
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from . import _oauth_tokens
except ImportError:
    _oauth_spec = importlib.util.spec_from_file_location("_oauth_tokens", Path(__file__).resolve().parent / "_oauth_tokens.py")
    _oauth_tokens = importlib.util.module_from_spec(_oauth_spec)
    assert _oauth_spec.loader is not None
    _oauth_spec.loader.exec_module(_oauth_tokens)

# Overridable via env var so tests can point at a tmp_path instead of the
# real ~/.config/email-client/accounts.json.
CONFIG_DIR = Path(os.environ["EMAIL_CLIENT_CONFIG_DIR"]) if os.environ.get("EMAIL_CLIENT_CONFIG_DIR") \
    else Path.home() / ".config" / "email-client"
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"
SECRET_NAMESPACE = "email-client"

GMAIL_DEFAULTS = {
    "imap": {"host": "imap.gmail.com", "port": 993},
    "smtp": {"host": "smtp.gmail.com", "port": 465, "starttls": False},
}


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load() -> dict:
    if not ACCOUNTS_FILE.exists():
        return {}
    return json.loads(ACCOUNTS_FILE.read_text())


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2) + "\n")
    ACCOUNTS_FILE.chmod(0o600)


def credential_key(nickname: str, purpose: str) -> str:
    return f"{nickname}:{purpose}"


def credential_keys(nickname: str, purpose: str, record: dict | None = None) -> list[str]:
    keys = [credential_key(nickname, purpose)]
    legacy_key = (record or {}).get(f"{purpose}_service")
    if legacy_key and legacy_key not in keys:
        keys.append(legacy_key)
    return keys


def store_credential(nickname: str, purpose: str, record: dict, secret: str) -> None:
    for key in credential_keys(nickname, purpose, record):
        secret_store.store(SECRET_NAMESPACE, key, secret)


def clear_credentials(nickname: str, record: dict) -> None:
    for purpose in ("imap", "smtp"):
        for key in credential_keys(nickname, purpose, record):
            secret_store.clear(SECRET_NAMESPACE, key)
    _oauth_tokens.clear_oauth_credentials(nickname)


def cmd_list(args: argparse.Namespace) -> None:
    data = load()
    out = {nick: {"email": rec["email"], "display_name": rec.get("display_name", "")} for nick, rec in data.items()}
    print(json.dumps(out, indent=2))


def cmd_add(args: argparse.Namespace) -> None:
    data = load()
    if args.nickname in data:
        die(f"account '{args.nickname}' already exists; use 'update' to change it")
    if not args.email:
        die("--email is required")

    record = {
        "email": args.email,
        "display_name": args.display_name or "",
        "imap": {
            "host": args.imap_host or GMAIL_DEFAULTS["imap"]["host"],
            "port": args.imap_port or GMAIL_DEFAULTS["imap"]["port"],
        },
        "smtp": {
            "host": args.smtp_host or GMAIL_DEFAULTS["smtp"]["host"],
            "port": args.smtp_port or GMAIL_DEFAULTS["smtp"]["port"],
            "starttls": bool(args.starttls),
        },
        "auth": args.auth,
        "imap_service": f"email-client-{args.nickname}-imap",
        "smtp_service": f"email-client-{args.nickname}-smtp",
    }
    data[args.nickname] = record
    save(data)
    print(f"Added account '{args.nickname}'.")
    if args.auth == _oauth_tokens.AUTH_GMAIL_OAUTH:
        print("Now run setup-oauth with a Google desktop OAuth client JSON file.")
    else:
        print("Now set credentials:")
        print(f"  echo -n '<app-password>' | dispatcher ... accounts set-password --nickname {args.nickname} --purpose imap")
        print(f"  echo -n '<app-password>' | dispatcher ... accounts set-password --nickname {args.nickname} --purpose smtp")


def cmd_update(args: argparse.Namespace) -> None:
    data = load()
    if args.nickname not in data:
        die(f"no account '{args.nickname}'; use 'add' first")
    record = data[args.nickname]
    if args.email:
        record["email"] = args.email
    if args.display_name is not None:
        record["display_name"] = args.display_name
    if args.imap_host:
        record["imap"]["host"] = args.imap_host
    if args.imap_port:
        record["imap"]["port"] = args.imap_port
    if args.smtp_host:
        record["smtp"]["host"] = args.smtp_host
    if args.smtp_port:
        record["smtp"]["port"] = args.smtp_port
    if args.auth:
        record["auth"] = args.auth
    save(data)
    print(f"Updated account '{args.nickname}'")


def cmd_remove(args: argparse.Namespace) -> None:
    data = load()
    if args.nickname not in data:
        die(f"no account '{args.nickname}'")
    record = data.pop(args.nickname)
    save(data)
    if args.purge_credentials:
        try:
            clear_credentials(args.nickname, record)
        except secret_store.SecretStoreError as exc:
            die(f"could not purge credentials for '{args.nickname}': {exc}")
        print(f"Removed account '{args.nickname}' and purged its stored credentials")
    else:
        print(f"Removed account '{args.nickname}' (stored credentials left in place)")


def cmd_set_password(args: argparse.Namespace) -> None:
    data = load()
    if args.nickname not in data:
        die(f"no account '{args.nickname}'; use 'add' first")
    if args.purpose not in ("imap", "smtp"):
        die("--purpose must be 'imap' or 'smtp'")
    secret = sys.stdin.read().strip()
    if not secret:
        die("no secret provided on stdin")
    try:
        store_credential(args.nickname, args.purpose, data[args.nickname], secret)
    except secret_store.SecretStoreError as exc:
        die(f"could not store {args.purpose} credential for '{args.nickname}': {exc}")
    print(f"Stored {args.purpose} credential for '{args.nickname}'")


def cmd_setup_oauth(args: argparse.Namespace) -> None:
    data = load()
    if args.nickname not in data:
        die(f"no account '{args.nickname}'; use 'add' first")
    try:
        _oauth_tokens.run_google_oauth_setup(
            args.nickname,
            data[args.nickname],
            Path(args.client_config),
            open_browser=not args.no_open_browser,
        )
    except (OSError, json.JSONDecodeError, secret_store.SecretStoreError, _oauth_tokens.OAuthError) as exc:
        die(f"OAuth setup failed for '{args.nickname}': {exc}")
    save(data)
    print(f"Stored Gmail OAuth credentials for '{args.nickname}'")


def cmd_resolve(args: argparse.Namespace) -> None:
    data = load()
    if args.nickname not in data:
        die(f"unknown account '{args.nickname}'. Known: {', '.join(data) or '(none)'}")
    print(json.dumps(data[args.nickname], indent=2))


class Interface(PythonArgvMachineInterface):
    prog = "accounts.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p_add = sub.add_parser("add")
    p_add.add_argument("--nickname", required=True)
    p_add.add_argument("--email", required=True)
    p_add.add_argument("--display-name")
    p_add.add_argument("--imap-host")
    p_add.add_argument("--imap-port", type=int)
    p_add.add_argument("--smtp-host")
    p_add.add_argument("--smtp-port", type=int)
    p_add.add_argument("--starttls", action="store_true")
    p_add.add_argument("--auth", choices=(_oauth_tokens.AUTH_APP_PASSWORD, _oauth_tokens.AUTH_GMAIL_OAUTH),
                       default=_oauth_tokens.AUTH_APP_PASSWORD)
    p_add.set_defaults(func=cmd_add)

    p_update = sub.add_parser("update")
    p_update.add_argument("--nickname", required=True)
    p_update.add_argument("--email")
    p_update.add_argument("--display-name")
    p_update.add_argument("--imap-host")
    p_update.add_argument("--imap-port", type=int)
    p_update.add_argument("--smtp-host")
    p_update.add_argument("--smtp-port", type=int)
    p_update.add_argument("--auth", choices=(_oauth_tokens.AUTH_APP_PASSWORD, _oauth_tokens.AUTH_GMAIL_OAUTH))
    p_update.set_defaults(func=cmd_update)

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("--nickname", required=True)
    p_remove.add_argument("--purge-credentials", action="store_true")
    p_remove.set_defaults(func=cmd_remove)

    p_setpw = sub.add_parser("set-password")
    p_setpw.add_argument("--nickname", required=True)
    p_setpw.add_argument("--purpose", required=True)
    p_setpw.set_defaults(func=cmd_set_password)

    p_oauth = sub.add_parser("setup-oauth")
    p_oauth.add_argument("--nickname", required=True)
    p_oauth.add_argument("--client-config", required=True)
    p_oauth.add_argument("--no-open-browser", action="store_true")
    p_oauth.set_defaults(func=cmd_setup_oauth)

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--nickname", required=True)
    p_resolve.set_defaults(func=cmd_resolve)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

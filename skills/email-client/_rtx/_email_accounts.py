#!/usr/bin/env python3
"""Account registry for email-client: nickname -> {email, IMAP/SMTP settings}.

Lives at ~/.config/email-client/accounts.json — deliberately OUTSIDE the
skills git repo (which may go public). Passwords are never stored here; they
stay in the host credential store via officina.credentials.secret_store.

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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import officina.credentials.secret_store as secret_store
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
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


class CredentialFileBindingError(RuntimeError):
    """Stable service-owned failure returned to the Google coordinator."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def accounts_use_google_credential(*, nickname: str, credential_id: str, home: Path, platform: str = sys.platform) -> None:
    """Bind one email-client account to a shared connect-google credential.

    Validates the credential grants Gmail scope *before* writing anything,
    then stores only the opaque ``credential_id`` on the TARGET ACCOUNT's own
    record in accounts.json — never the client secret or refresh token,
    which stay in officina.credentials.google' registry/secret store.

    Mutates the loaded record in place and writes the whole registry back via
    save(), the same merge-not-replace convention cmd_update already uses, so
    every other field on the account (email, imap/smtp settings, auth mode,
    legacy oauth block, etc.) survives untouched.
    """
    from officina.credentials.google import SERVICE_SCOPES, GoogleCredentialError, load_credential

    data = load()
    if nickname not in data:
        raise SystemExit(f"unknown account '{nickname}'. Known: {', '.join(data) or '(none)'}")

    try:
        ref = load_credential(credential_id, home=home, platform=platform)
        if not SERVICE_SCOPES["gmail"] <= ref.granted_scopes:
            raise GoogleCredentialError(f"credential {credential_id} lacks Gmail scope")
    except GoogleCredentialError as exc:
        raise SystemExit(str(exc)) from exc

    data[nickname]["credential_id"] = credential_id
    # Binding a credential must also flip the account into OAuth mode: every
    # real XOAUTH2 call site (_imap_gateway.py, _smtp_transport.py) gates
    # exclusively on is_gmail_oauth(account)/account_auth_mode(account),
    # which look only at the "auth" field -- credential_id's mere presence
    # is never consulted at authentication time. Without this, binding a
    # credential is a silent no-op that leaves the account on its prior auth
    # mode (e.g. app-password) with no error.
    data[nickname]["auth"] = _oauth_tokens.AUTH_GMAIL_OAUTH
    save(data)


def _existing_binding_subject(
    record: dict[str, object], *, home: Path, platform: str
) -> tuple[bool, str | None]:
    """Return whether prior Gmail OAuth state exists and its stable subject if provable."""
    from officina.credentials.google import (
        GoogleCredentialError,
        load_credential,
        load_credential_file,
    )

    if "credential_file" in record:
        value = record["credential_file"]
        if not isinstance(value, str) or not value.strip():
            return True, None
        try:
            return True, load_credential_file(Path(value)).subject
        except GoogleCredentialError:
            return True, None
    if "credential_id" in record:
        value = record["credential_id"]
        if not isinstance(value, str) or not value.strip():
            return True, None
        try:
            return True, load_credential(
                value.strip(), home=home, platform=platform
            ).subject
        except GoogleCredentialError:
            return True, None
    legacy_oauth = bool(record.get("oauth")) or (
        record.get("auth") == _oauth_tokens.AUTH_GMAIL_OAUTH
    )
    return legacy_oauth, None


def accounts_use_google_credential_file(
    *,
    nickname: str,
    credential_file: Path,
    home: Path,
    allow_account_change: bool = False,
    platform: str = sys.platform,
    urlopen: Callable = urllib.request.urlopen,
) -> dict[str, object]:
    """Validate, live-probe, then bind one named Gmail account to a file."""
    from officina.credentials.google import (
        GoogleCredentialError,
        SERVICE_SCOPES,
        load_credential_file,
        refresh_access_token_from_file,
    )

    try:
        data = load()
    except Exception as exc:
        raise CredentialFileBindingError(
            "invalid-service-config", f"could not read {ACCOUNTS_FILE}: {exc}"
        ) from exc
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, dict)
        for key, value in data.items()
    ):
        raise CredentialFileBindingError(
            "invalid-service-config",
            f"{ACCOUNTS_FILE} must contain an object of account objects",
        )
    if nickname not in data:
        raise CredentialFileBindingError(
            "unknown-account", f"unknown account '{nickname}'. Known: {', '.join(data) or '(none)'}"
        )
    record = data[nickname]
    path = Path(credential_file).expanduser().resolve()
    try:
        ref = load_credential_file(path)
    except GoogleCredentialError as exc:
        raise CredentialFileBindingError("invalid-credential-file", str(exc)) from exc
    if not SERVICE_SCOPES["gmail"] <= ref.granted_scopes:
        raise CredentialFileBindingError(
            "insufficient-scope", f"credential file {path} lacks Gmail scope"
        )

    configured_email = record.get("email")
    if not isinstance(configured_email, str) or not configured_email.strip():
        raise CredentialFileBindingError(
            "invalid-account-email", f"account '{nickname}' has no configured email"
        )
    if configured_email.strip().casefold() != ref.account.casefold():
        raise CredentialFileBindingError(
            "account-email-mismatch",
            f"descriptor account {ref.account} does not match {nickname} email {configured_email}",
        )

    has_prior_state, prior_subject = _existing_binding_subject(
        record, home=home, platform=platform
    )
    if has_prior_state and prior_subject != ref.subject and not allow_account_change:
        raise CredentialFileBindingError(
            "account-change-confirmation-required",
            f"existing Gmail credential identity for '{nickname}' differs or cannot be established",
        )

    try:
        token = refresh_access_token_from_file(
            path,
            required_scopes=SERVICE_SCOPES["gmail"],
            urlopen=urlopen,
        )
        request = urllib.request.Request(
            GMAIL_PROFILE_URL,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urlopen(request, timeout=30) as response:
            profile = json.loads(response.read().decode("utf-8"))
        profile_email = profile.get("emailAddress") if isinstance(profile, dict) else None
        if not isinstance(profile_email, str) or (
            profile_email.casefold() != ref.account.casefold()
        ):
            raise CredentialFileBindingError(
                "live-account-mismatch",
                "Gmail profile identity does not match the credential descriptor",
            )
    except CredentialFileBindingError:
        raise
    except Exception as exc:
        raise CredentialFileBindingError("live-check-failed", str(exc)) from exc

    record["credential_file"] = str(path)
    record["auth"] = _oauth_tokens.AUTH_GMAIL_OAUTH
    save(data)
    return {
        "service": "gmail",
        "credential_file": str(path),
        "account": ref.account,
        "bound": True,
        "verified": True,
    }


def cmd_use_google_credential(args: argparse.Namespace) -> None:
    accounts_use_google_credential(nickname=args.nickname, credential_id=args.credential_id, home=Path(args.home))
    print(f"Bound account '{args.nickname}' to shared Google credential '{args.credential_id}'")


def cmd_use_google_credential_file(args: argparse.Namespace) -> int:
    path = Path(args.credential_file).expanduser().resolve()
    try:
        result = accounts_use_google_credential_file(
            nickname=args.nickname,
            credential_file=path,
            home=Path(args.home),
            allow_account_change=args.allow_account_change,
        )
    except CredentialFileBindingError as exc:
        print(
            json.dumps(
                {
                    "service": "gmail",
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

    p_use_cred = sub.add_parser("use-google-credential")
    p_use_cred.add_argument("--nickname", required=True)
    p_use_cred.add_argument("--credential-id", required=True)
    p_use_cred.add_argument("--home", required=True)
    p_use_cred.set_defaults(func=cmd_use_google_credential)

    p_use_file = sub.add_parser("use-google-credential-file")
    p_use_file.add_argument("--nickname", required=True)
    p_use_file.add_argument("--credential-file", required=True)
    p_use_file.add_argument("--home", required=True)
    p_use_file.add_argument("--allow-account-change", action="store_true")
    p_use_file.set_defaults(func=cmd_use_google_credential_file)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

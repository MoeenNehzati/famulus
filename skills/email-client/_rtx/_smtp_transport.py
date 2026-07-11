#!/usr/bin/env python3
"""Send-side email client: compose MIME and deliver over SMTP, stdlib only."""
from __future__ import annotations

import argparse
import importlib.util
import mimetypes
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import COMMASPACE
from pathlib import Path
from typing import Callable, Sequence

from officina.common import secret_store
from officina.runtime.python_machine_interface import PythonMachineInterface

try:
    from . import _email_accounts, _oauth_tokens
except ImportError:
    SCRIPT_DIR = Path(__file__).resolve().parent
    _accounts_spec = importlib.util.spec_from_file_location("_email_accounts", SCRIPT_DIR / "_email_accounts.py")
    _email_accounts = importlib.util.module_from_spec(_accounts_spec)
    assert _accounts_spec.loader is not None
    _accounts_spec.loader.exec_module(_email_accounts)
    _oauth_spec = importlib.util.spec_from_file_location("_oauth_tokens", SCRIPT_DIR / "_oauth_tokens.py")
    _oauth_tokens = importlib.util.module_from_spec(_oauth_spec)
    assert _oauth_spec.loader is not None
    _oauth_spec.loader.exec_module(_oauth_tokens)


SECRET_NAMESPACE = "email-client"
SMTP_SECRET_PURPOSE = "smtp"
SMTP_TIMEOUT_SECONDS = 30


class SendEmailError(RuntimeError):
    """Raised when a send-email request cannot be completed."""


@dataclass(frozen=True)
class AttachmentSpec:
    path: Path
    display_name: str


@dataclass(frozen=True)
class SendEmailRequest:
    nickname: str
    to_addrs: list[str]
    subject: str
    attachments: list[AttachmentSpec]
    in_reply_to: str
    references: str


def resolve_account(nickname: str) -> dict:
    """Resolve an account nickname through the email-client registry."""
    accounts = _email_accounts.load()
    if nickname not in accounts:
        known = ", ".join(accounts) or "(none)"
        raise SendEmailError(f"unknown account '{nickname}'. Known: {known}")
    return accounts[nickname]


def credential_key(nickname: str) -> str:
    return f"{nickname}:{SMTP_SECRET_PURPOSE}"


def credential_keys(nickname: str, account: dict) -> list[str]:
    keys = [credential_key(nickname)]
    legacy_key = account.get("smtp_service")
    if legacy_key and legacy_key not in keys:
        keys.append(legacy_key)
    return keys


def get_smtp_password(nickname: str, account: dict) -> str:
    """Return the SMTP credential from the shared secret_store API."""
    checked_keys = credential_keys(nickname, account)
    try:
        for key in checked_keys:
            password = secret_store.lookup(SECRET_NAMESPACE, key)
            if password:
                return password
    except secret_store.SecretStoreError as exc:
        raise SendEmailError(f"could not read SMTP credential for account '{nickname}': {exc}") from exc
    raise SendEmailError(f"no SMTP credential for account '{nickname}'; checked keys: {', '.join(checked_keys)}")


def parse_attachment_spec(raw: str) -> AttachmentSpec:
    """Parse ``path[:DisplayName]`` without treating every colon as metadata."""
    full_path = Path(raw)
    if full_path.exists():
        return AttachmentSpec(full_path, full_path.name)

    maybe_path, sep, display_name = raw.rpartition(":")
    if sep and maybe_path and display_name:
        candidate = Path(maybe_path)
        if candidate.exists():
            return AttachmentSpec(candidate, display_name)

    return AttachmentSpec(full_path, full_path.name)


def request_from_args(args: argparse.Namespace) -> SendEmailRequest:
    references = args.references
    if args.in_reply_to and not references:
        references = args.in_reply_to
    attachments = [parse_attachment_spec(raw) for raw in args.attachments or []]
    return SendEmailRequest(
        nickname=args.nickname,
        to_addrs=list(args.to_addrs),
        subject=args.subject,
        attachments=attachments,
        in_reply_to=args.in_reply_to or "",
        references=references or "",
    )


def build_message(request: SendEmailRequest, account: dict, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = account["email"]
    msg["To"] = COMMASPACE.join(request.to_addrs)
    msg["Subject"] = request.subject
    if request.in_reply_to:
        msg["In-Reply-To"] = request.in_reply_to
    if request.references:
        msg["References"] = request.references
    msg.set_content(body, charset="utf-8")

    for attachment in request.attachments:
        if not attachment.path.is_file():
            raise SendEmailError(f"attachment not found: {attachment.path}")
        data = attachment.path.read_bytes()
        content_type, _encoding = mimetypes.guess_type(str(attachment.path))
        if not content_type:
            content_type = "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.display_name,
        )

    return msg


def open_smtp_connection(account: dict):
    smtp = account["smtp"]
    host = smtp["host"]
    port = int(smtp["port"])
    context = ssl.create_default_context()
    if smtp.get("starttls"):
        client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        return client
    return smtplib.SMTP_SSL(host, port, context=context, timeout=SMTP_TIMEOUT_SECONDS)


def authenticate_smtp(
    client: object,
    nickname: str,
    account: dict,
    password_resolver: Callable[[str, dict], str] = get_smtp_password,
    access_token_resolver: Callable[[str, dict], str] = _oauth_tokens.refresh_google_access_token,
) -> None:
    if _oauth_tokens.is_gmail_oauth(account):
        try:
            access_token = access_token_resolver(nickname, account)
        except _oauth_tokens.OAuthError as exc:
            raise SendEmailError(str(exc)) from exc
        client.auth("XOAUTH2", lambda _challenge=None: _oauth_tokens.xoauth2_string(account["email"], access_token))
        return

    password = password_resolver(nickname, account)
    client.login(account["email"], password)


def deliver_message(
    request: SendEmailRequest,
    body: str,
    account_resolver: Callable[[str], dict] = resolve_account,
    password_resolver: Callable[[str, dict], str] = get_smtp_password,
    smtp_opener: Callable[[dict], object] = open_smtp_connection,
) -> None:
    account = account_resolver(request.nickname)
    message = build_message(request, account, body)

    with smtp_opener(account) as client:
        authenticate_smtp(client, request.nickname, account, password_resolver=password_resolver)
        client.send_message(message, from_addr=account["email"], to_addrs=request.to_addrs)


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument("--from", dest="nickname", required=True)
    parser.add_argument("--to", dest="to_addrs", action="append", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--attach", dest="attachments", action="append", default=[])
    parser.add_argument("--in-reply-to", default="")
    parser.add_argument("--references", default="")
    return parser


class Interface(PythonMachineInterface):
    prog = "send-email"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--from", dest="nickname", required=True)
        parser.add_argument("--to", dest="to_addrs", action="append", required=True)
        parser.add_argument("--subject", required=True)
        parser.add_argument("--attach", dest="attachments", action="append", default=[])
        parser.add_argument("--in-reply-to", default="")
        parser.add_argument("--references", default="")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        try:
            deliver_message(request_from_args(args), sys.stdin.read())
        except SendEmailError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser("send-email")
    args = parser.parse_args(argv)
    try:
        deliver_message(request_from_args(args), sys.stdin.read())
    except SendEmailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

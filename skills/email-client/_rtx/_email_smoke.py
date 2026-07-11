#!/usr/bin/env python3
"""Live provider smoke checks for configured email-client accounts."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from . import _email_accounts, _imap_gateway, _smtp_transport
except ImportError:
    _THIS_DIR = Path(__file__).resolve().parent

    def _load_peer(name: str):
        spec = importlib.util.spec_from_file_location(name, _THIS_DIR / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    _email_accounts = _load_peer("_email_accounts")
    _imap_gateway = _load_peer("_imap_gateway")
    _smtp_transport = _load_peer("_smtp_transport")


class SmokeError(RuntimeError):
    """Raised when a live provider check fails."""


@dataclass(frozen=True)
class SmokeResult:
    check: str
    ok: bool
    detail: str

    def to_json(self) -> dict:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


def resolve_account(nickname: str) -> dict:
    accounts = _email_accounts.load()
    if nickname not in accounts:
        known = ", ".join(accounts) or "(none)"
        raise SmokeError(f"unknown account '{nickname}'. Known: {known}")
    return accounts[nickname]


def check_imap(
    nickname: str,
    connector: Callable[[str], tuple[object, dict]] = _imap_gateway.connect,
) -> SmokeResult:
    conn, _account = connector(nickname)
    try:
        status, _data = conn.noop()
        if status != "OK":
            raise SmokeError(f"IMAP NOOP returned {status}")
        return SmokeResult("imap", True, "authenticated and NOOP succeeded")
    finally:
        conn.logout()


def check_smtp_auth(
    nickname: str,
    account_resolver: Callable[[str], dict] = resolve_account,
    smtp_opener: Callable[[dict], object] = _smtp_transport.open_smtp_connection,
) -> SmokeResult:
    account = account_resolver(nickname)
    with smtp_opener(account) as client:
        _smtp_transport.authenticate_smtp(client, nickname, account)
        if hasattr(client, "noop"):
            code, detail = client.noop()
            if code >= 400:
                raise SmokeError(f"SMTP NOOP returned {code} {detail!r}")
    return SmokeResult("smtp-auth", True, "authenticated and NOOP succeeded")


def check_send_self(
    nickname: str,
    body: str,
    deliverer: Callable[[_smtp_transport.SendEmailRequest, str], None] = _smtp_transport.deliver_message,
    account_resolver: Callable[[str], dict] = resolve_account,
) -> SmokeResult:
    account = account_resolver(nickname)
    request = _smtp_transport.SendEmailRequest(
        nickname=nickname,
        to_addrs=[account["email"]],
        subject="email-client live smoke",
        attachments=[],
        in_reply_to="",
        references="",
    )
    deliverer(request, body)
    return SmokeResult("send-self", True, f"sent smoke email to {account['email']}")


def run_smoke(args: argparse.Namespace) -> list[SmokeResult]:
    results = []
    if args.imap:
        results.append(check_imap(args.account))
    if args.smtp_auth:
        results.append(check_smtp_auth(args.account))
    if args.send_self:
        body = args.body or "email-client live smoke test"
        results.append(check_send_self(args.account, body))
    if not results:
        raise SmokeError("select at least one smoke check")
    return results


class Interface(PythonArgvMachineInterface):
    prog = "email-smoke.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument("-a", "--account", required=True)
    parser.add_argument("--imap", action="store_true", help="Authenticate to IMAP and run NOOP.")
    parser.add_argument("--smtp-auth", action="store_true", help="Authenticate to SMTP and run NOOP.")
    parser.add_argument("--send-self", action="store_true", help="Send a smoke email to the account's own address.")
    parser.add_argument("--body", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser("email-smoke")
    args = parser.parse_args(argv)
    try:
        results = run_smoke(args)
    except SmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"ok": all(result.ok for result in results), "results": [r.to_json() for r in results]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import importlib.util
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
SKILL_ROOT = Path(__file__).parent.parent
for path in (str(REPO_SRC), str(SKILL_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

SMOKE_PY = SKILL_ROOT / "_rtx" / "_email_smoke.py"
spec = importlib.util.spec_from_file_location("_rtx._email_smoke", SMOKE_PY)
smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


def test_check_imap_runs_noop_and_logout():
    calls = []

    class FakeConn:
        def noop(self):
            calls.append(("noop",))
            return "OK", []

        def logout(self):
            calls.append(("logout",))

    result = smoke.check_imap("work", connector=lambda nickname: (FakeConn(), {}))

    assert result.to_json() == {"check": "imap", "ok": True, "detail": "authenticated and NOOP succeeded"}
    assert calls == [("noop",), ("logout",)]


def test_check_smtp_auth_authenticates_without_sending(monkeypatch):
    calls = []
    account = {"email": "me@example.com", "smtp": {"host": "smtp.example.com", "port": 465}}

    class FakeClient:
        def __enter__(self):
            calls.append(("enter",))
            return self

        def __exit__(self, exc_type, exc, tb):
            calls.append(("exit", exc_type))

        def noop(self):
            calls.append(("noop",))
            return 250, b"OK"

    def authenticate(client, nickname, resolved_account):
        calls.append(("auth", nickname, resolved_account["email"]))

    monkeypatch.setattr(smoke._smtp_transport, "authenticate_smtp", authenticate)

    result = smoke.check_smtp_auth(
        "work",
        account_resolver=lambda nickname: account,
        smtp_opener=lambda resolved_account: FakeClient(),
    )

    assert result.to_json() == {"check": "smtp-auth", "ok": True, "detail": "authenticated and NOOP succeeded"}
    assert calls == [("enter",), ("auth", "work", "me@example.com"), ("noop",), ("exit", None)]


def test_check_send_self_requires_explicit_deliverer_call():
    calls = []

    def deliver(request, body):
        calls.append((request.nickname, tuple(request.to_addrs), request.subject, body))

    result = smoke.check_send_self(
        "work",
        "body",
        deliverer=deliver,
        account_resolver=lambda nickname: {"email": "me@example.com"},
    )

    assert result.to_json() == {"check": "send-self", "ok": True, "detail": "sent smoke email to me@example.com"}
    assert calls == [("work", ("me@example.com",), "email-client live smoke", "body")]

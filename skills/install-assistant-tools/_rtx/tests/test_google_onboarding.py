from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _google_onboarding import run_google_onboarding


class _FakeDispatcher:
    def __init__(self, path: Path):
        self.path = path

    @property
    def calls(self):
        log_path = self.path.parent / "calls.jsonl"
        if not log_path.exists():
            return []
        calls = []
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            calls.append((record["interface"], record["args"]))
        return calls


_FAKE_DISPATCHER_TEMPLATE = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    RESPONSES = json.loads((HERE / "responses.json").read_text())

    def main() -> int:
        argv = sys.argv[1:]
        # dispatcher --caller-skill <caller> <interface> [args...]
        assert argv[0] == "--caller-skill"
        caller = argv[1]
        interface = argv[2]
        args = argv[3:]

        with (HERE / "calls.jsonl").open("a") as fh:
            fh.write(json.dumps({"caller": caller, "interface": interface, "args": args}) + "\\n")

        response = RESPONSES.get(interface)
        if response is None:
            print("{}")
            return 0
        if isinstance(response, dict) and response.get("__exit_nonzero__"):
            sys.stderr.write("simulated failure\\n")
            return 1
        if isinstance(response, dict) and "client_secret" in response:
            # Simulate a hypothetical downstream leak: secret-shaped text
            # written to stderr, which _dispatch never reads on success.
            sys.stderr.write("noise client_secret=leaked-secret refresh_token=leaked-token\\n")
        print(json.dumps(response))
        return 0

    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


def _write_fake_dispatcher(tmp_path: Path, responses: dict) -> _FakeDispatcher:
    script_dir = tmp_path / "fake_dispatcher"
    script_dir.mkdir(exist_ok=True)
    (script_dir / "responses.json").write_text(json.dumps(responses))

    if sys.platform.startswith("win"):
        # `dispatcher_launcher_path` resolves to `dispatcher.bat` on Windows
        # (real dispatcher.bat is itself a batch wrapper that invokes a
        # python interpreter -- see _install_launcher/_windows_launcher.py's
        # _windows_dispatcher_content). A bare shebang script named
        # `dispatcher` (no `.bat`/`.exe`) can't be launched directly by
        # subprocess.run on Windows, which doesn't interpret shebang lines
        # and fails with WinError 193 trying to load it as a PE executable.
        logic_path = script_dir / "dispatcher_impl.py"
        logic_path.write_text(_FAKE_DISPATCHER_TEMPLATE)
        script_path = script_dir / "dispatcher.bat"
        script_path.write_text(
            f'@echo off\r\n"{sys.executable}" "{logic_path}" %*\r\n', newline=""
        )
    else:
        script_path = script_dir / "dispatcher"
        script_path.write_text(_FAKE_DISPATCHER_TEMPLATE)
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return _FakeDispatcher(script_path)


_DEFAULT_RESPONSES = {
    "connect-google._rtx.interface.client-status": {
        "status": "valid",
        "client_type": "desktop",
        "path": "/fake/client.json",
    },
    "connect-google._rtx.interface.authorize-services": {
        "schema_version": 1,
        "account": "user@example.com",
        "credential_id": "cred-123",
        "requested_services": ["drive", "calendar", "gmail"],
        "granted_services": ["drive", "calendar", "gmail"],
        "denied_services": [],
    },
    "cloud-files._rtx.interface.use-google-credential": {},
    "g-calendar._rtx.interface.use-google-credential": {},
    "email-client._rtx.interface.accounts-use-google-credential": {},
}


@pytest.fixture
def fake_dispatcher(tmp_path):
    return _write_fake_dispatcher(tmp_path, _DEFAULT_RESPONSES)


@pytest.fixture
def fake_dispatcher_with_leaky_stdout(tmp_path):
    responses = dict(_DEFAULT_RESPONSES)
    responses["connect-google._rtx.interface.authorize-services"] = {
        "schema_version": 1,
        "account": "user@example.com",
        "credential_id": "cred-123",
        "requested_services": ["drive"],
        "granted_services": ["drive"],
        "denied_services": [],
        # Simulate a hypothetical downstream bug leaking secret-shaped keys
        # into the response payload -- our result object must not surface
        # them regardless.
        "client_secret": "leaked-secret",
        "refresh_token": "leaked-token",
    }
    return _write_fake_dispatcher(tmp_path, responses)


def test_run_google_onboarding_calls_dispatcher_in_order(fake_dispatcher, tmp_path):
    result = run_google_onboarding(
        ["drive", "calendar", "gmail"], dispatcher_path=fake_dispatcher.path,
        home=tmp_path, stdin_isatty=False,
    )
    assert fake_dispatcher.calls[0][0] == "connect-google._rtx.interface.client-status"
    assert any(c[0] == "connect-google._rtx.interface.authorize-services" for c in fake_dispatcher.calls)
    assert result.status == "partial"  # gmail deferred (no nickname supplied)
    assert result.credential_id == "cred-123"


def test_run_google_onboarding_binds_drive_and_calendar(fake_dispatcher, tmp_path):
    run_google_onboarding(
        ["drive", "calendar"], dispatcher_path=fake_dispatcher.path,
        home=tmp_path, stdin_isatty=False,
    )
    interfaces = [c[0] for c in fake_dispatcher.calls]
    assert "cloud-files._rtx.interface.use-google-credential" in interfaces
    assert "g-calendar._rtx.interface.use-google-credential" in interfaces
    drive_call = next(c for c in fake_dispatcher.calls if c[0] == "cloud-files._rtx.interface.use-google-credential")
    assert "--credential-id" in drive_call[1]
    assert "cred-123" in drive_call[1]


def test_run_google_onboarding_non_interactive_with_no_services_does_not_block(fake_dispatcher, tmp_path):
    result = run_google_onboarding(None, dispatcher_path=fake_dispatcher.path, home=tmp_path, stdin_isatty=False)
    assert result.status in {"skipped", "needs_selection"}
    assert fake_dispatcher.calls == []


def test_run_google_onboarding_dry_run_does_not_call_dispatcher(fake_dispatcher, tmp_path):
    result = run_google_onboarding(
        ["drive"], dispatcher_path=fake_dispatcher.path, home=tmp_path,
        dry_run=True, stdin_isatty=False,
    )
    assert result.status == "skipped"
    assert fake_dispatcher.calls == []


def test_run_google_onboarding_skips_when_client_not_installed(tmp_path):
    responses = dict(_DEFAULT_RESPONSES)
    responses["connect-google._rtx.interface.client-status"] = {"status": "missing", "client_type": "none", "path": "x"}
    dispatcher = _write_fake_dispatcher(tmp_path, responses)

    result = run_google_onboarding(
        ["drive"], dispatcher_path=dispatcher.path, home=tmp_path, stdin_isatty=False,
    )
    assert result.status == "skipped"
    # Only client-status should have been called -- authorize-services must
    # not run without a valid client.
    assert [c[0] for c in dispatcher.calls] == ["connect-google._rtx.interface.client-status"]


def test_run_google_onboarding_output_never_contains_secrets(fake_dispatcher_with_leaky_stdout, tmp_path):
    result = run_google_onboarding(
        ["drive"], dispatcher_path=fake_dispatcher_with_leaky_stdout.path,
        home=tmp_path, stdin_isatty=False,
    )
    dumped = str(result)
    assert "client_secret" not in dumped
    assert "leaked-secret" not in dumped
    assert "refresh_token" not in dumped
    assert "leaked-token" not in dumped


def test_run_google_onboarding_defers_gmail_binding_without_an_account_nickname(fake_dispatcher, tmp_path):
    result = run_google_onboarding(
        ["gmail"], dispatcher_path=fake_dispatcher.path, home=tmp_path, stdin_isatty=False,
    )
    interfaces = [c[0] for c in fake_dispatcher.calls]
    assert "email-client._rtx.interface.accounts-use-google-credential" not in interfaces
    assert "gmail" in result.granted_services
    assert "gmail" in result.deferred_services
    assert result.credential_id == "cred-123"
    assert result.status == "partial"


def test_run_google_onboarding_binds_gmail_when_nickname_supplied(fake_dispatcher, tmp_path):
    result = run_google_onboarding(
        ["gmail"], dispatcher_path=fake_dispatcher.path, home=tmp_path,
        gmail_nickname="personal", stdin_isatty=False,
    )
    call = next(
        c for c in fake_dispatcher.calls
        if c[0] == "email-client._rtx.interface.accounts-use-google-credential"
    )
    assert "--nickname" in call[1] and "personal" in call[1]
    assert "gmail" not in result.deferred_services
    assert result.status == "completed"


def test_run_google_onboarding_reports_denied_services_as_partial(tmp_path):
    responses = dict(_DEFAULT_RESPONSES)
    responses["connect-google._rtx.interface.authorize-services"] = {
        "schema_version": 1,
        "account": "user@example.com",
        "credential_id": "cred-123",
        "requested_services": ["drive", "calendar"],
        "granted_services": ["drive"],
        "denied_services": ["calendar"],
    }
    dispatcher = _write_fake_dispatcher(tmp_path, responses)

    result = run_google_onboarding(
        ["drive", "calendar"], dispatcher_path=dispatcher.path, home=tmp_path, stdin_isatty=False,
    )
    assert result.status == "partial"
    assert result.denied_services == ("calendar",)


def test_run_google_onboarding_rejects_unknown_service(fake_dispatcher, tmp_path):
    with pytest.raises(ValueError):
        run_google_onboarding(
            ["not-a-real-service"], dispatcher_path=fake_dispatcher.path,
            home=tmp_path, stdin_isatty=False,
        )


def test_run_google_onboarding_survives_a_later_service_binding_failure(tmp_path):
    # drive binds fine; calendar's use-google-credential dispatch fails
    # unexpectedly (e.g. a downstream skill bug). The partial success on
    # drive must not be lost -- the function must not raise, and the
    # returned result must show drive bound and calendar's failure captured.
    responses = dict(_DEFAULT_RESPONSES)
    responses["connect-google._rtx.interface.authorize-services"] = {
        "schema_version": 1,
        "account": "user@example.com",
        "credential_id": "cred-123",
        "requested_services": ["drive", "calendar"],
        "granted_services": ["drive", "calendar"],
        "denied_services": [],
    }
    responses["g-calendar._rtx.interface.use-google-credential"] = {"__exit_nonzero__": True}
    dispatcher = _write_fake_dispatcher(tmp_path, responses)

    result = run_google_onboarding(
        ["drive", "calendar"], dispatcher_path=dispatcher.path, home=tmp_path, stdin_isatty=False,
    )

    assert "drive" in result.bound_services
    assert "calendar" not in result.bound_services
    assert any(service == "calendar" for service, _msg in result.failed_services)
    assert result.status == "partial"

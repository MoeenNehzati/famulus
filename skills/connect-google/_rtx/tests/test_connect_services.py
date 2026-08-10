from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_connect_services.py"
RTX_ROOT = MODULE_PATH.parent
SRC_ROOT = MODULE_PATH.parents[3] / "src"
for import_root in (SRC_ROOT, RTX_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

@pytest.fixture(scope="module")
def connect_services_module():
    """Load the production module once; individual fakes remain test-local."""
    spec = importlib.util.spec_from_file_location(
        "connect_google_connect_services", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def completed() -> Callable[..., subprocess.CompletedProcess]:
    def make_completed(
        payload: dict, *, returncode: int = 0
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        )

    return make_completed


@pytest.fixture(scope="module")
def authorization() -> Callable[..., SimpleNamespace]:
    def make_authorization(
        *,
        requested=("calendar", "drive"),
        granted=("calendar", "drive"),
        denied=(),
    ) -> SimpleNamespace:
        return SimpleNamespace(
            account="person@example.com",
            subject="google-subject",
            credential_file="/absolute/credential.json",
            requested_services=requested,
            granted_services=granted,
            denied_services=denied,
        )

    return make_authorization


def test_connect_services_dispatches_only_selected_granted_services(
    connect_services_module,
    completed: Callable[..., subprocess.CompletedProcess],
    authorization: Callable[..., SimpleNamespace],
) -> None:
    """Dispatching from the global service catalog would mutate unselected configs."""
    calls: list[tuple[str, list[str]]] = []

    def fake_authorize(*_args, **_kwargs):
        return authorization()

    def fake_dispatch(key, *, args, **_kwargs):
        calls.append((key, list(args)))
        return completed(
            {
                "service": key,
                "credential_file": "/absolute/credential.json",
                "account": "person@example.com",
                "bound": True,
                "verified": True,
            }
        )

    result = connect_services_module.connect_services(
        services=("calendar", "drive"),
        home=Path("/home/person"),
        account_hint=None,
        gmail_nickname=None,
        allow_account_change=(),
        authorize=fake_authorize,
        dispatch=fake_dispatch,
    )

    assert [key for key, _args in calls] == ["calendar", "drive"]
    assert all("/absolute/credential.json" in args for _key, args in calls)
    assert all("/home/person" in args for _key, args in calls)
    assert result == {
        "schema_version": 1,
        "credential_file": "/absolute/credential.json",
        "requested_services": ["calendar", "drive"],
        "granted_services": ["calendar", "drive"],
        "denied_services": [],
        "bound_services": ["calendar", "drive"],
        "verified_services": ["calendar", "drive"],
        "incomplete_services": {},
        "complete": True,
    }


def test_partial_binding_reports_error_and_retry_reuses_same_file(
    connect_services_module,
    completed: Callable[..., subprocess.CompletedProcess],
    authorization: Callable[..., SimpleNamespace],
) -> None:
    """Retrying authorization instead of the returned path would duplicate consent."""
    authorization_calls = 0
    drive_attempts = 0

    def fake_authorize(*_args, **_kwargs):
        nonlocal authorization_calls
        authorization_calls += 1
        return authorization()

    def fake_dispatch(key, *, args, **_kwargs):
        nonlocal drive_attempts
        if key == "drive":
            drive_attempts += 1
            if drive_attempts == 1:
                return completed(
                    {
                        "service": "drive",
                        "credential_file": "/absolute/credential.json",
                        "bound": False,
                        "verified": False,
                        "error": {"code": "live-check-failed", "message": "HTTP 403"},
                    },
                    returncode=1,
                )
        return completed(
            {
                "service": key,
                "credential_file": args[args.index("--credential-file") + 1],
                "account": "person@example.com",
                "bound": True,
                "verified": True,
            }
        )

    first = connect_services_module.connect_services(
        services=("calendar", "drive"),
        home=Path("/home/person"),
        account_hint=None,
        gmail_nickname=None,
        allow_account_change=(),
        authorize=fake_authorize,
        dispatch=fake_dispatch,
    )
    retry = connect_services_module.bind_credential_file(
        credential_file=Path(first["credential_file"]),
        requested_services=("drive",),
        granted_services=("drive",),
        home=Path("/home/person"),
        gmail_nickname=None,
        allow_account_change=(),
        dispatch=fake_dispatch,
    )

    assert authorization_calls == 1
    assert first["complete"] is False
    assert first["incomplete_services"] == {
        "drive": {"code": "live-check-failed", "message": "HTTP 403"}
    }
    assert retry["credential_file"] == first["credential_file"]
    assert retry["complete"] is True


def test_granted_gmail_without_nickname_is_incomplete_but_does_not_block_drive(
    connect_services_module,
    completed: Callable[..., subprocess.CompletedProcess],
    authorization: Callable[..., SimpleNamespace],
) -> None:
    """Treating the missing nickname as a global error would block independent Drive setup."""
    calls: list[str] = []

    def fake_dispatch(key, *, args, **_kwargs):
        calls.append(key)
        return completed(
            {
                "service": key,
                "credential_file": "/absolute/credential.json",
                "account": "person@example.com",
                "bound": True,
                "verified": True,
            }
        )

    result = connect_services_module.connect_services(
        services=("drive", "gmail"),
        home=Path("/home/person"),
        account_hint=None,
        gmail_nickname=None,
        allow_account_change=(),
        authorize=lambda *_args, **_kwargs: authorization(
            requested=("drive", "gmail"), granted=("drive", "gmail")
        ),
        dispatch=fake_dispatch,
    )

    assert calls == ["drive"]
    assert result["bound_services"] == ["drive"]
    assert result["incomplete_services"]["gmail"]["code"] == "missing-gmail-nickname"
    assert result["complete"] is False


def test_denied_gmail_without_nickname_reports_denial_only(
    connect_services_module,
    completed: Callable[..., subprocess.CompletedProcess],
    authorization: Callable[..., SimpleNamespace],
) -> None:
    result = connect_services_module.connect_services(
        services=("drive", "gmail"),
        home=Path("/home/person"),
        account_hint=None,
        gmail_nickname=None,
        allow_account_change=(),
        authorize=lambda *_args, **_kwargs: authorization(
            requested=("drive", "gmail"), granted=("drive",), denied=("gmail",)
        ),
        dispatch=lambda key, *, args, **_kwargs: completed(
            {
                "service": key,
                "credential_file": "/absolute/credential.json",
                "account": "person@example.com",
                "bound": True,
                "verified": True,
            }
        ),
    )

    assert result["denied_services"] == ["gmail"]
    assert "gmail" not in result["incomplete_services"]
    assert result["complete"] is False


def test_gmail_dispatch_requires_nickname_and_forwards_account_change_approval(
    connect_services_module,
    completed: Callable[..., subprocess.CompletedProcess],
    authorization: Callable[..., SimpleNamespace],
) -> None:
    calls: list[list[str]] = []

    def fake_dispatch(_key, *, args, **_kwargs):
        calls.append(list(args))
        return completed(
            {
                "service": "gmail",
                "credential_file": "/absolute/credential.json",
                "account": "person@example.com",
                "bound": True,
                "verified": True,
            }
        )

    result = connect_services_module.connect_services(
        services=("gmail",),
        home=Path("/home/person"),
        account_hint=None,
        gmail_nickname="work",
        allow_account_change=("gmail",),
        authorize=lambda *_args, **_kwargs: authorization(
            requested=("gmail",), granted=("gmail",)
        ),
        dispatch=fake_dispatch,
    )

    assert result["complete"] is True
    assert calls == [[
        "--nickname",
        "work",
        "--credential-file",
        "/absolute/credential.json",
        "--home",
        "/home/person",
        "--allow-account-change",
    ]]

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


CONNECT_RTX = Path(__file__).resolve().parents[1]
SKILLS_ROOT = CONNECT_RTX.parents[1]
SRC_ROOT = CONNECT_RTX.parents[2] / "src"
for import_root in (SRC_ROOT, CONNECT_RTX):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def service_modules():
    return SimpleNamespace(
        coordinator=_load_module(
            "google_file_e2e_coordinator", CONNECT_RTX / "_connect_services.py"
        ),
        calendar_bind=_load_module(
            "google_file_e2e_calendar_bind",
            SKILLS_ROOT / "g-calendar" / "_rtx" / "_ensure_oauth.py",
        ),
        calendar_runtime=_load_module(
            "google_file_e2e_calendar_runtime",
            SKILLS_ROOT / "g-calendar" / "_rtx" / "_gcal_client.py",
        ),
        drive_bind=_load_module(
            "google_file_e2e_drive_bind",
            SKILLS_ROOT / "cloud-files" / "_rtx" / "_ensure_oauth.py",
        ),
        drive_runtime=_load_module(
            "google_file_e2e_drive_runtime",
            SKILLS_ROOT / "cloud-files" / "_rtx" / "_drive_gateway.py",
        ),
        email_accounts=_load_module(
            "google_file_e2e_email_accounts",
            SKILLS_ROOT / "email-client" / "_rtx" / "_email_accounts.py",
        ),
        email_tokens=_load_module(
            "google_file_e2e_email_tokens",
            SKILLS_ROOT / "email-client" / "_rtx" / "_oauth_tokens.py",
        ),
    )


class FakeSecretBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, value: str) -> None:
        self.values[(namespace, key)] = value

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.values.pop((namespace, key), None) is not None


@pytest.fixture
def integration_env(
    service_modules,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from officina.common import google_credentials

    backend = FakeSecretBackend()
    backend.store(
        "connect-google",
        "oauth-client:client-id:client-secret",
        "raw-client-secret",
    )

    def create_credential(
        *,
        account: str,
        subject: str,
        services: tuple[str, ...],
        minute: int,
        unique_id: str,
    ):
        scopes = {"openid", "email"}
        for service in services:
            scopes.update(google_credentials.SERVICE_SCOPES[service])
        return google_credentials.create_credential_file(
            subject=subject,
            account=account,
            client_id="client-id",
            token_uri="https://oauth.example.test/token",
            granted_services=services,
            granted_scopes=frozenset(scopes),
            refresh_token=f"raw-refresh-token-{unique_id}",
            home=tmp_path,
            platform="linux",
            now=datetime(2026, 8, 10, 15, minute, tzinfo=UTC),
            unique_id=unique_id,
            secret_backend=backend,
        )

    credential = create_credential(
        account="person@example.com",
        subject="google-subject",
        services=("calendar", "drive", "gmail"),
        minute=30,
        unique_id="a1b2c3d4",
    )
    original_refresh = google_credentials.refresh_access_token_from_file

    def refresh_with_test_backend(path, **kwargs):
        kwargs.setdefault("urlopen", urlopen)
        return original_refresh(path, secret_backend=backend, **kwargs)

    monkeypatch.setattr(
        google_credentials,
        "refresh_access_token_from_file",
        refresh_with_test_backend,
    )

    email_dir = tmp_path / ".config" / "email-client"
    email_path = email_dir / "accounts.json"
    email_dir.mkdir(parents=True)
    email_path.write_text(
        json.dumps(
            {
                "work": {
                    "email": "person@example.com",
                    "display_name": "Person",
                    "imap": {"host": "imap.gmail.com", "port": 993},
                    "smtp": {"host": "smtp.gmail.com", "port": 465},
                    "auth": "app-password",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_modules.email_accounts, "CONFIG_DIR", email_dir)
    monkeypatch.setattr(service_modules.email_accounts, "ACCOUNTS_FILE", email_path)

    calendar_config = tmp_path / ".config" / "g-calendar" / "config.json"
    calendar_config.parent.mkdir(parents=True)
    calendar_config.write_text(json.dumps({"calendar": "primary"}), encoding="utf-8")
    drive_config = tmp_path / ".config" / "cloud-files" / "config.json"
    drive_config.parent.mkdir(parents=True)
    drive_config.write_text(
        json.dumps({"remote_llm_root": "assistant/", "timeout_seconds": 45}),
        encoding="utf-8",
    )

    state = SimpleNamespace(
        fail_drive=False,
        probes={"calendar": 0, "drive": 0, "gmail": 0},
        token_requests=0,
        consent_calls=0,
    )

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def urlopen(request, **_kwargs):
        url = request.full_url
        if url == "https://oauth.example.test/token":
            state.token_requests += 1
            return Response({"access_token": "ephemeral-access-token"})
        if "calendar/v3/users/me/calendarList" in url:
            state.probes["calendar"] += 1
            return Response({"items": []})
        if "drive/v3/files" in url:
            state.probes["drive"] += 1
            if state.fail_drive:
                raise OSError("simulated Drive probe failure")
            return Response({"files": []})
        if "gmail/v1/users/me/profile" in url:
            state.probes["gmail"] += 1
            return Response({"emailAddress": "person@example.com"})
        raise AssertionError(f"unexpected URL {url}")

    def authorize(services, **_kwargs):
        state.consent_calls += 1
        requested = tuple(services)
        return SimpleNamespace(
            account="person@example.com",
            subject="google-subject",
            credential_file=str(credential.path),
            requested_services=requested,
            granted_services=requested,
            denied_services=(),
        )

    binders = {
        "calendar": service_modules.calendar_bind.use_google_credential_file,
        "drive": service_modules.drive_bind.use_google_credential_file,
        "gmail": service_modules.email_accounts.accounts_use_google_credential_file,
    }

    def dispatch(service, *, args, **_kwargs):
        def value(flag: str) -> str:
            return args[args.index(flag) + 1]

        kwargs = {
            "credential_file": Path(value("--credential-file")),
            "home": Path(value("--home")),
            "allow_account_change": "--allow-account-change" in args,
            "urlopen": urlopen,
        }
        if service == "gmail":
            kwargs["nickname"] = value("--nickname")
        try:
            payload = binders[service](**kwargs)
            returncode = 0
        except Exception as exc:  # service machine boundary for coordinator test
            payload = {
                "service": service,
                "credential_file": value("--credential-file"),
                "bound": False,
                "verified": False,
                "error": {
                    "code": getattr(exc, "code", "binding-failed"),
                    "message": str(exc),
                },
            }
            returncode = 1
        return subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        )

    return SimpleNamespace(
        modules=service_modules,
        home=tmp_path,
        credential=credential,
        create_credential=create_credential,
        backend=backend,
        state=state,
        urlopen=urlopen,
        authorize=authorize,
        dispatch=dispatch,
        calendar_config=calendar_config,
        drive_config=drive_config,
        email_path=email_path,
    )


def test_one_descriptor_binds_and_refreshes_all_selected_services(
    integration_env,
) -> None:
    env = integration_env
    result = env.modules.coordinator.connect_services(
        services=("calendar", "drive", "gmail"),
        home=env.home,
        account_hint=None,
        gmail_nickname="work",
        allow_account_change=(),
        authorize=env.authorize,
        dispatch=env.dispatch,
    )

    path = str(env.credential.path)
    assert result["complete"] is True
    assert result["credential_file"] == path
    assert env.state.consent_calls == 1
    assert env.state.probes == {"calendar": 1, "drive": 1, "gmail": 1}
    assert len(tuple(env.credential.path.parent.glob("*.json"))) == 1
    assert json.loads(env.calendar_config.read_text())["credential_file"] == path
    assert json.loads(env.drive_config.read_text())["credential_file"] == path
    account = json.loads(env.email_path.read_text())["work"]
    assert account["credential_file"] == path

    assert env.modules.calendar_runtime.get_access_token(home=env.home) == "ephemeral-access-token"
    drive = env.modules.drive_runtime.load_config(env.home)
    assert env.modules.drive_runtime.get_access_token(drive) == "ephemeral-access-token"
    assert (
        env.modules.email_tokens.get_gmail_access_token(
            "work", account, home=env.home, urlopen=env.urlopen
        )
        == "ephemeral-access-token"
    )

    secret_free = "\n".join(
        [
            env.credential.path.read_text(),
            json.dumps(result),
            env.calendar_config.read_text(),
            env.drive_config.read_text(),
            env.email_path.read_text(),
        ]
    )
    assert "raw-client-secret" not in secret_free
    assert "raw-refresh-token" not in secret_free
    assert "ephemeral-access-token" not in secret_free


def test_failed_drive_probe_preserves_config_and_retry_reuses_same_file(
    integration_env,
) -> None:
    env = integration_env
    before = env.drive_config.read_bytes()
    before_email = env.email_path.read_bytes()
    env.state.fail_drive = True
    first = env.modules.coordinator.connect_services(
        services=("calendar", "drive"),
        home=env.home,
        account_hint=None,
        gmail_nickname=None,
        allow_account_change=(),
        authorize=env.authorize,
        dispatch=env.dispatch,
    )

    assert first["complete"] is False
    assert first["incomplete_services"]["drive"]["code"] == "live-check-failed"
    assert env.drive_config.read_bytes() == before
    assert env.email_path.read_bytes() == before_email
    assert env.state.consent_calls == 1

    env.state.fail_drive = False
    retry = env.modules.coordinator.bind_credential_file(
        credential_file=Path(first["credential_file"]),
        requested_services=("drive",),
        granted_services=("drive",),
        home=env.home,
        gmail_nickname=None,
        allow_account_change=(),
        dispatch=env.dispatch,
    )

    assert retry["complete"] is True
    assert retry["credential_file"] == first["credential_file"]
    assert env.state.consent_calls == 1
    assert env.email_path.read_bytes() == before_email
    assert json.loads(env.drive_config.read_text())["credential_file"] == first[
        "credential_file"
    ]


def test_services_and_gmail_nicknames_keep_independent_descriptor_paths(
    integration_env,
) -> None:
    env = integration_env
    calendar = env.create_credential(
        account="person@example.com",
        subject="google-subject",
        services=("calendar",),
        minute=31,
        unique_id="ca1e0da1",
    )
    drive = env.create_credential(
        account="person@example.com",
        subject="google-subject",
        services=("drive",),
        minute=32,
        unique_id="d21e0001",
    )
    work_gmail = env.create_credential(
        account="person@example.com",
        subject="google-subject",
        services=("gmail",),
        minute=33,
        unique_id="6a110001",
    )
    personal_gmail = env.create_credential(
        account="other@example.com",
        subject="other-google-subject",
        services=("gmail",),
        minute=34,
        unique_id="6a110002",
    )

    accounts = json.loads(env.email_path.read_text())
    accounts["personal"] = {
        **accounts["work"],
        "email": "other@example.com",
        "display_name": "Other",
    }
    env.email_path.write_text(json.dumps(accounts), encoding="utf-8")

    env.modules.calendar_bind.use_google_credential_file(
        credential_file=calendar.path, home=env.home, urlopen=env.urlopen
    )
    env.modules.drive_bind.use_google_credential_file(
        credential_file=drive.path, home=env.home, urlopen=env.urlopen
    )
    env.modules.email_accounts.accounts_use_google_credential_file(
        nickname="work",
        credential_file=work_gmail.path,
        home=env.home,
        urlopen=env.urlopen,
    )

    class ProfileResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b'{"emailAddress": "other@example.com"}'

    def other_account_probe(request, **kwargs):
        if "gmail/v1/users/me/profile" in request.full_url:
            return ProfileResponse()
        return env.urlopen(request, **kwargs)

    env.modules.email_accounts.accounts_use_google_credential_file(
        nickname="personal",
        credential_file=personal_gmail.path,
        home=env.home,
        urlopen=other_account_probe,
    )

    bound_accounts = json.loads(env.email_path.read_text())
    bound_paths = {
        json.loads(env.calendar_config.read_text())["credential_file"],
        json.loads(env.drive_config.read_text())["credential_file"],
        bound_accounts["work"]["credential_file"],
        bound_accounts["personal"]["credential_file"],
    }
    assert bound_paths == {
        str(calendar.path),
        str(drive.path),
        str(work_gmail.path),
        str(personal_gmail.path),
    }

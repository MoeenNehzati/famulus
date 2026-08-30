"""Integration tests for accounts.py subcommands. All tests operate on a
tmp_path config dir via EMAIL_CLIENT_CONFIG_DIR — never touch the real
~/.config/email-client/accounts.json.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ACCOUNTS_PY = Path(__file__).parent.parent / "_email_accounts.py"
REPO_SRC = Path(__file__).resolve().parents[4] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def run(config_dir, *args, input=None):
    env = os.environ.copy()
    env["EMAIL_CLIENT_CONFIG_DIR"] = str(config_dir)
    env["PATH"] = "/usr/bin:/bin"
    env["PYTHONPATH"] = str(REPO_SRC)
    return subprocess.run(
        [sys.executable, str(ACCOUNTS_PY), *args],
        capture_output=True, text=True, input=input,
        env=env,
    )


def read_registry(config_dir: Path) -> dict[str, dict[str, object]]:
    """Read the persisted registry directly after mutation commands.

    Dedicated ``list`` and ``resolve`` tests retain CLI coverage; mutation tests
    inspect canonical ``accounts.json`` without a redundant observation process.
    """
    return json.loads((config_dir / "accounts.json").read_text(encoding="utf-8"))


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / "email-client"


@pytest.fixture
def registered_account(config_dir):
    """Seed the canonical state for tests whose contract starts after add."""
    config_dir.mkdir(parents=True)
    accounts_file = config_dir / "accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "work": {
                    "email": "me@example.com",
                    "display_name": "",
                    "imap": {"host": "imap.gmail.com", "port": 993},
                    "smtp": {
                        "host": "smtp.gmail.com",
                        "port": 465,
                        "starttls": False,
                    },
                    "auth": "app-password",
                    "imap_service": "email-client-work-imap",
                    "smtp_service": "email-client-work-smtp",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    accounts_file.chmod(0o600)
    return config_dir


def test_public_list_add_and_resolve_contract(config_dir):
    result = run(config_dir, "list")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}

    result = run(config_dir, "add", "--nickname", "work", "--email", "me@example.com", "--display-name", "Me")
    assert result.returncode == 0

    result = run(config_dir, "list")
    data = json.loads(result.stdout)
    assert data == {"work": {"email": "me@example.com", "display_name": "Me"}}

    result = run(config_dir, "resolve", "--nickname", "work")
    record = json.loads(result.stdout)
    assert record["imap"] == {"host": "imap.gmail.com", "port": 993}
    assert record["smtp"] == {"host": "smtp.gmail.com", "port": 465, "starttls": False}
    assert record["auth"] == "app-password"
    assert record["imap_service"] == "email-client-work-imap"
    assert record["smtp_service"] == "email-client-work-smtp"
    # POSIX mode bits do not model Windows ACL ownership.
    if sys.platform != "win32":
        assert (config_dir / "accounts.json").stat().st_mode & 0o777 == 0o600


def test_add_accepts_explicit_provider_and_auth_settings(config_dir):
    run(
        config_dir, "add", "--nickname", "other", "--email", "me@example.com",
        "--imap-host", "imap.example.com", "--imap-port", "993",
        "--smtp-host", "smtp.example.com", "--smtp-port", "587", "--starttls",
        "--auth", "gmail-oauth",
    )
    record = read_registry(config_dir)["other"]
    assert record["imap"] == {"host": "imap.example.com", "port": 993}
    assert record["smtp"] == {"host": "smtp.example.com", "port": 587, "starttls": True}
    assert record["auth"] == "gmail-oauth"


def test_add_duplicate_nickname_fails(registered_account):
    before = read_registry(registered_account)
    result = run(registered_account, "add", "--nickname", "work", "--email", "other@example.com")
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert read_registry(registered_account) == before


def test_update_then_remove_preserves_lifecycle_contract(registered_account):
    result = run(registered_account, "update", "--nickname", "work", "--display-name", "New Name", "--auth", "gmail-oauth")
    assert result.returncode == 0
    record = read_registry(registered_account)["work"]
    assert record["display_name"] == "New Name"
    assert record["email"] == "me@example.com"  # untouched fields survive
    assert record["auth"] == "gmail-oauth"

    result = run(registered_account, "remove", "--nickname", "work")
    assert result.returncode == 0
    assert read_registry(registered_account) == {}


def test_unknown_account_errors_are_command_specific(registered_account):
    before = read_registry(registered_account)
    result = run(registered_account, "update", "--nickname", "ghost", "--display-name", "X")
    assert result.returncode != 0
    assert "no account" in result.stderr

    result = run(registered_account, "remove", "--nickname", "ghost")
    assert result.returncode != 0

    result = run(registered_account, "resolve", "--nickname", "ghost")
    assert result.returncode != 0
    assert "work" in result.stderr
    assert read_registry(registered_account) == before


# ── set-password / remove --purge-credentials (stubbed keyring) ─────────────

@pytest.fixture
def fake_keyring(tmp_path):
    """A fake keyring package with process-persistent JSON storage."""
    module_dir = tmp_path / "fake-keyring"
    keyring_dir = module_dir / "keyring"
    keyring_dir.mkdir(parents=True)
    log_file = tmp_path / "keyring-calls.log"
    store_file = tmp_path / "keyring-store.json"
    (keyring_dir / "__init__.py").write_text(
        """
import json
import os
from pathlib import Path

from . import errors


class Backend:
    priority = 1


def get_keyring():
    return Backend()


def _store_path():
    return Path(os.environ["FAKE_KEYRING_STORE"])


def _log_path():
    return Path(os.environ["FAKE_KEYRING_LOG"])


def _read():
    path = _store_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write(data):
    _store_path().write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n")


def _log(*parts):
    with _log_path().open("a") as fh:
        fh.write(" ".join(parts) + "\\n")


def set_password(service, username, password):
    _log("set", service, username)
    data = _read()
    data.setdefault(service, {})[username] = password
    _write(data)


def get_password(service, username):
    _log("get", service, username)
    return _read().get(service, {}).get(username)


def delete_password(service, username):
    _log("delete", service, username)
    data = _read()
    if username not in data.get(service, {}):
        raise errors.PasswordDeleteError(username)
    del data[service][username]
    _write(data)
"""
    )
    (keyring_dir / "errors.py").write_text(
        """
class KeyringError(Exception):
    pass


class PasswordDeleteError(KeyringError):
    pass
"""
    )
    return module_dir, log_file, store_file


def run_with_fake_keyring(config_dir, fake_keyring, *args, input=None):
    module_dir, log_file, store_file = fake_keyring
    env = os.environ.copy()
    env["EMAIL_CLIENT_CONFIG_DIR"] = str(config_dir)
    env["PATH"] = "/usr/bin:/bin"
    env["PYTHONPATH"] = os.pathsep.join([str(module_dir), str(REPO_SRC)])
    env["FAKE_KEYRING_LOG"] = str(log_file)
    env["FAKE_KEYRING_STORE"] = str(store_file)
    return subprocess.run(
        [sys.executable, str(ACCOUNTS_PY), *args],
        capture_output=True, text=True, input=input,
        env=env,
    )


def test_set_password_reads_from_stdin_not_argv(registered_account, fake_keyring):
    _, log_file, store_file = fake_keyring
    result = run_with_fake_keyring(
        registered_account,
        fake_keyring,
        "set-password",
        "--nickname",
        "work",
        "--purpose",
        "imap",
        input="s3cret\n",
    )
    assert result.returncode == 0
    calls = log_file.read_text()
    assert "Famulus:email-client work:imap" in calls
    assert "Famulus:email-client email-client-work-imap" in calls
    assert "s3cret" not in calls  # secret goes over stdin, never appears in the logged argv
    stored = json.loads(store_file.read_text())
    assert stored["Famulus:email-client"]["work:imap"] == "s3cret"


# ── use-google-credential (shared connect-google credential, per account) ──

class FakeSecretBackend:
    """Provide an in-memory backend while seeding Google credential fixtures.

    The scope check reads only the JSON registry, so this backend exists solely
    to accept ``store_google_credential``'s refresh-token write.
    """

    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored[(namespace, key)] = value

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.stored.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.stored.pop((namespace, key), None) is not None



# Must match the real runtime platform: the registry is written here via a
# direct in-process call, but read back by `run()` below through a real
# subprocess invocation of `_email_accounts.py`, whose `--home`-resolving
# CLI defaults `platform` to `sys.platform` (the actual OS running the
# test). A hardcoded "linux" here previously matched on Linux CI by
# coincidence but silently wrote the fake registry to the wrong (Linux)
# path layout on macOS/Windows CI, where the subprocess looks it up under
# the platform-correct layout and finds nothing.
CREDENTIAL_PLATFORM = sys.platform


def _store_credential(home: Path, *, granted_gmail_scope: bool) -> str:
    from officina.credentials.google import SERVICE_SCOPES, store_google_credential

    scopes = {"openid", "email"}
    if granted_gmail_scope:
        scopes |= SERVICE_SCOPES["gmail"]

    ref = store_google_credential(
        subject="sub1",
        account="user@example.com",
        client_id="test-client-id",
        token_uri="https://oauth2.example.test/token",
        granted_scopes=frozenset(scopes),
        refresh_token="refresh-token-value",
        home=home,
        platform=CREDENTIAL_PLATFORM,
        secret_backend=FakeSecretBackend(),
    )
    return ref.credential_id


@pytest.fixture
def fake_registry_with_gmail_scope(tmp_path):
    return _store_credential(tmp_path / "credential-home", granted_gmail_scope=True)


@pytest.fixture
def fake_registry_missing_gmail_scope(tmp_path):
    return _store_credential(tmp_path / "credential-home", granted_gmail_scope=False)


def test_use_google_credential_updates_only_binding_fields(
    registered_account, tmp_path, fake_registry_with_gmail_scope
):
    credential_id = fake_registry_with_gmail_scope
    data = read_registry(registered_account)
    data["work"].update(
        {
            "display_name": "Work Mail",
            "imap": {"host": "imap.example.com", "port": 993},
            "smtp": {
                "host": "smtp.example.com",
                "port": 587,
                "starttls": True,
            },
        }
    )
    (registered_account / "accounts.json").write_text(
        json.dumps(data) + "\n", encoding="utf-8"
    )

    result = run(
        registered_account, "use-google-credential",
        "--nickname", "work", "--credential-id", credential_id, "--home", str(tmp_path / "credential-home"),
    )
    assert result.returncode == 0

    record = read_registry(registered_account)["work"]
    assert record["credential_id"] == credential_id
    assert record["auth"] == "gmail-oauth"
    assert "client_secret" not in record
    assert "refresh_token" not in record
    assert "access_token" not in record
    assert record["email"] == "me@example.com"
    assert record["display_name"] == "Work Mail"
    assert record["imap"] == {"host": "imap.example.com", "port": 993}
    assert record["smtp"] == {
        "host": "smtp.example.com",
        "port": 587,
        "starttls": True,
    }
    assert record["imap_service"] == "email-client-work-imap"
    assert record["smtp_service"] == "email-client-work-smtp"


def test_use_google_credential_rejects_insufficient_scope(registered_account, tmp_path, fake_registry_missing_gmail_scope):
    credential_id = fake_registry_missing_gmail_scope
    before = read_registry(registered_account)

    result = run(
        registered_account, "use-google-credential",
        "--nickname", "work", "--credential-id", credential_id, "--home", str(tmp_path / "credential-home"),
    )
    assert result.returncode != 0

    assert read_registry(registered_account) == before


def test_use_google_credential_rejects_unknown_nickname(config_dir, tmp_path, fake_registry_with_gmail_scope):
    credential_id = fake_registry_with_gmail_scope

    result = run(
        config_dir, "use-google-credential",
        "--nickname", "ghost", "--credential-id", credential_id, "--home", str(tmp_path / "credential-home"),
    )

    assert result.returncode != 0
    assert "unknown account" in result.stderr
    assert not (config_dir / "accounts.json").exists()


def test_remove_purge_credentials_clears_both_services(registered_account, fake_keyring):
    _, log_file, store_file = fake_keyring
    store_file.write_text(
        json.dumps(
            {
                "Famulus:email-client": {
                    "work:imap": "imap-secret",
                    "work:smtp": "smtp-secret",
                    "email-client-work-imap": "imap-secret",
                    "email-client-work-smtp": "smtp-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_with_fake_keyring(registered_account, fake_keyring, "remove", "--nickname", "work", "--purge-credentials")

    assert result.returncode == 0
    calls = log_file.read_text()
    assert "delete Famulus:email-client work:imap" in calls
    assert "delete Famulus:email-client work:smtp" in calls
    assert "delete Famulus:email-client email-client-work-imap" in calls
    assert "delete Famulus:email-client email-client-work-smtp" in calls
    assert json.loads(store_file.read_text()) == {"Famulus:email-client": {}}

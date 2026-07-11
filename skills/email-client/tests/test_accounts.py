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

ACCOUNTS_PY = Path(__file__).parent.parent / "_rtx" / "_email_accounts.py"
REPO_SRC = Path(__file__).resolve().parents[3] / "src"


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


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / "email-client"


def test_list_empty_registry(config_dir):
    result = run(config_dir, "list")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}


def test_add_then_list(config_dir):
    result = run(config_dir, "add", "--nickname", "work", "--email", "me@example.com", "--display-name", "Me")
    assert result.returncode == 0

    result = run(config_dir, "list")
    data = json.loads(result.stdout)
    assert data == {"work": {"email": "me@example.com", "display_name": "Me"}}


def test_add_defaults_to_gmail_settings(config_dir):
    run(config_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run(config_dir, "resolve", "--nickname", "work")
    record = json.loads(result.stdout)
    assert record["imap"] == {"host": "imap.gmail.com", "port": 993}
    assert record["smtp"] == {"host": "smtp.gmail.com", "port": 465, "starttls": False}
    assert record["imap_service"] == "email-client-work-imap"
    assert record["smtp_service"] == "email-client-work-smtp"


def test_add_explicit_non_gmail_settings(config_dir):
    run(
        config_dir, "add", "--nickname", "other", "--email", "me@example.com",
        "--imap-host", "imap.example.com", "--imap-port", "993",
        "--smtp-host", "smtp.example.com", "--smtp-port", "587", "--starttls",
    )
    record = json.loads(run(config_dir, "resolve", "--nickname", "other").stdout)
    assert record["imap"] == {"host": "imap.example.com", "port": 993}
    assert record["smtp"] == {"host": "smtp.example.com", "port": 587, "starttls": True}


def test_add_duplicate_nickname_fails(config_dir):
    run(config_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run(config_dir, "add", "--nickname", "work", "--email", "other@example.com")
    assert result.returncode != 0
    assert "already exists" in result.stderr


def test_update_changes_fields(config_dir):
    run(config_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run(config_dir, "update", "--nickname", "work", "--display-name", "New Name")
    assert result.returncode == 0
    record = json.loads(run(config_dir, "resolve", "--nickname", "work").stdout)
    assert record["display_name"] == "New Name"
    assert record["email"] == "me@example.com"  # untouched fields survive


def test_update_unknown_nickname_fails(config_dir):
    result = run(config_dir, "update", "--nickname", "ghost", "--display-name", "X")
    assert result.returncode != 0
    assert "no account" in result.stderr


def test_remove_drops_from_registry(config_dir):
    run(config_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run(config_dir, "remove", "--nickname", "work")
    assert result.returncode == 0
    assert json.loads(run(config_dir, "list").stdout) == {}


def test_remove_unknown_nickname_fails(config_dir):
    result = run(config_dir, "remove", "--nickname", "ghost")
    assert result.returncode != 0


def test_resolve_unknown_nickname_lists_known(config_dir):
    run(config_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run(config_dir, "resolve", "--nickname", "ghost")
    assert result.returncode != 0
    assert "work" in result.stderr


def test_registry_file_permissions_are_owner_only(config_dir):
    run(config_dir, "add", "--nickname", "work", "--email", "me@example.com")
    accounts_file = config_dir / "accounts.json"
    mode = accounts_file.stat().st_mode & 0o777
    assert mode == 0o600


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


def test_set_password_reads_from_stdin_not_argv(config_dir, fake_keyring):
    _, log_file, store_file = fake_keyring
    run_with_fake_keyring(config_dir, fake_keyring, "add", "--nickname", "work", "--email", "me@example.com")
    result = run_with_fake_keyring(
        config_dir,
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


def test_remove_purge_credentials_clears_both_services(config_dir, fake_keyring):
    _, log_file, store_file = fake_keyring
    run_with_fake_keyring(config_dir, fake_keyring, "add", "--nickname", "work", "--email", "me@example.com")
    run_with_fake_keyring(
        config_dir,
        fake_keyring,
        "set-password",
        "--nickname",
        "work",
        "--purpose",
        "imap",
        input="imap-secret\n",
    )
    run_with_fake_keyring(
        config_dir,
        fake_keyring,
        "set-password",
        "--nickname",
        "work",
        "--purpose",
        "smtp",
        input="smtp-secret\n",
    )

    result = run_with_fake_keyring(config_dir, fake_keyring, "remove", "--nickname", "work", "--purge-credentials")

    assert result.returncode == 0
    calls = log_file.read_text()
    assert "delete Famulus:email-client work:imap" in calls
    assert "delete Famulus:email-client work:smtp" in calls
    assert "delete Famulus:email-client email-client-work-imap" in calls
    assert "delete Famulus:email-client email-client-work-smtp" in calls
    assert json.loads(store_file.read_text()) == {"Famulus:email-client": {}}

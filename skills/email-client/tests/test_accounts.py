"""Integration tests for accounts.py subcommands. All tests operate on a
tmp_path config dir via EMAIL_CLIENT_CONFIG_DIR — never touch the real
~/.config/email-client/accounts.json.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ACCOUNTS_PY = Path(__file__).parent.parent / "scripts" / "accounts.py"


def run(config_dir, *args, input=None):
    return subprocess.run(
        [sys.executable, str(ACCOUNTS_PY), *args],
        capture_output=True, text=True, input=input,
        env={"EMAIL_CLIENT_CONFIG_DIR": str(config_dir), "PATH": "/usr/bin:/bin"},
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


# ── set-password / remove --purge-credentials (stubbed secret-tool) ────────

@pytest.fixture
def stub_secret_tool(tmp_path):
    """A fake secret-tool that logs invocations instead of touching the real
    keyring, so set-password/remove --purge-credentials are testable without
    real GNOME keyring access."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    log_file = tmp_path / "calls.log"
    script = bin_dir / "secret-tool"
    script.write_text(f"""#!/usr/bin/env bash
echo "$@" >> {log_file}
if [[ "$1" == "store" ]]; then cat > /dev/null; fi
exit 0
""")
    script.chmod(0o755)
    return bin_dir, log_file


def run_with_stub(config_dir, stub_bin_dir, *args, input=None):
    return subprocess.run(
        [sys.executable, str(ACCOUNTS_PY), *args],
        capture_output=True, text=True, input=input,
        env={
            "EMAIL_CLIENT_CONFIG_DIR": str(config_dir),
            "PATH": f"{stub_bin_dir}:/usr/bin:/bin",
        },
    )


def test_set_password_reads_from_stdin_not_argv(config_dir, stub_secret_tool):
    bin_dir, log_file = stub_secret_tool
    run_with_stub(config_dir, bin_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run_with_stub(config_dir, bin_dir, "set-password", "--nickname", "work", "--purpose", "imap", input="s3cret\n")
    assert result.returncode == 0
    calls = log_file.read_text()
    assert "email-client-work-imap" in calls
    assert "s3cret" not in calls  # secret goes over stdin, never appears in the logged argv


def test_remove_purge_credentials_clears_both_services(config_dir, stub_secret_tool):
    bin_dir, log_file = stub_secret_tool
    run_with_stub(config_dir, bin_dir, "add", "--nickname", "work", "--email", "me@example.com")
    result = run_with_stub(config_dir, bin_dir, "remove", "--nickname", "work", "--purge-credentials")
    assert result.returncode == 0
    calls = log_file.read_text()
    assert "email-client-work-imap" in calls
    assert "email-client-work-smtp" in calls
    assert "clear" in calls

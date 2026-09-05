"""Runtime-boundary tests for the connect-google shared-credential machine interfaces.

These exercise the real dispatcher machine-interface runner
(``officina.runtime.python_machine_interface_runner``) against the declared
process entries, proving the public argv contracts are actually enforced and
that the interfaces are loadable/executable through the normal Officina
runtime -- not just that their underlying pure functions behave correctly
(already covered by test_selected_credential.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = TEST_ROOT.parent
SKILL_ROOT = RUNTIME_ROOT.parent if RUNTIME_ROOT.name == "_rtx" else RUNTIME_ROOT
REPO_ROOT = SKILL_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"
RUNNER = "officina.runtime.python_machine_interface_runner"

sys.path.insert(0, str(SRC_ROOT))
from officina.credentials.google import create_credential_file, install_client  # noqa: E402

PLATFORM = "linux"


class FakeSecretBackend:
    def __init__(self) -> None:
        self.stored: list[tuple[str, str, str]] = []

    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored.append((namespace, key, value))

    def lookup(self, namespace: str, key: str) -> str | None:
        for stored_namespace, stored_key, value in reversed(self.stored):
            if stored_namespace == namespace and stored_key == key:
                return value
        return None

    def clear(self, namespace: str, key: str) -> bool:
        return False


def _desktop_client_payload() -> dict:
    return {
        "installed": {
            "client_id": "test-client-id",
            "project_id": "famulus-test",
            "auth_uri": "https://oauth2.example.test/auth",
            "token_uri": "https://oauth2.example.test/token",
            "client_secret": "shh-its-a-secret",
            "redirect_uris": ["http://localhost"],
        }
    }


def _create_test_credential(home: Path, secret_backend: FakeSecretBackend) -> Path:
    install_client(
        _desktop_client_payload(),
        home=home,
        platform=PLATFORM,
        replace=False,
        secret_backend=secret_backend,
    )
    credential = create_credential_file(
        subject="test-subject",
        account="test@example.com",
        client_id="test-client-id",
        token_uri="https://oauth2.example.test/token",
        granted_services=["drive", "calendar", "gmail"],
        granted_scopes=frozenset(
            {
                "openid",
                "email",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/calendar",
                "https://mail.google.com/",
            }
        ),
        refresh_token="test-refresh-token-secret",
        home=home,
        platform=PLATFORM,
        secret_backend=secret_backend,
    )
    return credential.path


def run_interface(
    args: list[str],
    *,
    home: Path,
    process_entry: str = "SelectSharedCredentialInterface",
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_ROOT),
        "PYTHONIOENCODING": "utf-8:strict",
        "HOME": str(home),
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            RUNNER,
            "_rtx/_selected_credential.py",
            process_entry,
            *args,
        ],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        check=False,
    )


def test_select_shared_credential_executes_and_emits_json(tmp_path: Path) -> None:
    secret_backend = FakeSecretBackend()
    credential_path = _create_test_credential(tmp_path, secret_backend)

    result = run_interface(
        ["--credential-file", str(credential_path)],
        home=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected"] is True
    assert payload["account"] == "test@example.com"


def test_shared_credential_executes_and_emits_json(tmp_path: Path) -> None:
    secret_backend = FakeSecretBackend()
    credential_path = _create_test_credential(tmp_path, secret_backend)
    select_result = run_interface(
        ["--credential-file", str(credential_path)],
        home=tmp_path,
    )
    assert select_result.returncode == 0, select_result.stderr

    result = run_interface([], home=tmp_path, process_entry="SharedCredentialInterface")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected"] is True
    assert payload["credential_file"] == str(credential_path)


def test_shared_credential_error_execution_emits_json_and_fails(tmp_path: Path) -> None:
    result = run_interface([], home=tmp_path, process_entry="SharedCredentialInterface")

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "no-selection"


def test_select_shared_credential_rejects_public_home_flag(tmp_path: Path) -> None:
    result = run_interface(
        ["--credential-file", "/tmp/does-not-matter.json", "--home", str(tmp_path)],
        home=tmp_path,
    )

    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_shared_credential_does_not_accept_public_platform_flag(tmp_path: Path) -> None:
    result = run_interface(
        ["--platform", "linux"],
        home=tmp_path,
        process_entry="SharedCredentialInterface",
    )

    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr

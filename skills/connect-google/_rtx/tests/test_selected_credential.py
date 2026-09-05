from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "_selected_credential.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(MODULE_PATH.parent) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH.parent))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SPEC = importlib.util.spec_from_file_location("connect_google_selected_credential", MODULE_PATH)
selected_credential_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = selected_credential_module
SPEC.loader.exec_module(selected_credential_module)

select_shared_credential = selected_credential_module.select_shared_credential
shared_credential = selected_credential_module.shared_credential

from officina.credentials.google import create_credential_file, install_client

PLATFORM = "linux"

class FakeSecretBackend:
    def __init__(self) -> None:
        self.stored = []
    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored.append((namespace, key, value))
    def lookup(self, namespace: str, key: str) -> str | None:
        for stored_namespace, stored_key, value in reversed(self.stored):
            if stored_namespace == namespace and stored_key == key:
                return value
        return None
    def clear(self, namespace: str, key: str) -> bool:
        return False

def desktop_client_payload(client_id="test-client-id", auth_uri="https://oauth2.example.test/auth", token_uri="https://oauth2.example.test/token"):
    return {"installed": {"client_id": client_id, "project_id": "famulus-test", "auth_uri": auth_uri, "token_uri": token_uri, "client_secret": "shh-its-a-secret", "redirect_uris": ["http://localhost"]}}

@pytest.fixture
def secret_backend():
    return FakeSecretBackend()

@pytest.fixture
def home_dir(tmp_path: Path):
    return tmp_path

def create_test_credential(home: Path, secret_backend: FakeSecretBackend, services: list[str]):
    install_client(desktop_client_payload(), home=home, platform=PLATFORM, replace=False, secret_backend=secret_backend)
    credential = create_credential_file(subject="test-subject", account="test@example.com", client_id="test-client-id", token_uri="https://oauth2.example.test/token", granted_services=services, granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/calendar", "https://mail.google.com/"}), refresh_token="test-refresh-token-secret", home=home, platform=PLATFORM, secret_backend=secret_backend)
    return credential.path

def test_select_full_descriptor(home_dir: Path, secret_backend: FakeSecretBackend):
    path = create_test_credential(home_dir, secret_backend, ["drive", "calendar", "gmail"])
    result = select_shared_credential(credential_file=str(path), home=home_dir, platform=PLATFORM)
    assert "error" not in result
    assert result["selected"] is True
    assert result["account"] == "test@example.com"
    assert result["subject"] == "test-subject"
    assert result["credential_file"] == str(path)
    assert set(result["granted_services"]) == {"drive", "calendar", "gmail"}
    pointer_path = home_dir / ".config" / "famulus" / "connect-google" / "selected-credential.json"
    assert pointer_path.exists()
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer == {"credential_file": str(path), "account": "test@example.com", "subject": "test-subject"}
    assert "client_secret" not in json.dumps(result)
    assert "refresh_token" not in json.dumps(result)
    assert "shh-its-a-secret" not in json.dumps(result) and "test-refresh-token-secret" not in json.dumps(result)

def test_partial_service_fails_unchanged(home_dir: Path, secret_backend: FakeSecretBackend):
    full = create_test_credential(home_dir, secret_backend, ["drive", "calendar", "gmail"])
    select_shared_credential(credential_file=str(full), home=home_dir, platform=PLATFORM)
    pointer_path = home_dir / ".config" / "famulus" / "connect-google" / "selected-credential.json"
    original = pointer_path.read_bytes()
    partial = create_test_credential(home_dir, secret_backend, ["drive", "calendar"])
    result = select_shared_credential(credential_file=str(partial), home=home_dir, platform=PLATFORM)
    assert "error" in result
    assert result["error"]["code"] == "missing-services"
    assert pointer_path.read_bytes() == original

def test_shared_credential_succeeds(home_dir: Path, secret_backend: FakeSecretBackend):
    path = create_test_credential(home_dir, secret_backend, ["drive", "calendar", "gmail"])
    select_shared_credential(credential_file=str(path), home=home_dir, platform=PLATFORM)
    result = shared_credential(home=home_dir, platform=PLATFORM)
    assert "error" not in result
    assert result["selected"] is True
    assert result["account"] == "test@example.com"
    assert result["subject"] == "test-subject"
    assert result["credential_file"] == str(path)
    assert set(result["granted_services"]) == {"drive", "calendar", "gmail"}

def test_identity_drift_fails(home_dir: Path, secret_backend: FakeSecretBackend):
    path = create_test_credential(home_dir, secret_backend, ["drive", "calendar", "gmail"])
    select_shared_credential(credential_file=str(path), home=home_dir, platform=PLATFORM)
    pointer_path = home_dir / ".config" / "famulus" / "connect-google" / "selected-credential.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["account"] = "different@example.com"
    pointer_path.write_text(json.dumps(pointer) + "\n", encoding="utf-8")
    result = shared_credential(home=home_dir, platform=PLATFORM)
    assert "error" in result
    assert result["error"]["code"] == "account-drift"

def test_no_secrets_exposed(home_dir: Path, secret_backend: FakeSecretBackend):
    path = create_test_credential(home_dir, secret_backend, ["drive", "calendar", "gmail"])
    result = select_shared_credential(credential_file=str(path), home=home_dir, platform=PLATFORM)
    pointer_json = (home_dir / ".config" / "famulus" / "connect-google" / "selected-credential.json").read_text(encoding="utf-8")
    for secret in ["shh-its-a-secret", "test-refresh-token-secret"]:
        assert secret not in json.dumps(result)
        assert secret not in pointer_json

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_oauth_bootstrap.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
SPEC = importlib.util.spec_from_file_location("cloud_files_oauth_transaction", MODULE_PATH)
oauth = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(oauth)


def old_credentials(path: Path) -> bytes:
    data = b'{"client_id":"old","client_secret":"old-secret","refresh_token":"old-refresh"}\n'
    path.write_bytes(data)
    return data


def test_missing_access_token_preserves_existing_credentials(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    before = old_credentials(path)
    with pytest.raises(SystemExit, match="access_token"):
        oauth.persist_verified_credentials(
            {"refresh_token": "new-refresh"}, "new", "new-secret", path
        )
    assert path.read_bytes() == before


def test_verification_failure_preserves_existing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.json"
    before = old_credentials(path)
    monkeypatch.setattr(
        oauth,
        "verify_access_token",
        lambda _token: (_ for _ in ()).throw(RuntimeError("verification failed")),
    )
    with pytest.raises(RuntimeError, match="verification failed"):
        oauth.persist_verified_credentials(
            {"access_token": "access", "refresh_token": "refresh"},
            "new",
            "new-secret",
            path,
        )
    assert path.read_bytes() == before


def test_successful_verification_replaces_only_persistent_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.json"
    old_credentials(path)
    seen: list[str] = []
    monkeypatch.setattr(oauth, "verify_access_token", seen.append)

    oauth.persist_verified_credentials(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "scope": "ignored",
        },
        "new",
        "new-secret",
        path,
    )

    assert seen == ["access"]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "client_id": "new",
        "client_secret": "new-secret",
        "refresh_token": "refresh",
    }


def test_drive_verification_uses_bearer_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_urlopen(request):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        return io.BytesIO(b'{"user":{"displayName":"Test"}}')

    monkeypatch.setattr(oauth.urllib.request, "urlopen", fake_urlopen)
    oauth.verify_access_token("access")

    assert seen == {
        "url": "https://www.googleapis.com/drive/v3/about?fields=user",
        "authorization": "Bearer access",
    }

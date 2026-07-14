from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from officina.common.oauth_json import OAuthJsonError, write_oauth_json


def test_write_oauth_json_creates_parent_and_private_file(tmp_path: Path) -> None:
    path = tmp_path / "config" / "client.json"

    write_oauth_json(path, {"installed": {"client_id": "cid"}})

    assert json.loads(path.read_text(encoding="utf-8"))["installed"]["client_id"] == "cid"
    assert path.read_bytes().endswith(b"\n")
    if os.name == "posix":
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600


def test_write_oauth_json_atomically_replaces_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    write_oauth_json(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def test_write_oauth_json_rejects_symlink_destination(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        # famulus-skip: category=platform-contract; reason=symlink creation is unavailable on some hosts; alternate=regular-file replacement and atomic-file tests cover the write path
        pytest.skip("symlinks unavailable")
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "client.json"
    link.symlink_to(target)

    with pytest.raises(OAuthJsonError, match="symbolic link"):
        write_oauth_json(link, {"new": True})

    assert target.read_text(encoding="utf-8") == "{}\n"

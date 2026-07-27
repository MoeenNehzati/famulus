import os
from pathlib import Path

import pytest

from officina.common.famulus_paths import resolve_famulus_paths


def _assert_derived_fields(paths):
    assert paths.runtime_root == paths.data_root / "runtime"
    assert paths.releases_root == paths.runtime_root / "releases"
    assert paths.current_pointer == paths.runtime_root / "current.json"
    assert paths.install_state_root == paths.state_root / "install"
    assert paths.uv_bin == paths.data_root / "tools" / "uv"
    assert paths.python_install_dir == paths.data_root / "python"
    assert paths.worker_root == paths.state_root / "workers"
    assert paths.launcher_profile_root == paths.data_root / "launcher-profiles"
    assert paths.recurring_config_root == paths.config_root / "recurring-tasks"
    assert paths.recurring_state_root == paths.state_root / "recurring-tasks"
    assert paths.email_triage_state_root == paths.state_root / "email-triage"


def test_macos_paths_avoid_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    paths = resolve_famulus_paths(platform="darwin", home=tmp_path)
    assert "Documents" not in str(paths.user_bin)
    assert "Documents" not in str(paths.data_root)
    expected_base = tmp_path / "Library" / "Application Support" / "Famulus"
    assert paths.data_root == expected_base
    assert paths.config_root == expected_base / "config"
    assert paths.state_root == expected_base / "state"
    assert paths.user_bin == tmp_path / ".local" / "bin"
    _assert_derived_fields(paths)


def test_linux_paths_avoid_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    paths = resolve_famulus_paths(platform="linux", home=tmp_path)
    assert "Documents" not in str(paths.user_bin)
    assert paths.data_root == tmp_path / ".local" / "share" / "famulus"
    assert paths.config_root == tmp_path / ".config" / "famulus"
    assert paths.state_root == tmp_path / ".local" / "state" / "famulus"
    assert paths.user_bin == tmp_path / ".local" / "bin"
    _assert_derived_fields(paths)


def test_windows_requires_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError):
        resolve_famulus_paths(platform="win32", home=tmp_path)


def test_windows_paths_resolve_under_localappdata(monkeypatch, tmp_path):
    local_app_data = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    paths = resolve_famulus_paths(platform="win32", home=tmp_path)
    expected_base = local_app_data / "Famulus"
    assert "Documents" not in str(paths.user_bin)
    assert paths.data_root == expected_base
    assert paths.config_root == expected_base / "config"
    assert paths.state_root == expected_base / "state"
    assert paths.user_bin == expected_base / "bin"
    _assert_derived_fields(paths)


def test_xdg_data_home_override_is_honored(monkeypatch, tmp_path):
    override = tmp_path / "custom-xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(override))
    paths = resolve_famulus_paths(platform="linux", home=tmp_path)
    assert str(paths.data_root).startswith(str(override))
    assert paths.data_root == override / "famulus"
    _assert_derived_fields(paths)


def test_relative_home_is_rejected():
    with pytest.raises(ValueError):
        resolve_famulus_paths(platform="linux", home=Path("relative/home"))

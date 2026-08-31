from pathlib import Path

import pytest

from officina.common.famulus_paths import resolve_famulus_paths


def _assert_derived_fields(paths):
    assert paths.worker_root == paths.state_root / "workers"
    assert paths.recurring_config_root == paths.config_root / "recurring-tasks"
    assert paths.recurring_state_root == paths.state_root / "recurring-tasks"
    assert paths.email_triage_state_root == paths.state_root / "email-triage"


def test_macos_paths_avoid_documents(monkeypatch, tmp_path):
    paths = resolve_famulus_paths(platform="darwin", home=tmp_path, environ={})
    assert "Documents" not in str(paths.user_bin)
    assert "Documents" not in str(paths.data_root)
    expected_base = tmp_path / "Library" / "Application Support" / "Famulus"
    assert paths.data_root == expected_base
    assert paths.config_root == expected_base / "config"
    assert paths.state_root == expected_base / "state"
    assert paths.user_bin == tmp_path / ".local" / "bin"
    _assert_derived_fields(paths)


def test_linux_paths_avoid_documents(monkeypatch, tmp_path):
    paths = resolve_famulus_paths(platform="linux", home=tmp_path, environ={})
    assert "Documents" not in str(paths.user_bin)
    assert paths.data_root == tmp_path / ".local" / "share" / "famulus"
    assert paths.config_root == tmp_path / ".config" / "famulus"
    assert paths.state_root == tmp_path / ".local" / "state" / "famulus"
    assert paths.user_bin == tmp_path / ".local" / "bin"
    _assert_derived_fields(paths)


def test_windows_missing_overrides_use_home_appdata_conventions(tmp_path):
    paths = resolve_famulus_paths(platform="win32", home=tmp_path, environ={})

    assert paths.data_root == tmp_path / "AppData" / "Local" / "Famulus"
    assert paths.config_root == tmp_path / "AppData" / "Roaming" / "Famulus"
    assert paths.state_root == paths.data_root / "state"


def test_windows_paths_resolve_under_localappdata(monkeypatch, tmp_path):
    local_app_data = tmp_path / "AppData" / "Local"
    app_data = tmp_path / "AppData" / "Roaming"
    paths = resolve_famulus_paths(
        platform="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": str(local_app_data), "APPDATA": str(app_data)},
    )
    expected_base = local_app_data / "Famulus"
    assert "Documents" not in str(paths.user_bin)
    assert paths.data_root == expected_base
    assert paths.config_root == app_data / "Famulus"
    assert paths.state_root == expected_base / "state"
    assert paths.user_bin == expected_base / "bin"
    _assert_derived_fields(paths)


def test_xdg_overrides_redirect_every_durable_mutable_path(tmp_path):
    override = tmp_path / "custom-xdg"
    paths = resolve_famulus_paths(
        platform="linux",
        home=tmp_path,
        environ={
            "XDG_DATA_HOME": str(override / "data"),
            "XDG_CONFIG_HOME": str(override / "config"),
            "XDG_STATE_HOME": str(override / "state"),
        },
    )
    assert paths.data_root == override / "data" / "famulus"
    assert paths.config_root == override / "config" / "famulus"
    assert paths.state_root == override / "state" / "famulus"
    assert paths.worker_root == override / "state" / "famulus" / "workers"
    assert paths.recurring_config_root == override / "config" / "famulus" / "recurring-tasks"
    assert paths.recurring_state_root == override / "state" / "famulus" / "recurring-tasks"
    assert paths.email_triage_state_root == override / "state" / "famulus" / "email-triage"


def test_relative_home_is_rejected():
    with pytest.raises(ValueError):
        resolve_famulus_paths(platform="linux", home=Path("relative/home"), environ={})


@pytest.mark.parametrize("home", [Path(""), Path("relative/home")])
def test_empty_or_relative_home_is_rejected(home):
    with pytest.raises(ValueError):
        resolve_famulus_paths(platform="linux", home=home, environ={})


@pytest.mark.parametrize(
    ("platform", "key"),
    [
        ("linux", "XDG_DATA_HOME"),
        ("linux", "XDG_CONFIG_HOME"),
        ("linux", "XDG_STATE_HOME"),
        ("win32", "LOCALAPPDATA"),
        ("win32", "APPDATA"),
    ],
)
@pytest.mark.parametrize("value", ["", "relative/path"])
def test_empty_or_relative_environment_roots_are_rejected(tmp_path, platform, key, value):
    environ = {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
        key: value,
    }
    with pytest.raises(ValueError):
        resolve_famulus_paths(platform=platform, home=tmp_path, environ=environ)


def test_invalid_known_override_is_rejected_even_when_platform_would_not_use_it(tmp_path):
    with pytest.raises(ValueError):
        resolve_famulus_paths(
            platform="darwin",
            home=tmp_path,
            environ={"XDG_DATA_HOME": "relative/path"},
        )


def test_explicit_environment_does_not_fall_through_to_process_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ambient-must-not-be-used"))
    paths = resolve_famulus_paths(platform="linux", home=tmp_path, environ={})
    assert paths.data_root == tmp_path / ".local" / "share" / "famulus"


def test_paths_with_spaces_separators_and_unicode_are_absolute(tmp_path):
    root = tmp_path / "root with spaces" / "fåmulus"
    paths = resolve_famulus_paths(
        platform="linux",
        home=tmp_path,
        environ={
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
        },
    )
    assert paths.data_root == root / "data" / "famulus"
    assert all(
        value.is_absolute()
        for value in vars(paths).values()
        if isinstance(value, Path)
    )

import importlib
from pathlib import Path

import pytest

import officina.common.famulus_paths as famulus_paths
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


def test_plugin_context_is_absent_without_normalized_host_variables(tmp_path):
    paths = resolve_famulus_paths(platform="linux", home=tmp_path, environ={})

    assert paths.assistant_host is None
    assert paths.plugin_data is None
    assert paths.logging_path is None
    assert paths.setup_status is None
    assert paths.data_root == tmp_path / ".local" / "share" / "famulus"
    assert paths.state_root == tmp_path / ".local" / "state" / "famulus"


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_plugin_context_derives_every_named_path_for_each_host(tmp_path, host):
    plugin_data = tmp_path / host / "persistent plugin data"
    environ = {
        "FAMULUS_HOST": host,
        "FAMULUS_PLUGIN_DATA": str(plugin_data),
        "PLUGIN_DATA": str(tmp_path / "must-not-be-read"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "must-not-be-read-either"),
    }

    paths = resolve_famulus_paths(platform="linux", home=tmp_path, environ=environ)

    assert paths.assistant_host == host
    assert paths.plugin_data == plugin_data
    assert paths.logging_path == plugin_data / "milestones"
    assert paths.setup_status == plugin_data / "setup" / "status.json"
    assert famulus_paths.FamulusPaths.get(
        "plugin-data", platform="linux", home=tmp_path, environ=environ
    ) == plugin_data
    assert famulus_paths.FamulusPaths.get(
        "logging-path", platform="linux", home=tmp_path, environ=environ
    ) == plugin_data / "milestones"
    assert famulus_paths.FamulusPaths.get(
        "setup-status", platform="linux", home=tmp_path, environ=environ
    ) == plugin_data / "setup" / "status.json"


@pytest.mark.parametrize(
    ("host", "plugin_data"),
    [
        ("claude", None),
        (None, "absolute"),
        ("", "absolute"),
        ("claude", ""),
        ("claude", "relative/path"),
        ("other-client", "absolute"),
        (" Claude ", "absolute"),
    ],
)
def test_invalid_plugin_context_is_rejected(tmp_path, host, plugin_data):
    environ = {}
    if host is not None:
        environ["FAMULUS_HOST"] = host
    if plugin_data is not None:
        environ["FAMULUS_PLUGIN_DATA"] = (
            str(tmp_path / "plugin-data") if plugin_data == "absolute" else plugin_data
        )

    with pytest.raises(famulus_paths.InvalidFamulusPluginContextError):
        resolve_famulus_paths(platform="linux", home=tmp_path, environ=environ)


def test_get_rejects_unknown_names_before_context_lookup(tmp_path):
    with pytest.raises(famulus_paths.UnknownFamulusPathError):
        famulus_paths.FamulusPaths.get(
            "data_root", platform="linux", home=tmp_path, environ={}
        )


def test_get_requires_plugin_context_for_known_names(tmp_path):
    with pytest.raises(famulus_paths.FamulusPluginContextRequiredError):
        famulus_paths.FamulusPaths.get(
            "plugin-data", platform="linux", home=tmp_path, environ={}
        )


def test_get_requires_explicit_resolution_inputs():
    with pytest.raises(TypeError):
        famulus_paths.FamulusPaths.get("plugin-data")


def test_exported_path_mapping_cannot_be_mutated():
    fields = famulus_paths.FAMULUS_PATH_FIELDS
    original = fields["plugin-data"]
    try:
        with pytest.raises(TypeError):
            fields["plugin-data"] = "state_root"
    finally:
        if isinstance(fields, dict):
            fields["plugin-data"] = original


def test_resolve_and_get_do_not_create_plugin_paths(tmp_path):
    plugin_data = tmp_path / "not-created"
    environ = {
        "FAMULUS_HOST": "codex",
        "FAMULUS_PLUGIN_DATA": str(plugin_data),
    }

    resolve_famulus_paths(platform="linux", home=tmp_path, environ=environ)
    famulus_paths.FamulusPaths.get(
        "setup-status", platform="linux", home=tmp_path, environ=environ
    )

    assert not plugin_data.exists()


def test_get_interface_prints_one_selected_absolute_path(monkeypatch, capsys, tmp_path):
    plugin_data = tmp_path / "adapter-data"
    monkeypatch.setenv("FAMULUS_HOST", "claude")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    module = importlib.import_module("officina.common.famulus_paths._get_interface")

    assert module.Interface().run(["setup-status"]) == 0

    assert capsys.readouterr().out == f"{plugin_data / 'setup' / 'status.json'}\n"


def test_get_interface_rejects_names_outside_finite_choices():
    module = importlib.import_module("officina.common.famulus_paths._get_interface")

    with pytest.raises(SystemExit) as error:
        module.Interface().run(["data_root"])

    assert error.value.code == 2

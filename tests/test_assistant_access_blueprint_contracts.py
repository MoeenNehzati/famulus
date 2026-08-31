from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from officina.common import codex_toml, toml_io


ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str) -> dict[str, object]:
    payload = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _interface(source: dict[str, object]) -> dict[str, object]:
    interfaces = source["interfaces"]
    assert isinstance(interfaces, dict) and len(interfaces) == 1
    interface = next(iter(interfaces.values()))
    assert isinstance(interface, dict)
    return interface


def _callable_arguments(module: object, names: tuple[str, ...]) -> set[str]:
    return {
        parameter
        for name in names
        for parameter in inspect.signature(getattr(module, name)).parameters
    }


def test_toml_io_v2_contract_matches_live_python_api() -> None:
    source = _load("src/officina/common/blueprints/toml-io.yaml")
    interface = _interface(source)
    callables = (
        "open",
        "validate_toml_file",
        "toml_string",
        "key_value",
        "profile_config_filename",
        "repository_config_filename",
        "iter_profile_configs",
        "managed_file_state",
        "plan_managed_string_array_update",
        "plan_managed_string_array_removal",
        "inspect_managed_string_array",
        "apply_managed_array_plan",
    )

    assert interface["version"] == source["version"] == 2
    assert set(interface["contract"]["arguments"]) == _callable_arguments(
        toml_io, callables
    )


def test_codex_toml_v2_contract_matches_live_python_api() -> None:
    source = _load("src/officina/common/blueprints/codex-toml.yaml")
    interface = _interface(source)
    callables = (
        "config_filename",
        "config_path",
        "config_state",
        "plan_access_roots",
        "plan_access_removal",
        "inspect_access_roots",
        "apply_access_plan",
    )

    assert interface["version"] == source["version"] == 2
    assert set(interface["contract"]["arguments"]) == _callable_arguments(
        codex_toml, callables
    )


def test_common_exports_have_no_deleted_installer_callers() -> None:
    """Catch a stale graph edge that would require a deleted module."""
    module = _load("src/officina/common/blueprint.yaml")
    callers = {
        caller
        for export in module["exports"].values()
        for caller in export["access"].get("allowed_callers", [])
    }

    assert "install" not in callers
    assert "install-assistant-tools" not in callers
    assert "install-assistant-tools._rtx" not in callers

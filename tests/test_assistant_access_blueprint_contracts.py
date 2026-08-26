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


def _uses(source: dict[str, object]) -> set[tuple[str, int]]:
    return {
        (item["interface"], item["version"])
        for item in source.get("uses_interfaces", [])
    }


def _dependencies(source: dict[str, object]) -> set[tuple[str, int]]:
    return {(item["source"], item["version"]) for item in source["dependencies"]}


def _callable_arguments(module: object, names: tuple[str, ...]) -> set[str]:
    return {
        parameter
        for name in names
        for parameter in inspect.signature(getattr(module, name)).parameters
    }


def _annotated_return_names(module: object, names: tuple[str, ...]) -> set[str]:
    annotations: set[str] = set()
    for name in names:
        annotation = inspect.signature(getattr(module, name)).return_annotation
        if annotation is inspect.Signature.empty:
            continue
        rendered = str(annotation).strip("'")
        annotations.add(rendered.rsplit(".", 1)[-1])
    return annotations


def test_toml_io_v2_contract_models_managed_plan_inputs_and_private_results() -> None:
    source = _load("src/officina/common/blueprints/toml-io.yaml")
    interface = _interface(source)
    contract = interface["contract"]
    arguments = contract["arguments"]
    assert interface["version"] == source["version"] == 2
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
    assert set(arguments) == _callable_arguments(toml_io, callables)
    assert {
        "base",
        "name",
        "mode",
        "path",
        "value",
        "key",
        "agent",
        "directory",
        "table_name",
        "key_name",
        "required",
        "prior",
        "begin",
        "end",
        "ownership",
        "plan",
    }.issubset(arguments)
    assert arguments["required"]["type"] == {
        "kind": "list",
        "element_type": {"kind": "string", "sensitivity": "user-private"},
        "sensitivity": "user-private",
    }
    for name in ("prior", "ownership", "plan"):
        assert "schema v6 has no" in arguments[name]["type"]["description"].lower()
    result = contract["outputs"][0]
    assert result["id"] == "result"
    assert result["type"]["sensitivity"] == "user-private"
    assert "schema v6 has no" in result["type"]["description"].lower()
    for runtime_type in _annotated_return_names(toml_io, callables) | {
        "Path iterator"
    }:
        assert runtime_type in result["description"]
    assert set(contract["direct_io"]["writes"][1]["formats"]) == {
        "text",
        "path-list",
        "python-object",
    }
    safety = contract["execution"]["mutation_safety"]
    assert "non_atomic" in safety["atomicity"]
    assert "possible" in safety["partial_effects_on_failure"]


def test_codex_toml_v2_contract_models_every_facade_input_and_private_result() -> None:
    source = _load("src/officina/common/blueprints/codex-toml.yaml")
    interface = _interface(source)
    contract = interface["contract"]
    arguments = contract["arguments"]
    assert interface["version"] == source["version"] == 2
    assert arguments["base"]["required"] is False
    callables = (
        "config_filename",
        "config_path",
        "config_state",
        "plan_access_roots",
        "plan_access_removal",
        "inspect_access_roots",
        "apply_access_plan",
    )
    assert set(arguments) == _callable_arguments(codex_toml, callables)
    assert set(arguments) == {
        "base",
        "required",
        "prior",
        "begin",
        "end",
        "ownership",
        "plan",
    }
    assert arguments["required"]["type"] == {
        "kind": "list",
        "element_type": {"kind": "string", "sensitivity": "user-private"},
        "sensitivity": "user-private",
    }
    for name in ("prior", "ownership", "plan"):
        assert "schema v6 has no" in arguments[name]["type"]["description"].lower()
    result = contract["outputs"][0]
    assert result["id"] == "result"
    assert result["type"]["sensitivity"] == "user-private"
    assert "schema v6 has no" in result["type"]["description"].lower()
    for runtime_type in _annotated_return_names(codex_toml, callables):
        assert runtime_type in result["description"]
    assert any(
        "CodexTomlError" in warning["external-side-effect"]
        for warning in contract["caller_warnings"]
    )
    assert set(contract["direct_io"]["writes"][0]["formats"]) == {
        "string",
        "path",
        "python-object",
    }
    safety = contract["execution"]["mutation_safety"]
    assert "atomic" in safety["atomicity"]
    assert "unsafe" in safety["concurrent_invocations"]
    assert "possible" in safety["partial_effects_on_failure"]


def test_file_contracts_do_not_claim_portable_byte_cas_against_external_writers() -> None:
    atomic = _interface(
        _load("src/officina/common/blueprints/atomic-files.yaml")
    )["contract"]
    codex = _interface(
        _load("src/officina/common/blueprints/codex-toml.yaml")
    )["contract"]
    access = _interface(
        _load(
            "skills/install-assistant-tools/_rtx/blueprints/rtx-assistant-access-config.yaml"
        )
    )["contract"]

    for contract in (atomic, codex, access):
        concurrency = contract["execution"]["mutation_safety"][
            "concurrent_invocations"
        ]
        assert set(concurrency) == {"unsafe"}
        explanation = concurrency["unsafe"].lower()
        assert "already-open" in explanation
        assert "post-state" in explanation

    assert "cooperating" in access["execution"]["mutation_safety"][
        "concurrent_invocations"
    ]["unsafe"].lower()
    assert "symlink" in atomic["execution"]["mutation_safety"][
        "concurrent_invocations"
    ]["unsafe"].lower()


def test_atomic_files_v2_contract_includes_compare_publish_and_delete_calls() -> None:
    source = _load("src/officina/common/blueprints/atomic-files.yaml")
    interface = _interface(source)
    contract = interface["contract"]
    assert source["version"] == interface["version"] == 2
    operations = {
        item["value"] for item in contract["arguments"]["operation"]["type"]["values"]
    }
    assert {"compare-and-replace", "compare-and-delete"}.issubset(operations)
    arguments = contract["arguments"]
    assert "compare-and-append" in arguments["expected-previous-bytes"][
        "description"
    ]
    assert "compare-and-replace" in arguments["expected-previous-bytes"][
        "description"
    ]
    assert "compare-and-delete" in arguments["expected-previous-bytes"][
        "description"
    ]
    assert arguments["expected-previous-mode"]["type"]["kind"] == "integer"


def test_installer_sources_declare_direct_toml_and_owned_helper_dependencies() -> None:
    toml = _load("src/officina/common/blueprints/toml-io.yaml")
    assert ("common.source.atomic-files", 2) in _dependencies(toml)
    assert ("common.interface.atomic-files", 2) in _uses(toml)

    access = _load(
        "skills/install-assistant-tools/_rtx/blueprints/rtx-assistant-access-config.yaml"
    )
    assert ("common.source.atomic-files", 2) in _dependencies(access)
    assert ("common.interface.atomic-files", 2) in _uses(access)
    assert ("common.source.toml-io", 2) in _dependencies(access)
    assert ("common.interface.toml-io", 2) in _uses(access)

    runtime = _load("skills/install-assistant-tools/_rtx/blueprints/rtx-init.yaml")
    assert {
        ("common.source.codex-toml", 2),
        ("common.source.atomic-files", 2),
        ("common.source.toml-io", 2),
        ("common.source.famulus-paths", 1),
        ("install.source.context", 1),
        ("install.source.doctor", 1),
        ("install-assistant-tools._rtx.source.rtx-assistant-access-config", 1),
    }.issubset(_dependencies(runtime))
    assert {
        ("common.interface.codex-toml", 2),
        ("common.interface.atomic-files", 2),
        ("common.interface.toml-io", 2),
        ("common.interface.famulus-paths", 1),
        ("install.interface.context", 1),
        ("install.interface.doctor", 1),
        (
            "install-assistant-tools._rtx.source.rtx-assistant-access-config.interface.python-api",
            1,
        ),
    }.issubset(_uses(runtime))


def test_assistant_access_contracts_disclose_python_objects_paths_and_lock_state() -> None:
    resolver = _interface(
        _load("src/officina/install/blueprints/assistant-access.yaml")
    )["contract"]
    assert "schema v6 has no" in resolver["arguments"]["context"]["type"][
        "description"
    ].lower()
    roots = resolver["outputs"][0]
    assert "tuple[Path, ...]" in roots["description"]
    assert "schema v6 has no" in roots["type"]["description"].lower()

    source = _load(
        "skills/install-assistant-tools/_rtx/blueprints/rtx-assistant-access-config.yaml"
    )
    contract = _interface(source)["contract"]
    for name in ("context", "manifest"):
        assert "schema v6 has no" in contract["arguments"][name]["type"][
            "description"
        ].lower()
    lock_path = "<famulus-install-state-root>/assistant-access.lock"
    lock_entries = [
        item
        for direction in ("reads", "writes")
        for item in contract["direct_io"][direction]
        if item.get("path") == lock_path
    ]
    assert {item["access"] for item in lock_entries} >= {"read", "read-write"}
    assert "assistant-access-lock-state" in {
        effect["id"] for effect in contract["execution"]["effects"]
    }
    module = _load("skills/install-assistant-tools/_rtx/blueprint.yaml")
    assert any(
        item["path"] == lock_path for item in module["authority"]["owns_filesystem"]
    )
    warnings = " ".join(
        warning["external-side-effect"] for warning in contract["caller_warnings"]
    ).lower()
    assert "dry-run" in warnings and "stable empty sidecar" in warnings


def test_phase_entry_depends_on_access_owner_without_claiming_neighbor_intrinsics() -> None:
    phase = _load(
        "skills/install-assistant-tools/_rtx/blueprints/rtx-phase-entry.yaml"
    )
    assert (
        "install-assistant-tools._rtx.source.rtx-assistant-access-config",
        1,
    ) in _dependencies(phase)
    assert ("common.interface.codex-toml", 2) not in _uses(phase)


def test_email_triage_log_sources_declare_direct_famulus_path_dependency() -> None:
    for name in ("rtx-decision-sink", "rtx-log-compactor"):
        source = _load(f"skills/email-triage/_rtx/blueprints/{name}.yaml")
        assert ("common.source.famulus-paths", 1) in _dependencies(source)
        assert ("common.interface.famulus-paths", 1) in _uses(source)
        interface = _interface(source)
        assert ("common.interface.famulus-paths", 1) in {
            (item["interface"], item["version"])
            for item in interface["uses_interfaces"]
        }

    decision = _load(
        "skills/email-triage/_rtx/blueprints/rtx-decision-sink.yaml"
    )
    decision_contract = _interface(decision)["contract"]
    assert decision_contract["direct_io"]["writes"][0]["path"] == (
        "<email_triage_state_root>/triage.log"
    )
    assert any(
        item["path"] == "<module-root>/triage.log"
        for item in decision_contract["direct_io"]["reads"]
    )
    assert "migrate-legacy-log" in {
        effect["id"] for effect in decision_contract["execution"]["effects"]
    }

    compactor = _load(
        "skills/email-triage/_rtx/blueprints/rtx-log-compactor.yaml"
    )
    compactor_contract = _interface(compactor)["contract"]
    assert {
        item["path"]
        for direction in ("reads", "writes")
        for item in compactor_contract["direct_io"][direction]
        if item["medium"] == "local-filesystem"
    } == {
        "<email_triage_state_root>/triage.log",
        "<module-root>/triage.log",
    }
    assert any(
        item["path"] == "<module-root>/triage.log"
        for item in compactor_contract["direct_io"]["reads"]
    )
    assert "migrate-legacy-log" in {
        effect["id"] for effect in compactor_contract["execution"]["effects"]
    }
    warning_text = " ".join(
        warning["external-side-effect"]
        for warning in compactor_contract["caller_warnings"]
    ).lower()
    assert "email_triage_state_dir" in warning_text
    assert "disables legacy migration" in warning_text
    partial = compactor_contract["execution"]["mutation_safety"][
        "partial_effects_on_failure"
    ]["possible"].lower()
    assert "migrated" in partial and "prun" in partial


def test_list_manager_cloud_store_declares_managed_private_cache_and_locks() -> None:
    source = _load("skills/list-manager/_rtx/blueprints/rtx-yaml-store.yaml")
    assert ("common.source.famulus-paths", 1) in _dependencies(source)
    assert ("common.interface.famulus-paths", 1) in _uses(source)
    interfaces = source["interfaces"]
    cloud_names = {
        "cloud-create-entry",
        "cloud-delete",
        "cloud-init",
        "cloud-read",
        "cloud-update",
    }
    for suffix in cloud_names:
        interface = interfaces[
            f"list-manager._rtx.source.rtx-yaml-store.interface.{suffix}"
        ]
        assert ("common.interface.famulus-paths", 1) in {
            (item["interface"], item["version"])
            for item in interface["uses_interfaces"]
        }
        contract = interface["contract"]
        entries = contract["direct_io"]["reads"] + contract["direct_io"]["writes"]
        cache = [
            item
            for item in entries
            if item.get("path") == "<famulus-state-root>/list-manager/cache/**"
        ]
        assert {item["access"] for item in cache} >= {"read", "write", "delete"}
        assert all(item["sensitivity"] == "user-private" for item in cache)
        failure = contract["execution"]["mutation_safety"][
            "partial_effects_on_failure"
        ]["possible"].lower()
        assert "cache" in failure and "crash" in failure
        locks = [
            item
            for item in entries
            if item.get("path") == "<famulus-state-root>/list-manager/locks/*.yaml.lock"
        ]
        if suffix == "cloud-read":
            assert locks == []
        else:
            assert locks and all(
                item["sensitivity"] == "user-private" for item in locks
            )

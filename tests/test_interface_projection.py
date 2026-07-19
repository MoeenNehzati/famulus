from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.blueprint_template import load_schema, schema_validator
from officina.common.certification_view import CertificationDecision, RejectingCertificationView
from officina.common.interface_projection import (
    InterfaceProjectionError,
    project_consumer_interfaces,
    standalone_export_size,
)


class _PassingView:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def check_export(self, module_id: str, interface_id: str, interface_version: int) -> CertificationDecision:
        self.checked.append(interface_id)
        return CertificationDecision(True, "current", "Current.")


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _skill(root: Path, name: str, uses: list[dict[str, object]]) -> Path:
    skill = root / "skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 3,
            "node_type": "skill",
            "id": name,
            "gateway": {"kind": "instruction-file", "path": "SKILL.md"},
            "content": [r"SKILL\.md"],
            "default_interface": {
                "version": 1,
                "description": "Default consumer.",
                "allow_all_skills": True,
                "uses_interfaces": uses,
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            "interfaces": [],
        },
    )
    return skill


def _llm_sidecar(skill: Path, local_name: str, description: str) -> str:
    gateway = skill / "llm_interfaces" / f"{local_name}.md"
    gateway.parent.mkdir(parents=True, exist_ok=True)
    gateway.write_text(description + "\n", encoding="utf-8")
    interface_id = f"{skill.name}.llm.{local_name}"
    _write_yaml(
        gateway.with_name(f".{gateway.name}.blueprint.yaml"),
        {
            "schema_version": 3,
            "node_type": "llm-interface",
            "id": interface_id,
            "version": 1,
            "description": description,
            "gateway": {"kind": "instruction-file", "path": f"llm_interfaces/{local_name}.md"},
            "content": [rf"llm_interfaces/{local_name}\.md"],
            "allow_all_skills": True,
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    return interface_id


def _contract(schema_path: str | None = None) -> dict[str, object]:
    output: dict[str, object] = {
        "id": "result",
        "cardinality": {"minimum": 0, "maximum": 100},
    }
    if schema_path is not None:
        output["schema"] = {"path": schema_path, "fragment": "#"}
    return {
        "arguments": {
            "name": {
                "description": "Name.",
                "required": True,
                "sensitivity": "public",
                "invocation_binding": {
                    "kind": "positional",
                    "position": 0,
                    "arity": {"minimum": 1, "maximum": 1},
                },
                "type": {"kind": "string", "format": {"named": "identifier"}},
            }
        },
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [output],
        "outcomes": [{"id": "success"}],
        "execution": {"state_effect": "read-only", "lifecycle": "finite"},
    }


def _export(interface_id: str, *, helpers: list[dict[str, object]] | None = None, uses: list[dict[str, object]] | None = None, schema_path: str | None = None) -> dict[str, object]:
    return {
        "id": interface_id,
        "version": 1,
        "description": f"Contract for {interface_id}.",
        "allow_all_skills": True,
        "allowed_callers": [],
        "invocation_binding": {"fixed": []},
        "uses_interfaces": uses or [],
        "helpers": helpers or [],
        "direct_io": {"reads": [], "writes": [{"id": "stdout", "medium": "stdout"}], "network": []},
        "owns_filesystem": [{"path": "private/", "syntax": "literal", "allowed_readers": []}],
        "contract": _contract(schema_path),
    }


def _repository(root: Path, *, oversized: bool = False) -> None:
    provider = _skill(root, "provider-skill", [])
    advisor = _llm_sidecar(provider, "advisor", "Provider advice.")
    consumer = _skill(root, "consumer-skill", [])
    coach = _llm_sidecar(consumer, "coach", "Local coaching.")
    consumer_blueprint = yaml.safe_load((consumer / "blueprint.yaml").read_text(encoding="utf-8"))
    consumer_blueprint["default_interface"]["uses_interfaces"] = [
        {"interface": "provider-skill.machine.run", "version": 1},
        {"interface": advisor, "version": 1},
        {"interface": coach, "version": 1},
    ]
    _write_yaml(consumer / "blueprint.yaml", consumer_blueprint)

    (provider / "result.schema.json").write_text(
        '{"type":"object","properties":{"value":{"$ref":"value.schema.json#"}}}',
        encoding="utf-8",
    )
    (provider / "value.schema.json").write_text('{"type":"string"}', encoding="utf-8")
    runtime = provider / "_rtx"
    runtime.mkdir(exist_ok=True)
    (runtime / "_worker.py").write_text("class Interface:\n    pass\n", encoding="utf-8")
    (provider / "interface-conformance.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    helper = {
        "id": "lookup",
        "role": "Supplies names.",
        "interface": "provider-skill.machine.lookup",
        "version": 1,
        "inputs": {},
        "result": {"output_ref": "result", "selector": {"kind": "whole-output"}},
        "route": {"kind": "argument-enum", "target": "name"},
        "empty": {"outcome": "empty", "caller_action": "Stop."},
        "failure": {"outcome": "failed"},
    }
    run = _export(
        "provider-skill.machine.run",
        helpers=[helper],
        uses=[{"interface": "provider-skill.machine.lookup", "version": 1}],
        schema_path="result.schema.json",
    )
    if oversized:
        run["description"] = "x" * 17_000
    declaration = {
        "schema_version": 3,
        "node_type": "machine-module",
        "id": "provider-skill.machine-module.worker",
        "version": 1,
        "description": "Provider worker.",
        "gateway": {
            "kind": "python-entrypoint",
            "path": "_rtx/_worker.py",
            "symbol": "Interface",
            "args_prefix": [],
            "conformance": {
                "adapter_protocol": "officina-python-adapters@1",
                "bind_method": "bind_conformance_adapters",
                "sandbox_profile": "officina-isolated-effects@1",
            },
        },
        "content": [r"_rtx/_worker\.py"],
        "conformance_manifest": {"base": "skill-root", "path": "interface-conformance.yaml"},
        "platform_support": {"linux": True, "macos": True, "windows": True},
        "dependencies": [],
        "behavior_sources": [],
        "owns_filesystem": [{"path": "private/", "syntax": "literal", "allowed_readers": []}],
        "uses_interfaces": [],
        "interfaces": {
            "run": run,
            "lookup": _export("provider-skill.machine.lookup"),
            "sibling": _export("provider-skill.machine.sibling"),
        },
    }
    _write_yaml(runtime / "._worker.py.blueprint.yaml", declaration)


def test_projection_selects_direct_exports_helpers_and_llm_routes_only(tmp_path: Path) -> None:
    _repository(tmp_path)
    graph = load_repository_blueprint_graph(tmp_path)
    certification = _PassingView()
    projection = project_consumer_interfaces(
        graph, "consumer-skill.llm.default", certification
    )

    document = projection.document
    assert list(document["interfaces"]) == ["provider-skill.machine.run"]
    assert list(document["helper_interfaces"]) == ["provider-skill.machine.lookup"]
    assert "provider-skill.machine.sibling" not in str(document)
    assert "gateway" not in document["interfaces"]["provider-skill.machine.run"]
    assert "owns_filesystem" not in document["interfaces"]["provider-skill.machine.run"]
    assert document["llm_interfaces"]["provider-skill.llm.advisor"]["route"] == {
        "kind": "provider-skill",
        "skill": "provider-skill",
    }
    assert document["llm_interfaces"]["consumer-skill.llm.coach"]["gateway"] == "llm_interfaces/coach.md"
    assert set(certification.checked) == {
        "provider-skill.machine.run",
        "provider-skill.machine.lookup",
    }
    assert "provider-skill-route" in projection.vocabulary
    assert "type:string" in projection.vocabulary
    assert all("value.schema.json" not in str(item.get("contract")) for item in document["interfaces"].values())
    assert document["definitions"]
    schema_validator(load_schema("references/blueprint/interface-projection.schema.json")).validate(document)
    assert standalone_export_size(document["interfaces"]["provider-skill.machine.run"]) > 0


def test_projection_rejects_failed_certification_and_combined_overflow(tmp_path: Path) -> None:
    _repository(tmp_path)
    graph = load_repository_blueprint_graph(tmp_path)
    with pytest.raises(InterfaceProjectionError, match="certification-unavailable"):
        project_consumer_interfaces(
            graph, "consumer-skill.llm.default", RejectingCertificationView()
        )

    oversized = tmp_path / "oversized"
    _repository(oversized, oversized=True)
    graph = load_repository_blueprint_graph(oversized)
    with pytest.raises(InterfaceProjectionError, match="limit is 16384"):
        project_consumer_interfaces(graph, "consumer-skill.llm.default", _PassingView())


def test_projection_with_no_dependencies_is_empty_and_valid(tmp_path: Path) -> None:
    _skill(tmp_path, "empty-skill", [])
    projection = project_consumer_interfaces(
        load_repository_blueprint_graph(tmp_path),
        "empty-skill.llm.default",
        _PassingView(),
    )
    assert projection.document["interfaces"] == {}
    assert projection.document["helper_interfaces"] == {}
    assert projection.document["llm_interfaces"] == {}
    assert projection.vocabulary == frozenset()


def test_enum_helper_target_must_be_read_only_and_finitely_bounded(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    sidecar = next((tmp_path / "skills" / "provider-skill").rglob("._worker.py.blueprint.yaml"))
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["interfaces"]["lookup"]["contract"]["execution"]["state_effect"] = "mutating"
    _write_yaml(sidecar, declaration)

    with pytest.raises(InterfaceProjectionError, match="must be read-only"):
        project_consumer_interfaces(
            load_repository_blueprint_graph(tmp_path),
            "consumer-skill.llm.default",
            _PassingView(),
        )

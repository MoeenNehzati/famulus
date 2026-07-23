from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.common.blueprint_graph import (
    HelperEdge,
    InterfaceExport,
    load_repository_blueprint_graph,
)
from officina.common.blueprint_template import load_schema, schema_validator
from officina.common.certification_view import CertificationDecision, RejectingCertificationView
from officina.common.interface_projection import (
    InterfaceProjectionError,
    _validate_helper_target,
    project_consumer_interfaces,
    standalone_export_size,
)


class _PassingView:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str | None,
    ) -> CertificationDecision:
        self.checked.append(interface_id)
        return CertificationDecision(True, "current", "Current.")


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_legacy_projection_consumer(root: Path) -> str:
    skill = root / "skills" / "legacy-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Legacy instructions.\n", encoding="utf-8")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 3,
            "node_type": "skill",
            "id": "legacy-skill",
            "gateway": {"kind": "instruction-file", "path": "SKILL.md"},
            "content": [r"SKILL\.md"],
            "default_interface": {
                "version": 1,
                "description": "Default consumer.",
                "allow_all_skills": True,
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            "interfaces": [],
        },
    )
    return "legacy-skill.llm.default"


def _contract(
    *,
    helper: dict[str, object] | None = None,
    schema_path: str | None = None,
) -> dict[str, object]:
    output: dict[str, object] = {
        "id": "result",
        "audience": "machine",
        "description": "Result.",
        "direct_io_ref": "stdout",
        "cardinality": {"minimum": 0, "maximum": 100},
        "ordering": "stable",
        "pagination": {"kind": "none"},
        "truncation": {"kind": "none"},
        "empty": "May be empty.",
    }
    if schema_path is None:
        output["type"] = {"kind": "string"}
    else:
        output["schema"] = {"path": schema_path, "fragment": "#"}
    return {
        "arguments": {
            "name": {
                "description": "Name.",
                "required": True,
                "sensitivity": "public",
                "type": {"kind": "string", "format": {"named": "identifier"}},
            }
        },
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [output],
        "outcomes": [
            {
                "id": "success",
                "class": "success",
                "outputs": ["result"],
                "effects": [],
                "caller_action": "Continue.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [helper] if helper is not None else [],
        "direct_io": {
            "reads": [],
            "writes": [
                {
                    "id": "stdout",
                    "medium": "stdout",
                    "access": "write",
                    "content": "Result.",
                    "formats": ["text"],
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _process_binding() -> dict[str, object]:
    return {
        "kind": "process",
        "entry": "Interface",
        "arguments": {
            "name": {
                "kind": "positional",
                "position": 0,
                "arity": {"minimum": 1, "maximum": 1},
            }
        },
        "fixed": [],
    }


def _write_module(
    root: Path,
    module_id: str,
    sources: dict[str, dict[str, object]],
    exports: dict[str, dict[str, object]],
) -> None:
    module = root / "skills" / module_id
    module.mkdir(parents=True, exist_ok=True)
    content = sorted(
        {
            pattern
            for source in sources.values()
            for pattern in source["content"]  # type: ignore[index]
        }
    )
    gateway_path = str(next(iter(sources.values()))["gateway"]["path"])  # type: ignore[index]
    for source_id, source in sources.items():
        _write_yaml(module / "blueprints" / f"{source_id.rsplit('.', 1)[-1]}.yaml", source)
    _write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "description": f"{module_id} module.",
            "gateway": {"path": gateway_path, "language": "Markdown"},
            "content": content,
            "authority": {"owns_filesystem": []},
            "sources": {
                source_id: {
                    "blueprint": {
                        "base": "module-root",
                        "path": f"blueprints/{source_id.rsplit('.', 1)[-1]}.yaml",
                    }
                }
                for source_id in sources
            },
            "exports": exports,
        },
    )


def _repository(root: Path, *, oversized: bool = False) -> None:
    provider = root / "skills" / "provider-skill"
    (provider / "_rtx").mkdir(parents=True)
    (provider / "_rtx" / "worker.py").write_text("class Interface:\n    pass\n")
    (provider / "_rtx" / "lookup.py").write_text("class Interface:\n    pass\n")
    (provider / "result.schema.json").write_text('{"type":"string"}')
    lookup_source = "provider-skill.source.lookup"
    lookup_interface = f"{lookup_source}.interface.names"
    worker_source = "provider-skill.source.worker"
    run_interface = f"{worker_source}.interface.run"
    helper = {
        "id": "lookup",
        "role": "Supplies names.",
        "interface": lookup_interface,
        "version": 1,
        "inputs": {},
        "result": {"output_ref": "result", "selector": {"kind": "whole-output"}},
        "route": {"kind": "argument-enum", "target": "name"},
        "empty": {"outcome": "success", "caller_action": "Stop."},
        "failure": {"outcome": "success"},
    }
    lookup = {
        "schema_version": 4,
        "node_type": "behavioral_source",
        "id": lookup_source,
        "version": 1,
        "description": "Lookup source.",
        "gateway": {"path": "_rtx/lookup.py", "language": "Python"},
        "content": [r"_rtx/lookup\.py"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {
            lookup_interface: {
                "version": 1,
                "description": "Lookup names.",
                "contract": _contract(),
                "process_binding": _process_binding(),
            },
            f"{lookup_source}.interface.sibling": {
                "version": 1,
                "description": "Unused sibling.",
                "contract": _contract(),
            },
        },
    }
    worker = {
        "schema_version": 4,
        "node_type": "behavioral_source",
        "id": worker_source,
        "version": 1,
        "description": "Worker source.",
        "gateway": {"path": "_rtx/worker.py", "language": "Python"},
        "content": [r"_rtx/worker\.py", r"result\.schema\.json"],
        "dependencies": [],
        "uses_interfaces": [{"interface": lookup_interface, "version": 1}],
        "interfaces": {
            run_interface: {
                "version": 1,
                "description": "x" * 17_000 if oversized else "Run.",
                "contract": _contract(helper=helper, schema_path="result.schema.json"),
                "process_binding": _process_binding(),
            }
        },
    }
    _write_module(
        root,
        "provider-skill",
        {lookup_source: lookup, worker_source: worker},
        {
            "provider-skill.interface.run": {
                "source_interface": run_interface,
                "access": {"allow_all_modules": True, "allowed_callers": []},
            }
        },
    )

    consumer = root / "skills" / "consumer-skill"
    consumer.mkdir(parents=True)
    (consumer / "SKILL.md").write_text("Consumer instructions.\n")
    consumer_source = "consumer-skill.source.gateway"
    _write_module(
        root,
        "consumer-skill",
        {
            consumer_source: {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": consumer_source,
                "version": 1,
                "description": "Consumer.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md"],
                "dependencies": [],
                "uses_interfaces": [
                    {"interface": "provider-skill.interface.run", "version": 1}
                ],
                "interfaces": {},
            }
        },
        {},
    )


def test_projection_selects_generic_exports_and_helper_closure(tmp_path: Path) -> None:
    _repository(tmp_path)
    graph = load_repository_blueprint_graph(tmp_path)
    certification = _PassingView()

    projection = project_consumer_interfaces(
        graph, "consumer-skill.source.gateway", certification
    )
    document = projection.document

    assert document["schema_version"] == 2
    assert list(document["interfaces"]) == ["provider-skill.interface.run"]
    assert list(document["helper_interfaces"]) == [
        "provider-skill.source.lookup.interface.names"
    ]
    assert "sibling" not in str(document)
    run = document["interfaces"]["provider-skill.interface.run"]
    assert run["source_module"] == "provider-skill"
    assert run["source_interface"] == "provider-skill.source.worker.interface.run"
    assert run["gateway"] == {"path": "_rtx/worker.py", "language": "Python"}
    assert run["contract"]["outputs"][0]["schema"]["path"] == "result.schema.json"
    assert document["definitions"]
    assert set(certification.checked) == {
        "provider-skill.interface.run",
        "provider-skill.source.lookup.interface.names",
    }
    assert "type:string" in projection.vocabulary
    schema_validator(
        load_schema("references/blueprint/interface-projection.schema.json")
    ).validate(document)
    assert standalone_export_size(run) > 0


def test_projection_rejects_failed_certification_and_combined_overflow(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    with pytest.raises(InterfaceProjectionError, match="certification-unavailable"):
        project_consumer_interfaces(
            load_repository_blueprint_graph(tmp_path),
            "consumer-skill.source.gateway",
            RejectingCertificationView(),
        )

    oversized = tmp_path / "oversized"
    _repository(oversized, oversized=True)
    with pytest.raises(InterfaceProjectionError, match="limit is 16384"):
        project_consumer_interfaces(
            load_repository_blueprint_graph(oversized),
            "consumer-skill.source.gateway",
            _PassingView(),
        )


def test_projection_with_no_dependencies_is_empty_and_valid(tmp_path: Path) -> None:
    module = tmp_path / "skills" / "empty-skill"
    module.mkdir(parents=True)
    (module / "SKILL.md").write_text("Empty.\n")
    source_id = "empty-skill.source.gateway"
    _write_module(
        tmp_path,
        "empty-skill",
        {
            source_id: {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
                "description": "Empty.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {},
            }
        },
        {},
    )
    projection = project_consumer_interfaces(
        load_repository_blueprint_graph(tmp_path), source_id, _PassingView()
    )
    assert projection.document["interfaces"] == {}
    assert projection.document["helper_interfaces"] == {}
    assert projection.vocabulary == frozenset()
    schema_validator(
        load_schema("references/blueprint/interface-projection.schema.json")
    ).validate(projection.document)


def test_legacy_projection_producer_validates_against_shared_schema(
    tmp_path: Path,
) -> None:
    consumer_id = _write_legacy_projection_consumer(tmp_path)

    projection = project_consumer_interfaces(
        load_repository_blueprint_graph(tmp_path),
        consumer_id,
        _PassingView(),
    )

    assert projection.document["schema_version"] == 1
    schema_validator(
        load_schema("references/blueprint/interface-projection.schema.json")
    ).validate(projection.document)


def test_enum_helper_target_must_be_read_only(tmp_path: Path) -> None:
    _repository(tmp_path)
    graph = load_repository_blueprint_graph(tmp_path)
    declaration = graph.nodes["provider-skill.source.lookup"].declaration
    target = declaration["interfaces"]["provider-skill.source.lookup.interface.names"]
    target["contract"]["execution"]["state_effect"] = "mutating"

    with pytest.raises(InterfaceProjectionError, match="must be read-only"):
        project_consumer_interfaces(
            graph,
            "consumer-skill.source.gateway",
            _PassingView(),
        )


@pytest.mark.parametrize("source_node_id", [None, "provider-skill.source.lookup"])
@pytest.mark.parametrize("maximum", [pytest.param(..., id="missing"), None, "100", True])
def test_enum_helper_target_requires_integer_finite_output_cardinality(
    source_node_id: str | None,
    maximum: object,
) -> None:
    output: dict[str, object] = {
        "id": "result",
        "cardinality": {"minimum": 0},
    }
    if maximum is not ...:
        output["cardinality"]["maximum"] = maximum  # type: ignore[index]
    target = InterfaceExport(
        interface_id="provider-skill.interface.names",
        version=1,
        local_name="names",
        module_node_id="provider-skill",
        declaration={
            "contract": {
                "execution": {"state_effect": "read-only"},
                "outputs": [output],
            }
        },
        source_node_id=source_node_id,
    )
    edge = HelperEdge(
        source_export_id="consumer-skill.interface.run",
        local_helper_id="lookup",
        target_interface_id=target.interface_id,
        target_version=1,
        binding={
            "route": {"kind": "argument-enum", "target": "name"},
            "result": {
                "output_ref": "result",
                "selector": {"kind": "whole-output"},
            },
        },
    )

    with pytest.raises(InterfaceProjectionError, match="finite output cardinality"):
        _validate_helper_target(edge, target)

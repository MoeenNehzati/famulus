from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.common.blueprint_graph import (
    HelperEdge,
    InterfaceExport,
    load_repository_blueprint_graph,
)

_canonical_load_repository_blueprint_graph = load_repository_blueprint_graph


def load_repository_blueprint_graph(repo_root: Path, **kwargs: object):
    kwargs.setdefault("expected_schema_version", 4)
    kwargs.setdefault(
        "schema_root",
        Path(__file__).resolve().parents[1]
        / "references"
        / "blueprint"
        / "migrations"
        / "v4",
    )
    return _canonical_load_repository_blueprint_graph(repo_root, **kwargs)
from officina.common.blueprint_template import load_schema, schema_validator
from officina.common.certification_view import CertificationDecision, RejectingCertificationView
from officina.common.interface_projection import (
    InterfaceProjectionError,
    _validate_helper_target,
    project_consumer_interfaces,
    standalone_export_size,
)
from v5_blueprint_fixtures import copy_v5_fixture_tree


V5_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "blueprint"
    / "migrations"
    / "v5"
)
V5_AUTHORIZATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "authorization"
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


def _v5_projection_repository(
    tmp_path: Path,
    *,
    with_helper: bool = False,
) -> Path:
    root = copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE,
        tmp_path / "repo",
    )
    gateway_path = root / "skills" / "demo" / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["uses_interfaces"] = [
        {"interface": "demo.interface.execute", "version": 3}
    ]
    gateway_path.write_text(
        yaml.safe_dump(gateway, sort_keys=False),
        encoding="utf-8",
    )
    child_root = root / "skills" / "demo" / "_rtx"
    child_marker = child_root / "blueprint.yaml"
    child = yaml.safe_load(child_marker.read_text(encoding="utf-8"))
    child["content"] = [
        r"(?:__init__\.py|runtime\.py|lookup\.py|result\.schema\.json)"
    ]

    runtime_path = child_root / "blueprints" / "runtime.yaml"
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    runtime["content"] = [r"runtime\.py", r"result\.schema\.json"]
    execute = runtime["interfaces"]["demo-rtx.source.runtime.interface.execute"]
    execute["contract"] = _contract(schema_path="result.schema.json")
    execute["process_binding"] = _process_binding()

    if with_helper:
        lookup_export = "demo-rtx.interface.lookup"
        helper = {
            "id": "lookup",
            "role": "Supplies names.",
            "interface": lookup_export,
            "version": 1,
            "inputs": {},
            "result": {
                "output_ref": "result",
                "selector": {"kind": "whole-output"},
            },
            "route": {"kind": "argument-enum", "target": "name"},
            "empty": {"outcome": "success", "caller_action": "Stop."},
            "failure": {"outcome": "success"},
        }
        execute["contract"] = _contract(
            helper=helper,
            schema_path="result.schema.json",
        )
        runtime["uses_interfaces"] = [
            {"interface": lookup_export, "version": 1}
        ]
        lookup_source_interface = (
            "demo-rtx.source.lookup.interface.lookup"
        )
        child["sources"]["demo-rtx.source.lookup"] = {
            "blueprint": {
                "base": "module-root",
                "path": "blueprints/lookup.yaml",
            }
        }
        child["exports"][lookup_export] = {
            "source_interface": lookup_source_interface,
            "access": {
                "allow_all_modules": False,
                "allowed_callers": [],
            },
        }
        _write_yaml(
            child_root / "blueprints" / "lookup.yaml",
            {
                "schema_version": 5,
                "node_type": "behavioral_source",
                "id": "demo-rtx.source.lookup",
                "version": 1,
                "gateway": {
                    "path": "lookup.py",
                    "language": "Python>=3.11",
                },
                "content": [r"lookup\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    lookup_source_interface: {
                        "version": 1,
                        "description": "Lookup names.",
                        "contract": _contract(),
                        "process_binding": _process_binding(),
                    }
                },
            },
        )
        (child_root / "lookup.py").write_text(
            "class Interface:\n    pass\n",
            encoding="utf-8",
        )

    child_marker.write_text(
        yaml.safe_dump(child, sort_keys=False),
        encoding="utf-8",
    )
    runtime_path.write_text(
        yaml.safe_dump(runtime, sort_keys=False),
        encoding="utf-8",
    )
    (child_root / "result.schema.json").write_text(
        '{"type":"string"}',
        encoding="utf-8",
    )
    return root


def _load_v5_projection_graph(root: Path):
    return load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )


def test_v5_projection_derives_facade_contract_from_terminal_child(
    tmp_path: Path,
) -> None:
    root = _v5_projection_repository(tmp_path)
    certification = _PassingView()

    projection = project_consumer_interfaces(
        _load_v5_projection_graph(root),
        "demo.source.gateway",
        certification,
    )

    projected = projection.document["interfaces"]["demo.interface.execute"]
    assert projected["id"] == "demo.interface.execute"
    assert projected["version"] == 3
    assert projected["source_module"] == "demo-rtx"
    assert projected["source_interface"] == (
        "demo-rtx.source.runtime.interface.execute"
    )
    assert projected["gateway"] == {
        "path": "runtime.py",
        "language": "Python>=3.11",
    }
    assert projected["process_binding"] == _process_binding()
    assert {
        definition["source_module"]
        for definition in projection.document["definitions"].values()
    } == {"demo-rtx"}
    assert certification.checked == ["demo.interface.execute"]


def test_v5_projection_follows_helper_closure_through_facade(
    tmp_path: Path,
) -> None:
    root = _v5_projection_repository(tmp_path, with_helper=True)

    projection = project_consumer_interfaces(
        _load_v5_projection_graph(root),
        "demo.source.gateway",
        _PassingView(),
    )

    assert list(projection.document["helper_interfaces"]) == [
        "demo-rtx.interface.lookup"
    ]
    helper = projection.document["helper_interfaces"][
        "demo-rtx.interface.lookup"
    ]
    assert helper["source_module"] == "demo-rtx"
    assert helper["source_interface"] == (
        "demo-rtx.source.lookup.interface.lookup"
    )


def test_v5_projection_rejects_denied_authorization_result(
    tmp_path: Path,
) -> None:
    root = _v5_projection_repository(tmp_path)
    graph = _load_v5_projection_graph(root)
    terminal = graph.exports["demo-rtx.interface.execute"]
    assert isinstance(terminal.export_declaration, dict)
    terminal.export_declaration["access"] = {
        "allow_all_modules": False,
        "allowed_callers": [],
    }

    with pytest.raises(
        InterfaceProjectionError,
        match=(
            r"demo\.interface\.execute: authorization rejected "
            r"\[caller-filtered:terminal-export:"
            r"demo-rtx\.interface\.execute\]"
        ),
    ):
        project_consumer_interfaces(
            graph,
            "demo.source.gateway",
            _PassingView(),
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


def test_projection_rejects_failed_certification_and_standalone_overflow(
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
    with pytest.raises(
        InterfaceProjectionError,
        match=r"provider-skill\.interface\.run: standalone interface projection "
        r"is \d+ bytes; limit is 12288",
    ):
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

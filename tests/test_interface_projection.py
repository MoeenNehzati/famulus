from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from officina.blueprints.graph import (
    HelperEdge,
    InterfaceExport,
    RepositoryBlueprintGraph,
    resolve_export,
)

from officina.certification.view import CertificationDecision
from officina.blueprints.projection import (
    InterfaceProjectionError,
    _validate_helper_target,
    project_consumer_interfaces,
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


def test_live_repository_exports_project_their_complete_cli_contracts(
    ordinary_repository_graph: RepositoryBlueprintGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_interface = (
        "pdf-to-markdown._rtx.interface.scripts-fetch-arxiv-source"
    )
    source_interface = (
        "pdf-to-markdown._rtx.source.rtx-source-fetcher.interface."
        "scripts-fetch-arxiv-source"
    )
    graph = ordinary_repository_graph

    module, source, export = resolve_export(graph, public_interface, 1)
    projection = project_consumer_interfaces(
        graph,
        "pdf-to-markdown.source.gateway",
        _PassingView(),
    )
    projected = projection.document["interfaces"][public_interface]

    assert module.node_id == "pdf-to-markdown._rtx"
    assert source.node_id == "pdf-to-markdown._rtx.source.rtx-source-fetcher"
    assert export.terminal_interface_id == (
        "pdf-to-markdown._rtx.interface.scripts-fetch-arxiv-source"
    )
    assert export.source_interface_id == source_interface
    assert export.declaration["usage"] == "<arxiv-id> [<output-dir>]"
    assert projected["id"] == public_interface
    assert projected["source_module"] == "pdf-to-markdown._rtx"
    assert projected["source_interface"] == source_interface
    output_directory = projected["contract"]["arguments"]["output-dir"]
    assert output_directory["required"] is False
    assert output_directory["default"] == "."
    assert projected["process_binding"]["patterns"] == [
        {
            "max_positionals": 2,
            "min_positionals": 1,
            "name": "owner",
        }
    ]

    operations = ("disable", "enable")
    public_interfaces = [
        f"recurring-tasks._rtx.interface.scripts-{operation}"
        for operation in operations
    ]
    gateway = graph.nodes["recurring-tasks.source.gateway"]
    declared_uses = gateway.declaration["uses_interfaces"]
    assert all(
        {"interface": public_interface, "version": 1} in declared_uses
        for public_interface in public_interfaces
    )
    monkeypatch.setitem(
        gateway.declaration,
        "uses_interfaces",
        [
            {"interface": public_interface, "version": 1}
            for public_interface in public_interfaces
        ],
    )
    projection = project_consumer_interfaces(
        graph,
        "recurring-tasks.source.gateway",
        _PassingView(),
    )

    for operation, public_interface in zip(
        operations, public_interfaces, strict=True
    ):
        child_interface = f"recurring-tasks._rtx.interface.scripts-{operation}"
        source_interface = (
            "recurring-tasks._rtx.source.rtx-job-control.interface."
            f"scripts-{operation}"
        )
        module, source, export = resolve_export(graph, public_interface, 1)
        projected = projection.document["interfaces"][public_interface]

        assert module.node_id == "recurring-tasks._rtx"
        assert source.node_id == "recurring-tasks._rtx.source.rtx-job-control"
        assert export.terminal_interface_id == child_interface
        assert export.source_interface_id == source_interface
        assert export.declaration["usage"] == "<name>"
        assert projected["id"] == public_interface
        assert projected["source_module"] == "recurring-tasks._rtx"
        assert projected["source_interface"] == source_interface
        arguments = projected["contract"]["arguments"]
        assert set(arguments) == {"name"}
        assert arguments["name"]["required"] is True
        assert projected["process_binding"]["patterns"] == [
            {
                "allowed_flags": [],
                "max_positionals": 1,
                "min_positionals": 1,
                "name": "owner",
            }
        ]

    list_export = graph.exports["list-manager._rtx.interface.read-list"]
    assert list_export.declaration["usage"] == "<file> [filters] [--sort FIELD]"



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

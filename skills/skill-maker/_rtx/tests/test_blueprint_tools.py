#!/usr/bin/env python3
"""Focused tests for canonical version-5 blueprint synchronization."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
TESTS_ROOT = REPO_ROOT / "tests"
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from v5_blueprint_fixtures import copy_v5_fixture_tree


SYNCER_PATH = REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
BLUEPRINT_TEMPLATE = REPO_ROOT / "references" / "blueprint" / "template.yaml"
V5_SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"
V5_AUTHORIZATION_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "blueprint_v5" / "authorization"
)


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def syncer():
    return load_module("sync_module_blueprints_v5_tests", SYNCER_PATH)


def _copy_managed_skill(repo_root: Path) -> Path:
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


def _copy_v5_managed_skill(repo_root: Path) -> tuple[Path, dict[str, object]]:
    root = copy_v5_fixture_tree(V5_AUTHORIZATION_FIXTURE, repo_root)

    runtime_path = (
        root
        / "skills"
        / "demo"
        / "_rtx"
        / "blueprints"
        / "runtime.yaml"
    )
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    dependency = {
        "kind": "python-package",
        "name": "PyYAML",
        "version": ">=6",
        "platforms": {"linux": True, "macos": True, "windows": True},
        "reason": "Parses YAML.",
    }
    runtime["platform_support"] = {
        "linux": True,
        "macos": True,
        "windows": True,
    }
    runtime["runtime_dependencies"] = [dependency]
    runtime["interfaces"][
        "demo-rtx.source.runtime.interface.execute"
    ]["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    runtime_path.write_text(
        yaml.safe_dump(runtime, sort_keys=False),
        encoding="utf-8",
    )
    return root, dependency


def test_blueprint_template_is_canonical_v5_module() -> None:
    manifest = yaml.safe_load(BLUEPRINT_TEMPLATE.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 5
    assert manifest["node_type"] == "module"
    assert manifest["children"] == {}
    assert manifest["namespace_exports"] == {}


def test_syncer_loads_canonical_v5_modules_from_repository_graph(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_managed_skill(tmp_path)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", tmp_path / "skills")

    blueprint = syncer.load_blueprints()["loose-mode"]

    assert blueprint.data["node_type"] == "module"
    assert blueprint.repository_graph.nodes[
        "loose-mode.source.gateway"
    ].node_type == "behavioral_source"


def test_generated_blocks_use_canonical_v5_exports(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_managed_skill(tmp_path)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", tmp_path / "skills")
    blueprint = syncer.load_blueprints()["loose-mode"]

    contract = syncer.generated_contract_block(
        blueprint.name,
        blueprint.data,
        blueprint.repository_graph,
    )
    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )

    assert (
        "Catalog: assistant-interaction; topics: reasoning-control; "
        "visibility: featured"
    ) in contract
    assert "Activation: user-request; persistent modifier: yes" in contract
    assert "Skill Version: 2" in contract
    assert "`loose-mode.interface.default`" in contract
    assert "Instruction Interfaces:" in interfaces
    assert "`loose-mode.interface.default`" in interfaces


def test_v5_generated_views_are_parent_only_and_derive_facade_contract(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, dependency = _copy_v5_managed_skill(tmp_path / "repo")
    monkeypatch.setattr(syncer, "SKILLS_ROOT", root / "skills")

    blueprints = syncer.load_blueprints(
        schema_version=5,
        schema_root=V5_SCHEMA_ROOT,
    )

    assert set(blueprints) == {"demo"}
    blueprint = blueprints["demo"]
    contract = syncer.generated_contract_block(
        blueprint.name,
        blueprint.data,
        blueprint.repository_graph,
    )
    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )
    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert "`demo.interface.execute`" in contract
    assert "demo.source.gateway -> demo.interface.execute@3" not in contract
    assert "demo-rtx.interface.execute" not in contract + interfaces
    assert "`demo.interface.execute` — Execute the demo." in interfaces
    assert "dispatcher --caller-skill demo demo.interface.execute" in interfaces
    assert set(manifest["skills"]) == {"demo"}
    assert manifest["skills"]["demo"]["interfaces"]["execute"] == {
        "id": "demo.interface.execute",
        "dependencies": [dependency],
    }


def test_v5_generated_facade_view_uses_validated_structural_binding(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _dependency = _copy_v5_managed_skill(tmp_path / "repo")
    monkeypatch.setattr(syncer, "SKILLS_ROOT", root / "skills")
    blueprint = syncer.load_blueprints(
        schema_version=5,
        schema_root=V5_SCHEMA_ROOT,
    )["demo"]
    terminal = blueprint.repository_graph.exports[
        "demo-rtx.interface.execute"
    ]
    assert isinstance(terminal.export_declaration, dict)
    terminal.export_declaration["access"] = {
        "allow_all_modules": False,
        "allowed_callers": ["outsider"],
    }

    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )

    assert "`demo.interface.execute` — Execute the demo." in interfaces
    assert "demo-rtx.interface.execute" not in interfaces


def test_v5_syncer_rejects_generated_dispatch_missing_gateway_use(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _dependency = _copy_v5_managed_skill(tmp_path / "repo")
    gateway_path = root / "skills" / "demo" / "blueprints" / "gateway.yaml"
    gateway = yaml.safe_load(gateway_path.read_text(encoding="utf-8"))
    gateway["uses_interfaces"] = [
        entry
        for entry in gateway["uses_interfaces"]
        if entry["interface"] != "demo.interface.execute"
    ]
    gateway_path.write_text(
        yaml.safe_dump(gateway, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(syncer, "SKILLS_ROOT", root / "skills")
    blueprint = syncer.load_blueprints(
        schema_version=5,
        schema_root=V5_SCHEMA_ROOT,
    )["demo"]

    assert syncer.validate_gateway_declares_generated_dispatches(
        blueprint.name,
        blueprint.repository_graph,
    ) == [
        "demo.source.gateway: generated dispatcher exports are missing from "
        "uses_interfaces: demo.interface.execute@3"
    ]


def test_sync_module_check_then_refreshes_generated_blocks(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_root = _copy_managed_skill(tmp_path)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", tmp_path / "skills")
    blueprint = syncer.load_blueprints()["loose-mode"]
    gateway = module_root / "SKILL.md"
    gateway.write_text(
        gateway.read_text(encoding="utf-8").replace(
            "Catalog: assistant-interaction; topics: reasoning-control; "
            "visibility: featured",
            "Catalog: stale",
            1,
        ),
        encoding="utf-8",
    )

    assert syncer.sync_module(blueprint, check_only=True) == [
        f"{gateway}: generated blueprint blocks are out of sync"
    ]
    assert syncer.sync_module(blueprint, check_only=False) == []
    assert syncer.sync_module(blueprint, check_only=True) == []


def test_generated_contract_requires_catalog_discovery(syncer) -> None:
    graph = SimpleNamespace(module_sources={}, nodes={}, exports={})

    with pytest.raises(syncer.BlueprintError, match="discovery.*mapping"):
        syncer.generated_contract_block(
            "demo-skill",
            {"version": 1},
            graph,
        )


def test_runtime_dependency_manifest_uses_export_source_closure(syncer) -> None:
    dependency = {
        "kind": "python-package",
        "name": "PyYAML",
        "version": ">=6",
        "platforms": {"linux": True, "macos": True, "windows": True},
        "reason": "Parses YAML.",
    }
    source = SimpleNamespace(
        node_id="demo-skill.source.runner",
        declaration={"runtime_dependencies": [dependency]},
    )
    export = SimpleNamespace(
        module_node_id="demo-skill",
        local_name="run",
        source_node_id=source.node_id,
        declaration={"process_binding": {"kind": "process"}},
    )
    graph = SimpleNamespace(
        exports={"demo-skill.interface.run": export},
        nodes={source.node_id: source},
        node_edges=(),
    )
    blueprints = {
        "demo-skill": syncer.ModuleBlueprint(
            "demo-skill",
            Path("skills/demo-skill/blueprint.yaml"),
            {"schema_version": 4, "node_type": "module", "id": "demo-skill"},
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert manifest["skills"]["demo-skill"]["interfaces"]["run"] == {
        "id": "demo-skill.interface.run",
        "dependencies": [dependency],
    }
    assert manifest["all"]["python-package"] == ["PyYAML"]


def test_consumer_blocks_use_root_and_named_gateway_placement(
    tmp_path: Path,
    syncer,
) -> None:
    module_root = tmp_path / "demo-skill"
    root_gateway = module_root / "SKILL.md"
    named_gateway = module_root / "instructions" / "coach.md"
    named_gateway.parent.mkdir(parents=True)
    root_gateway.write_text(
        "---\nname: demo-skill\n---\n"
        f"{syncer.CONTRACT_START}\nContract\n{syncer.CONTRACT_END}\n"
        "Root body.\n",
        encoding="utf-8",
    )
    named_gateway.write_text("Named body.\n", encoding="utf-8")
    graph = SimpleNamespace(
        nodes={
            "demo-skill.source.gateway": SimpleNamespace(
                node_type="behavioral_source",
                gateway_path=root_gateway,
                module_root=module_root,
            ),
            "demo-skill.source.coach": SimpleNamespace(
                node_type="behavioral_source",
                gateway_path=named_gateway,
                module_root=module_root,
            ),
        }
    )
    selected = {
        "schema_version": 2,
        "consumer": "demo-skill.source.gateway",
        "interfaces": {"provider.interface.run": {"id": "provider.interface.run"}},
        "helper_interfaces": {},
        "definitions": {},
    }
    projections = {
        "demo-skill.source.gateway": SimpleNamespace(document=selected),
        "demo-skill.source.coach": SimpleNamespace(
            document={**selected, "consumer": "demo-skill.source.coach"}
        ),
    }

    planned = syncer.plan_consumer_interface_updates(graph, projections)

    assert planned[root_gateway].index(syncer.USED_INTERFACES_START) > planned[
        root_gateway
    ].index(syncer.CONTRACT_END)
    assert planned[named_gateway].startswith(syncer.USED_INTERFACES_START)
    assert planned[named_gateway].endswith("Named body.\n")


def test_consumer_update_planning_rejects_shared_gateway(
    tmp_path: Path,
    syncer,
) -> None:
    module_root = tmp_path / "demo-skill"
    module_root.mkdir()
    gateway = module_root / "instructions.md"
    gateway.write_text("Body.\n", encoding="utf-8")
    graph = SimpleNamespace(
        nodes={
            node_id: SimpleNamespace(
                node_type="behavioral_source",
                gateway_path=gateway,
                module_root=module_root,
            )
            for node_id in ("demo-skill.source.one", "demo-skill.source.two")
        }
    )
    projections = {
        node_id: SimpleNamespace(
            document={
                "schema_version": 2,
                "consumer": node_id,
                "interfaces": {},
                "helper_interfaces": {},
                "definitions": {},
            }
        )
        for node_id in graph.nodes
    }

    with pytest.raises(syncer.BlueprintError, match="shared by consumers"):
        syncer.plan_consumer_interface_updates(graph, projections)


def test_generated_used_interface_block_is_deterministic(syncer) -> None:
    document = {
        "schema_version": 2,
        "consumer": "demo-skill.source.gateway",
        "interfaces": {"provider.interface.run": {"version": 1}},
        "helper_interfaces": {},
        "definitions": {},
    }

    first = syncer.generated_used_interfaces_block(document)
    second = syncer.generated_used_interfaces_block(
        json.loads(json.dumps(document))
    )

    assert first == second
    assert first.startswith(syncer.USED_INTERFACES_START)
    assert first.endswith(f"{syncer.USED_INTERFACES_END}\n")

#!/usr/bin/env python3
"""Focused tests for canonical version-6 blueprint synchronization."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]


SYNCER_PATH = REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def syncer():
    return load_module("sync_module_blueprints_v6_tests", SYNCER_PATH)


def _copy_managed_skill(repo_root: Path) -> Path:
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


def test_syncer_loads_canonical_module_and_generates_interface_block(
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

    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )

    assert "Used Interfaces: none" in interfaces
    assert "`loose-mode.interface.default`" not in interfaces


def test_syncer_rejects_schema_version_selection_from_its_public_parser(
    syncer,
) -> None:
    with pytest.raises(SystemExit):
        syncer.Interface().build_parser().parse_args(["--schema-version", "5"])


def test_blueprints_from_graph_rejects_pre_v6_graph(
    tmp_path: Path,
    syncer,
) -> None:
    with pytest.raises(syncer.BlueprintError, match="requires schema version 6"):
        syncer.blueprints_from_graph(
            SimpleNamespace(schema_version=5),
            skills_root=tmp_path / "skills",
        )


def _gateway_use_graph(*, edges=(), exports=None, source_interfaces=None):
    gateway_path = Path("skills/consumer/SKILL.md")
    return SimpleNamespace(
        nodes={
            "consumer": SimpleNamespace(gateway_path=gateway_path),
            "consumer.source.gateway": SimpleNamespace(node_id="consumer.source.gateway", gateway_path=gateway_path),
        },
        module_sources={"consumer": ("consumer.source.gateway",)},
        node_edges=edges,
        exports=exports or {},
        source_interfaces=source_interfaces or {},
    )
def _interface_export(interface_id, version, declaration):
    return SimpleNamespace(
        interface_id=interface_id,
        version=version,
        local_name=interface_id.rsplit(".", 1)[-1],
        module_node_id="provider",
        declaration=declaration,
    )


def _blueprints_with_blank_gateway_description(tmp_path: Path, syncer):
    skill_dir = tmp_path / "skills" / "consumer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: consumer\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    interface_id = "provider.interface.run"
    graph = _gateway_use_graph(
        edges=(
            SimpleNamespace(
                source_id="consumer.source.gateway",
                relation="uses-export",
                target_id=interface_id,
                required_version=1,
            ),
        ),
        exports={
            interface_id: _interface_export(
                interface_id,
                1,
                {"description": " ", "process_binding": {}},
            ),
        },
    )
    return {
        "consumer": syncer.ModuleBlueprint(
            "consumer",
            skill_dir / "blueprint.yaml",
            {},
            graph,
        )
    }


def test_generated_interface_block_renders_only_declared_gateway_uses(syncer) -> None:
    gateway = "consumer.source.gateway"
    alpha = "provider.interface.alpha"
    run = "provider.interface.run"
    coach = "provider._rtx.interface.coach"
    exports = {
        alpha: _interface_export(alpha, 1, {"description": "Alpha.", "process_binding": {}}),
        run: _interface_export(run, 2, {"description": "Run.", "process_binding": {}}),
        "provider.interface.unused": _interface_export("provider.interface.unused", 1, {"description": "Unused.", "process_binding": {}}),
    }
    graph = _gateway_use_graph(
        edges=(
            SimpleNamespace(source_id=gateway, relation="uses-export", target_id=run, required_version=2),
            SimpleNamespace(source_id=gateway, relation="uses-private-interface", target_id=coach, required_version=4),
            SimpleNamespace(source_id=gateway, relation="uses-export", target_id=alpha, required_version=1),
            SimpleNamespace(source_id="consumer.source.worker", relation="uses-export", target_id="provider.interface.transitive", required_version=1),
        ),
        exports=exports,
        source_interfaces={coach: _interface_export(coach, 4, {"description": "Coach."})},
    )

    block = syncer.generated_interface_block("consumer", graph)

    assert "`provider.interface.alpha@1` — Alpha." in block
    assert "`provider.interface.run@2` — Run." in block
    assert "`provider._rtx.interface.coach@4` — Coach." in block
    assert "dispatcher --caller-skill consumer provider.interface.run ..." in block
    assert block.index("provider.interface.alpha@1") < block.index("provider.interface.run@2")
    assert all(interface_id not in block for interface_id in ("provider.interface.unused", "provider.interface.transitive"))
def test_generated_interface_block_rejects_blank_declared_use_description(syncer) -> None:
    interface_id = "provider.interface.run"
    graph = _gateway_use_graph(
        edges=(SimpleNamespace(source_id="consumer.source.gateway", relation="uses-export", target_id=interface_id, required_version=1),),
        exports={interface_id: _interface_export(interface_id, 1, {"description": " ", "process_binding": {}})},
    )
    with pytest.raises(syncer.BlueprintError, match="description"):
        syncer.generated_interface_block("consumer", graph)
def test_generated_interface_block_rejects_unresolved_declared_use(syncer) -> None:
    with pytest.raises(syncer.BlueprintError, match="unresolved"): syncer.generated_interface_block("consumer", _gateway_use_graph(edges=(SimpleNamespace(source_id="consumer.source.gateway", relation="uses-export", target_id="provider.interface.missing", required_version=1),)))
def test_generated_interface_block_keeps_empty_gateway_use_block(syncer) -> None:
    block = syncer.generated_interface_block("consumer", _gateway_use_graph())
    assert block.count(syncer.INTERFACES_START) == 1
    assert "Used Interfaces: none" in block
def test_sync_interface_block_preserves_bytes_outside_existing_markers(syncer) -> None:
    text = "---\nname: demo\n---\n\n<!-- BEGIN BLUEPRINT INTERFACES -->\nold\n<!-- END BLUEPRINT INTERFACES -->\n\n\nBody.\n"
    replacement = "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n<!-- END BLUEPRINT INTERFACES -->\n"

    assert syncer.sync_interface_block(text, replacement) == (
        "---\nname: demo\n---\n\n<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n\n\nBody.\n"
    )


def test_run_sync_reports_gateway_description_error(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blueprints = _blueprints_with_blank_gateway_description(tmp_path, syncer)
    monkeypatch.setattr(syncer, "load_blueprints", lambda: blueprints)

    assert syncer.run_sync(check_only=True) == 1
    assert capsys.readouterr().err == (
        "error: provider.interface.run: description must be non-empty\n"
    )


def test_validate_sync_state_returns_gateway_description_diagnostic(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blueprints = _blueprints_with_blank_gateway_description(tmp_path, syncer)
    monkeypatch.setattr(
        syncer,
        "blueprints_from_graph",
        lambda _graph, *, skills_root: blueprints,
    )

    assert syncer.validate_sync_state(
        repository_graph=SimpleNamespace(schema_version=6),
        repository_root=tmp_path,
        skills_root=tmp_path / "skills",
        runtime_dependencies_path=tmp_path / "runtime_dependencies.json",
    ) == ["provider.interface.run: description must be non-empty"]


def test_validate_sync_state_does_not_swallow_unrelated_errors(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blueprints = _blueprints_with_blank_gateway_description(tmp_path, syncer)
    monkeypatch.setattr(
        syncer,
        "blueprints_from_graph",
        lambda _graph, *, skills_root: blueprints,
    )

    def _unexpected_failure(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(syncer, "sync_module", _unexpected_failure)

    with pytest.raises(RuntimeError, match="unrelated failure"):
        syncer.validate_sync_state(
            repository_graph=SimpleNamespace(schema_version=6),
            repository_root=tmp_path,
            skills_root=tmp_path / "skills",
            runtime_dependencies_path=tmp_path / "runtime_dependencies.json",
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
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "demo-skill",
                "maturity": "stable",
                "installation_tier": "core",
                "personal_preference": {"applies": False},
            },
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert manifest["version"] == 2
    module = manifest["skills"]["demo-skill"]
    assert module["maturity"] == "stable"
    assert module["installation_tier"] == "core"
    assert module["personal_preference"] == {"applies": False}
    assert module["interfaces"]["demo-skill.interface.run"] == {
        "dependencies": [dependency],
    }
    assert manifest["all"]["python-package"] == ["PyYAML"]


def test_runtime_dependency_manifest_keeps_module_without_executable_interfaces(
    syncer,
) -> None:
    graph = SimpleNamespace(exports={}, nodes={}, node_edges=())
    blueprints = {
        "metadata-only": syncer.ModuleBlueprint(
            "metadata-only",
            Path("skills/metadata-only/blueprint.yaml"),
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "metadata-only",
                "maturity": "experimental",
                "installation_tier": "optional",
                "personal_preference": {"applies": False},
            },
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert manifest["skills"] == {
        "metadata-only": {
            "maturity": "experimental",
            "installation_tier": "optional",
            "personal_preference": {"applies": False},
            "interfaces": {},
        }
    }
    assert manifest["all"] == {
        kind: [] for kind in syncer.RUNTIME_DEPENDENCY_KINDS
    }


def test_runtime_dependency_manifest_v2_keeps_all_descendant_interface_ids(syncer) -> None:
    """Canonical IDs prevent equal child-local names from overwriting, and
    aggregation follows ownership rather than namespace exposure."""
    dependencies = {
        "demo._rtx": {
            "kind": "python-package", "name": "PyYAML", "version": ">=6",
            "platforms": {"linux": True, "macos": True, "windows": True},
            "reason": "Private child parser.",
        },
        "demo.worker": {
            "kind": "python-package", "name": "rich", "version": "any",
            "platforms": {"linux": True, "macos": True, "windows": True},
            "reason": "Worker output.",
        },
    }
    exports = {}
    nodes = {}
    for owner, dependency in dependencies.items():
        source_id = f"{owner}.source.runner"
        interface_id = f"{owner}.interface.run"
        nodes[source_id] = SimpleNamespace(
            node_id=source_id,
            declaration={"runtime_dependencies": [dependency]},
        )
        exports[interface_id] = SimpleNamespace(
            module_node_id=owner,
            local_name="run",
            source_node_id=source_id,
            declaration={"process_binding": {"kind": "process"}},
        )
    graph = SimpleNamespace(
        exports=exports,
        nodes=nodes,
        node_edges=(),
        module_ancestry={
            "demo": ("demo",),
            "demo._rtx": ("demo", "demo._rtx"),
            "demo.worker": ("demo", "demo.worker"),
        },
    )
    blueprints = {
        "demo": syncer.ModuleBlueprint(
            "demo", Path("skills/demo/blueprint.yaml"),
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "demo",
                "maturity": "stable",
                "installation_tier": "optional",
                "personal_preference": {
                    "applies": True,
                    "description": "The user selected this optional workflow.",
                },
            },
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    module = manifest["skills"]["demo"]
    assert module["maturity"] == "stable"
    assert module["installation_tier"] == "optional"
    assert module["personal_preference"] == {
        "applies": True,
        "description": "The user selected this optional workflow.",
    }
    interfaces = module["interfaces"]
    assert set(interfaces) == {
        "demo._rtx.interface.run",
        "demo.worker.interface.run",
    }
    assert interfaces["demo._rtx.interface.run"]["dependencies"] == [dependencies["demo._rtx"]]
    assert interfaces["demo.worker.interface.run"]["dependencies"] == [dependencies["demo.worker"]]


def test_sync_does_not_create_dispatch_routing_state(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    _copy_managed_skill(repo_root)
    schema_root = repo_root / "references" / "blueprint-schema"
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint-schema",
        schema_root,
    )
    skill_file = repo_root / "skills" / "loose-mode" / "SKILL.md"
    skill_file.write_text(
        "---\nname: loose-mode\ndescription: Test fixture.\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    manifest = schema_root / "runtime_dependencies.json"
    monkeypatch.setattr(syncer, "REPO_ROOT", repo_root)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", repo_root / "skills")
    monkeypatch.setattr(syncer, "BLUEPRINT_SCHEMA_ROOT", schema_root)
    monkeypatch.setattr(syncer, "RUNTIME_DEPENDENCIES_PATH", manifest)
    data_home = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    original_manifest = manifest.read_bytes()
    assert syncer.run_sync(check_only=False) == 0
    assert manifest.is_file()
    written_manifest = manifest.read_bytes()
    assert written_manifest != original_manifest
    assert syncer.run_sync(check_only=True) == 0
    assert manifest.read_bytes() == written_manifest
    assert not data_home.exists()


def test_validate_sync_state_reuses_the_provided_graph(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_managed_skill(tmp_path)
    skills_root = tmp_path / "skills"
    monkeypatch.setattr(syncer, "SKILLS_ROOT", skills_root)
    graph = syncer.load_blueprints()["loose-mode"].repository_graph
    skill_path = skills_root / "loose-mode" / "SKILL.md"; skill_path.write_text(syncer.sync_interface_block(skill_path.read_text(encoding="utf-8"), syncer.generated_interface_block("loose-mode", graph)), encoding="utf-8")
    runtime_dependencies_path = (
        tmp_path / "references" / "blueprint-schema" / "runtime_dependencies.json"
    )
    runtime_dependencies_path.parent.mkdir(parents=True)
    blueprints = syncer.blueprints_from_graph(
        graph,
        skills_root=skills_root,
    )
    runtime_dependencies_path.write_text(
        json.dumps(
            syncer.generated_runtime_dependencies_manifest(blueprints),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    def _unexpected_graph_load(*_args: object, **_kwargs: object) -> None:
        pytest.fail("sync-state checking must use the supplied graph")

    monkeypatch.setattr(
        syncer,
        "load_repository_blueprint_graph",
        _unexpected_graph_load,
    )

    assert syncer.validate_sync_state(
        repository_graph=graph,
        repository_root=tmp_path,
        skills_root=skills_root,
        runtime_dependencies_path=runtime_dependencies_path,
    ) == []

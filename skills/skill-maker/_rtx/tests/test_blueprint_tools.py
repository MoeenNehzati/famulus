#!/usr/bin/env python3
"""Focused tests for canonical blueprint synchronization."""

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
    return load_module("sync_module_blueprints_tests", SYNCER_PATH)


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


def test_generated_executable_interface_uses_famulus_metadata(syncer) -> None:
    """Break caught: generated skill guidance falls back to Dispatcher syntax."""
    blueprints = syncer.load_blueprints()

    interfaces = syncer.generated_interface_block(
        "milestone-logging",
        blueprints["milestone-logging"].repository_graph,
    )

    assert "Executable Interfaces:" in interfaces
    assert "Caller: `milestone-logging`" in interfaces
    assert "Version: 1" in interfaces
    assert '"positionals": ["DOING", "PREV"]' in interfaces
    assert '"--role": "ROLE"' in interfaces
    assert '"--done": "PREV"' in interfaces
    assert '"--path": true' in interfaces
    assert "Omit optional positionals and options that are not needed." in interfaces
    assert "Ordered outer JSON" not in interfaces
    assert "Alternative: `milestone`" in interfaces
    assert "dispatcher --caller-skill" not in interfaces


def test_generated_interface_block_renders_only_direct_gateway_uses(syncer) -> None:
    """Direct gateway uses reach MCP guidance without transitive leakage."""
    gateway = "consumer.source.gateway"
    process = "provider.interface.run"
    owner = "consumer.interface.owner"
    unused = "provider.interface.unused"
    instructions = "provider.interface.instructions"
    source = "provider.source.cli"
    unused_source = "provider.source.unused"
    process_spec = {
        "description": "Run the provider.",
        "usage": "",
        "process_binding": {
            "kind": "process",
            "entry": "Interface",
            "patterns": [
                {
                    "name": "default",
                    "min_positionals": 0,
                    "max_positionals": 0,
                    "allow_stdin": False,
                }
            ],
        },
    }
    graph = SimpleNamespace(
        schema_version=6,
        module_sources={"consumer": (gateway,)},
        module_ancestry={"consumer": ("consumer",), "provider": ("provider",)},
        nodes={
            "consumer": SimpleNamespace(gateway_path=Path("SKILL.md")),
            gateway: SimpleNamespace(node_id=gateway, gateway_path=Path("SKILL.md")),
            source: SimpleNamespace(version=1),
            unused_source: SimpleNamespace(version=1),
        },
        exports={
            process: SimpleNamespace(
                interface_id=process,
                version=1,
                module_node_id="provider",
                declaration=process_spec,
                source_node_id=source,
            ),
            instructions: SimpleNamespace(
                interface_id=instructions,
                version=1,
                module_node_id="provider",
                declaration={"description": "Read the provider."},
                source_node_id=source,
            ),
            owner: SimpleNamespace(
                interface_id=owner,
                version=1,
                module_node_id="consumer",
                declaration=process_spec,
                source_node_id=source,
            ),
            unused: SimpleNamespace(
                interface_id=unused,
                version=1,
                module_node_id="provider",
                declaration=process_spec,
                source_node_id=unused_source,
            ),
        },
        source_interfaces={},
        node_edges=(
            SimpleNamespace(
                relation="uses-source",
                source_id=gateway,
                target_id=source,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-source",
                source_id=gateway,
                target_id=unused_source,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=process,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=owner,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=process,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=instructions,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id="consumer.source.worker",
                target_id="provider.interface.transitive",
                required_version=1,
            ),
        ),
    )

    rendered = syncer.generated_interface_block("consumer", graph)

    assert "`provider.interface.run`" in rendered
    assert rendered.count("`provider.interface.run`") == 1
    assert rendered.count("`consumer.interface.owner`") == 1
    assert "Caller: `consumer`" in rendered
    assert instructions in rendered
    assert unused not in rendered
    assert "provider.interface.transitive" not in rendered

    original_edges = graph.node_edges
    graph.node_edges = tuple(
        SimpleNamespace(
            relation=edge.relation,
            source_id=edge.source_id,
            target_id=edge.target_id,
            required_version=(
                2
                if edge.relation == "uses-export" and edge.target_id == process
                else edge.required_version
            ),
        )
        for edge in original_edges
    )
    with pytest.raises(syncer.BlueprintError, match="use version"):
        syncer.generated_interface_block("consumer", graph)


def test_generated_interface_block_rejects_blank_direct_use_description(syncer) -> None:
    interface_id = "provider.interface.run"
    gateway = "consumer.source.gateway"
    graph = SimpleNamespace(
        nodes={
            "consumer": SimpleNamespace(gateway_path=Path("SKILL.md")),
            gateway: SimpleNamespace(node_id=gateway, gateway_path=Path("SKILL.md")),
        },
        module_sources={"consumer": (gateway,)},
        node_edges=(
            SimpleNamespace(
                source_id=gateway,
                relation="uses-export",
                target_id=interface_id,
                required_version=1,
            ),
        ),
        exports={
            interface_id: SimpleNamespace(
                version=1,
                declaration={"description": " ", "process_binding": {}},
            )
        },
        source_interfaces={},
    )

    with pytest.raises(syncer.BlueprintError, match="description"):
        syncer.generated_interface_block("consumer", graph)


def test_generated_interface_block_rejects_unresolved_direct_use(syncer) -> None:
    gateway = "consumer.source.gateway"
    graph = SimpleNamespace(
        nodes={
            "consumer": SimpleNamespace(gateway_path=Path("SKILL.md")),
            gateway: SimpleNamespace(node_id=gateway, gateway_path=Path("SKILL.md")),
        },
        module_sources={"consumer": (gateway,)},
        node_edges=(
            SimpleNamespace(
                source_id=gateway,
                relation="uses-export",
                target_id="provider.interface.missing",
                required_version=1,
            ),
        ),
        exports={},
        source_interfaces={},
    )

    with pytest.raises(syncer.BlueprintError, match="unresolved"):
        syncer.generated_interface_block("consumer", graph)


def test_sync_interface_block_preserves_bytes_outside_existing_markers(syncer) -> None:
    text = (
        "---\nname: demo\n---\n\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nold\n"
        "<!-- END BLUEPRINT INTERFACES -->\n\n\nBody.\n"
    )
    replacement = (
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
    )

    assert syncer.sync_interface_block(text, replacement) == (
        "---\nname: demo\n---\n\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n\n\nBody.\n"
    )


def test_sync_interface_block_replaces_legacy_contract_without_touching_body(syncer) -> None:
    """Break caught: interface regeneration leaves the obsolete contract block behind."""
    text = (
        "---\nname: demo\n---\n\n"
        "<!-- BEGIN BLUEPRINT CONTRACT -->\nlegacy\n"
        "<!-- END BLUEPRINT CONTRACT -->\n"
        "Body.\n"
    )
    replacement = (
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
    )

    assert syncer.sync_interface_block(text, replacement) == (
        "---\nname: demo\n---\n\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "Body.\n"
    )


def test_sync_interface_block_removes_legacy_contract_before_replacing_interface(syncer) -> None:
    """Break caught: an existing interface block masks a preceding legacy contract."""
    text = (
        "---\nname: demo\n---\n\n"
        "<!-- BEGIN BLUEPRINT CONTRACT -->\nlegacy\n"
        "<!-- END BLUEPRINT CONTRACT -->\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nold\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "Body.\n"
    )
    replacement = (
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
    )

    assert syncer.sync_interface_block(text, replacement) == (
        "---\nname: demo\n---\n\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nnew\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "Body.\n"
    )


def _managed_gate_graph(
    *,
    kind: str = "markdown",
    opted_in: bool = True,
    gateway_language: str = "Markdown",
):
    module_id = "managed"
    gateway_id = "managed.source.gateway"
    setup_interface = "managed.interface.setup"
    return SimpleNamespace(
        schema_version=6,
        module_sources={module_id: (gateway_id,)},
        module_ancestry={module_id: (module_id,)},
        nodes={
            module_id: SimpleNamespace(gateway_path=Path("SKILL.md")),
            gateway_id: SimpleNamespace(
                node_id=gateway_id,
                gateway_path=Path("SKILL.md"),
                declaration={"gateway": {"language": gateway_language}},
            ),
        },
        node_edges=(),
        exports={
            setup_interface: SimpleNamespace(
                interface_id=setup_interface,
                module_node_id=module_id,
                version=1,
                declaration={"description": "Set up managed."},
            )
        },
        source_interfaces={},
        managed_setups=(
            {
                setup_interface: SimpleNamespace(
                    setup_interface=setup_interface,
                    setup_version=1,
                    teardown_interface="managed.interface.teardown",
                    teardown_version=1,
                    kind=kind,
                )
            }
            if opted_in
            else {}
        ),
    )


def test_generated_interface_block_includes_the_managed_markdown_protocol(syncer) -> None:
    """Catches a gate that skips ready authorization or exposes continuation data."""
    block = syncer.generated_interface_block("managed", _managed_gate_graph())

    assert "### Managed setup gate" in block
    assert "`managed.interface.setup@1`" in block
    assert "`managed.interface.teardown@1`" in block
    assert "`setup-interface-manager._rtx.interface.status@1`" in block
    assert "`setup-interface-manager._rtx.interface.begin@1`" in block
    assert "`setup-interface-manager._rtx.interface.run-markdown@1`" in block
    assert "`setup-interface-manager._rtx.interface.settle@1`" in block
    assert "`setup-interface-manager._rtx.interface.authorize@1`" in block
    ordinary_protocol = block[block.index("For an ordinary invocation"):]
    assert ordinary_protocol.index("status") < ordinary_protocol.index("permission") < ordinary_protocol.index("begin")
    assert ordinary_protocol.index("ready recheck") < ordinary_protocol.index("authorize") < ordinary_protocol.index("Retry")
    assert (
        "begin(setup, ROOT_SETUP_INTERFACE, ORIGINAL_CALLER, ORIGINAL_INTERFACE, "
        "ORIGINAL_VERSION)" in block
    )
    assert (
        "begin(teardown, managed.interface.setup, ORIGINAL_CALLER, "
        "ORIGINAL_INTERFACE, ORIGINAL_VERSION)" in block
    )
    assert "caller, interface, version, arguments, and stdin outside the ledger" in block
    assert "exact structured current step" in block
    assert "Generic setup prose does not activate this gate" in block
    assert "path" not in block.lower()


def test_generated_interface_block_limits_and_removes_the_managed_markdown_gate(syncer) -> None:
    """Catches gates leaking to bootstrap/plain exports or surviving opt-out."""
    managed = _managed_gate_graph()
    block = syncer.generated_interface_block("managed", managed)
    assert block == syncer.generated_interface_block("managed", managed)
    assert "### Managed setup gate" not in syncer.generated_interface_block(
        "managed", _managed_gate_graph(kind="python")
    )
    assert "### Managed setup gate" not in syncer.generated_interface_block(
        "managed", _managed_gate_graph(gateway_language="Python")
    )
    assert "### Managed setup gate" not in syncer.generated_interface_block(
        "managed", _managed_gate_graph(opted_in=False)
    )
    bootstrap = syncer.load_blueprints()["setup-dispatcher-runtime"]
    assert "### Managed setup gate" not in syncer.generated_interface_block(
        bootstrap.name, bootstrap.repository_graph
    )

    original = (
        "---\nname: managed\n---\n\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\nold\n"
        "<!-- END BLUEPRINT INTERFACES -->\n\nBody bytes stay put.\n"
    )
    gated = syncer.sync_interface_block(original, block)
    ungated = syncer.sync_interface_block(
        gated,
        syncer.generated_interface_block("managed", _managed_gate_graph(opted_in=False)),
    )

    assert gated.startswith("---\nname: managed\n---\n\n")
    assert gated.endswith("\n\nBody bytes stay put.\n")
    assert syncer.sync_interface_block(gated, block) == gated
    assert "### Managed setup gate" not in ungated
    assert ungated.startswith("---\nname: managed\n---\n\n")
    assert ungated.endswith("\n\nBody bytes stay put.\n")


def test_llm_wakeup_generated_interfaces_are_exact(syncer) -> None:
    blueprint = syncer.load_blueprints()["llm-wakeup"]
    skill = blueprint.path.parent / "SKILL.md"
    generated = skill.read_text(encoding="utf-8")

    assert syncer.generated_interface_block(
        blueprint.name, blueprint.repository_graph
    ) in generated


def test_public_syncer_repairs_corrupt_llm_wakeup_entry(
    syncer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: public sync check does not repair a selected external use."""
    repository = tmp_path / "repository"
    shutil.copytree(REPO_ROOT / "skills", repository / "skills")
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint-schema",
        repository / "references" / "blueprint-schema",
    )
    shutil.copytree(REPO_ROOT / "src", repository / "src")
    shutil.copytree(
        REPO_ROOT / "references" / "node-standards",
        repository / "references" / "node-standards",
    )
    shutil.copy2(REPO_ROOT / "officina.toml", repository / "officina.toml")
    monkeypatch.setattr(syncer, "REPO_ROOT", repository)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", repository / "skills")
    monkeypatch.setattr(
        syncer,
        "BLUEPRINT_SCHEMA_ROOT",
        repository / "references" / "blueprint-schema",
    )
    monkeypatch.setattr(
        syncer,
        "RUNTIME_DEPENDENCIES_PATH",
        repository / "references" / "blueprint-schema" / "runtime_dependencies.json",
    )
    skill = repository / "skills" / "llm-wakeup" / "SKILL.md"
    original = skill.read_text(encoding="utf-8")
    start = original.index(syncer.INTERFACES_START)
    skill.write_text(
        original[:start]
        + original[start:].replace(
            "`wakeup.interface.explicit-schedule`", "`wakeup.interface.removed`", 1
        ),
        encoding="utf-8",
    )

    check = SimpleNamespace(check=True, schema_version=6)
    assert syncer.Interface().run(check) == 1
    assert syncer.Interface().run(SimpleNamespace(check=False, schema_version=6)) == 0

    blueprint = syncer.load_blueprints()["llm-wakeup"]
    repaired = skill.read_text(encoding="utf-8")
    assert syncer.generated_interface_block(
        blueprint.name, blueprint.repository_graph
    ) in repaired
    assert syncer.Interface().run(check) == 0


def test_generated_executable_preserves_patterns_placeholders_and_arity(syncer) -> None:
    """Catch lossy aliases, arity, placeholders, or generated fallbacks."""
    blueprints = syncer.load_blueprints()
    graph = blueprints["email-client"].repository_graph

    interfaces = syncer.generated_interface_block("email-client", graph)
    start = interfaces.index("email-client._rtx.interface.mail-attachments")
    end = interfaces.index("email-client._rtx.interface.mail-folders")
    attachments = interfaces[start:end]

    assert "Alternative: `short-account`" in attachments
    short_attachments = attachments[:attachments.index("Alternative: `long-account`")]
    assert 'Required options: ["-a"]; positional arity: 1..unbounded; stdin: forbidden' in short_attachments
    assert '"positionals": ["uid", "uid..."]' in short_attachments
    assert '"-a": "nickname"' in short_attachments
    assert '"--folder": "inbox|sent|drafts|trash|all|<literal>"' in short_attachments
    assert '"--account":' not in short_attachments
    long_attachments = attachments[attachments.index("Alternative: `long-account`"):]
    assert '"positionals": ["uid", "uid..."]' in long_attachments
    assert '"--account": "nickname"' in long_attachments
    assert '"-a":' not in long_attachments
    folders = interfaces[interfaces.index("email-client._rtx.interface.mail-folders"):]
    assert "Alternative: `long-account`" in folders
    assert 'Required options: ["--account"]' in folders
    assert "stdin: permitted" in interfaces

    assert '"--attach": "/path[:DisplayName]"' in interfaces
    for skill in ("email-client", "daily-plan", "node-certify", "node-drift"):
        blueprint = blueprints[skill]
        generated = syncer.generated_interface_block(
            skill, blueprint.repository_graph
        )
        assert "POSITIONAL_" not in generated

    daily = syncer.generated_interface_block(
        "daily-plan", blueprints["daily-plan"].repository_graph
    )
    indexed = daily[daily.index("Alternative: `indexed-or-add`"):daily.index("Alternative: `set-deadline`")]
    assert "set-deadline" not in indexed
    assert '"positionals": ["set-deadline", "actions|triage", "indices-or-item-id", "deadline-for-set-deadline"]' in daily

    triage = syncer.generated_interface_block(
        "email-triage", blueprints["email-triage"].repository_graph
    )
    assert '"--total-scanned": "N"' in triage
    assert '"--added-todo": "N"' in triage


def test_generated_executable_rejects_ambiguous_usage_and_option_alias(syncer) -> None:
    graph = syncer.load_blueprints()["email-client"].repository_graph
    attachments = graph.exports[
        "email-client._rtx.interface.mail-attachments"
    ].declaration
    original_usage = attachments["usage"]
    attachments["usage"] = ""

    with pytest.raises(
        syncer.BlueprintError,
        match="usage cannot be projected unambiguously",
    ):
        syncer.generated_interface_block("email-client", graph)

    attachments["usage"] = original_usage
    export = graph.exports["email-client._rtx.interface.mail-folders"]
    spec = export.declaration
    long_pattern = spec["process_binding"]["patterns"][1]
    spec["process_binding"]["patterns"] = [long_pattern]
    spec["usage"] = "-a <nickname> -b <other>"
    long_pattern["forbidden_flags"] = ["-a", "-b"]

    with pytest.raises(syncer.BlueprintError, match="ambiguous option alias"):
        syncer.generated_interface_block("email-client", graph)


def _blueprints_with_unprojectable_usage(tmp_path: Path, syncer):
    skill_dir = tmp_path / "skills" / "consumer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: consumer\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    interface_id = "provider.interface.run"
    gateway_path = skill_dir / "SKILL.md"
    graph = SimpleNamespace(
        nodes={
            "consumer": SimpleNamespace(gateway_path=gateway_path),
            "consumer.source.gateway": SimpleNamespace(
                node_id="consumer.source.gateway",
                gateway_path=gateway_path,
            ),
        },
        module_sources={"consumer": ("consumer.source.gateway",)},
        node_edges=(
            SimpleNamespace(
                source_id="consumer.source.gateway",
                relation="uses-export",
                target_id=interface_id,
                required_version=1,
            ),
        ),
        exports={
            interface_id: SimpleNamespace(
                version=1,
                declaration={
                    "description": "Run.",
                    "usage": "",
                    "process_binding": {
                        "kind": "process",
                        "min_positionals": 1,
                        "max_positionals": 1,
                    },
                },
            ),
        },
        source_interfaces={},
    )
    return {
        "consumer": syncer.ModuleBlueprint(
            "consumer",
            skill_dir / "blueprint.yaml",
            {},
            graph,
        )
    }


def test_run_sync_reports_usage_projection_error(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blueprints = _blueprints_with_unprojectable_usage(tmp_path, syncer)
    monkeypatch.setattr(syncer, "load_blueprints", lambda: blueprints)

    assert syncer.run_sync(check_only=True) == 1
    assert capsys.readouterr().err == (
        "error: provider.interface.run: usage cannot be projected "
        "unambiguously: positional labels\n"
    )


def test_validate_sync_state_returns_usage_projection_diagnostic(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blueprints = _blueprints_with_unprojectable_usage(tmp_path, syncer)
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
    ) == [
        "provider.interface.run: usage cannot be projected unambiguously: "
        "positional labels"
    ]


def test_validate_sync_state_does_not_swallow_unrelated_errors(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blueprints = _blueprints_with_unprojectable_usage(tmp_path, syncer)
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


def test_validate_sync_state_reuses_the_provided_graph(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_managed_skill(tmp_path)
    skills_root = tmp_path / "skills"
    monkeypatch.setattr(syncer, "SKILLS_ROOT", skills_root)
    graph = syncer.load_blueprints()["loose-mode"].repository_graph
    skill_path = skills_root / "loose-mode" / "SKILL.md"
    skill_path.write_text(
        syncer.sync_interface_block(
            skill_path.read_text(encoding="utf-8"),
            syncer.generated_interface_block("loose-mode", graph),
        ),
        encoding="utf-8",
    )
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

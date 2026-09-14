"""Scoped code checks retain context without inspecting unrelated subjects."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from officina.common.python_source_cache import PythonSourceCache
from validators import cross_platform, duplicate_subcommand_tokens, platform_neutral
from validators.skill import boundaries, blueprints, dependencies, dispatch_caller_module


@pytest.mark.parametrize("name, source", [
    ("cross_platform", "import os\nos.system('grep')\n"),
    ("platform_neutral", "value = 'Windows'\n"),
    ("boundaries", "value = 'skills/other-skill/_rtx/run.py'\n"),
    ("dispatch_caller_module", "from officina.dispatcher import dispatch\ndispatch(caller_skill='wrong')\n"),
])
def test_scoped_runtime_checks_read_only_selected_files(tmp_path, monkeypatch, name, source):
    relative = "skills/demo-skill/_rtx/selected.py"
    selected = tmp_path / relative
    selected.parent.mkdir(parents=True)
    selected.write_text(source, encoding="utf-8")
    selected.with_name("unrelated.py").write_text(source, encoding="utf-8")
    (selected.parent.parent / "blueprint.yaml").write_text("", encoding="utf-8")
    (tmp_path / "skills/other-skill").mkdir()
    owner = SimpleNamespace(node_type="module", node_id="demo-skill.inner", module_root=selected.parent)
    graph = SimpleNamespace(nodes={"demo-skill.inner": owner})
    monkeypatch.setattr(platform_neutral, "_git_ignored_paths", lambda _root: frozenset())
    read_text = Path.read_text

    def read_subject(path, *args, **kwargs):
        assert path == selected, f"unrelated file read: {path}"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_subject)
    monkeypatch.setattr(Path, "rglob", lambda *_args: pytest.fail("scoped scan traversed repository"))

    def run(paths):
        cache = PythonSourceCache(tmp_path)
        if name == "cross_platform":
            return cross_platform._validate(tmp_path, graph, cache, paths, ())
        if name == "platform_neutral":
            return platform_neutral._validate(tmp_path, frozenset(), paths)
        if name == "boundaries":
            return boundaries.validate(tmp_path, paths, SimpleNamespace(nodes={}))
        return dispatch_caller_module._validate(tmp_path, graph, cache, paths)

    findings = run((relative,))
    assert findings and all(relative in finding for finding in findings)
    if name == "dispatch_caller_module":
        assert any("demo-skill.inner" in finding for finding in findings)
    assert run(()) == []


def test_scoped_declaration_rules_keep_all_interfaces_of_selected_source(tmp_path):
    source = SimpleNamespace(
        node_type="behavioral_source", module_root=tmp_path / "skills/demo-skill",
        blueprint_path=tmp_path / "skills/demo-skill/blueprints/source.yaml",
        declaration={
            "runtime_dependencies": [{"kind": "binary", "name": "grep", "platforms": {
                "linux": True, "macos": True, "windows": True,
            }}],
            "interfaces": {name: {"process_binding": {"args_prefix": ["same"], "entry": name}}
                           for name in ("first", "second")},
        },
    )
    graph = SimpleNamespace(nodes={"selected": source, "unrelated": object()})
    assert cross_platform._validate_blueprints(graph, tmp_path, ("selected",))
    findings = duplicate_subcommand_tokens.validate_with_graph(tmp_path, graph, ("selected",))
    assert len(findings) == 1 and "first, second" in findings[0]
    assert cross_platform._validate_blueprints(graph, tmp_path, ()) == []
    assert duplicate_subcommand_tokens.validate_with_graph(tmp_path, graph, ()) == []


def test_scoped_gateway_guard_uses_graph_instead_of_reading_other_blueprints(tmp_path, monkeypatch):
    relative = "skills/demo-skill/_rtx/gateway.py"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("import sys\nsys.path.insert(0, 'other')\n", encoding="utf-8")
    graph = SimpleNamespace(nodes={"gateway": SimpleNamespace(
        gateway_path=path, declaration={"gateway": {"language": "Python"}},
    )})
    monkeypatch.setattr(boundaries, "_gateway_paths", lambda *_args: pytest.fail("unrelated YAML scan"))
    assert boundaries.validate_gateway_sys_path(tmp_path, graph, (relative,))
    assert boundaries.validate_gateway_sys_path(tmp_path, graph, ()) == []


def test_scoped_dependency_subject_retains_unselected_module_vocabulary(tmp_path, monkeypatch):
    nodes = {}
    for name in ("demo-skill", "other-skill"):
        root = tmp_path / "skills" / name
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text("Use other-skill.\n", encoding="utf-8")
        nodes[name] = SimpleNamespace(node_id=name, node_type="module", module_root=root)
    graph = SimpleNamespace(nodes=nodes, module_sources={}, module_ancestry={}, exports={}, node_edges=())
    read_text = Path.read_text

    def read_subject(path, *args, **kwargs):
        assert path == tmp_path / "skills/demo-skill/SKILL.md"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_subject)
    findings = dependencies._validate_graph(tmp_path, graph, (), ("demo-skill",))
    assert len(findings) == 1 and "other-skill" in findings[0]
    assert dependencies._validate_graph(tmp_path, graph, (), ()) == []


def test_blueprint_preflight_and_authored_checks_stay_on_selected_subject(tmp_path, monkeypatch):
    nodes = {}
    for name in ("demo-skill", "other-skill"):
        root = tmp_path / "skills" / name
        root.mkdir(parents=True)
        (root / "blueprint.yaml").write_text("", encoding="utf-8")
        (root / "SKILL.md").write_text(
            f"{blueprints.INTERFACES_START}\n{blueprints.INTERFACES_END}\n"
            if name == "demo-skill" else "broken markers\n", encoding="utf-8",
        )
        nodes[name] = SimpleNamespace(
            node_id=name, node_type="module", module_root=root,
            blueprint_path=root / "blueprint.yaml", declaration={},
        )
    template = tmp_path / "references/blueprint-schema/template.yaml"
    template.parent.mkdir(parents=True)
    template.write_text("", encoding="utf-8")
    graph = SimpleNamespace(nodes=nodes, schema_version=6)
    paths = ("skills/demo-skill/SKILL.md",)
    assert blueprints.preflight(tmp_path, prepared_graph=graph, validation_paths=paths,
                                validation_node_ids=("demo-skill",)) == ([], graph)
    assert blueprints.preflight(tmp_path, prepared_graph=graph)[0]
    template.unlink()
    assert blueprints.preflight(tmp_path, prepared_graph=graph, validation_paths=paths,
                                validation_node_ids=("demo-skill",)) == ([], graph)
    assert "missing blueprint template" in blueprints.preflight(
        tmp_path, prepared_graph=graph,
        validation_paths=("references/blueprint-schema/template.yaml",), validation_node_ids=(),
    )[0][0]
    tracked = {paths[0]: (("100644", "0"),), "skills/other-skill/_cx/bad": (("100644", "0"),)}
    monkeypatch.setattr(blueprints, "_git_tracked_files", lambda _root: tracked)

    def authored(node, _root):
        assert node.node_id == "demo-skill"
        return (tmp_path / paths[0],)

    monkeypatch.setattr(blueprints, "authored_node_input_paths", authored)
    synced = []
    syncer = SimpleNamespace(
        ModuleBlueprint=lambda name, *_args: name,
        sync_module=lambda module, **_kwargs: synced.append(module) or [],
        validate_sync_state=lambda **_kwargs: pytest.fail("unrelated aggregate sync"),
    )
    monkeypatch.setattr(blueprints, "_load_blueprint_syncer", lambda _root: syncer)
    assert blueprints.validate_with_graph(tmp_path, graph, paths, ("demo-skill",)) == []
    assert synced == ["demo-skill"]
    tracked[paths[0]] = (("120000", "0"),)
    assert blueprints.validate_with_graph(tmp_path, graph, paths, ("demo-skill",))

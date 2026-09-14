"""Scoped skill checks keep subjects separate from reference and collision context."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from validators.skill import dispatcher_usage, names, skill_body_execution, skill_metadata, skill_md_dispatch
from validators import skill_runtime_doc_references, skill_runtime_files


def _write(root, relative, text=""):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _graph(root):
    nodes = {name: SimpleNamespace(node_id=name, node_type="module", module_root=root / "skills" / name)
             for name in ("demo-skill", "other-skill")}
    nodes["demo-skill.source.runtime"] = SimpleNamespace(node_type="behavioral_source")
    return SimpleNamespace(nodes=nodes, exports={}, module_parents={}, direct_file_owners={})


def _forbid_read(monkeypatch, forbidden):
    read = Path.read_text

    def checked(path, *args, **kwargs):
        assert path != forbidden, "unrelated subject was read"
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", checked)


@pytest.mark.parametrize("validator", [names, skill_metadata, skill_body_execution])
def test_entry_checks_select_documents_or_modules_not_source_owners(tmp_path, monkeypatch, validator):
    text = "---\nname: wrong-name\n---\nRun _rtx/_calendar_gateway.py.\n"
    selected = "skills/demo-skill/SKILL.md"
    _write(tmp_path, selected, text)
    other = _write(tmp_path, "skills/other-skill/SKILL.md", text)
    _forbid_read(monkeypatch, other)
    graph = _graph(tmp_path)
    assert validator.validate(tmp_path, (selected,), (), graph)
    assert validator.validate(tmp_path, (), ("demo-skill",), graph)
    assert validator.validate(tmp_path, ("skills/demo-skill/_rtx/_calendar_gateway.py",),
                              ("demo-skill.source.runtime",), graph) == []


def test_selected_module_retains_missing_entrypoint_check(tmp_path):
    (tmp_path / "skills/demo-skill").mkdir(parents=True)
    assert names.validate(tmp_path, (), ("demo-skill",), _graph(tmp_path)) == [
        f"{tmp_path / 'skills/demo-skill'}: missing SKILL.md"
    ]


def test_missing_skills_root_cannot_drop_explicit_entry_subjects(tmp_path):
    graph = _graph(tmp_path)
    selected = ("skills/demo-skill/SKILL.md",)
    assert names.validate(tmp_path, selected, ("demo-skill",), graph) == [
        f"{tmp_path / 'skills/demo-skill'}: missing SKILL.md",
    ]
    with pytest.raises(FileNotFoundError):
        skill_metadata.validate(tmp_path, selected, ("demo-skill",), graph)
    with pytest.raises(FileNotFoundError):
        skill_runtime_doc_references.validate_with_graph(tmp_path, graph, selected, ("demo-skill",))


def test_empty_runtime_scope_never_queries_git_inventory(tmp_path, monkeypatch):
    def no_inventory(_root):
        pytest.fail("empty runtime scope must not request repository inventory")

    monkeypatch.setattr(skill_runtime_files, "git_ignored_paths", no_inventory)
    assert skill_runtime_files.validate_with_graph(tmp_path, _graph(tmp_path), ()) == []


def test_python_dispatcher_check_reads_only_selected_runtime_file(tmp_path, monkeypatch):
    selected = "skills/demo-skill/_rtx/_calendar_gateway.py"
    _write(tmp_path, "skills/demo-skill/blueprint.yaml")
    _write(tmp_path, selected, "import officina.dispatcher\n")
    other = _write(tmp_path, "skills/demo-skill/_rtx/_other_gateway.py", "import officina.dispatcher\n")
    _forbid_read(monkeypatch, other)
    findings = dispatcher_usage.validate(tmp_path, (selected,))
    assert len(findings) == 1 and selected in findings[0]
    assert dispatcher_usage.validate(tmp_path, ()) == []


def test_document_references_use_only_owning_runtime_inventory(tmp_path, monkeypatch):
    selected = "skills/demo-skill/SKILL.md"
    _write(tmp_path, selected, "Use _Calendar_Gateway.py.\n")
    _write(tmp_path, "skills/demo-skill/_rtx/_Calendar_Gateway.py")
    other = _write(tmp_path, "skills/other-skill/SKILL.md", "Use _Calendar_Gateway.py.\n")
    _forbid_read(monkeypatch, other)
    inventory = skill_runtime_doc_references._runtime_stems_for_skill
    calls = []

    def selected_inventory(skill_dir, graph):
        calls.append(skill_dir.name)
        assert skill_dir.name == "demo-skill"
        return inventory(skill_dir, graph)

    monkeypatch.setattr(skill_runtime_doc_references, "_runtime_stems_for_skill", selected_inventory)
    graph = _graph(tmp_path)
    findings = skill_runtime_doc_references.validate_with_graph(tmp_path, graph, (selected,), ())
    assert findings and all(selected in finding for finding in findings)
    assert calls == ["demo-skill"]
    assert skill_runtime_doc_references.validate_with_graph(
        tmp_path, graph, ("skills/demo-skill/_rtx/_Calendar_Gateway.py",), (),
    ) == []
    assert calls == ["demo-skill"]


def test_generated_dispatch_check_keeps_exports_but_not_other_documents(tmp_path, monkeypatch):
    selected = "skills/demo-skill/SKILL.md"
    _write(tmp_path, selected, "No generated block.\n")
    other = _write(tmp_path, "skills/demo-skill/other.md", "dispatcher invoke --caller-skill demo\n")
    _forbid_read(monkeypatch, other)
    graph = _graph(tmp_path)
    graph.exports = {"demo-skill.interface.run": SimpleNamespace(
        module_node_id="demo-skill", declaration={"process_binding": {}},
    )}
    findings = skill_md_dispatch.validate_with_graph(tmp_path, graph, (selected,), ())
    assert len(findings) == 1 and "missing generated blueprint interface block" in findings[0]
    assert skill_md_dispatch.validate_with_graph(tmp_path, graph, (), ("demo-skill.source.runtime",)) == []


@pytest.mark.parametrize(("selected", "peer"), [
    ("_Calendar_Gateway.py", "_calendar_gateway.py"),
    ("_Install_Launcher/_linux_launcher.py", "_install_launcher/_osx_launcher.py"),
])
def test_runtime_layout_checks_selected_names_and_case_collision_peers(tmp_path, monkeypatch, selected, peer):
    prefix = "skills/demo-skill/_rtx/"
    target = _write(tmp_path, prefix + selected)
    neighbor = _write(tmp_path, prefix + peer)
    if (tmp_path / prefix / Path(selected).parts[0]).samefile(tmp_path / prefix / Path(peer).parts[0]):
        # famulus-skip: category=capability-unavailable; reason=filesystem aliases case-only names; alternate=case-sensitive hosts run both collision cases
        pytest.skip("filesystem cannot represent distinct case-colliding paths")
    _write(tmp_path, prefix + "bad_sibling.py")
    _write(tmp_path, "skills/other-skill/_rtx/bad_runtime.py")
    findings = skill_runtime_files.validate_with_graph(tmp_path, _graph(tmp_path), (prefix + selected,))
    assert len(findings) == 1 and "case-insensitive runtime path collision" in findings[0]
    assert "bad_sibling" not in findings[0] and "other-skill" not in findings[0]
    monkeypatch.setattr(skill_runtime_files, "git_ignored_paths", lambda _root: {Path(prefix + peer)})
    assert skill_runtime_files.validate_with_graph(tmp_path, _graph(tmp_path), (prefix + selected,)) == []
    findings = skill_runtime_files.validate_with_graph(tmp_path, _graph(tmp_path), (prefix + "bad_sibling.py",))
    assert len(findings) == 1 and "filename stem" in findings[0]

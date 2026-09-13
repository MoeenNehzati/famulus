"""Tests for validators/skill_runtime_doc_references.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import yaml

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "skill_runtime_doc_references.py"
_spec = importlib.util.spec_from_file_location("skill_runtime_doc_references", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _skill(repo_root: Path, name: str = "demo-skill") -> Path:
    skill = repo_root / "skills" / name
    (skill / "_rtx").mkdir(parents=True)
    (skill / "_rtx" / "_Calendar_Gateway.py").write_text(
        "# runtime\n", encoding="utf-8"
    )
    return skill


def _source_export(skill_name: str = "demo-skill") -> dict[str, object]:
    return {
        "source_interface": f"{skill_name}.source.gateway.interface.calendar-gateway",
        "access": {"allow_all_modules": True, "allowed_callers": []},
    }


def _write_module_blueprint(
    skill: Path,
    export_id: str,
    export_declaration: object,
    *,
    schema_version: int = 6,
) -> None:
    document = {
        "schema_version": schema_version,
        "node_type": "module",
        "id": skill.name,
        "version": 1,
        "maturity": "stable",
        "gateway": {"language": "Markdown", "path": "SKILL.md"},
        "content": [r"SKILL\.md"],
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {},
        "namespace_exports": {},
        "exports": {export_id: export_declaration},
    }
    (skill / "blueprint.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )


def test_real_schema_public_interface_masking_is_exact(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    interface_id = "demo-skill._rtx.interface.calendar-gateway"
    _write_module_blueprint(skill, interface_id, _source_export())
    (skill / "SKILL.md").write_text(
        f"Use `{interface_id}@1`, not `calendar-gateway`.\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == [
        "skills/demo-skill/SKILL.md:1: skill-facing Markdown must not mention "
        "private runtime name `_Calendar_Gateway`"
    ]


def test_invalid_blueprint_fails_closed_and_interface_ids_are_canonical(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path)
    interface_id = "demo-skill.interface.calendar-gateway"
    _write_module_blueprint(
        skill,
        interface_id,
        {},
    )
    (skill / "SKILL.md").write_text(f"Use `{interface_id}`.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == [
        "skills/demo-skill/SKILL.md:1: skill-facing Markdown must not mention "
        "private runtime name `_Calendar_Gateway`"
    ]
    assert _mod._is_same_skill_public_interface("demo-skill", interface_id)
    assert _mod._is_same_skill_public_interface(
        "demo-skill", "demo-skill._rtx.interface.calendar-gateway"
    )
    assert not _mod._is_same_skill_public_interface("demo-skill", "calendar-gateway")
    assert not _mod._is_same_skill_public_interface(
        "demo-skill", "other-skill.interface.calendar-gateway"
    )
    assert not _mod._is_same_skill_public_interface("demo-skill", None)


def test_graph_exports_are_indexed_once_for_all_skills(tmp_path: Path) -> None:
    class CountingExports(dict):
        item_calls = 0

        def items(self):
            self.item_calls += 1
            return super().items()

    exports = CountingExports()
    for skill_name in ("alpha-skill", "beta-skill"):
        skill = _skill(tmp_path, skill_name)
        interface_id = f"{skill_name}.interface.calendar-gateway"
        (skill / "SKILL.md").write_text(f"Use `{interface_id}`.\n", encoding="utf-8")
        exports[interface_id] = SimpleNamespace(module_node_id=skill_name)
    graph = SimpleNamespace(exports=exports, module_parents={}, nodes={})

    assert _mod.validate_with_graph(tmp_path, graph) == []
    assert exports.item_calls == 1


def test_runtime_patterns_are_prepared_once_per_stem(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = _skill(tmp_path)
    (skill / "_rtx" / "_Other_Helper.py").write_text("", encoding="utf-8")
    (skill / "SKILL.md").write_text("first\nsecond\n", encoding="utf-8")
    (skill / "README.md").write_text("third\nfourth\n", encoding="utf-8")
    original_stem_patterns = _mod._stem_patterns
    original_suffix_patterns = _mod._suffix_patterns_for_stem
    stem_calls: list[str] = []
    suffix_calls: list[str] = []

    def counted_stem_patterns(stem: str):
        stem_calls.append(stem)
        return original_stem_patterns(stem)

    def counted_suffix_patterns(stem: str):
        suffix_calls.append(stem)
        return original_suffix_patterns(stem)

    monkeypatch.setattr(_mod, "_stem_patterns", counted_stem_patterns)
    monkeypatch.setattr(_mod, "_suffix_patterns_for_stem", counted_suffix_patterns)

    assert _mod.validate(tmp_path) == []
    assert stem_calls == ["_Calendar_Gateway", "_Other_Helper"]
    assert suffix_calls == ["_Calendar_Gateway", "_Other_Helper"]
    casefold_patterns = _mod._combined_patterns_for_stems(
        ["_Alpha_Beta", "_alpha_beta"]
    )
    assert _mod._suffix_findings(
        casefold_patterns, "Alpha_Beta.py _alpha_beta.py"
    ) == [
        ("_Alpha_Beta", "_alpha_beta.py"),
        ("_alpha_beta", "_alpha_beta.py"),
    ]


def test_runtime_findings_keep_pass_stem_spelling_and_line_order(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path)
    (skill / "_rtx" / "_Other_Helper.py").write_text("", encoding="utf-8")
    (skill / "README.md").write_text(
        "_rtx scripts/legacy.py Calendar_Gateway.py _Calendar_Gateway.py "
        "_Other_Helper.py other helper calendar gateway\n"
        "Other_Helper.py other helper\n"
        "Calendar_Gateway\n"
        "calendar gateway\n"
        "calendar-gateway\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == [
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention `_rtx`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention old runtime path `scripts/legacy.py`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention runtime file `_Calendar_Gateway.py`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention runtime file `_Other_Helper.py`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention private runtime name `_Calendar_Gateway`",
        "skills/demo-skill/README.md:2: skill-facing Markdown must not mention runtime file `Other_Helper.py`",
        "skills/demo-skill/README.md:2: skill-facing Markdown must not mention private runtime name `_Other_Helper`",
        "skills/demo-skill/README.md:3: skill-facing Markdown must not mention private runtime name `_Calendar_Gateway`",
        "skills/demo-skill/README.md:4: skill-facing Markdown must not mention private runtime name `_Calendar_Gateway`",
        "skills/demo-skill/README.md:5: skill-facing Markdown must not mention private runtime name `_Calendar_Gateway`",
    ]


def test_nested_runtime_package_name_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    package = skill / "_rtx" / "_install_launcher"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_windows_launcher.py").write_text("# runtime\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("Use install launcher internals.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_install_launcher`" in error for error in errors)


def test_assets_and_generated_build_markdown_are_exempt(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "assets").mkdir()
    (skill / "assets" / "README.md").write_text(
        "Run _Calendar_Gateway.py.\n", encoding="utf-8"
    )
    (skill / "_build").mkdir()
    (skill / "_build" / "report.md").write_text(
        "Run _rtx and _Calendar_Gateway.py.\n", encoding="utf-8"
    )

    assert _mod.validate(tmp_path) == []


def test_registered_child_artifacts_and_legacy_graph_fallback(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    child = skill / "_rtx"
    (child / "assets").mkdir(parents=True)
    (child / "assets" / "_artifact_name.py").write_text("", encoding="utf-8")
    (child / "tests").mkdir()
    (child / "tests" / "_test_helper.py").write_text("", encoding="utf-8")
    (child / "_Child_Gateway.py").write_text("", encoding="utf-8")
    (child / "blueprint.yaml").write_text("", encoding="utf-8")
    (child / "README.md").write_text("Child Gateway is private.\n", encoding="utf-8")
    interface_id = "demo-skill.interface.child-gateway"
    _write_module_blueprint(skill, interface_id, _source_export())
    (skill / "SKILL.md").write_text(
        f"Use `{interface_id}`. Artifact Name and Test Helper are ordinary prose; "
        "Child Gateway is private.\n",
        encoding="utf-8",
    )
    graph = SimpleNamespace(
        module_parents={"demo-rtx": "demo-skill"},
        nodes={"demo-rtx": SimpleNamespace(module_root=child)},
    )

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert not any("_artifact_name" in error for error in errors)
    assert not any("_test_helper" in error for error in errors)
    assert errors == [
        "skills/demo-skill/SKILL.md:1: skill-facing Markdown must not mention "
        "private runtime name `_Child_Gateway`"
    ]

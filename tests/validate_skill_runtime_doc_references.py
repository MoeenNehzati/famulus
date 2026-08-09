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


def _skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "_rtx" / "_Calendar_Gateway.py").write_text("# runtime\n", encoding="utf-8")
    return skill


def _source_export() -> dict[str, object]:
    return {
        "source_interface": "demo-skill.source.gateway.interface.calendar-gateway",
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
        "id": "demo-skill",
        "version": 1,
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


def test_public_interface_name_passes(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Use the `read-calendar` interface.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == []


def test_declared_public_interface_id_may_match_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(
        skill,
        "demo-skill.interface.calendar-gateway",
        _source_export(),
    )
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`.\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == []


def test_empty_export_declaration_cannot_mask_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(
        skill,
        "demo-skill.interface.calendar-gateway",
        {},
    )
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`.\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_malformed_export_declaration_cannot_mask_private_runtime_stem(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path)
    malformed = _source_export()
    malformed.pop("access")
    _write_module_blueprint(
        skill,
        "demo-skill.interface.calendar-gateway",
        malformed,
    )
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`.\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_noncanonical_export_cannot_mask_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(skill, "calendar-gateway", _source_export())
    (skill / "SKILL.md").write_text("Use `calendar-gateway`.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_malformed_module_blueprint_cannot_mask_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(
        skill,
        "demo-skill.interface.calendar-gateway",
        _source_export(),
        schema_version=4,
    )
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`.\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_public_id_does_not_mask_adjacent_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(
        skill,
        "demo-skill.interface.calendar-gateway",
        _source_export(),
    )
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`, not `calendar-gateway`.\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


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


def test_runtime_findings_keep_pass_and_stem_order(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "_rtx" / "_Other_Helper.py").write_text("", encoding="utf-8")
    (skill / "README.md").write_text(
        "_rtx scripts/legacy.py _Calendar_Gateway.py _Other_Helper.py "
        "calendar gateway other helper\nother helper\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == [
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention `_rtx`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention old runtime path `scripts/legacy.py`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention runtime file `_Calendar_Gateway.py`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention runtime file `_Other_Helper.py`",
        "skills/demo-skill/README.md:1: skill-facing Markdown must not mention private runtime name `_Calendar_Gateway`",
        "skills/demo-skill/README.md:2: skill-facing Markdown must not mention private runtime name `_Other_Helper`",
    ]


def test_private_runtime_directory_name_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run _rtx directly.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention `_rtx`" in error for error in errors)


def test_dotted_child_interface_id_is_not_a_runtime_path_reference(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text(
        "Use `demo-skill._rtx.interface.read-calendar@1`.\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == []


def test_suffix_qualified_runtime_file_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run _Calendar_Gateway.py.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention runtime file" in error for error in errors)


def test_private_stem_with_underscore_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run Calendar_Gateway.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_private_stem_as_words_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run the calendar gateway.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_private_stem_with_hyphen_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run calendar-gateway.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_nested_runtime_package_name_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    package = skill / "_rtx" / "_install_launcher"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_windows_launcher.py").write_text("# runtime\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("Use install launcher internals.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_install_launcher`" in error for error in errors)


def test_assets_markdown_is_exempt(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "assets" / "README.md").parent.mkdir()
    (skill / "assets" / "README.md").write_text("Run install.sh.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == []


def test_registered_child_artifacts_are_not_runtime_names_but_executable_is(
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
    (child / "README.md").write_text(
        "Child Gateway is private.\n", encoding="utf-8"
    )
    (skill / "SKILL.md").write_text(
        "Artifact Name and Test Helper are ordinary prose; "
        "Child Gateway is private.\n",
        encoding="utf-8",
    )
    graph = SimpleNamespace(
        module_parents={"demo-rtx": "demo-skill"},
        nodes={
            "demo-rtx": SimpleNamespace(module_root=child),
        },
    )

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert not any("_artifact_name" in error for error in errors)
    assert not any("_test_helper" in error for error in errors)
    assert any("_Child_Gateway" in error for error in errors)
    assert not any("README.md" in error for error in errors)

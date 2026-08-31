from pathlib import Path

from validators import platform_neutral as module_under_test
from validators.platform_neutral import _validate


def test_platform_name_in_generic_reference_prose_remains_rejected(tmp_path: Path) -> None:
    prose = tmp_path / "references" / "generic.md"
    prose.parent.mkdir(parents=True)
    prose.write_text("Only Linux is supported.\n", encoding="utf-8")

    assert _validate(tmp_path, frozenset()) == [
        "references/generic.md:1: Only Linux is supported."
    ]


def test_relocation_engine_is_the_exact_host_projection_boundary(
    tmp_path: Path,
) -> None:
    engine = (
        tmp_path
        / "skills"
        / "relocate-nodes"
        / "_rtx"
        / "_relocation_engine.py"
    )
    adjacent = engine.with_name("_relocation_closure.py")
    engine.parent.mkdir(parents=True)
    content = 'EXCLUSIONS = (".claude", ".codex")\n'
    engine.write_text(content, encoding="utf-8")
    adjacent.write_text(content, encoding="utf-8")

    assert _validate(tmp_path, frozenset()) == [
        'skills/relocate-nodes/_rtx/_relocation_closure.py:1: '
        'EXCLUSIONS = (".claude", ".codex")'
    ]


def test_metadata_checks_only_eligible_files_and_restrict_schema_blueprints(
    tmp_path: Path,
    monkeypatch,
) -> None:
    blueprint = tmp_path / "skills" / "demo" / "blueprint.yaml"
    blueprint.parent.mkdir(parents=True)
    blueprint.write_text(
        "platform_support:\n"
        "  linux: true\n"
        "  macos: false\n"
        "  windows: false\n"
        "description: Use Claude from prose.\n",
        encoding="utf-8",
    )
    ordinary = tmp_path / "references" / "ordinary.md"
    ordinary.parent.mkdir()
    ordinary.write_text(
        "A neutral line.\nUse Codex here.\nOnly Windows works.\n",
        encoding="utf-8",
    )
    schema_blueprint = (
        tmp_path / "references" / "blueprint-schema" / "blueprint.yaml"
    )
    schema_blueprint.parent.mkdir()
    schema_blueprint.write_text(
        "linux: true\n"
        "description: Only Linux is supported.\n",
        encoding="utf-8",
    )

    original_check = module_under_test._is_allowed_platform_metadata_line
    checked_paths: list[Path] = []

    def counting_check(rel_path: Path, line: str, *args, **kwargs) -> bool:
        checked_paths.append(rel_path)
        return original_check(rel_path, line, *args, **kwargs)

    monkeypatch.setattr(
        module_under_test,
        "_is_allowed_platform_metadata_line",
        counting_check,
    )

    assert _validate(tmp_path, frozenset()) == [
        "skills/demo/blueprint.yaml:5: description: Use Claude from prose.",
        "references/ordinary.md:2: Use Codex here.",
        "references/ordinary.md:3: Only Windows works.",
    ]
    assert set(checked_paths) == {
        Path("skills/demo/blueprint.yaml"),
        Path("references/blueprint-schema/blueprint.yaml"),
    }
    assert checked_paths.count(Path("skills/demo/blueprint.yaml")) == 5
    assert (
        checked_paths.count(Path("references/blueprint-schema/blueprint.yaml"))
        == 2
    )

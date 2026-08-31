from pathlib import Path

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

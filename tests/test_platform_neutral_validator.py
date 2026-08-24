from pathlib import Path

from validators.platform_neutral import _validate


def test_generated_runtime_lock_allows_pep508_platform_markers(tmp_path: Path) -> None:
    lock = tmp_path / "references" / "runtime" / "requirements-core.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        "package==1.0 ; sys_platform == 'linux' "
        f"--hash=sha256:{'a' * 64}\n",
        encoding="utf-8",
    )

    assert _validate(tmp_path, frozenset()) == []


def test_runtime_lock_generator_may_define_structured_platform_markers(tmp_path: Path) -> None:
    source = tmp_path / "src" / "officina" / "install" / "runtime_lock.py"
    source.parent.mkdir(parents=True)
    source.write_text("MARKER = \"sys_platform == 'win32'\"\n", encoding="utf-8")

    assert _validate(tmp_path, frozenset()) == []


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

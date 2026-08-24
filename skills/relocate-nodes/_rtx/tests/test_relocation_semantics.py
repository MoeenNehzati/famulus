"""Behavioral tests for bounded semantic relocation discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from .._relocation_engine import ChangeSet, DerivedIdentityMap, RelocationError, Rename
from .._relocation_semantics import SemanticScan, logical_fragment_mappings


def _write(path: Path, payload: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.encode("utf-8") if isinstance(payload, str) else payload)


def test_logical_fragments_are_segment_aware_and_longest_first() -> None:
    """Character-prefix stripping or shortest-first ordering breaks this mapping."""

    nested = DerivedIdentityMap(
        "skills/a/b/c",
        "skills/a/d/e",
        source_node_id="a.b.c",
        target_node_id="a.d.e",
    )
    same_parent = DerivedIdentityMap(
        "skills/a/b/c",
        "skills/a/b/e",
        source_node_id="a.b.c",
        target_node_id="a.b.e",
    )
    top_level = DerivedIdentityMap(
        "skills/g-calendar",
        "skills/online-calendar",
        source_node_id="g-calendar",
        target_node_id="online-calendar",
    )

    assert [(item.old, item.new) for item in logical_fragment_mappings(nested)] == [
        ("b.c", "d.e"),
        ("b/c", "d/e"),
        ("b", "d"),
    ]
    assert [(item.old, item.new) for item in logical_fragment_mappings(same_parent)] == [
        ("c", "e")
    ]
    assert [(item.old, item.new) for item in logical_fragment_mappings(top_level)] == [
        ("g-calendar", "online-calendar")
    ]


def test_semantic_scan_covers_all_text_and_only_exact_default_exclusions(
    tmp_path: Path,
) -> None:
    """Suffix filters and mechanical cache exclusions must not shrink review scope."""

    mapping = DerivedIdentityMap(
        "skills/a/b/c",
        "skills/a/d/e",
        source_node_id="a.b.c",
        target_node_id="a.d.e",
        python_modules=(Rename("old_pkg.api", "new_pkg.api"),),
    )
    fixtures = {
        "code.py": "é b.c and old_pkg.api\r\n",
        "proof.tex": "b/c\n",
        "README.md": "about b bystander x.b.c b.c.more /x/b/c/y b.c\n",
        "data.yaml": "value: b.c\n",
        "data.json": '{"value":"b.c"}\n',
        "run.sh": "echo b.c\n",
        "NOTICE": "b.c\n",
        ".agents/notes": "b.c\n",
        ".worktrees/cache/notes": "b.c\n",
        "node_modules/pkg/notes": "b.c\n",
        ".git/ignored": "b.c\n",
        ".claude/ignored": "b.c\n",
        ".codex/ignored": "b.c\n",
        ".superpowers/ignored": "b.c\n",
    }
    for relative, text in fixtures.items():
        _write(tmp_path / relative, text)
    _write(tmp_path / "binary.bin", b"b.c\x00rest")
    _write(tmp_path / "nonutf8", b"b.c\xff")
    (tmp_path / "internal-link").symlink_to("README.md")
    (tmp_path / "dangling-link").symlink_to("missing")
    (tmp_path / "escaping-link").symlink_to("../outside")
    changes = ChangeSet(
        tmp_path,
        inventory_exclusions=(".git", ".claude", ".codex", ".superpowers"),
        derived_relocations=(mapping,),
    )

    result = SemanticScan(changes).run()

    paths = [item.path for item in result.occurrences]
    assert ".agents/notes" in paths
    assert ".worktrees/cache/notes" in paths
    assert "node_modules/pkg/notes" in paths
    assert not any(path.startswith((".git/", ".claude/", ".codex/", ".superpowers/")) for path in paths)
    readme = [item for item in result.occurrences if item.path == "README.md"]
    assert [item.match for item in readme] == ["b", "b.c"]
    code = next(item for item in result.occurrences if item.path == "code.py" and item.match == "b.c")
    assert (code.line, code.column) == (1, 3)
    assert changes.read_bytes("code.py")[code.byte_start:code.byte_end] == b"b.c"
    assert code.occurrence_id.startswith("sha256:")
    assert code.projected_digest.startswith("sha256:")
    skipped = {item.path for item in result.skipped_text_files}
    assert {"binary.bin", "nonutf8", "internal-link", "dangling-link", "escaping-link"} <= skipped


def test_physical_baseline_is_distinct_from_projected_semantic_inventory(
    tmp_path: Path,
) -> None:
    """Projection cannot replace the raw preflight concurrency baseline."""

    _write(tmp_path / "source.txt", "source\n")
    _write(tmp_path / "binary.bin", b"\xff\x00")
    (tmp_path / "source-link").symlink_to("source.txt")
    _write(tmp_path / ".scratch/ignored.txt", "ignored\n")
    changes = ChangeSet(tmp_path, inventory_exclusions=(".scratch",))
    changes.write_text("target.txt", "source\n")
    changes.deletes.add("source.txt")

    baseline = {entry.path: entry for entry in changes.physical_baseline}

    assert set(baseline) == {
        ".scratch",
        "binary.bin",
        "source-link",
        "source.txt",
    }
    assert baseline[".scratch"].kind == "directory"
    assert baseline["binary.bin"].kind == "regular"
    assert baseline["binary.bin"].digest.startswith("sha256:")
    assert baseline["source-link"].kind == "symlink"
    assert baseline["source-link"].digest.startswith("sha256:")
    assert "target.txt" in changes.projected_files()
    assert "source.txt" not in changes.projected_files()


def test_semantic_scan_fails_closed_on_conflicting_same_span_candidates(
    tmp_path: Path,
) -> None:
    """Two relocations cannot silently choose different replacements for one span."""

    _write(tmp_path / "notes.md", "b.c\n")
    changes = ChangeSet(
        tmp_path,
        derived_relocations=(
            DerivedIdentityMap(
                "skills/a/b/c",
                "skills/a/d/e",
                source_node_id="a.b.c",
                target_node_id="a.d.e",
            ),
            DerivedIdentityMap(
                "skills/x/b/c",
                "skills/x/y/z",
                source_node_id="x.b.c",
                target_node_id="x.y.z",
            ),
        ),
    )

    with pytest.raises(RelocationError, match="conflicting semantic candidates"):
        SemanticScan(changes).run()

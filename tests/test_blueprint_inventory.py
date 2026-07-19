from __future__ import annotations

from pathlib import Path

import pytest

from officina.common.blueprint_inventory import (
    BlueprintInventoryError,
    collect_blueprints,
    iter_blueprints,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_inventory_discovers_roots_and_hidden_sidecars_in_lexical_order(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "skills" / "zeta" / "blueprint.yaml", "schema_version: 3\nnode_type: skill\nid: zeta\n")
    _write(tmp_path / "skills" / "alpha" / "blueprint.yaml", "schema_version: 3\nnode_type: skill\nid: alpha\n")
    _write(tmp_path / "skills" / "alpha" / "_rtx" / "._worker.py.blueprint.yaml", "schema_version: 3\nnode_type: machine-module\nid: alpha.machine-module.worker\n")
    _write(tmp_path / "references" / ".policy.md.blueprint.yaml", "schema_version: 3\nnode_type: behavior-source\nid: references.source.policy\n")

    documents = tuple(iter_blueprints(tmp_path))

    assert [item.relative_path.as_posix() for item in documents] == sorted(
        item.relative_path.as_posix() for item in documents
    )
    module = next(item for item in documents if item.node_type == "machine-module")
    assert module.owner_root == tmp_path / "skills" / "alpha"
    repository_source = next(item for item in documents if item.node_id == "references.source.policy")
    assert repository_source.owner_root == tmp_path


@pytest.mark.parametrize(
    ("text", "diagnostic"),
    [
        ("id: one\nid: two\n", "duplicate key"),
        ("id: !custom value\n", "could not determine a constructor"),
        ("? [not, a, string]\n: value\n", "mapping key must be a string"),
        ("- not\n- a\n- mapping\n", "document root must be a mapping"),
        ("created: 2026-07-19\n", "non-JSON value"),
        ("value: .nan\n", "non-JSON number"),
    ],
)
def test_strict_inventory_aggregates_parse_and_normalization_failures(
    tmp_path: Path, text: str, diagnostic: str
) -> None:
    _write(tmp_path / "skills" / "broken" / "blueprint.yaml", text)

    with pytest.raises(BlueprintInventoryError, match=diagnostic):
        tuple(iter_blueprints(tmp_path))


def test_diagnostic_collection_returns_valid_documents_and_all_issues(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "skills" / "valid" / "blueprint.yaml", "schema_version: 3\nnode_type: skill\nid: valid\n")
    _write(tmp_path / "skills" / "a" / "blueprint.yaml", "id: one\nid: two\n")
    _write(tmp_path / "skills" / "b" / "blueprint.yaml", "- invalid\n")

    result = collect_blueprints(tmp_path, skip_parse_errors=True)

    assert [item.node_id for item in result.documents] == ["valid"]
    assert len(result.issues) == 2
    assert [issue.relative_path.as_posix() for issue in result.issues] == sorted(
        issue.relative_path.as_posix() for issue in result.issues
    )


def test_inventory_ignores_nonhidden_sidecars_and_symlinks(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "valid"
    _write(skill / "blueprint.yaml", "id: valid\n")
    _write(skill / "plain.blueprint.yaml", "id: not-a-sidecar\n")
    outside = tmp_path / "outside"
    _write(outside / ".escaped.blueprint.yaml", "id: escaped\n")
    (skill / "linked").symlink_to(outside, target_is_directory=True)
    (skill / ".linked.blueprint.yaml").symlink_to(outside / ".escaped.blueprint.yaml")

    documents = tuple(iter_blueprints(tmp_path))

    assert [document.node_id for document in documents] == ["valid"]

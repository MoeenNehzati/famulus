"""Route-local tests for the v6 direct blueprint repository."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from officina.configuration.repository import RepositoryConfiguration
from officina.dispatcher.direct_blueprints import (
    DirectBlueprintError,
    DirectBlueprintRepository,
    parse_interface_id,
)


def _configuration(tmp_path: Path, *root_names: str) -> RepositoryConfiguration:
    roots = tuple(tmp_path / name for name in root_names)
    for root in roots:
        root.mkdir()
    return RepositoryConfiguration(1, tmp_path / "officina.toml", tmp_path, roots)


def _module_document(module_id: str, *, children: tuple[str, ...] = ()) -> str:
    child_lines = "{}" if not children else "\n" + "\n".join(
        f"  {child}: {{}}" for child in children
    )
    return f"""schema_version: 6
node_type: module
id: {module_id}
version: 1
gateway: {{path: __init__.py, language: Python}}
content: [__init__\\.py]
authority: {{owns_filesystem: []}}
sources: {{}}
children: {child_lines}
namespace_exports: {{}}
exports: {{}}
"""


def _write_module(root: Path, module_id: str, *, children: tuple[str, ...] = ()) -> Path:
    directory = root.joinpath(*module_id.split("."))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "blueprint.yaml"
    path.write_text(_module_document(module_id, children=children), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("interface_id", "expected"),
    [
        ("list-manager.interface.read-list", ("list-manager", "read-list")),
        ("list-manager._rtx.interface.read-list", ("list-manager._rtx", "read-list")),
    ],
)
def test_parse_interface_id(interface_id: str, expected: tuple[str, str]) -> None:
    assert parse_interface_id(interface_id) == expected


@pytest.mark.parametrize(
    "interface_id",
    ["missing", "root.interface.", ".interface.read", "root.interface.read.extra", "root.source.x.interface.read"],
)
def test_parse_interface_id_rejects_noncanonical_values(interface_id: str) -> None:
    with pytest.raises(DirectBlueprintError) as caught:
        parse_interface_id(interface_id)
    assert caught.value.code == "dispatcher.invalid_interface_id"


def test_load_ancestry_uses_dotted_paths_and_registration(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, "skills", "officina")
    root = configuration.module_roots[0]
    _write_module(root, "root", children=("alpha",))
    _write_module(root, "root.alpha", children=("leaf",))
    leaf_path = _write_module(root, "root.alpha.leaf")

    ancestry = DirectBlueprintRepository(configuration).load_ancestry("root.alpha.leaf")

    assert [module.module_id for module in ancestry] == ["root", "root.alpha", "root.alpha.leaf"]
    assert ancestry[-1].blueprint_path == leaf_path
    assert ancestry[-1].root == root


def test_top_level_collision_is_rejected(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, "skills", "officina")
    for root in configuration.module_roots:
        _write_module(root, "duplicate")

    with pytest.raises(DirectBlueprintError) as caught:
        DirectBlueprintRepository(configuration).load_module("duplicate")
    assert caught.value.code == "dispatcher.module_ambiguous"


def test_missing_child_registration_fails_before_child_load(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, "skills")
    root = configuration.module_roots[0]
    _write_module(root, "root")
    child_path = _write_module(root, "root.child")
    child_path.write_text("not: valid for dispatch", encoding="utf-8")

    with pytest.raises(DirectBlueprintError) as caught:
        DirectBlueprintRepository(configuration).load_module("root.child")
    assert caught.value.code == "dispatcher.child_unregistered"


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ("schema_version: 5\nnode_type: module\nid: root\n", "dispatcher.blueprint_schema_mismatch"),
        (_module_document("other"), "dispatcher.blueprint_identity_mismatch"),
        ("[unterminated", "dispatcher.blueprint_malformed"),
    ],
)
def test_relevant_blueprint_failures_have_stable_codes(
    tmp_path: Path,
    replacement: str,
    code: str,
) -> None:
    configuration = _configuration(tmp_path, "skills")
    path = _write_module(configuration.module_roots[0], "root")
    path.write_text(replacement, encoding="utf-8")

    with pytest.raises(DirectBlueprintError) as caught:
        DirectBlueprintRepository(configuration).load_module("root")
    assert caught.value.code == code


@pytest.mark.parametrize("invalid_version", ["true", "0", "-1"])
def test_module_version_must_be_a_positive_integer(
    tmp_path: Path,
    invalid_version: str,
) -> None:
    configuration = _configuration(tmp_path, "skills")
    path = _write_module(configuration.module_roots[0], "root")
    path.write_text(
        _module_document("root").replace("version: 1", f"version: {invalid_version}")
    )

    with pytest.raises(DirectBlueprintError) as caught:
        DirectBlueprintRepository(configuration).load_module("root")

    assert caught.value.code == "dispatcher.blueprint_malformed"


def test_unrelated_malformed_blueprint_is_never_read(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, "skills")
    root = configuration.module_roots[0]
    expected = _write_module(root, "good")
    bad = root / "bad" / "blueprint.yaml"
    bad.parent.mkdir()
    bad.write_text("[unterminated", encoding="utf-8")

    loaded = DirectBlueprintRepository(configuration).load_module("good")

    assert loaded.blueprint_path == expected


def test_direct_lookup_uses_no_enumeration_subprocess_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _configuration(tmp_path, "skills")
    _write_module(configuration.module_roots[0], "root")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden operation reached")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(os, "walk", forbidden)

    assert DirectBlueprintRepository(configuration).load_module("root").module_id == "root"


def test_symlinked_module_path_is_rejected(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, "skills")
    root = configuration.module_roots[0]
    outside = tmp_path / "outside"
    _write_module(outside, "child")
    (root / "child").symlink_to(outside / "child", target_is_directory=True)

    with pytest.raises(DirectBlueprintError) as caught:
        DirectBlueprintRepository(configuration).load_module("child")
    assert caught.value.code == "dispatcher.unsafe_blueprint_path"

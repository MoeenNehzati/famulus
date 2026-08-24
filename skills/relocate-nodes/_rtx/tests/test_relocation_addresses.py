"""Behavioral tests for configured-root relocation addresses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from .._relocation_addresses import (
    AddressResolutionError,
    DerivedRelocation,
    NodeAddress,
    derive_relocations,
)
from .._relocation_engine import Move


def _repository(tmp_path: Path, roots: tuple[str, ...] = ("packages", "lib/officina")) -> Path:
    """Create one confined repository with the selected configured roots."""

    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    for root in roots:
        (repository / root).mkdir(parents=True)
    rendered_roots = ", ".join(f'"{root}"' for root in roots)
    (repository / "officina.toml").write_text(
        "schema_version = 1\n\n[modules]\n"
        f"roots = [{rendered_roots}]\n",
        encoding="utf-8",
    )
    return repository


def _blueprint(path: Path, node_id: str, children: dict[str, object] | None = None) -> None:
    """Write the schema-v6 facts address validation needs from one node."""

    path.mkdir(parents=True, exist_ok=True)
    (path / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": node_id,
                "node_type": "module",
                "children": {} if children is None else children,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _registered_node(repository: Path, relative: str) -> None:
    """Register every parent-child edge leading to one configured-root node."""

    root = ("lib", "officina") if relative.startswith("lib/officina/") else ("packages",)
    parts = tuple(relative.split("/"))
    suffix = parts[len(root) :]
    for count in range(1, len(suffix) + 1):
        node_path = repository.joinpath(*root, *suffix[:count])
        node_id = ".".join(suffix[:count])
        children = {suffix[count]: {}} if count < len(suffix) else {}
        _blueprint(node_path, node_id, children)


def _address(node_id: str, root: str, path: str) -> NodeAddress:
    return NodeAddress(
        node_id=node_id,
        configured_root=root,
        repository_path=path,
    )


def test_derives_root_relative_nested_node_addresses(tmp_path: Path) -> None:
    """A wrong root-relative segment-to-ID derivation must fail this test."""

    repository = _repository(tmp_path)
    _registered_node(repository, "packages/a/b/c")

    derived = derive_relocations(
        repository,
        (Move("packages/a/b/c", "lib/officina/a/d/e"),),
    )

    assert derived == (
        DerivedRelocation(
            source=_address("a.b.c", "packages", "packages/a/b/c"),
            target=_address("a.d.e", "lib/officina", "lib/officina/a/d/e"),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        derived[0].source.node_id = "changed"  # type: ignore[misc]


def test_cross_root_relocation_preserves_a_root_relative_id(tmp_path: Path) -> None:
    """Root names must not leak into IDs when two roots have the same suffix."""

    repository = _repository(tmp_path)
    _registered_node(repository, "packages/foo")

    derived = derive_relocations(
        repository,
        (Move("packages/foo", "lib/officina/foo"),),
    )

    assert derived[0].source.node_id == "foo"
    assert derived[0].target.node_id == "foo"


def test_source_and_target_repository_states_derive_the_same_pair(tmp_path: Path) -> None:
    """Derivation must be stable before and after a physical relocation."""

    source_state = _repository(tmp_path / "source")
    target_state = _repository(tmp_path / "target")
    _registered_node(source_state, "packages/a/b/c")
    _registered_node(target_state, "lib/officina/a/d/e")
    entries = (Move("packages/a/b/c", "lib/officina/a/d/e"),)

    source_derived = derive_relocations(source_state, entries)
    target_derived = derive_relocations(target_state, entries)

    assert source_derived == target_derived


@pytest.mark.parametrize(
    ("source_exists", "target_exists", "message"),
    [
        (True, True, "both"),
        (False, False, "neither"),
    ],
)
def test_rejects_moves_without_exactly_one_physical_endpoint(
    tmp_path: Path,
    source_exists: bool,
    target_exists: bool,
    message: str,
) -> None:
    """A dual-state resolver must reject either ambiguous endpoint condition."""

    repository = _repository(tmp_path)
    if source_exists:
        _registered_node(repository, "packages/foo")
    if target_exists:
        _registered_node(repository, "lib/officina/foo")

    with pytest.raises(AddressResolutionError, match=message):
        derive_relocations(repository, (Move("packages/foo", "lib/officina/foo"),))


def test_rejects_endpoint_outside_every_configured_root(tmp_path: Path) -> None:
    """An endpoint outside configured roots cannot receive an authoritative ID."""

    repository = _repository(tmp_path)
    _blueprint(repository / "outside/foo", "foo")

    with pytest.raises(AddressResolutionError, match="configured root"):
        derive_relocations(repository, (Move("outside/foo", "packages/foo"),))


def test_rejects_nested_configured_root_ambiguity(tmp_path: Path) -> None:
    """A nested configured root makes its descendants' identities ambiguous."""

    repository = _repository(tmp_path, roots=("packages", "packages/a"))
    _registered_node(repository, "packages/a/foo")

    with pytest.raises(AddressResolutionError, match="multiple configured roots"):
        derive_relocations(repository, (Move("packages/a/foo", "lib/officina/foo"),))


def test_rejects_existing_blueprint_with_a_mismatched_derived_id(tmp_path: Path) -> None:
    """A move must not infer an address that contradicts its existing blueprint."""

    repository = _repository(tmp_path)
    _registered_node(repository, "packages/a/b/c")
    _blueprint(repository / "packages/a/b/c", "wrong.id")

    with pytest.raises(AddressResolutionError, match="blueprint ID"):
        derive_relocations(repository, (Move("packages/a/b/c", "lib/officina/a/d/e"),))


@pytest.mark.parametrize(
    ("children", "message"),
    [
        ({}, "missing parent registration"),
        ({"wrong": {}}, "mismatched parent registration"),
    ],
)
def test_rejects_missing_or_mismatched_existing_parent_registration(
    tmp_path: Path,
    children: dict[str, object],
    message: str,
) -> None:
    """A registered child must be named by its existing parent blueprint."""

    repository = _repository(tmp_path)
    _registered_node(repository, "packages/a/b/c")
    _blueprint(repository / "packages/a/b", "a.b", children)

    with pytest.raises(AddressResolutionError, match=message):
        derive_relocations(repository, (Move("packages/a/b/c", "lib/officina/a/d/e"),))


def test_rejects_a_physical_target_collision(tmp_path: Path) -> None:
    """A target already present alongside its source is not a relocatable state."""

    repository = _repository(tmp_path)
    _registered_node(repository, "packages/foo")
    _registered_node(repository, "lib/officina/foo")

    with pytest.raises(AddressResolutionError, match="physical target collision"):
        derive_relocations(repository, (Move("packages/foo", "lib/officina/foo"),))


@pytest.mark.parametrize(
    ("source_name", "target_name"),
    [
        ("Foo", "foo"),
        ("cafe\u0301", "caf\u00e9"),
    ],
)
def test_rejects_aliasing_case_or_unicode_target_names_when_the_platform_aliases(
    tmp_path: Path,
    source_name: str,
    target_name: str,
) -> None:
    """Alias-preserving filesystems cannot host a distinct spelling target."""

    repository = _repository(tmp_path)
    _registered_node(repository, f"packages/{source_name}")
    source = repository / "packages" / source_name
    target = repository / "packages" / target_name
    if not target.exists():
        # famulus-skip: category=platform-contract; reason=filesystem keeps the candidate spellings distinct; alternate=the preceding existence probe runs on every platform
        pytest.skip("filesystem keeps the candidate spellings distinct")

    with pytest.raises(AddressResolutionError):
        derive_relocations(
            repository,
            (Move(source.relative_to(repository).as_posix(), target.relative_to(repository).as_posix()),),
        )

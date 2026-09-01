"""Tests for the route-local sparse managed-setup graph loader."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from officina.blueprints.graph import (
    BlueprintGraphError,
    InterfaceExport,
    load_repository_blueprint_graph,
    managed_setup_order,
)
from officina.configuration.repository import RepositoryConfiguration
from officina.dispatcher.direct_authorization import authorize_direct_invocation
from officina.blueprints.direct_setup import (
    load_direct_setup_graph,
    load_direct_setup_projection,
)
from officina.dispatcher.errors import DirectBlueprintError


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "setup_interface_manager"
    / "repository"
    / "python-canary"
)


def _access() -> dict[str, object]:
    return {"allow_all_modules": True, "allowed_callers": []}


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _configuration(repository: Path) -> RepositoryConfiguration:
    modules = repository / "skills"
    modules.mkdir(parents=True, exist_ok=True)
    config_path = repository / "officina.toml"
    config_path.write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n',
        encoding="utf-8",
    )
    return RepositoryConfiguration(1, config_path, repository, (modules,))


def _register_child(repository: Path, child_module_id: str) -> None:
    parent_id, child_segment = child_module_id.rsplit(".", 1)
    parent_path = (
        repository / "skills" / Path(*parent_id.split(".")) / "blueprint.yaml"
    )
    child_path = (
        repository
        / "skills"
        / Path(*child_module_id.split("."))
        / "blueprint.yaml"
    )
    parent = yaml.safe_load(parent_path.read_text(encoding="utf-8"))
    child = yaml.safe_load(child_path.read_text(encoding="utf-8"))
    parent["children"][child_segment] = {}
    parent["namespace_exports"][child_segment] = {
        "version": child["version"],
        "access": _access(),
        "surface": {
            "only": {
                export_id: 1 for export_id in sorted(child["exports"])
            }
        },
    }
    _write_yaml(parent_path, parent)


def _clone_module(
    repository: Path,
    module_id: str,
    *,
    managed: bool,
    prerequisites: tuple[tuple[str, int], ...] = (),
) -> Path:
    destination = repository / "skills" / Path(*module_id.split("."))
    copytree(FIXTURE, destination, ignore=lambda _path, names: {"__pycache__"} & set(names))
    for blueprint_path in destination.glob("**/*.yaml"):
        blueprint_path.write_text(
            blueprint_path.read_text(encoding="utf-8").replace(
                "python-canary", module_id
            ),
            encoding="utf-8",
        )

    module_path = destination / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    if managed:
        module["exports"][f"{module_id}.interface.setup"][
            "setup_requires_setup_of"
        ] = [
            {"interface": interface_id, "version": version}
            for interface_id, version in prerequisites
        ]
    else:
        module["exports"] = {
            f"{module_id}.interface.execute": {
                "access": _access(),
                "source_interface": f"{module_id}.source.setup.interface.setup",
            }
        }
        module["sources"] = {
            f"{module_id}.source.setup": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/lifecycle.yaml",
                }
            }
        }
        (destination / "blueprints" / "teardown.yaml").unlink()
    _write_yaml(module_path, module)
    if "." in module_id:
        _register_child(repository, module_id)
    return destination


def _managed_repository(
    tmp_path: Path,
    *,
    root_prerequisites: tuple[tuple[str, int], ...] = (
        ("dependency.interface.setup", 1),
    ),
    dependency_prerequisites: tuple[tuple[str, int], ...] = (),
) -> tuple[RepositoryConfiguration, Path]:
    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(
        repository,
        "root",
        managed=True,
        prerequisites=root_prerequisites,
    )
    _clone_module(repository, "root.leaf", managed=False)
    _clone_module(
        repository,
        "dependency",
        managed=True,
        prerequisites=dependency_prerequisites,
    )
    return configuration, repository


def _authorize(
    configuration: RepositoryConfiguration,
    interface_id: str = "root.leaf.interface.execute",
):
    return authorize_direct_invocation(
        configuration=configuration,
        caller_module_id="root",
        interface_id=interface_id,
        interface_version=1,
    )


def _forbid_loader_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("sparse setup loading attempted a forbidden side effect")

    original_open = Path.open

    def read_only_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        assert not set(mode) & set("wax+")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rename", forbidden)
    monkeypatch.setattr(Path, "replace", forbidden)
    monkeypatch.setattr(Path, "symlink_to", forbidden)
    monkeypatch.setattr(Path, "open", read_only_open)
    monkeypatch.setattr(os, "walk", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def test_sparse_projection_matches_canonical_setup_graph_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches projection drift or repository-wide discovery in the live loader."""

    configuration, repository_root = _managed_repository(tmp_path)
    authorized = _authorize(configuration)
    canonical = load_repository_blueprint_graph(repository_root)
    _forbid_loader_side_effects(monkeypatch)

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert projection.graph.setup_requirements == canonical.setup_requirements
    assert projection.graph.managed_setups == canonical.managed_setups
    assert projection.graph.exports == canonical.exports
    assert managed_setup_order(projection.graph, "root.interface.setup") == (
        *managed_setup_order(canonical, "root.interface.setup"),
    )
    assert all(
        isinstance(export, InterfaceExport)
        for export in projection.graph.exports.values()
    )
    assert projection.graph.module_parents == {
        "dependency": None,
        "root": None,
        "root.leaf": "root",
    }
    assert projection.lifecycle is None


def test_manager_loader_uses_the_same_sparse_repository_path(tmp_path: Path) -> None:
    """Catches the manager convenience loader falling back to inventory loading."""

    configuration, _repository_root = _managed_repository(tmp_path)

    graph = load_direct_setup_graph(configuration, "root.leaf.interface.execute")

    assert tuple(step.setup_interface for step in managed_setup_order(
        graph, "root.interface.setup"
    )) == ("dependency.interface.setup", "root.interface.setup")


def test_no_managed_owner_returns_only_the_target_without_following_setup_refs(
    tmp_path: Path,
) -> None:
    """Catches parsing an irrelevant setup prerequisite when no owner opts in."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=False)
    _clone_module(repository, "root.leaf", managed=False)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.setup"] = {
        **root["exports"]["root.interface.execute"],
        "setup_requires_setup_of": [
            {"interface": "missing.interface.setup", "version": 1}
        ],
    }
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert tuple(projection.graph.exports) == ("root.leaf.interface.execute",)
    assert projection.graph.setup_requirements == {}
    assert projection.graph.managed_setups == {}
    assert projection.lifecycle is None


def test_relevant_malformed_setup_management_fails_closed(tmp_path: Path) -> None:
    """Catches treating malformed owner metadata as an unmanaged ancestry."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.setup"]["setup_management"] = "invalid"
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    with pytest.raises(BlueprintGraphError, match="must be a mapping"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_scalar_ancestry_export_entry_fails_closed(tmp_path: Path) -> None:
    """Catches malformed ancestry exports disabling managed setup detection."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.setup"] = "invalid"
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    with pytest.raises(BlueprintGraphError, match="invalid export declaration"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_null_setup_management_does_not_select_a_nearer_owner(
    tmp_path: Path,
) -> None:
    """Catches null metadata eclipsing the nearest actual managed owner."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=True)
    leaf = _clone_module(repository, "root.leaf", managed=True)
    _clone_module(repository, "root.leaf.worker", managed=False)
    leaf_path = leaf / "blueprint.yaml"
    declaration = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
    declaration["exports"]["root.leaf.interface.setup"]["setup_management"] = None
    _write_yaml(leaf_path, declaration)
    authorized = _authorize(
        configuration,
        "root.leaf.worker.interface.execute",
    )

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert tuple(projection.graph.managed_setups) == ("root.interface.setup",)


def test_public_export_alias_matches_canonical_export_value(tmp_path: Path) -> None:
    """Catches copying a source-local name into an aliased public export."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.ready"] = root["exports"].pop(
        "root.interface.setup-status"
    )
    root["exports"]["root.interface.setup"]["setup_management"]["setup_verifier"][
        "interface"
    ] = "root.interface.ready"
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)
    canonical = load_repository_blueprint_graph(repository)

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert (
        projection.graph.exports["root.interface.ready"]
        == canonical.exports["root.interface.ready"]
    )


@pytest.mark.parametrize(
    "lifecycle_export",
    ("setup-owner", "teardown", "setup-verifier", "teardown-verifier"),
)
def test_foreign_lifecycle_export_ids_fail_closed(
    tmp_path: Path,
    lifecycle_export: str,
) -> None:
    """Catches synthesizing a foreign public lifecycle export as module-local."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    management = root["exports"]["root.interface.setup"]["setup_management"]
    if lifecycle_export == "setup-owner":
        local_id = "root.interface.setup"
        foreign_id = "foreign.interface.setup"
    elif lifecycle_export == "teardown":
        local_id = "root.interface.teardown"
        foreign_id = "foreign.interface.teardown"
        management["teardown"]["interface"] = foreign_id
    elif lifecycle_export == "setup-verifier":
        local_id = "root.interface.setup-status"
        foreign_id = "foreign.interface.setup-status"
        management["setup_verifier"]["interface"] = foreign_id
    else:
        local_id = "root.interface.teardown-status"
        foreign_id = "foreign.interface.teardown-status"
        management["teardown"]["verifier"]["interface"] = foreign_id
    root["exports"][foreign_id] = root["exports"].pop(local_id)
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="not owned by 'root'"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_non_string_prerequisite_export_id_fails_as_invalid_interface(
    tmp_path: Path,
) -> None:
    """Catches sorting malformed export keys before canonical ID parsing."""

    configuration, repository = _managed_repository(tmp_path)
    dependency_path = repository / "skills" / "dependency" / "blueprint.yaml"
    dependency = yaml.safe_load(dependency_path.read_text(encoding="utf-8"))
    dependency["exports"][17] = dependency["exports"].pop(
        "dependency.interface.teardown"
    )
    _write_yaml(dependency_path, dependency)
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="invalid interface id"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_empty_managed_owner_id_is_not_treated_as_no_owner(tmp_path: Path) -> None:
    """Catches a falsy malformed owner ID activating the no-owner shortcut."""

    configuration, repository = _managed_repository(tmp_path)
    root_path = repository / "skills" / "root" / "blueprint.yaml"
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"][""] = root["exports"].pop("root.interface.setup")
    _write_yaml(root_path, root)
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="invalid interface id"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_missing_setup_prerequisite_fails_closed(tmp_path: Path) -> None:
    """Catches silently dropping a referenced module that does not exist."""

    configuration, _repository = _managed_repository(
        tmp_path,
        root_prerequisites=(("missing.interface.setup", 1),),
    )
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="module not found"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_setup_prerequisite_version_mismatch_fails_closed(tmp_path: Path) -> None:
    """Catches accepting a prerequisite whose pinned export version differs."""

    configuration, _repository = _managed_repository(
        tmp_path,
        root_prerequisites=(("dependency.interface.setup", 2),),
    )
    authorized = _authorize(configuration)

    with pytest.raises((BlueprintGraphError, DirectBlueprintError), match="version"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_setup_dependency_cycle_fails_closed(tmp_path: Path) -> None:
    """Catches recursive loading that fails to validate a completed cycle."""

    configuration, _repository = _managed_repository(
        tmp_path,
        dependency_prerequisites=(("root.interface.setup", 1),),
    )
    authorized = _authorize(configuration)

    with pytest.raises(BlueprintGraphError, match="cycle"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_symlinked_setup_prerequisite_fails_closed(tmp_path: Path) -> None:
    """Catches following a symlink while resolving an explicit prerequisite."""

    configuration, repository = _managed_repository(tmp_path)
    dependency = repository / "skills" / "dependency"
    real_dependency = repository / "skills" / "dependency-real"
    dependency.rename(real_dependency)
    dependency.symlink_to(real_dependency, target_is_directory=True)
    authorized = _authorize(configuration)

    with pytest.raises(DirectBlueprintError, match="symlink"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_nearest_ancestry_managed_owner_wins(tmp_path: Path) -> None:
    """Catches selecting a farther managed owner when a nearer one exists."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=True)
    _clone_module(repository, "root.leaf", managed=True)
    _clone_module(repository, "root.leaf.worker", managed=False)
    authorized = _authorize(
        configuration,
        "root.leaf.worker.interface.execute",
    )

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert tuple(projection.graph.managed_setups) == ("root.leaf.interface.setup",)
    assert tuple(projection.graph.setup_requirements) == (
        "root.leaf.interface.setup",
    )


def test_nearer_owner_does_not_hide_farther_duplicate_owners(tmp_path: Path) -> None:
    """Catches nearest-owner selection suppressing farther duplicate validation."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    root_path = _clone_module(repository, "root", managed=True) / "blueprint.yaml"
    _clone_module(repository, "root.leaf", managed=True)
    _clone_module(repository, "root.leaf.worker", managed=False)
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.other"] = dict(
        root["exports"]["root.interface.setup"]
    )
    _write_yaml(root_path, root)
    authorized = _authorize(
        configuration,
        "root.leaf.worker.interface.execute",
    )

    with pytest.raises(BlueprintGraphError, match="at most one managed setup"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


def test_nearer_owner_does_not_hide_farther_nonlocal_lifecycle_reference(
    tmp_path: Path,
) -> None:
    """Catches nearest-owner selection suppressing farther lifecycle validation."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    root_path = _clone_module(repository, "root", managed=True) / "blueprint.yaml"
    _clone_module(repository, "root.leaf", managed=True)
    _clone_module(repository, "root.leaf.worker", managed=False)
    root = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root["exports"]["root.interface.setup"]["setup_management"][
        "setup_verifier"
    ]["interface"] = "root.leaf.interface.setup-status"
    _write_yaml(root_path, root)
    authorized = _authorize(
        configuration,
        "root.leaf.worker.interface.execute",
    )

    with pytest.raises(BlueprintGraphError, match="same module"):
        load_direct_setup_projection(
            authorized.repository,
            authorized.target_modules,
            authorized.export,
        )


@pytest.mark.parametrize(
    ("interface_name", "kind"),
    [("setup", "setup"), ("teardown", "teardown")],
)
def test_exact_lifecycle_exports_are_classified(
    tmp_path: Path,
    interface_name: str,
    kind: str,
) -> None:
    """Catches classifying an exact managed lifecycle export as ordinary work."""

    repository = tmp_path / "repository"
    configuration = _configuration(repository)
    _clone_module(repository, "root", managed=True)
    authorized = _authorize(configuration, f"root.interface.{interface_name}")

    projection = load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert projection.lifecycle == ("root.interface.setup", kind)


def test_unrelated_modules_are_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches work scaling with unrelated repository module count."""

    configuration, repository = _managed_repository(tmp_path)
    for index in range(25):
        unrelated = repository / "skills" / f"unrelated-{index}"
        unrelated.mkdir()
        (unrelated / "blueprint.yaml").write_text("not: relevant\n", encoding="utf-8")
    authorized = _authorize(configuration)
    original_open = Path.open
    reads: list[Path] = []

    def counting_open(path: Path, *args: object, **kwargs: object):
        reads.append(path)
        if "unrelated-" in path.as_posix():
            raise AssertionError(f"unrelated module read: {path}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    load_direct_setup_projection(
        authorized.repository,
        authorized.target_modules,
        authorized.export,
    )

    assert reads
    assert not [path for path in reads if "unrelated-" in path.as_posix()]

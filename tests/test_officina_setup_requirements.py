from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest
import yaml

import conftest as root_conftest
from officina.blueprints.graph import (
    BlueprintNode,
    BlueprintGraphError,
    InterfaceExport,
    ManagedSetup,
    RepositoryBlueprintGraph,
    _managed_setup_metadata,
    _setup_requirements,
    load_repository_blueprint_graph,
    managed_setup_order,
    setup_order,
)


class _GraphFixtureRequest:
    def __init__(self, candidate: object) -> None:
        self.candidate = candidate

    def getfixturevalue(self, name: str) -> object:
        assert name == "graph"
        return self.candidate


def _ordinary_graph_with_paths(
    module_root: object,
    blueprint_path: object,
    gateway_path: object,
) -> RepositoryBlueprintGraph:
    return RepositoryBlueprintGraph(
        nodes={
            "demo": BlueprintNode(
                node_id="demo",
                node_type="module",
                version=1,
                module_root=module_root,
                blueprint_path=blueprint_path,
                gateway_path=gateway_path,
                declaration={},
            )
        },
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )


def test_ordinary_repository_graph_checks_fallback_type_and_materialized_paths(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relative = _ordinary_graph_with_paths(
        Path("skills/demo"),
        Path("skills/demo/blueprint.yaml"),
        None,
    )
    loaded_roots = []

    def load_fallback(root: Path) -> RepositoryBlueprintGraph:
        loaded_roots.append(root)
        return relative

    monkeypatch.setattr(
        root_conftest,
        "load_repository_blueprint_graph",
        load_fallback,
    )
    fixture = root_conftest.ordinary_repository_graph.__wrapped__

    assert fixture(_GraphFixtureRequest(None)) is relative
    assert loaded_roots == [root_conftest._REPOSITORY_ROOT]

    with pytest.raises(TypeError, match="must be a RepositoryBlueprintGraph"):
        fixture(_GraphFixtureRequest(object()))
    with pytest.raises(TypeError, match="must be pathlib.Path values"):
        fixture(
            _GraphFixtureRequest(
                _ordinary_graph_with_paths(
                    "skills/demo",
                    Path("blueprint.yaml"),
                    None,
                )
            )
        )
    with pytest.raises(AssertionError, match="different materialized root"):
        fixture(
            _GraphFixtureRequest(
                _ordinary_graph_with_paths(
                    tmp_path,
                    tmp_path / "blueprint.yaml",
                    None,
                )
            )
        )

def _graph(requirements: dict[str, tuple[tuple[str, int], ...]]) -> RepositoryBlueprintGraph:
    return RepositoryBlueprintGraph(
        nodes={},
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        setup_requirements=requirements,
    )


def _export(
    interface_id: str,
    *,
    version: int = 1,
    prerequisites: object = (),
) -> InterfaceExport:
    declaration = {
        "source_interface": f"{interface_id}.source",
        "access": {"allow_all_modules": True, "allowed_callers": []},
    }
    if prerequisites is not None:
        declaration["setup_requires_setup_of"] = prerequisites
    return InterfaceExport(
        interface_id=interface_id,
        version=version,
        local_name=interface_id.rsplit(".interface.", 1)[-1],
        module_node_id=interface_id.split(".interface.", 1)[0],
        declaration={},
        export_declaration=declaration,
    )


def _lifecycle_export(
    interface_id: str,
    *,
    version: int = 1,
    source_id: str | None = None,
    executable: bool = False,
    read_only: bool = True,
    arguments: dict[str, object] | None = None,
    management: object = None,
    prerequisites: object = None,
) -> InterfaceExport:
    declaration: dict[str, object] = {
        "source_interface": f"{interface_id}.source",
        "access": {"allow_all_modules": True, "allowed_callers": []},
    }
    if prerequisites is not None:
        declaration["setup_requires_setup_of"] = prerequisites
    if management is not None:
        declaration["setup_management"] = management
    source_declaration: dict[str, object] = {}
    if executable:
        source_declaration["process_binding"] = {"kind": "process"}
    if arguments is not None or not read_only:
        source_declaration["contract"] = {
            "arguments": arguments or {},
            "execution": {"state_effect": "read-only" if read_only else "mutating"},
        }
    return InterfaceExport(
        interface_id=interface_id,
        version=version,
        local_name=interface_id.rsplit(".interface.", 1)[-1],
        module_node_id=interface_id.split(".interface.", 1)[0],
        declaration=source_declaration,
        source_node_id=source_id or f"{interface_id}.source",
        source_interface_id=f"{interface_id}.source",
        export_declaration=declaration,
    )


def _managed_exports(
    *,
    setup_id: str = "demo.interface.setup",
    setup_version: int = 3,
    setup_source_id: str = "demo.source.setup",
    teardown_source_id: str = "demo.source.teardown",
    setup_verifier_source_id: str = "demo.source.setup-status",
    teardown_verifier_source_id: str = "demo.source.teardown-status",
    setup_executable: bool = True,
) -> dict[str, InterfaceExport]:
    module_id = setup_id.split(".interface.", 1)[0]
    setup_verifier_id = f"{module_id}.interface.setup-status"
    teardown_id = f"{module_id}.interface.teardown"
    teardown_verifier_id = f"{module_id}.interface.teardown-status"
    return {
        setup_id: _lifecycle_export(
            setup_id,
            version=setup_version,
            source_id=setup_source_id,
            executable=setup_executable,
            prerequisites=[],
            management={
                "setup_verifier": {"interface": setup_verifier_id, "version": 5},
                "teardown": {
                    "interface": teardown_id,
                    "version": 4,
                    "verifier": {"interface": teardown_verifier_id, "version": 6},
                },
            },
        ),
        setup_verifier_id: _lifecycle_export(
            setup_verifier_id,
            version=5,
            source_id=setup_verifier_source_id,
            executable=True,
            arguments={},
        ),
        teardown_id: _lifecycle_export(
            teardown_id,
            version=4,
            source_id=teardown_source_id,
            executable=setup_executable,
        ),
        teardown_verifier_id: _lifecycle_export(
            teardown_verifier_id,
            version=6,
            source_id=teardown_verifier_source_id,
            executable=True,
            arguments={},
        ),
    }


def _managed_graph(exports: dict[str, InterfaceExport]) -> RepositoryBlueprintGraph:
    return RepositoryBlueprintGraph(
        nodes={},
        node_edges=(),
        exports=exports,
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        setup_requirements=_setup_requirements(exports),
        managed_setups=_managed_setup_metadata(exports),
    )


def test_repository_setup_order_is_explicit_and_dependency_first(
    ordinary_repository_graph: RepositoryBlueprintGraph,
) -> None:
    graph = ordinary_repository_graph

    assert setup_order(graph, "connect-google.interface.setup") == (
        "connect-google.interface.setup",
    )
    expected = ("connect-google.interface.setup",)
    assert setup_order(graph, "cloud-files.interface.setup") == expected + (
        "cloud-files.interface.setup",
    )
    assert setup_order(graph, "online-calendar.interface.setup") == expected + (
        "online-calendar.interface.setup",
    )
    assert setup_order(graph, "list-manager.interface.setup") == (
        "list-manager.interface.setup",
    )
    for module_id in (
        "connect-google",
        "cloud-files",
        "online-calendar",
        "list-manager",
    ):
        assert (
            graph.exports[f"{module_id}.interface.setup"].source_interface_id
            == graph.exports[f"{module_id}.interface.default"].source_interface_id
        )


def test_setup_order_deduplicates_a_diamond() -> None:
    graph = _graph(
        {
            "root.interface.setup": (
                ("left.interface.setup", 1),
                ("right.interface.setup", 1),
            ),
            "left.interface.setup": (("leaf.interface.setup", 1),),
            "right.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )

    assert setup_order(graph, "root.interface.setup") == (
        "leaf.interface.setup",
        "left.interface.setup",
        "right.interface.setup",
        "root.interface.setup",
    )


def test_setup_order_rejects_cycles() -> None:
    graph = _graph(
        {
            "one.interface.setup": (("two.interface.setup", 1),),
            "two.interface.setup": (("one.interface.setup", 1),),
        }
    )

    with pytest.raises(BlueprintGraphError, match="setup dependency cycle"):
        setup_order(graph, "one.interface.setup")


def test_setup_order_rejects_an_unknown_root() -> None:
    with pytest.raises(BlueprintGraphError, match="not a public setup interface"):
        setup_order(_graph({}), "missing.interface.setup")


def test_setup_order_is_repeatable() -> None:
    graph = _graph(
        {
            "root.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )

    expected = ("leaf.interface.setup", "root.interface.setup")
    assert setup_order(graph, "root.interface.setup") == expected
    assert setup_order(graph, "root.interface.setup") == expected


def test_setup_order_handles_a_long_chain_iteratively() -> None:
    size = 2_000
    requirements = {
        f"node-{index}.interface.setup": (
            ((f"node-{index + 1}.interface.setup", 1),)
            if index + 1 < size
            else ()
        )
        for index in range(size)
    }
    graph = _graph(requirements)

    order = setup_order(graph, "node-0.interface.setup")

    assert len(order) == size
    assert order[0] == f"node-{size - 1}.interface.setup"
    assert order[-1] == "node-0.interface.setup"


def test_setup_requirements_reject_missing_declaration() -> None:
    exports = {"demo.interface.setup": _export("demo.interface.setup", prerequisites=None)}

    with pytest.raises(BlueprintGraphError, match="must declare"):
        _setup_requirements(exports)


def test_setup_requirements_reject_field_on_non_setup_export() -> None:
    exports = {"demo.interface.default": _export("demo.interface.default")}

    with pytest.raises(BlueprintGraphError, match="only setup interfaces"):
        _setup_requirements(exports)


@pytest.mark.parametrize(
    ("prerequisites", "message"),
    [
        ([{"interface": "other.interface.default", "version": 1}], "not a public setup"),
        ([{"interface": "other.interface.setup", "version": 2}], "pins version"),
        (
            [
                {"interface": "other.interface.setup", "version": 1},
                {"interface": "other.interface.setup", "version": 1},
            ],
            "duplicate",
        ),
    ],
)
def test_setup_requirements_reject_invalid_targets(
    prerequisites: list[dict[str, object]],
    message: str,
) -> None:
    exports = {
        "demo.interface.setup": _export(
            "demo.interface.setup", prerequisites=prerequisites
        ),
        "other.interface.setup": _export(
            "other.interface.setup", prerequisites=[]
        ),
        "other.interface.default": _export("other.interface.default", prerequisites=None),
    }

    with pytest.raises(BlueprintGraphError, match=message):
        _setup_requirements(exports)


def test_setup_exports_alias_existing_default_behavior(
    ordinary_repository_graph: RepositoryBlueprintGraph,
) -> None:
    graph = ordinary_repository_graph

    for module_id in (
        "connect-google",
        "cloud-files",
        "online-calendar",
        "list-manager",
    ):
        assert (
            graph.exports[f"{module_id}.interface.setup"].source_interface_id
            == graph.exports[f"{module_id}.interface.default"].source_interface_id
        )


def test_managed_setup_order_projects_immutable_lifecycle_metadata() -> None:
    graph = _managed_graph(_managed_exports())

    assert graph.managed_setups == {
        "demo.interface.setup": ManagedSetup(
            setup_interface="demo.interface.setup",
            setup_version=3,
            teardown_interface="demo.interface.teardown",
            teardown_version=4,
            setup_verifier_interface="demo.interface.setup-status",
            setup_verifier_version=5,
            teardown_verifier_interface="demo.interface.teardown-status",
            teardown_verifier_version=6,
            kind="python",
        )
    }
    assert managed_setup_order(graph, "demo.interface.setup") == (
        graph.managed_setups["demo.interface.setup"],
    )


def test_managed_setup_order_projects_markdown_source_kind() -> None:
    graph = _managed_graph(_managed_exports(setup_executable=False))

    assert graph.managed_setups["demo.interface.setup"].kind == "markdown"


def test_managed_setup_constructor_permits_optional_verifier_fields() -> None:
    """Verify that ManagedSetup dataclass allows optional setup verifier fields."""
    setup = ManagedSetup(
        setup_interface="demo.interface.setup",
        setup_version=1,
        kind="python",
        setup_verifier_interface=None,
        setup_verifier_version=None,
    )
    assert setup.setup_interface == "demo.interface.setup"
    assert setup.setup_version == 1
    assert setup.kind == "python"
    assert setup.setup_verifier_interface is None
    assert setup.setup_verifier_version is None
    assert setup.teardown_interface is None
    assert setup.teardown_version is None
    assert setup.teardown_verifier_interface is None
    assert setup.teardown_verifier_version is None


def test_managed_setup_constructor_permits_optional_teardown_fields() -> None:
    """Verify that ManagedSetup dataclass allows optional teardown fields."""
    setup = ManagedSetup(
        setup_interface="demo.interface.setup",
        setup_version=1,
        kind="markdown",
        teardown_interface=None,
        teardown_version=None,
        teardown_verifier_interface=None,
        teardown_verifier_version=None,
    )
    assert setup.setup_interface == "demo.interface.setup"
    assert setup.setup_version == 1
    assert setup.kind == "markdown"
    assert setup.teardown_interface is None
    assert setup.teardown_version is None
    assert setup.teardown_verifier_interface is None
    assert setup.teardown_verifier_version is None


def test_managed_setup_projection_still_fully_populates_from_legacy_setup_management() -> None:
    """Verify legacy setup_management projection still fully populates all fields."""
    graph = _managed_graph(_managed_exports())
    metadata = graph.managed_setups["demo.interface.setup"]

    # All fields should be populated from legacy setup_management
    assert metadata.setup_interface == "demo.interface.setup"
    assert metadata.setup_version == 3
    assert metadata.teardown_interface == "demo.interface.teardown"
    assert metadata.teardown_version == 4
    assert metadata.setup_verifier_interface == "demo.interface.setup-status"
    assert metadata.setup_verifier_version == 5
    assert metadata.teardown_verifier_interface == "demo.interface.teardown-status"
    assert metadata.teardown_verifier_version == 6
    assert metadata.kind == "python"
    # Verify no field is None
    assert metadata.teardown_interface is not None
    assert metadata.teardown_version is not None
    assert metadata.setup_verifier_interface is not None
    assert metadata.setup_verifier_version is not None
    assert metadata.teardown_verifier_interface is not None
    assert metadata.teardown_verifier_version is not None


def test_managed_setup_metadata_requires_matching_action_kinds() -> None:
    """Catches dispatching teardown through a different execution boundary."""
    exports = _managed_exports()
    teardown = exports["demo.interface.teardown"]
    exports[teardown.interface_id] = InterfaceExport(
        **{**teardown.__dict__, "declaration": {}}
    )

    with pytest.raises(BlueprintGraphError, match="same execution kind"):
        _managed_setup_metadata(exports)


@pytest.mark.parametrize(
    "target_id",
    ["demo.interface.setup", "demo.interface.teardown",
     "demo.interface.setup-status", "demo.interface.teardown-status"],
)
def test_managed_setup_metadata_requires_argument_free_lifecycle(target_id: str) -> None:
    """Catches admitting a fixed lifecycle action that needs persisted inputs."""
    exports = _managed_exports()
    target = exports[target_id]
    exports[target_id] = InterfaceExport(
        **{
            **target.__dict__,
            "declaration": {
                **target.declaration,
                "contract": {
                    "arguments": {"value": {}},
                    "execution": {"state_effect": "read-only"},
                },
            },
        }
    )

    with pytest.raises(BlueprintGraphError, match="must take no arguments"):
        _managed_setup_metadata(exports)


def test_managed_setup_order_preserves_setup_order_for_a_managed_closure() -> None:
    exports = _managed_exports(setup_id="root.interface.setup")
    exports.update(_managed_exports(setup_id="leaf.interface.setup"))
    root = exports["root.interface.setup"]
    exports["root.interface.setup"] = InterfaceExport(
        **{
            **root.__dict__,
            "export_declaration": {
                **(root.export_declaration or {}),
                "setup_requires_setup_of": [
                    {"interface": "leaf.interface.setup", "version": 3}
                ],
            },
        }
    )
    graph = _managed_graph(exports)

    assert tuple(step.setup_interface for step in managed_setup_order(graph, "root.interface.setup")) == (
        "leaf.interface.setup",
        "root.interface.setup",
    )


def test_managed_setup_metadata_rejects_management_on_non_setup_export() -> None:
    exports = _managed_exports()
    default = _lifecycle_export(
        "demo.interface.default",
        management={
            "setup_verifier": {"interface": "demo.interface.setup-status", "version": 5},
            "teardown": {
                "interface": "demo.interface.teardown",
                "version": 4,
                "verifier": {"interface": "demo.interface.teardown-status", "version": 6},
            },
        },
    )
    exports[default.interface_id] = default

    with pytest.raises(BlueprintGraphError, match="only public setup interfaces"):
        _managed_setup_metadata(exports)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        (
            "setup_verifier",
            {"interface": "other.interface.setup-status", "version": 5},
            "same module",
        ),
        (
            "setup_verifier",
            {"interface": "demo.interface.missing", "version": 5},
            "must exist",
        ),
        (
            "setup_verifier",
            {"interface": "demo.interface.setup-status", "version": 4},
            "pins version",
        ),
    ],
)
def test_managed_setup_metadata_requires_pinned_local_lifecycle_exports(
    field: str,
    replacement: dict[str, object],
    message: str,
) -> None:
    exports = _managed_exports()
    if replacement["interface"] == "other.interface.setup-status":
        exports["other.interface.setup-status"] = _lifecycle_export(
            "other.interface.setup-status",
            version=5,
            executable=True,
            arguments={},
        )
    setup = exports["demo.interface.setup"]
    management = dict((setup.export_declaration or {})["setup_management"])
    management[field] = replacement
    exports[setup.interface_id] = InterfaceExport(
        **{
            **setup.__dict__,
            "export_declaration": {
                **(setup.export_declaration or {}),
                "setup_management": management,
            },
        }
    )

    with pytest.raises(BlueprintGraphError, match=message):
        _managed_setup_metadata(exports)


def _write_nested_managed_setup_repository(repo: Path) -> None:
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "setup_interface_manager"
        / "repository"
        / "python-canary"
    )
    for segment in ("left", "right"):
        module = repo / "skills" / "root" / segment
        copytree(
            fixture,
            module,
            ignore=lambda _path, names: {"__pycache__"} & set(names),
        )
        for blueprint in module.rglob("*.yaml"):
            blueprint.write_text(
                blueprint.read_text(encoding="utf-8").replace(
                    "python-canary", f"root.{segment}"
                ),
                encoding="utf-8",
            )

    root = repo / "skills" / "root"
    (root / "SKILL.md").write_text("# root fixture\n", encoding="utf-8")
    (root / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "root",
                "version": 1,
                "maturity": "experimental",
                "description": "Nested managed-setup lifecycle test fixture.",
                "gateway": {"language": "Markdown", "path": "SKILL.md"},
                "content": [r"SKILL\.md"],
                "authority": {"owns_filesystem": []},
                "children": {"left": {}, "right": {}},
                "namespace_exports": {
                    segment: {
                        "version": 1,
                        "access": {"allow_all_modules": True, "allowed_callers": []},
                        "surface": {
                            "only": {
                                f"root.{segment}.interface.{name}": 1
                                for name in (
                                    "setup",
                                    "setup-status",
                                    "teardown",
                                    "teardown-status",
                                )
                            }
                        },
                    }
                    for segment in ("left", "right")
                },
                "exports": {},
                "sources": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_repository_graph_requires_same_module_lifecycle_references(
    tmp_path: Path,
) -> None:
    """Catches graph loading that accepts a sibling-module lifecycle verifier."""

    repo = tmp_path / "repository"
    _write_nested_managed_setup_repository(repo)
    load_repository_blueprint_graph(repo)

    blueprint_path = repo / "skills" / "root" / "left" / "blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    management = blueprint["exports"]["root.left.interface.setup"]["setup_management"]
    management["teardown"]["verifier"] = {
        "interface": "root.right.interface.teardown-status",
        "version": 1,
    }
    blueprint_path.write_text(
        yaml.safe_dump(blueprint, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(BlueprintGraphError, match="same module"):
        load_repository_blueprint_graph(repo)


def test_managed_setup_metadata_allows_at_most_one_managed_setup_per_module() -> None:
    """Catches recording multiple managed lifecycle owners for one module."""

    exports = _managed_exports(setup_id="first.interface.setup")
    duplicate_module_exports = _managed_exports(setup_id="second.interface.setup")
    for export_id, export in duplicate_module_exports.items():
        exports[export_id] = InterfaceExport(
            **{**export.__dict__, "module_node_id": "first"}
        )

    with pytest.raises(BlueprintGraphError, match="at most one managed setup"):
        _managed_setup_metadata(exports)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        (
            "teardown",
            {"interface": "other.interface.teardown", "version": 4},
            "same module",
        ),
        (
            "teardown",
            {"interface": "demo.interface.teardown", "version": 3},
            "pins version",
        ),
        (
            "teardown.verifier",
            {"interface": "other.interface.teardown-status", "version": 6},
            "same module",
        ),
        (
            "teardown.verifier",
            {"interface": "demo.interface.teardown-status", "version": 5},
            "pins version",
        ),
    ],
)
def test_managed_setup_metadata_pins_local_teardown_references(
    field: str,
    replacement: dict[str, object],
    message: str,
) -> None:
    exports = _managed_exports()
    if replacement["interface"].startswith("other."):
        exports[replacement["interface"]] = _lifecycle_export(
            replacement["interface"],
            version=replacement["version"],
            executable=field == "teardown.verifier",
            arguments={} if field == "teardown.verifier" else None,
        )
    setup = exports["demo.interface.setup"]
    management = dict((setup.export_declaration or {})["setup_management"])
    teardown = dict(management["teardown"])
    if field == "teardown":
        replacement_with_verifier = dict(replacement)
        replacement_with_verifier["verifier"] = teardown["verifier"]
        management["teardown"] = replacement_with_verifier
    else:
        teardown["verifier"] = replacement
        management["teardown"] = teardown
    exports[setup.interface_id] = InterfaceExport(
        **{
            **setup.__dict__,
            "export_declaration": {
                **(setup.export_declaration or {}),
                "setup_management": management,
            },
        }
    )

    with pytest.raises(BlueprintGraphError, match=message):
        _managed_setup_metadata(exports)


def test_managed_setup_metadata_requires_the_teardown_and_verifiers() -> None:
    for removed_id in (
        "demo.interface.teardown",
        "demo.interface.setup-status",
        "demo.interface.teardown-status",
    ):
        exports = _managed_exports()
        del exports[removed_id]

        with pytest.raises(BlueprintGraphError, match="must exist"):
            _managed_setup_metadata(exports)


def test_managed_setup_metadata_requires_dedicated_setup_and_teardown_sources() -> None:
    exports = _managed_exports(teardown_source_id="demo.source.setup")

    with pytest.raises(BlueprintGraphError, match="dedicated sources"):
        _managed_setup_metadata(exports)


@pytest.mark.parametrize(
    ("target", "kwargs", "message"),
    [
        ("demo.interface.setup-status", {"executable": False}, "must be executable"),
        (
            "demo.interface.setup-status",
            {"executable": True, "read_only": False},
            "must be read-only",
        ),
        (
            "demo.interface.teardown-status",
            {"executable": False},
            "must be executable",
        ),
        (
            "demo.interface.teardown-status",
            {"executable": True, "read_only": False},
            "must be read-only",
        ),
        (
            "demo.interface.setup-status",
            {"executable": True, "arguments": {"path": object()}},
            "take no arguments",
        ),
        (
            "demo.interface.teardown-status",
            {"executable": True, "arguments": {"path": object()}},
            "take no arguments",
        ),
    ],
)
def test_managed_setup_metadata_requires_read_only_argument_free_verifiers(
    target: str,
    kwargs: dict[str, object],
    message: str,
) -> None:
    exports = _managed_exports()
    old = exports[target]
    exports[target] = _lifecycle_export(
        target,
        version=old.version,
        source_id=old.source_node_id,
        **kwargs,
    )

    with pytest.raises(BlueprintGraphError, match=message):
        _managed_setup_metadata(exports)


def test_managed_setup_metadata_rejects_an_unmanaged_closure_member() -> None:
    exports = _managed_exports(setup_id="root.interface.setup")
    leaf = _lifecycle_export(
        "leaf.interface.setup",
        prerequisites=[],
    )
    exports[leaf.interface_id] = leaf
    root = exports["root.interface.setup"]
    exports[root.interface_id] = InterfaceExport(
        **{
            **root.__dict__,
            "export_declaration": {
                **(root.export_declaration or {}),
                "setup_requires_setup_of": [
                    {"interface": "leaf.interface.setup", "version": 1}
                ],
            },
        }
    )
    with pytest.raises(BlueprintGraphError, match="unmanaged closure member"):
        _managed_setup_metadata(exports)


def test_managed_setup_metadata_excludes_bootstrap_setup() -> None:
    exports = _managed_exports(setup_id="bootstrap-dispatcher-runtime.interface.setup")

    with pytest.raises(BlueprintGraphError, match="bootstrap-dispatcher-runtime cannot opt in"):
        _managed_setup_metadata(exports)

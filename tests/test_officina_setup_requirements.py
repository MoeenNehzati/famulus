from __future__ import annotations

from pathlib import Path

import pytest

import conftest as root_conftest
from officina.blueprints.graph import (
    BlueprintNode,
    BlueprintGraphError,
    InterfaceExport,
    RepositoryBlueprintGraph,
    _setup_requirements,
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

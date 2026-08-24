from __future__ import annotations

from pathlib import Path

import pytest

from officina.blueprints.graph import (
    BlueprintGraphError,
    InterfaceExport,
    RepositoryBlueprintGraph,
    _setup_requirements,
    load_repository_blueprint_graph,
    setup_order,
)


ROOT = Path(__file__).resolve().parents[1]


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


def test_repository_setup_order_is_explicit_and_dependency_first() -> None:
    graph = load_repository_blueprint_graph(
        ROOT,
        schema_root=ROOT / "references" / "blueprint-schema",
        expected_schema_version=6,
    )

    assert setup_order(graph, "install-assistant-tools.interface.setup") == (
        "install-assistant-tools.interface.setup",
    )
    assert setup_order(graph, "connect-google.interface.setup") == (
        "connect-google.interface.setup",
    )
    expected = ("connect-google.interface.setup",)
    assert setup_order(graph, "cloud-files.interface.setup") == expected + (
        "cloud-files.interface.setup",
    )
    assert setup_order(graph, "g-calendar.interface.setup") == expected + (
        "g-calendar.interface.setup",
    )
    assert setup_order(graph, "list-manager.interface.setup") == expected + (
        "list-manager.interface.setup",
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


def test_setup_exports_alias_existing_default_behavior() -> None:
    graph = load_repository_blueprint_graph(
        ROOT,
        schema_root=ROOT / "references" / "blueprint-schema",
        expected_schema_version=6,
    )

    for module_id in (
        "install-assistant-tools",
        "connect-google",
        "cloud-files",
        "g-calendar",
        "list-manager",
    ):
        assert (
            graph.exports[f"{module_id}.interface.setup"].source_interface_id
            == graph.exports[f"{module_id}.interface.default"].source_interface_id
        )

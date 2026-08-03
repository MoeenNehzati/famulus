from pathlib import Path
import time

from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintNode,
    CertificationEdge,
    ExportDependencyEdge,
    HelperEdge,
    InterfaceExport,
    NamespaceRoute,
    RepositoryBlueprintGraph,
    RoutedInterface,
    DispatchBlueprintGraph,
)
from officina.common.certification_view import CertificationDecision
import officina.dispatcher.catalog as dispatcher_catalog
from officina.dispatcher.catalog import (
    CatalogRoute,
    lookup_route_graph,
    decode_repository_graph,
    encode_repository_graph,
    load_route_graph,
    load_route_certification_decision,
    store_route_graph,
    store_route_certification_decision,
)


def _graph(root: Path) -> RepositoryBlueprintGraph:
    module = BlueprintNode(
        node_id="demo",
        node_type="module",
        version=1,
        module_root=root / "skills" / "demo",
        blueprint_path=root / "skills" / "demo" / "blueprint.yaml",
        gateway_path=None,
        declaration={"schema_version": 5, "exports": {}},
    )
    source = BlueprintNode(
        node_id="demo-rtx.source.gateway",
        node_type="behavioral_source",
        version=1,
        module_root=root / "skills" / "demo" / "_rtx",
        blueprint_path=root / "skills" / "demo" / "_rtx" / "blueprint.yaml",
        gateway_path=root / "skills" / "demo" / "_rtx" / "gateway.py",
        declaration={"schema_version": 5, "gateway": {"language": "Python"}},
    )
    export = InterfaceExport(
        interface_id="demo.interface.run",
        version=1,
        local_name="run",
        module_node_id="demo",
        declaration={"patterns": []},
        source_node_id=source.node_id,
        terminal_interface_id="demo-rtx.interface.run",
        terminal_module_node_id="demo-rtx",
    )
    routed = RoutedInterface(
        route_owner_id="demo",
        child_module_id="demo-rtx",
        interface_id=export.interface_id,
        version=1,
        terminal_module_id="demo-rtx",
        terminal_module_version=1,
    )
    route = NamespaceRoute(
        route_owner_id="demo",
        child_module_id="demo-rtx",
        child_version=1,
        declaration={"segment": "rtx"},
        materialized_interfaces=(routed,),
    )
    return RepositoryBlueprintGraph(
        nodes={module.node_id: module, source.node_id: source},
        node_edges=(BlueprintEdge("contains", module.node_id, source.node_id, 1),),
        exports={export.interface_id: export},
        export_edges=(ExportDependencyEdge(export.interface_id, export.interface_id, 1),),
        helper_edges=(
            HelperEdge(export.interface_id, "helper", export.interface_id, 1, {"x": 1}),
        ),
        certification_edges=(CertificationEdge("depends_on", module.node_id, source.node_id, 1),),
        module_sources={module.node_id: (source.node_id,)},
        direct_file_owners={source.gateway_path: source.node_id},
        schema_version=5,
        source_modules={source.node_id: module.node_id},
        source_interfaces={export.interface_id: export},
        module_parents={module.node_id: None},
        module_children={module.node_id: ("demo-rtx",)},
        module_local_segments={module.node_id: "demo"},
        module_ancestry={module.node_id: (module.node_id,)},
        namespace_routes={(module.node_id, "rtx"): route},
        routed_interfaces=(routed,),
    )


def test_repository_graph_catalog_round_trip_preserves_typed_values(
    tmp_path: Path,
) -> None:
    graph = _graph(tmp_path)

    decoded = decode_repository_graph(
        encode_repository_graph(graph),
        repo_root=tmp_path,
    )

    assert decoded == graph
    assert next(iter(decoded.direct_file_owners)) == (
        tmp_path / "skills" / "demo" / "_rtx" / "gateway.py"
    )
    assert ("demo", "rtx") in decoded.namespace_routes


def _materialize_blueprints(graph: RepositoryBlueprintGraph) -> None:
    for node in graph.nodes.values():
        node.blueprint_path.parent.mkdir(parents=True, exist_ok=True)
        node.blueprint_path.write_text(
            f"schema_version: 5\nid: {node.node_id}\n",
            encoding="utf-8",
        )
        if node.gateway_path is not None:
            node.gateway_path.parent.mkdir(parents=True, exist_ok=True)
            node.gateway_path.write_text("# gateway\n", encoding="utf-8")


def test_route_graph_store_reuses_only_fresh_matching_route(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    cache_root = tmp_path / "cache"
    graph = _graph(root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    scoped = DispatchBlueprintGraph(graph)

    store_route_graph(root, route, scoped, cache_root=cache_root)

    assert load_route_graph(root, route, cache_root=cache_root) == scoped
    assert (
        load_route_graph(
            root,
            CatalogRoute("other", "demo.interface.run"),
            cache_root=cache_root,
        )
        is None
    )

    graph.nodes["demo"].blueprint_path.write_text(
        "schema_version: 5\nid: changed\n",
        encoding="utf-8",
    )
    assert load_route_graph(root, route, cache_root=cache_root) is None


def test_route_graph_store_rejects_malformed_json(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    cache_root = tmp_path / "cache"
    graph = _graph(root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    store_route_graph(
        root,
        route,
        DispatchBlueprintGraph(graph),
        cache_root=cache_root,
    )
    catalog_file = next(cache_root.rglob("*.json"))
    catalog_file.write_text("not json", encoding="utf-8")

    assert load_route_graph(root, route, cache_root=cache_root) is None


def test_route_graph_lookup_reports_missing_malformed_stale_and_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    cache_root = tmp_path / "cache"
    graph = _graph(root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")

    assert lookup_route_graph(root, route, cache_root=cache_root).status == "missing"

    store_route_graph(
        root,
        route,
        DispatchBlueprintGraph(graph),
        cache_root=cache_root,
    )
    hit = lookup_route_graph(root, route, cache_root=cache_root)
    assert hit.status == "hit"
    assert hit.graph == DispatchBlueprintGraph(graph)

    catalog_file = next(cache_root.rglob("*.json"))
    catalog_file.write_text("not json", encoding="utf-8")
    malformed = lookup_route_graph(root, route, cache_root=cache_root)
    assert malformed.status == "malformed"
    assert malformed.graph is None

    store_route_graph(
        root,
        route,
        DispatchBlueprintGraph(graph),
        cache_root=cache_root,
    )
    graph.nodes["demo"].blueprint_path.write_text(
        "schema_version: 5\nid: changed\n",
        encoding="utf-8",
    )
    stale = lookup_route_graph(root, route, cache_root=cache_root)
    assert stale.status == "stale"
    assert stale.graph is None

    def unavailable_read(*_args, **_kwargs):
        raise OSError("cache device unavailable")

    monkeypatch.setattr(
        dispatcher_catalog,
        "read_regular_file_bytes",
        unavailable_read,
    )
    unavailable = lookup_route_graph(root, route, cache_root=cache_root)
    assert unavailable.status == "unavailable"
    assert unavailable.graph is None


def test_fresh_route_graph_load_has_a_bounded_local_cost(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    cache_root = tmp_path / "cache"
    graph = _graph(root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    store_route_graph(
        root,
        route,
        DispatchBlueprintGraph(graph),
        cache_root=cache_root,
    )

    started = time.monotonic()
    for _ in range(20):
        assert load_route_graph(root, route, cache_root=cache_root) is not None
    elapsed = time.monotonic() - started

    # A broad ceiling catches accidental repository reconstruction while
    # tolerating slow shared CI hosts.
    assert elapsed < 2.0


def test_route_certification_decision_is_bound_to_runtime_inputs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    cache_root = tmp_path / "cache"
    graph = _graph(root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    scoped = DispatchBlueprintGraph(graph)
    decision = CertificationDecision(False, "stale", "Certificate is stale.")
    store_route_graph(root, route, scoped, cache_root=cache_root)

    store_route_certification_decision(
        root,
        route,
        graph,
        decision,
        cache_root=cache_root,
    )

    assert (
        load_route_certification_decision(root, route, cache_root=cache_root)
        == decision
    )
    gateway = graph.nodes["demo-rtx.source.gateway"].gateway_path
    assert gateway is not None
    gateway.write_text("# changed gateway\n", encoding="utf-8")
    assert (
        load_route_certification_decision(root, route, cache_root=cache_root)
        is None
    )


def test_route_certification_decision_is_invalidated_by_new_runtime_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    cache_root = tmp_path / "cache"
    graph = _graph(root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    store_route_graph(
        root,
        route,
        DispatchBlueprintGraph(graph),
        cache_root=cache_root,
    )
    store_route_certification_decision(
        root,
        route,
        graph,
        CertificationDecision(True, "current", "Current certificate."),
        cache_root=cache_root,
    )

    (root / "skills" / "demo" / "_rtx" / "new_runtime.py").write_text(
        "# new input\n",
        encoding="utf-8",
    )

    assert (
        load_route_certification_decision(root, route, cache_root=cache_root)
        is None
    )

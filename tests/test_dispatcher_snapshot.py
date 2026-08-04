from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from officina.common.blueprint_graph import DispatchBlueprintGraph
from officina.common.certification_view import CertificationDecision
from officina.dispatcher.catalog import CatalogRoute
import officina.dispatcher.core as dispatcher_core
from test_dispatcher_catalog import _graph, _materialize_blueprints
from test_officina_dispatcher import _load_v5_dispatch_graph
from v5_blueprint_fixtures import copy_v5_fixture_tree


def _snapshot_module():
    try:
        return importlib.import_module("officina.install.dispatch_snapshot")
    except ModuleNotFoundError:
        pytest.fail("dispatcher snapshot support is missing")


def _snapshot_builder_module():
    try:
        return importlib.import_module("officina.install.dispatch_snapshot_builder")
    except ModuleNotFoundError:
        pytest.fail("dispatcher snapshot builder is missing")


def test_missing_active_snapshot_fails_with_repair_command(tmp_path: Path) -> None:
    snapshot = _snapshot_module()

    with pytest.raises(Exception) as caught:
        snapshot.load_snapshot_route(
            tmp_path / "repo",
            CatalogRoute("caller", "demo.interface.run"),
            snapshot_root=tmp_path / "snapshots",
        )

    assert caught.value.code == "dispatcher.snapshot_missing"
    assert "officina.install.dispatch_snapshot_builder" in str(caught.value)


def test_activated_snapshot_round_trips_exact_route(tmp_path: Path) -> None:
    snapshot = _snapshot_module()
    repo_root = tmp_path / "repo"
    graph = _graph(repo_root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    decision = CertificationDecision(False, "stale", "Certificate is stale.")

    snapshot.activate_snapshot(
        repo_root,
        {route: snapshot.SnapshotRoute(DispatchBlueprintGraph(graph), decision)},
        snapshot_root=tmp_path / "snapshots",
    )

    loaded = snapshot.load_snapshot_route(
        repo_root,
        route,
        snapshot_root=tmp_path / "snapshots",
    )
    assert loaded.graph == DispatchBlueprintGraph(graph)
    assert loaded.certification == decision


def test_snapshot_rejects_unsupported_authorization_semantics(tmp_path: Path) -> None:
    snapshot = _snapshot_module()
    repo_root = tmp_path / "repo"
    graph = _graph(repo_root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    snapshot_root = tmp_path / "snapshots"
    snapshot.activate_snapshot(
        repo_root,
        {route: snapshot.SnapshotRoute(DispatchBlueprintGraph(graph), None)},
        snapshot_root=snapshot_root,
    )
    pointer = next(snapshot_root.rglob("current.json"))
    manifest_path = pointer.parent / json.loads(pointer.read_text())["manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["authorization_semantics_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(Exception) as caught:
        snapshot.load_snapshot_route(repo_root, route, snapshot_root=snapshot_root)

    assert caught.value.code == "dispatcher.snapshot_unsupported"


def test_snapshot_pointer_cannot_escape_repository_generation_root(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_module()
    repo_root = tmp_path / "repo"
    snapshot_root = tmp_path / "snapshots"
    repo_snapshot_root = snapshot.repository_snapshot_root(
        repo_root,
        snapshot_root=snapshot_root,
    )
    repo_snapshot_root.mkdir(parents=True)
    (repo_snapshot_root / "current.json").write_text(
        json.dumps({"format_version": 1, "manifest": "../../outside.json"}),
        encoding="utf-8",
    )

    with pytest.raises(Exception) as caught:
        snapshot.load_snapshot_route(
            repo_root,
            CatalogRoute("caller", "demo.interface.run"),
            snapshot_root=snapshot_root,
        )

    assert caught.value.code == "dispatcher.snapshot_malformed"


def test_builder_activates_authorized_host_routes_from_canonical_graph(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_module()
    builder = _snapshot_builder_module()
    repo_root, _graph_value = _load_v5_dispatch_graph(tmp_path)
    snapshot_root = tmp_path / "snapshots"

    result = builder.build_dispatch_snapshot(
        repo_root,
        snapshot_root=snapshot_root,
        certification_view=builder.RejectingCertificationView(),
    )

    assert result.route_count >= 1
    loaded = snapshot.load_snapshot_route(
        repo_root,
        CatalogRoute("demo", "demo.interface.execute"),
        snapshot_root=snapshot_root,
    )
    assert loaded.graph.graph.schema_version == 5
    assert loaded.certification == CertificationDecision(
        False,
        "certification-unavailable",
        "repository certification state is unavailable",
    )

    with pytest.raises(Exception) as denied:
        snapshot.load_snapshot_route(
            repo_root,
            CatalogRoute("demo", "root.interface.admin"),
            snapshot_root=snapshot_root,
        )
    assert denied.value.code == "dispatcher.unauthorized_caller"
    assert "caller-filtered:terminal-export:root.interface.admin" in str(
        denied.value
    )

    nested = snapshot.load_snapshot_route(
        repo_root,
        CatalogRoute("beta", "leaf.interface.run"),
        snapshot_root=snapshot_root,
    )
    assert nested.graph is not None


def test_failed_candidate_does_not_replace_active_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_module()
    repo_root = tmp_path / "repo"
    graph = _graph(repo_root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    snapshot_root = tmp_path / "snapshots"
    original = snapshot.SnapshotRoute(DispatchBlueprintGraph(graph), None)
    snapshot.activate_snapshot(repo_root, {route: original}, snapshot_root=snapshot_root)
    real_encode = snapshot._encode_route

    def fail_encode(*args, **kwargs):
        raise ValueError("candidate generation failed")

    monkeypatch.setattr(snapshot, "_encode_route", fail_encode)
    with pytest.raises(ValueError, match="candidate generation failed"):
        snapshot.activate_snapshot(
            repo_root,
            {route: original},
            snapshot_root=snapshot_root,
        )
    monkeypatch.setattr(snapshot, "_encode_route", real_encode)

    assert snapshot.load_snapshot_route(
        repo_root,
        route,
        snapshot_root=snapshot_root,
    ) == original


def test_activation_retains_only_current_and_previous_generation(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_module()
    repo_root = tmp_path / "repo"
    graph = _graph(repo_root)
    _materialize_blueprints(graph)
    route = CatalogRoute("caller", "demo.interface.run")
    snapshot_root = tmp_path / "snapshots"
    value = snapshot.SnapshotRoute(DispatchBlueprintGraph(graph), None)

    for _ in range(3):
        snapshot.activate_snapshot(
            repo_root,
            {route: value},
            snapshot_root=snapshot_root,
        )

    generations = list(
        (
            snapshot.repository_snapshot_root(
                repo_root,
                snapshot_root=snapshot_root,
            )
            / "generations"
        ).iterdir()
    )
    assert len(generations) == 2


def test_host_dispatch_uses_snapshot_without_repository_or_certification_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _snapshot_builder_module()
    repo_root, _graph_value = _load_v5_dispatch_graph(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    builder.build_dispatch_snapshot(
        repo_root,
        certification_view=builder.RejectingCertificationView(),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("repository-scale work reached host dispatch")

    for name in (
        "collect_blueprints",
        "load_dispatch_blueprint_graph",
        "load_repository_blueprint_graph",
        "repository_certification_view",
        "store_route_graph",
        "store_route_certification_decision",
    ):
        monkeypatch.setattr(dispatcher_core, name, forbidden)

    metadata = dispatcher_core._resolve_host_dispatch_metadata(
        caller_skill="demo",
        target="demo-rtx.interface.execute",
        args=["--route-smoke"],
        repo_root=repo_root,
    )

    assert metadata.target == "demo-rtx.interface.execute"
    assert [item.code for item in metadata.diagnostics] == [
        "certification-unavailable"
    ]


def test_host_dispatch_fails_instead_of_building_when_snapshot_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    def forbidden(*args, **kwargs):
        raise AssertionError("dispatcher attempted repository repair")

    monkeypatch.setattr(dispatcher_core, "collect_blueprints", forbidden)
    with pytest.raises(Exception) as caught:
        dispatcher_core._resolve_host_dispatch_metadata(
            caller_skill="demo",
            target="demo.interface.execute",
            args=[],
            repo_root=tmp_path / "repo",
        )

    assert caught.value.code == "dispatcher.snapshot_missing"


def test_public_dispatch_fails_instead_of_building_when_snapshot_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    def forbidden(*args, **kwargs):
        raise AssertionError("public dispatcher attempted repository repair")

    monkeypatch.setattr(dispatcher_core, "collect_blueprints", forbidden)
    with pytest.raises(Exception) as caught:
        dispatcher_core.resolve_dispatch_metadata(
            caller_skill="demo",
            target="demo.interface.execute",
            args=[],
        )

    assert caught.value.code == "dispatcher.snapshot_missing"

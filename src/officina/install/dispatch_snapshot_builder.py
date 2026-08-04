"""Direct, non-dispatched builder for immutable dispatcher route snapshots."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from officina.common.blueprint_authorization import (
    AuthorizationRequest,
    resolve_interface_authorization,
)
from officina.common.blueprint_graph import (
    DispatchBlueprintGraph,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.common.certification_view import (
    CertificationView,
    RejectingCertificationView,
)
from officina.dispatcher.catalog import CatalogRoute, compact_route_graph
from officina.install.dispatch_snapshot import SnapshotRoute, activate_snapshot


@dataclass(frozen=True)
class SnapshotBuildResult:
    """Summary of one successfully activated dispatcher snapshot."""

    generation_root: Path
    route_count: int


class SnapshotBuildError(RuntimeError):
    """Canonical repository state cannot produce a complete snapshot."""


def _eligible_host_callers(
    graph: RepositoryBlueprintGraph,
) -> tuple[tuple[str, str], ...]:
    callers: list[tuple[str, str]] = []
    for module_id, module in sorted(graph.nodes.items()):
        if module.node_type != "module":
            continue
        discovery = module.declaration.get("discovery")
        if not isinstance(discovery, dict) or discovery.get("mechanism") != "skill":
            continue
        matches = tuple(
            source_id
            for source_id in graph.module_sources.get(module_id, ())
            if graph.nodes[source_id].gateway_path == module.gateway_path
        )
        if len(matches) != 1:
            raise SnapshotBuildError(
                f"discoverable host module {module_id!r} has {len(matches)} gateway sources; expected one"
            )
        callers.append((module_id, matches[0]))
    return tuple(callers)


def _candidate_routes(
    graph: RepositoryBlueprintGraph,
) -> tuple[tuple[CatalogRoute, str | None, int], ...]:
    """Enumerate host requests and declared nested dispatch requests."""

    candidates: dict[CatalogRoute, tuple[str | None, int]] = {}
    for caller_module_id, caller_source_id in _eligible_host_callers(graph):
        for interface_id, export in graph.exports.items():
            candidates[CatalogRoute(caller_module_id, interface_id)] = (
                caller_source_id,
                export.version,
            )
    for source_id, module_id in graph.source_modules.items():
        source = graph.nodes[source_id]
        uses = source.declaration.get("uses_interfaces", [])
        if not isinstance(uses, list):
            continue
        for use in uses:
            if not isinstance(use, dict):
                continue
            interface_id = use.get("interface")
            version = use.get("version")
            if (
                isinstance(interface_id, str)
                and isinstance(version, int)
                and not isinstance(version, bool)
                and interface_id in graph.exports
            ):
                candidates.setdefault(
                    CatalogRoute(module_id, interface_id),
                    (None, version),
                )
    return tuple(
        (route, source_id, version)
        for route, (source_id, version) in sorted(
            candidates.items(),
            key=lambda item: (
                item[0].caller_module_id,
                item[0].target_interface_id,
            ),
        )
    )


def _selected_certification_view(
    supplied: CertificationView | None,
) -> CertificationView:
    return supplied if supplied is not None else RejectingCertificationView()


def build_dispatch_snapshot(
    repo_root: Path,
    *,
    snapshot_root: Path | None = None,
    certification_view: CertificationView | None = None,
) -> SnapshotBuildResult:
    """Derive every authorized host route and activate it atomically."""

    root = Path(repo_root).resolve()
    graph = load_repository_blueprint_graph(root, expected_schema_version=5)
    selected_certification = _selected_certification_view(certification_view)
    routes: dict[CatalogRoute, SnapshotRoute] = {}
    for route, caller_source_id, version in _candidate_routes(graph):
        authorization = resolve_interface_authorization(
            graph,
            AuthorizationRequest(
                caller_module_id=route.caller_module_id,
                caller_source_id=caller_source_id,
                interface_id=route.target_interface_id,
                version=version,
            ),
        )
        if not authorization.allowed:
            routes[route] = SnapshotRoute(
                graph=None,
                certification=None,
                denial=authorization.diagnostic,
            )
            continue
        routes[route] = SnapshotRoute(
            graph=DispatchBlueprintGraph(
                compact_route_graph(graph, authorization),
            ),
            certification=selected_certification.check_authorization(
                authorization
            ),
        )
    generation_root = activate_snapshot(
        root,
        routes,
        snapshot_root=snapshot_root,
    )
    return SnapshotBuildResult(
        generation_root=generation_root,
        route_count=len(routes),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and atomically activate dispatcher route data.",
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--snapshot-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_dispatch_snapshot(
            args.repo_root,
            snapshot_root=args.snapshot_root,
        )
    except Exception as exc:  # builder boundary renders one concise failure
        print(f"error: dispatcher snapshot build failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"activated dispatcher snapshot with {result.route_count} routes at "
        f"{result.generation_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RejectingCertificationView",
    "SnapshotBuildError",
    "SnapshotBuildResult",
    "build_dispatch_snapshot",
    "main",
]

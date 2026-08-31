from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib
from pathlib import Path

import pytest
import yaml

from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from test_support.v5_blueprint_fixtures import copy_v5_fixture_tree


V5_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "blueprint_schemas"
    / "v5"
)
V5_AUTHORIZATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "authorization"
)
CALLER_SOURCES = {
    "demo": "demo.source.gateway",
    "demo-rtx": "demo-rtx.source.runtime",
    "root": "root.source.runtime",
    "alpha": "alpha.source.caller",
    "leaf": "leaf.source.caller",
    "beta": "beta.source.caller",
    "beta-leaf": "beta-leaf.source.caller",
    "outsider": "outsider.source.caller",
}


def _authorization_module():
    return importlib.import_module("officina.blueprints.authorization")


def _copy_v5_authorization_fixture(tmp_path: Path) -> Path:
    return copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE,
        tmp_path / "repo",
    )


@pytest.fixture(scope="module")
def authorization_graph(
    tmp_path_factory: pytest.TempPathFactory,
) -> RepositoryBlueprintGraph:
    """Load the immutable authorization fixture graph once for this module."""

    root = _copy_v5_authorization_fixture(tmp_path_factory.mktemp("authorization"))
    return load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        **{"expected_" + "schema_version": 5},
    )


@pytest.fixture
def mutable_authorization_graph(
    authorization_graph: RepositoryBlueprintGraph,
) -> RepositoryBlueprintGraph:
    """Give mutation cases an isolated copy of the shared base graph."""

    return deepcopy(authorization_graph)


def _resolve(
    graph,
    *,
    caller_module_id: str,
    interface_id: str,
    version: int = 1,
    caller_source_id: str | None = None,
):
    authorization = _authorization_module()
    if caller_source_id is None:
        caller_source_id = CALLER_SOURCES[caller_module_id]
    request = authorization.AuthorizationRequest(
        caller_module_id=caller_module_id,
        caller_source_id=caller_source_id,
        interface_id=interface_id,
        version=version,
    )
    return authorization.resolve_interface_authorization(graph, request)


@pytest.mark.parametrize(
    (
        "caller_module_id",
        "interface_id",
        "expected_lca",
        "expected_gates",
    ),
    [
        ("leaf", "leaf.interface.run", "leaf", ()),
        ("alpha", "leaf.interface.run", "alpha", ()),
        ("beta", "alpha.interface.status", "root", ()),
        ("root", "leaf.interface.run", "root", (("alpha", "leaf"),)),
        ("beta", "leaf.interface.run", "root", (("alpha", "leaf"),)),
        ("beta-leaf", "leaf.interface.run", "root", (("alpha", "leaf"),)),
        (
            "outsider",
            "leaf.interface.run",
            None,
            (("root", "alpha"), ("alpha", "leaf")),
        ),
        ("beta-leaf", "root.interface.admin", "root", ()),
    ],
)
def legacy_v5_authorization_uses_target_side_lca_gates(
    authorization_graph: RepositoryBlueprintGraph,
    caller_module_id: str,
    interface_id: str,
    expected_lca: str | None,
    expected_gates: tuple[tuple[str, str], ...],
) -> None:
    graph = authorization_graph

    result = _resolve(
        graph,
        caller_module_id=caller_module_id,
        interface_id=interface_id,
    )

    assert result.allowed, result.diagnostic
    assert result.diagnostic == "authorized"
    assert result.lca_module_id == expected_lca
    assert tuple(
        (gate.route_owner_id, gate.child_module_id)
        for gate in result.crossed_namespace_gates
    ) == expected_gates


def legacy_v5_relative_callers_admit_their_registered_descendants(
    mutable_authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = mutable_authorization_graph
    graph.nodes["beta-leaf.source.caller"].declaration[
        "uses_interfaces"
    ].append({"interface": "alpha.interface.status", "version": 1})

    allowed = _resolve(
        graph,
        caller_module_id="beta",
        interface_id="leaf.interface.run",
    )
    allowed_descendant = _resolve(
        graph,
        caller_module_id="beta-leaf",
        interface_id="alpha.interface.status",
    )

    assert allowed.allowed
    assert {
        (resolved.owner_module_id, resolved.reference, resolved.module_id)
        for resolved in allowed.resolved_callers
        if resolved.module_id == "beta"
    } >= {
        ("alpha", "..beta", "beta"),
        ("leaf", "...beta", "beta"),
    }
    assert allowed_descendant.allowed, allowed_descendant.diagnostic


def legacy_v5_authorization_distinguishes_private_unknown_and_versioned_targets(
    authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = authorization_graph

    private = _resolve(
        graph,
        caller_module_id="leaf",
        interface_id="leaf.source.runtime.interface.private",
    )
    unknown = _resolve(
        graph,
        caller_module_id="leaf",
        interface_id="missing.interface.call",
    )
    wrong_version = _resolve(
        graph,
        caller_module_id="leaf",
        interface_id="leaf.interface.run",
        version=2,
    )

    assert not private.allowed
    assert private.diagnostic == (
        "private-interface:leaf.source.runtime.interface.private"
    )
    assert not unknown.allowed
    assert unknown.diagnostic == "unknown-interface:missing.interface.call"
    assert not wrong_version.allowed
    assert wrong_version.diagnostic == (
        "version-mismatch:leaf.interface.run:requested=2:available=1"
    )


def legacy_v5_facade_preserves_caller_and_evaluates_self_at_both_owners(
    mutable_authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = mutable_authorization_graph
    graph.nodes["outsider.source.caller"].declaration[
        "uses_interfaces"
    ].append({"interface": "demo-rtx.interface.execute", "version": 3})

    facade = _resolve(
        graph,
        caller_module_id="demo",
        caller_source_id="demo.source.gateway",
        interface_id="demo.interface.execute",
        version=3,
    )
    direct_child = _resolve(
        graph,
        caller_module_id="demo",
        caller_source_id="demo.source.gateway",
        interface_id="demo-rtx.interface.execute",
        version=3,
    )
    unrelated_direct_child = _resolve(
        graph,
        caller_module_id="outsider",
        interface_id="demo-rtx.interface.execute",
        version=3,
    )

    assert facade.allowed, facade.diagnostic
    assert facade.caller_module_id == "demo"
    assert facade.requested_interface_id == "demo.interface.execute"
    assert facade.terminal_interface_id == "demo-rtx.interface.execute"
    assert facade.terminal_module_id == "demo-rtx"
    assert facade.implementing_source_id == "demo-rtx.source.runtime"
    assert not facade.crossed_namespace_gates
    assert [
        (
            access.kind,
            access.owner_module_id,
            access.caller_is_self,
            access.admits_caller,
        )
        for access in facade.effective_filters
    ] == [
        ("facade-export", "demo", True, True),
        ("terminal-export", "demo-rtx", False, True),
    ]
    assert {relation.relation for relation in facade.relations} >= {
        "contains-module",
        "facades-child-export",
        "facades-implementing-source",
    }
    assert {
        (
            relation.relation,
            relation.source_node_id,
            relation.target_node_id,
            relation.target_version,
        )
        for relation in facade.relations
    } == {
        ("contains-module", "demo", "demo-rtx", 1),
        ("facades-child-export", "demo", "demo-rtx", 1),
        (
            "facades-implementing-source",
            "demo",
            "demo-rtx.source.runtime",
            1,
        ),
    }
    assert {
        (requirement.node_id, requirement.version)
        for requirement in facade.required_certificates
    } == {
        ("demo", 1),
        ("demo-rtx", 1),
        ("demo-rtx.source.runtime", 1),
    }

    assert direct_child.allowed, direct_child.diagnostic
    assert not {
        "facades-child-export",
        "facades-implementing-source",
    } & {relation.relation for relation in direct_child.relations}
    assert not unrelated_direct_child.allowed
    assert unrelated_direct_child.diagnostic == (
        "missing-namespace-route:demo->demo-rtx"
    )


def legacy_v5_facade_owner_is_immediate_caller_of_child_export(
    mutable_authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = mutable_authorization_graph
    facade = graph.exports["demo.interface.execute"].export_declaration
    child = graph.exports[
        "demo-rtx.interface.execute"
    ].export_declaration
    assert isinstance(facade, dict)
    assert isinstance(child, dict)
    facade["access"] = {
        "allow_all_modules": True,
        "allowed_callers": [],
    }
    child["access"] = {
        "allow_all_modules": False,
        "allowed_callers": ["demo"],
    }

    result = _resolve(
        graph,
        caller_module_id="outsider",
        interface_id="demo.interface.execute",
        version=3,
    )

    assert result.allowed, result.diagnostic


def legacy_v5_namespace_route_owners_are_immediate_callers_of_next_hop(
    mutable_authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = mutable_authorization_graph
    root_route = graph.namespace_routes[("root", "alpha")].declaration
    alpha_route = graph.namespace_routes[("alpha", "leaf")].declaration
    leaf = graph.exports["leaf.interface.run"].export_declaration
    assert isinstance(root_route, dict)
    assert isinstance(alpha_route, dict)
    assert isinstance(leaf, dict)
    root_route["access"] = {
        "allow_all_modules": False,
        "allowed_callers": ["outsider"],
    }
    alpha_route["access"] = {
        "allow_all_modules": False,
        "allowed_callers": ["root"],
    }
    leaf["access"] = {
        "allow_all_modules": False,
        "allowed_callers": ["alpha"],
    }

    result = _resolve(
        graph,
        caller_module_id="outsider",
        interface_id="leaf.interface.run",
    )

    assert result.allowed, result.diagnostic


def legacy_v5_direct_child_request_bypasses_facade_filter(tmp_path: Path) -> None:
    root = _copy_v5_authorization_fixture(tmp_path)
    child_root = root / "skills" / "demo" / "_rtx"
    child_marker = child_root / "blueprint.yaml"
    child_declaration = yaml.safe_load(
        child_marker.read_text(encoding="utf-8")
    )
    child_declaration["content"] = [
        r"(?:__init__\.py|runtime\.py|caller\.py)"
    ]
    child_declaration["sources"]["demo-rtx.source.caller"] = {
        "blueprint": {
            "base": "module-root",
            "path": "blueprints/caller.yaml",
        }
    }
    child_marker.write_text(
        yaml.safe_dump(child_declaration, sort_keys=False),
        encoding="utf-8",
    )
    (child_root / "caller.py").write_text("", encoding="utf-8")
    (child_root / "blueprints" / "caller.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 5,
                "node_type": "behavioral_source",
                "id": "demo-rtx.source.caller",
                "version": 1,
                "gateway": {
                    "path": "caller.py",
                    "language": "Python>=3.11",
                },
                "content": [r"caller\.py"],
                "dependencies": [],
                "uses_interfaces": [
                    {"interface": "demo.interface.execute", "version": 3},
                    {
                        "interface": "demo-rtx.interface.execute",
                        "version": 3,
                    },
                ],
                "interfaces": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    graph = load_repository_blueprint_graph(
        root,
        schema_root=V5_SCHEMA_ROOT,
        **{"expected_" + "schema_version": 5},
    )
    facade_declaration = graph.exports[
        "demo.interface.execute"
    ].export_declaration
    assert isinstance(facade_declaration, dict)
    facade_declaration["access"] = {
        "allow_all_modules": False,
        "allowed_callers": [],
    }

    facade = _resolve(
        graph,
        caller_module_id="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        interface_id="demo.interface.execute",
        version=3,
    )
    direct_child = _resolve(
        graph,
        caller_module_id="demo-rtx",
        caller_source_id="demo-rtx.source.caller",
        interface_id="demo-rtx.interface.execute",
        version=3,
    )

    assert not facade.allowed
    assert facade.diagnostic == (
        "caller-filtered:facade-export:demo.interface.execute"
    )
    assert direct_child.allowed, direct_child.diagnostic
    assert not {
        "facades-child-export",
        "facades-implementing-source",
    } & {relation.relation for relation in direct_child.relations}


def legacy_v5_all_and_only_routes_are_materialized_not_wildcards(
    mutable_authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = mutable_authorization_graph
    graph.nodes["outsider.source.caller"].declaration[
        "uses_interfaces"
    ].append({"interface": "leaf.interface.hidden", "version": 1})

    cross_branch_hidden = _resolve(
        graph,
        caller_module_id="beta",
        interface_id="leaf.interface.hidden",
    )
    unrelated_hidden = _resolve(
        graph,
        caller_module_id="outsider",
        interface_id="leaf.interface.hidden",
    )

    assert cross_branch_hidden.allowed, cross_branch_hidden.diagnostic
    assert tuple(
        (gate.route_owner_id, gate.child_module_id)
        for gate in cross_branch_hidden.crossed_namespace_gates
    ) == (("alpha", "leaf"),)
    assert "namespace-interface" in {
        access.kind for access in cross_branch_hidden.effective_filters
    }
    assert not unrelated_hidden.allowed
    assert unrelated_hidden.diagnostic == (
        "route-surface-excludes-interface:root->alpha:"
        "leaf.interface.hidden@1"
    )


def legacy_v5_authorization_ignores_caller_source_identity_and_declared_use(
    mutable_authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = mutable_authorization_graph

    authorization = _authorization_module()
    missing_source = authorization.resolve_interface_authorization(
        graph,
        authorization.AuthorizationRequest(
            caller_module_id="beta",
            caller_source_id=None,
            interface_id="leaf.interface.run",
            version=1,
        ),
    )
    mismatched_source = _resolve(
        graph,
        caller_module_id="beta",
        caller_source_id="outsider.source.caller",
        interface_id="leaf.interface.run",
    )
    source = graph.nodes["outsider.source.caller"]
    source.declaration["uses_interfaces"] = []
    undeclared_use = _resolve(
        graph,
        caller_module_id="outsider",
        caller_source_id="outsider.source.caller",
        interface_id="leaf.interface.run",
    )

    assert missing_source.allowed, missing_source.diagnostic
    assert mismatched_source.allowed, mismatched_source.diagnostic
    assert undeclared_use.allowed, undeclared_use.diagnostic


def legacy_v5_result_has_exact_relations_and_minimal_consulted_certificate_set(
    authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = authorization_graph

    result = _resolve(
        graph,
        caller_module_id="outsider",
        interface_id="leaf.interface.run",
    )

    assert result.allowed
    assert {
        (
            relation.relation,
            relation.source_node_id,
            relation.target_node_id,
            relation.target_version,
        )
        for relation in result.relations
    } == {
        ("contains-module", "root", "alpha", 1),
        ("contains-module", "alpha", "leaf", 1),
        ("contains-module", "root", "beta", 1),
        ("contains-module", "beta", "beta-leaf", 1),
        ("routes-child-namespace", "root", "alpha", 1),
        ("routes-terminal-module", "root", "leaf", 1),
        ("routes-child-namespace", "alpha", "leaf", 1),
        ("routes-terminal-module", "alpha", "leaf", 1),
    }
    assert {
        (requirement.node_id, requirement.version)
        for requirement in result.required_certificates
    } == {
        ("root", 1),
        ("alpha", 1),
        ("beta", 1),
        ("leaf", 1),
        ("leaf.source.runtime", 1),
        ("outsider", 1),
    }
    assert all(
        relation.relation != "contains-module"
        for relation in graph.certification_edges
    )


def legacy_v5_authorization_result_is_deeply_immutable(
    authorization_graph: RepositoryBlueprintGraph,
) -> None:
    graph = authorization_graph
    result = _resolve(
        graph,
        caller_module_id="outsider",
        interface_id="leaf.interface.run",
    )

    assert isinstance(result.caller_ancestry, tuple)
    assert isinstance(result.target_ancestry, tuple)
    assert isinstance(result.terminal_ancestry, tuple)
    assert isinstance(result.crossed_namespace_gates, tuple)
    assert isinstance(result.resolved_callers, tuple)
    assert isinstance(result.effective_filters, tuple)
    assert isinstance(result.relations, tuple)
    assert isinstance(result.required_certificates, frozenset)
    with pytest.raises(FrozenInstanceError):
        result.allowed = False
    with pytest.raises(FrozenInstanceError):
        result.effective_filters[0].admits_caller = False


def test_authorization_uses_its_relocated_module() -> None:
    authorization = _authorization_module()

    assert authorization.AuthorizationRequest.__module__ == "officina.blueprints.authorization"
    assert authorization.AuthorizationResult.__module__ == "officina.blueprints.authorization"

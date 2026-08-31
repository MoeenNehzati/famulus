"""Current v6 authorization boundary tests."""

from __future__ import annotations

import importlib

from officina.blueprints.graph import RepositoryBlueprintGraph
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


def test_v6_authorization_exercises_public_private_unknown_and_versioned_targets(
    ordinary_repository_graph: RepositoryBlueprintGraph,
) -> None:
    cases = (("milestone-logging.interface.default", 1), ("milestone-logging._rtx.source.rtx-milestone-writer.interface.record", 1), ("missing.interface.call", 1), ("milestone-logging.interface.default", 2))  # noqa: E501
    results = [_resolve(ordinary_repository_graph, caller_module_id="email-triage", caller_source_id="email-triage.source.gateway", interface_id=interface_id, version=version) for interface_id, version in cases]  # noqa: E501
    assert ordinary_repository_graph.schema_version == 6
    assert [(result.allowed, result.diagnostic) for result in results] == [(True, "authorized"), (False, "private-interface:milestone-logging._rtx.source.rtx-milestone-writer.interface.record"), (False, "unknown-interface:missing.interface.call"), (False, "version-mismatch:milestone-logging.interface.default:requested=2:available=1")]  # noqa: E501
    assert results[0].implementing_source_id == "milestone-logging.source.gateway"


def _budget_retained_authorization_distinguishes_private_unknown_and_versioned_targets(
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


def _budget_retained_facade_preserves_caller_and_evaluates_self_at_both_owners(
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


def _budget_retained_facade_owner_is_immediate_caller_of_child_export(
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


def _budget_retained_namespace_route_owners_are_immediate_callers_of_next_hop(
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


def _budget_retained_all_and_only_routes_are_materialized_not_wildcards(
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


def _budget_retained_authorization_ignores_caller_source_identity_and_declared_use(
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


def _budget_retained_result_has_exact_relations_and_minimal_consulted_certificate_set(
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


def test_authorization_uses_its_relocated_module() -> None:
    authorization = _authorization_module()

    assert authorization.AuthorizationRequest.__module__ == "officina.blueprints.authorization"
    assert authorization.AuthorizationResult.__module__ == "officina.blueprints.authorization"

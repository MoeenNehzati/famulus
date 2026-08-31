"""Behavioral tests for hierarchical blueprint visualization payloads."""

from __future__ import annotations

from pathlib import Path

import pytest

from officina.blueprints.graph import (
    BlueprintEdge,
    BlueprintNode,
    RepositoryBlueprintGraph,
)
from officina.visualization.from_blueprint.extractor import (
    build_payload_from_repository_graph,
)
from officina.visualization.graph import Graph


REPO_ROOT = Path(__file__).resolve().parents[1]


def _entity(entity_id: str, *, container: str | None = None) -> dict[str, object]:
    entity: dict[str, object] = {
        "id": entity_id,
        "type": "module",
        "short_title": entity_id,
        "position": 0,
        "connects_to": [],
    }
    if container is not None:
        entity["container"] = container
    return entity


def test_graph_validation_accepts_single_parent_containment() -> None:
    payload = {"schema_version": 2, "entities": [_entity("root"), _entity("child", container="root")]}

    Graph().validate_graph(payload)


@pytest.mark.parametrize(
    ("entities", "message"),
    [
        ([_entity("child", container="missing")], "unknown container"),
        ([_entity("a", container="b"), _entity("b", container="a")], "containment cycle"),
    ],
)
def test_graph_validation_rejects_invalid_containment(
    entities: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Graph().validate_graph({"schema_version": 2, "entities": entities})


def test_graph_validation_rejects_noncanonical_children() -> None:
    root = _entity("root")
    root["children"] = ["child"]

    with pytest.raises(ValueError, match="canonical 'container'"):
        Graph().validate_graph(
            {"schema_version": 2, "entities": [root, _entity("child", container="root")]}
        )


def test_selected_skill_scope_summarizes_crossing_relationships() -> None:
    root = Path("/repo")
    skill = BlueprintNode("skills.alpha", "module", 5, root, root / "a.yaml", None, {})
    source = BlueprintNode(
        "skills.alpha.source",
        "behavioral_source",
        5,
        root,
        root / "as.yaml",
        None,
        {"gateway": {"language": "Markdown"}},
    )
    outside = BlueprintNode("skills.beta", "module", 5, root, root / "b.yaml", None, {})
    outside_source = BlueprintNode(
        "skills.beta.source", "behavioral_source", 5, root, root / "bs.yaml", None, {}
    )
    graph = RepositoryBlueprintGraph(
        nodes={node.node_id: node for node in (skill, source, outside, outside_source)},
        node_edges=(
            BlueprintEdge("contains-source", skill.node_id, source.node_id, 5),
            BlueprintEdge("contains-source", outside.node_id, outside_source.node_id, 5),
            BlueprintEdge("uses-source", source.node_id, outside_source.node_id, 5),
        ),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources={skill.node_id: (source.node_id,), outside.node_id: (outside_source.node_id,)},
        source_modules={source.node_id: skill.node_id, outside_source.node_id: outside.node_id},
        module_parents={skill.node_id: None, outside.node_id: None},
        module_children={skill.node_id: (), outside.node_id: ()},
        schema_version=6,
    )

    payload = build_payload_from_repository_graph(
        graph, repo_root=REPO_ROOT, skills=(skill.node_id,)
    )
    entities = {entity["id"]: entity for entity in payload["entities"]}

    assert source.node_id in entities
    assert entities[skill.node_id]["type"] == "module"
    assert entities[skill.node_id]["kind"] == "markdown"
    assert entities[skill.node_id]["category"] == "module:markdown"
    assert entities[source.node_id]["type"] == "behavioral_source"
    assert entities[source.node_id]["kind"] == "markdown"
    assert payload["detail_levels"][0]["id"] == "module"
    assert payload["detail_levels"][-1]["id"] == "interface"
    assert payload["ui"]["visibility"]["detail_level"] == "module"
    assert payload["ui"]["edge_styles"]["depends-on-source"] == {"color": "#d97706"}
    assert payload["ui"]["edge_styles"]["uses-interface"] == {
        "color": "#2563eb",
        "dash": "10 5",
    }
    assert entities[skill.node_id]["detail_level"] == "module"
    assert entities[source.node_id]["detail_level"] == "source"
    assert outside_source.node_id not in entities
    assert "boundary:skills.beta" in entities
    boundary_edges = entities[source.node_id]["connects_to"]
    assert len(boundary_edges) == 1
    boundary_edge = boundary_edges[0]
    assert boundary_edge["to"] == "boundary:skills.beta"
    assert boundary_edge["type"] == "depends-on-source"
    assert boundary_edge["implicit"] is True
    assert boundary_edge["metadata"] == {
        "boundary": True,
        "outside_id": outside_source.node_id,
        "outside_root": outside.node_id,
        "provenance": "node_edges",
        "relation": "depends-on-source",
        "required_version": 5,
    }


def _discovery(
    *,
    domain: str,
    topics: list[str],
    visibility: str,
    activated_by: list[str],
    persistent_modifier: bool,
) -> dict[str, object]:
    return {
        "mechanism": "skill",
        "catalog": {
            "domain": domain,
            "topics": topics,
            "visibility": visibility,
        },
        "activated_by": activated_by,
        "persistent_modifier": persistent_modifier,
    }


def _presentation_node_graph() -> RepositoryBlueprintGraph:
    declarations = {
        "alpha": {
            "discovery": _discovery(
                domain="research",
                topics=["mathematical-reasoning", "visualization"],
                visibility="featured",
                activated_by=["user-request"],
                persistent_modifier=False,
            )
        },
        "alpha.runtime": {},
        "beta": {
            "discovery": _discovery(
                domain="research",
                topics=["visualization"],
                visibility="hidden",
                activated_by=["user-request", "scheduled-job"],
                persistent_modifier=True,
            )
        },
    }
    nodes = {
        node_id: BlueprintNode(
            node_id,
            "module",
            5,
            REPO_ROOT,
            REPO_ROOT / "skills" / node_id / "blueprint.yaml",
            None,
            declaration,
        )
        for node_id, declaration in declarations.items()
    }
    return RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources={node_id: () for node_id in nodes},
        module_parents={"alpha": None, "alpha.runtime": "alpha", "beta": None},
        module_children={"alpha": ("alpha.runtime",), "alpha.runtime": (), "beta": ()},
        schema_version=6,
    )


def test_blueprint_payload_emits_first_class_presentation_nodes() -> None:
    payload = build_payload_from_repository_graph(
        _presentation_node_graph(), repo_root=REPO_ROOT
    )

    control = payload["ui"]["presentation_node_controls"][0]
    assert control["id"] == "skill-grouping"
    assert control["label"] == "Skill grouping"
    assert control["selector_label"] == "Group skills by"
    assert control["default_facet"] is None
    assert [item["id"] for item in control["facets"]] == [
        "discovery.domain",
        "discovery.topics",
        "discovery.activated_by",
        "discovery.persistent_modifier",
        "discovery.visibility",
    ]
    facets = {item["id"]: item for item in control["facets"]}
    assert facets["discovery.topics"]["activation"] == "multiple"
    assert all(
        facet["activation"] == "all"
        for facet_id, facet in facets.items()
        if facet_id != "discovery.topics"
    )
    nodes = {node["id"]: node for node in payload["presentation_nodes"]}
    assert nodes["discovery.domain.research"]["member_ids"] == ["alpha", "beta"]
    assert nodes["discovery.activated_by.user-request"]["member_ids"] == [
        "alpha",
        "beta",
    ]
    assert nodes["discovery.activated_by.scheduled-job"]["member_ids"] == ["beta"]
    assert nodes["discovery.visibility.hidden"]["member_ids"] == ["beta"]
    assert nodes["discovery.persistent_modifier.persistent"]["member_ids"] == [
        "beta"
    ]
    assert nodes["discovery.persistent_modifier.not-persistent"]["member_ids"] == [
        "alpha"
    ]
    assert all(
        node["presentation"]
        == {
            "form": "supernode",
            "tone": "subtle",
            "default_visibility": "hidden",
        }
        and node["interaction"]
        == {
            "selectable": True,
            "inspectable": True,
            "draggable": "members",
            "collapse_effect": "self",
        }
        for node in nodes.values()
    )
    assert all(
        "alpha.runtime" not in node["member_ids"] for node in nodes.values()
    )
    assert set(facets["discovery.domain"]["node_ids"]) == {
        "discovery.domain.research"
    }


def test_blueprint_presentation_nodes_respect_selected_skill_scope() -> None:
    payload = build_payload_from_repository_graph(
        _presentation_node_graph(), repo_root=REPO_ROOT, skills=("alpha",)
    )

    members = {
        member
        for node in payload["presentation_nodes"]
        for member in node["member_ids"]
    }
    assert members == {"alpha"}
    assert not any(member.startswith("boundary:") for member in members)

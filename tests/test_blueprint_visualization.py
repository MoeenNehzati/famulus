"""Behavioral tests for hierarchical blueprint visualization payloads."""

from __future__ import annotations

from pathlib import Path

import pytest

from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintNode,
    RepositoryBlueprintGraph,
)
from officina.common.visualization.from_blueprint.extractor import (
    build_payload_from_repository_graph,
)
from officina.common.visualization.graph import Graph


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
    payload = {"schema_version": 1, "entities": [_entity("root"), _entity("child", container="root")]}

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
        Graph().validate_graph({"schema_version": 1, "entities": entities})


def test_graph_validation_rejects_noncanonical_children() -> None:
    root = _entity("root")
    root["children"] = ["child"]

    with pytest.raises(ValueError, match="canonical 'container'"):
        Graph().validate_graph(
            {"schema_version": 1, "entities": [root, _entity("child", container="root")]}
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
        schema_version=5,
    )

    payload = build_payload_from_repository_graph(graph, repo_root=root, skills=(skill.node_id,))
    entities = {entity["id"]: entity for entity in payload["entities"]}

    assert source.node_id in entities
    assert entities[skill.node_id]["type"] == "module"
    assert entities[skill.node_id]["kind"] == "markdown"
    assert entities[skill.node_id]["category"] == "module:markdown"
    assert entities[source.node_id]["type"] == "behavioral_source"
    assert entities[source.node_id]["kind"] == "markdown"
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

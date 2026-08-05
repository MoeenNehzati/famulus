"""Project blueprint discovery metadata into generic presentation nodes.

The adapter owns blueprint vocabulary.  It emits ordinary renderer instances:
top-level skill roots become members of independently interactive supernodes.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...blueprint_graph import RepositoryBlueprintGraph
from ...configured_schema import load_configuration


FACETS = (
    ("discovery.domain", "Domain", "domain"),
    ("discovery.topics", "Topics", "topics"),
    ("discovery.activated_by", "Activated by", "activated_by"),
    (
        "discovery.persistent_modifier",
        "Persistent modifier",
        "persistent_modifier",
    ),
    ("discovery.visibility", "Catalog visibility", "visibility"),
)

_BOOLEAN_VALUES = (
    ("persistent", "Persistent modifier", True),
    ("not-persistent", "Not a persistent modifier", False),
)


def _label(value: str) -> str:
    """Create a readable fallback label while retaining the stable value ID.

    Intent
    ------
    Turn a configured metadata token into a concise human-facing title.

    Rationale
    ---------
    Presentation labels should stay deterministic even when configuration
    supplies only stable kebab-case identifiers.

    Pseudocode
    ----------
    - set label = capitalized value with hyphens replaced by spaces
    - return the readable label

    Wraps
    -----
    - none
    """
    return value.replace("-", " ").capitalize()


def _eligible_discovery(
    graph: RepositoryBlueprintGraph,
    included_module_ids: Collection[str],
) -> dict[str, Mapping[str, Any]]:
    """Return discovery declarations for canonical in-scope skill roots only.

    Intent
    ------
    Select the discovery records eligible to become presentation memberships.

    Rationale
    ---------
    Presentation membership is defined over canonical roots; including child
    modules would create overlapping structural ownership and duplicate motion.

    Pseudocode
    ----------
    - set included = included module ids
    - set result = eligible root skill discovery declarations in stable order
    - return the root-id to discovery mapping

    Wraps
    -----
    - none
    """
    included = set(included_module_ids)
    result: dict[str, Mapping[str, Any]] = {}
    for module_id in sorted(graph.module_parents):
        if module_id not in included or graph.module_parents[module_id] is not None:
            continue
        discovery = graph.nodes[module_id].declaration.get("discovery")
        if not isinstance(discovery, Mapping) or discovery.get("mechanism") != "skill":
            continue
        result[module_id] = discovery
    return result


def _configured_values(catalog: Mapping[str, Any], field: str) -> tuple[str, ...]:
    """Read a configured ordered vocabulary and ignore malformed loose values.

    Intent
    ------
    Normalize one configured presentation vocabulary into stable string tokens.

    Rationale
    ---------
    The adapter preserves repository configuration order while refusing scalar
    strings and malformed entries that cannot define presentation identities.

    Pseudocode
    ----------
    - set raw = requested catalog field
    - return empty when the value is not a non-string sequence
    - set entries = nonempty string values in configured order
    - return the entries as an immutable tuple

    Wraps
    -----
    - none
    """
    raw = catalog.get(field, ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(value for value in raw if isinstance(value, str) and value)


def _members_for_value(
    discoveries: Mapping[str, Mapping[str, Any]],
    *,
    field: str,
    value: object,
) -> list[str]:
    """Resolve scalar and multi-value discovery fields to ordered root IDs.

    Intent
    ------
    Find every eligible skill root assigned to one configured metadata value.

    Rationale
    ---------
    Discovery fields mix scalar, Boolean, and list representations, so one
    bounded matcher keeps emitted memberships consistent across every facet.

    Pseudocode
    ----------
    - set members = roots whose scalar or list discovery field matches value
    - return the ordered members

    Wraps
    -----
    - none
    """
    members: list[str] = []
    for module_id, discovery in discoveries.items():
        if field in {"domain", "topics", "visibility"}:
            catalog = discovery.get("catalog")
            raw = catalog.get(field) if isinstance(catalog, Mapping) else None
        else:
            raw = discovery.get(field)
        matches = value in raw if isinstance(raw, list) else raw == value
        if matches:
            members.append(module_id)
    return members


def build_presentation_nodes(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    included_module_ids: Collection[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build generic presentation nodes and controls from discovery metadata.

    Intent
    ------
    Project configured blueprint discovery values into the shared renderer's
    domain-neutral presentation-node JSON contract.

    Rationale
    ---------
    Keeping blueprint vocabulary in this adapter lets the core renderer handle
    first-class overlapping view nodes without acquiring blueprint semantics.
    Empty values are omitted, and repeated root membership remains a non-owning
    view relation rather than canonical graph ownership.

    Pseudocode
    ----------
    - set catalog = configured blueprint metadata vocabularies
    - set discoveries = eligible in-scope skill-root declarations
    - set nodes = nonempty metadata value presentation instances
    - set facets = controls referencing emitted nodes
    - return the node instances and optional grouping control

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._configured_values:
      why:
        computes: "Reads each configured ordered vocabulary used to enumerate facet values."

    InstantiationsFromRepo
    ----------------------
    ...configured_schema.load_configuration:
      why:
        constructs: "Builds the validated repository configuration used as the metadata vocabulary authority."
    ._eligible_discovery:
      why:
        constructs: "Builds the stable mapping of in-scope canonical roots to discovery declarations."
    ._label:
      why:
        constructs: "Builds readable labels for configured values that do not carry separate labels."
    ._members_for_value:
      why:
        constructs: "Builds each presentation node's ordered canonical root membership."
    """
    configuration = load_configuration(
        Path(repo_root) / "references" / "blueprint" / "config.yaml"
    )
    raw_catalog = configuration.get("blueprint_catalog", {})
    catalog = raw_catalog if isinstance(raw_catalog, Mapping) else {}
    discoveries = _eligible_discovery(graph, included_module_ids)
    nodes: list[dict[str, object]] = []
    facets: list[dict[str, object]] = []

    for facet_id, facet_label, field in FACETS:
        if field == "persistent_modifier":
            candidates = _BOOLEAN_VALUES
        else:
            config_field = {
                "domain": "domains",
                "activated_by": "activated_by",
            }.get(field, field)
            candidates = tuple(
                (value, _label(value), value)
                for value in _configured_values(catalog, config_field)
            )

        node_ids: list[str] = []
        for value_id, value_label, source_value in candidates:
            members = _members_for_value(
                discoveries, field=field, value=source_value
            )
            if not members:
                continue
            node_id = f"{facet_id}.{value_id}"
            node_ids.append(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "type": "group",
                    "short_title": value_label,
                    "position": len(nodes),
                    "member_ids": members,
                    "presentation": {
                        "form": "supernode",
                        "tone": "subtle",
                        "default_visibility": "hidden",
                    },
                    "interaction": {
                        "selectable": True,
                        "inspectable": True,
                        "draggable": "members",
                        "collapse_effect": "self",
                    },
                }
            )
        if node_ids:
            facets.append(
                {
                    "id": facet_id,
                    "label": facet_label,
                    "activation": "multiple" if field == "topics" else "all",
                    "node_ids": node_ids,
                }
            )

    controls: list[dict[str, object]] = []
    if facets:
        controls.append(
            {
                "id": "skill-grouping",
                "label": "Skill grouping",
                "selector_label": "Group skills by",
                "default_facet": None,
                "facets": facets,
            }
        )
    return nodes, controls


__all__ = ["FACETS", "build_presentation_nodes"]

"""Blueprint-specific visualization categories and projection policy."""

from __future__ import annotations

from typing import Any, Iterable

NODE_ROLE_LABELS = {
    "module": "Module", "behavioral_source": "Behavioral Source",
    "interface-export": "Exported Interface", "private-interface": "Source Interface",
    "out-of-scope": "Out of Scope",
}

NODE_ROLE_DESCRIPTIONS = {
    "module": "A blueprint-owned logical boundary containing child modules, behavioral sources, and exported interfaces. Its boundary expresses repository ownership and namespace structure rather than runtime execution.",
    "behavioral_source": "A blueprint-declared unit of behavior with its own gateway, owned content, and source interfaces. It is where interface use and direct behavioral-source dependencies are declared.",
    "interface-export": "A stable module-level interface address made available to authorized repository consumers. It binds either to a source interface in the module or to an exported interface of a child module.",
    "private-interface": "A behavioral source's concrete contract, including arguments, outputs, effects, and execution rules. It remains source-scoped unless a module export binds a public logical address to it.",
    "out-of-scope": "A compact proxy representing repository nodes omitted from the selected visualization scope. Its incident edges preserve cross-scope relationships without expanding the omitted module and all of its contents.",
}

DETAIL_LEVELS = [
    {"id": "module", "label": "Modules", "description": "Show repository modules and out-of-scope boundaries."},
    {"id": "source", "label": "Sources", "description": "Also show behavioral sources owned by modules."},
    {"id": "interface", "label": "Interfaces", "description": "Show the complete module, source, and interface structure."},
]

EDGE_STYLES = {
    "depends-on-source": {"color": "#d97706"},
    "uses-interface": {"color": "#2563eb", "dash": "10 5"},
    "binds-interface": {"color": "#059669", "dash": "3 4"},
    "helper-dependency": {"color": "#9333ea", "dash": "12 4 2 4"},
    "certificate-indirectly-depends": {"color": "#be123c", "dash": "2 5"},
    "facades-child-export": {"color": "#0891b2", "dash": "14 4 3 4"},
    "facades-implementing-source": {"color": "#0891b2", "dash": "14 4 3 4"},
    "exposes-child-interface": {"color": "#0f766e", "dash": "8 4 2 4"},
    "indirectly-depends-on-source": {"color": "#d97706", "dash": "5 5"},
    "indirectly-uses-interface": {"color": "#2563eb", "dash": "5 5"},
    "indirectly-binds-interface": {"color": "#059669", "dash": "5 5"},
    "indirectly-depends-on": {"color": "#64748b", "dash": "5 5"},
}

RELATION_SEMANTICS = {
    "transformations": {"node_omission": {"rules": [
        {"id": "hidden-dependency-to-source", "causes": ["user-hidden"], "left_types": ["depends-on-source", "indirectly-depends-on-source", "uses-interface", "indirectly-uses-interface"], "right_types": ["depends-on-source"], "outcomes": [{"type": "indirectly-depends-on-source", "fidelity": "exact"}]},
        {"id": "hidden-dependency-to-interface", "causes": ["user-hidden"], "left_types": ["depends-on-source", "indirectly-depends-on-source", "uses-interface", "indirectly-uses-interface"], "right_types": ["uses-interface"], "outcomes": [{"type": "indirectly-uses-interface", "fidelity": "exact"}]},
        {"id": "hidden-interface-binding", "causes": ["user-hidden"], "left_types": ["uses-interface", "indirectly-uses-interface"], "right_types": ["binds-interface"], "outcomes": [{"type": "indirectly-uses-interface", "fidelity": "exact"}, {"type": "indirectly-depends-on", "fidelity": "degraded"}]},
        {"id": "hidden-binding-layer", "causes": ["user-hidden"], "left_types": ["binds-interface", "indirectly-binds-interface"], "right_types": ["binds-interface"], "outcomes": [{"type": "indirectly-binds-interface", "fidelity": "exact"}]},
        {"id": "hidden-dependency-through-other-alias", "causes": ["user-hidden"], "left_types": ["depends-on-source", "indirectly-depends-on-source", "helper-dependency", "certificate-indirectly-depends"], "right_types": ["binds-interface", "indirectly-binds-interface", "facades-child-export", "facades-implementing-source", "exposes-child-interface"], "outcomes": [{"type": "indirectly-depends-on", "fidelity": "degraded"}]},
        {"id": "hidden-use-through-other-alias", "causes": ["user-hidden"], "left_types": ["uses-interface", "indirectly-uses-interface"], "right_types": ["indirectly-binds-interface", "facades-child-export", "facades-implementing-source", "exposes-child-interface"], "outcomes": [{"type": "indirectly-depends-on", "fidelity": "degraded"}]},
        {"id": "hidden-alias-to-dependency", "causes": ["user-hidden"], "left_types": ["binds-interface", "indirectly-binds-interface", "facades-child-export", "facades-implementing-source", "exposes-child-interface"], "right_types": ["depends-on-source", "indirectly-depends-on-source", "uses-interface", "indirectly-uses-interface", "helper-dependency", "certificate-indirectly-depends"], "outcomes": [{"type": "indirectly-depends-on", "fidelity": "degraded"}]},
        {"id": "continue-hidden-coarse-dependency", "causes": ["user-hidden"], "left_types": ["indirectly-depends-on"], "right_types": ["depends-on-source", "uses-interface", "binds-interface", "facades-child-export", "facades-implementing-source", "exposes-child-interface", "helper-dependency", "certificate-indirectly-depends"], "outcomes": [{"type": "indirectly-depends-on", "fidelity": "degraded"}]},
    ]}},
    "subsumptions": [
        {"stronger_type": "depends-on-source", "weaker_types": ["indirectly-depends-on-source"]},
        {"stronger_type": "indirectly-depends-on-source", "weaker_types": ["indirectly-depends-on"]},
        {"stronger_type": "uses-interface", "weaker_types": ["indirectly-uses-interface"]},
        {"stronger_type": "indirectly-uses-interface", "weaker_types": ["indirectly-depends-on"]},
        {"stronger_type": "binds-interface", "weaker_types": ["indirectly-binds-interface"]},
        {"stronger_type": "helper-dependency", "weaker_types": ["indirectly-depends-on"]},
        {"stronger_type": "certificate-indirectly-depends", "weaker_types": ["indirectly-depends-on"]},
    ],
}


def category_id(role: str, kind: str) -> str:
    return f"{role}:{kind}"


def _description(category: str) -> str:
    role, separator, kind = category.partition(":")
    base = NODE_ROLE_DESCRIPTIONS.get(role, "A logical node category declared by the adapter.")
    kind_label = kind.replace("+", " + ").replace("_", " ").replace("-", " ").title()
    return f"{base} Its color identifies the {kind_label} gateway kind." if separator and role != "out-of-scope" else base


def build_node_categories(category_ids: Iterable[str]) -> list[dict[str, Any]]:
    ids = sorted(set(category_ids))
    parents = sorted({item.partition(":")[0] for item in ids if ":" in item})
    result = [{"id": parent, "label": NODE_ROLE_LABELS.get(parent, parent.replace("-", " ").title()), "description": _description(parent)} for parent in parents]
    result.extend({"id": item, "label": item.partition(":")[2].replace("+", " + ").replace("_", " ").replace("-", " ").title() if ":" in item else NODE_ROLE_LABELS.get(item, item.title()), "description": _description(item), **({"parent": item.partition(":")[0]} if ":" in item else {})} for item in ids)
    return result


def build_edge_categories(entities: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: dict[str, dict[str, Any]] = {}
    for entity in entities:
        for edge in entity["connects_to"]:
            examples.setdefault(str(edge["type"]), edge)
    parents = sorted({edge_type.partition(":")[0] for edge_type in examples if ":" in edge_type})
    result = [{"id": parent, "label": parent.replace("_", " ").replace("-", " ").title(), "description": f"Relationships grouped under {parent.replace('_', ' ').replace('-', ' ')}."} for parent in parents]
    result.extend({"id": edge_type, "label": str(edge.get("label") or edge_type).split(":", 1)[-1], "description": str(edge.get("description") or "A typed repository relationship."), **({"parent": edge_type.partition(":")[0]} if ":" in edge_type else {})} for edge_type, edge in sorted(examples.items()))
    indirect = {
        "indirectly-depends-on-source": ("Indirectly depends on source", "A visible source reaches another source through one or more omitted source dependencies; inspect the edge for its canonical witness path."),
        "indirectly-uses-interface": ("Indirectly uses interface", "A visible node reaches an interface through an omitted implementation; inspect the edge for the resolved implementation and canonical witness path."),
        "indirectly-binds-interface": ("Indirectly binds interface", "A visible interface binds to another interface through one or more omitted binding layers; inspect the edge for the canonical binding path."),
        "indirectly-depends-on": ("Indirectly depends", "A directed dependency-bearing witness reaches the target through omitted nodes, but the adapter cannot retain a more specific dependency kind."),
    }
    present = {item["id"] for item in result}
    result.extend({"id": edge_type, "label": label, "description": description} for edge_type, (label, description) in indirect.items() if edge_type not in present)
    return result


def build_relation_semantics(edge_types: Iterable[str]) -> dict[str, Any]:
    """Specialize repository-wide relation semantics to one scoped payload.

    Canonical edge types may appear on the right side of a transition. Exact or
    fallback results may subsequently appear on the left side. Removing inert
    rule branches keeps scoped payloads strict without advertising relation
    categories that their canonical graph cannot produce.
    """
    canonical_types = {str(edge_type) for edge_type in edge_types}
    state_types = set(canonical_types)
    rules = RELATION_SEMANTICS["transformations"]["node_omission"]["rules"]
    changed = True
    while changed:
        changed = False
        for rule in rules:
            if state_types.intersection(map(str, rule["left_types"])) and canonical_types.intersection(map(str, rule["right_types"])):
                for outcome in rule["outcomes"]:
                    if str(outcome["type"]) not in state_types:
                        state_types.add(str(outcome["type"]))
                        changed = True

    specialized = []
    for rule in rules:
        left_types = sorted(state_types.intersection(map(str, rule["left_types"])))
        right_types = sorted(canonical_types.intersection(map(str, rule["right_types"])))
        if left_types and right_types:
            specialized.append({**rule, "left_types": left_types, "right_types": right_types})

    full_weaker: dict[str, set[str]] = {}
    for rule in RELATION_SEMANTICS["subsumptions"]:
        full_weaker.setdefault(str(rule["stronger_type"]), set()).update(map(str, rule["weaker_types"]))
    changed = True
    while changed:
        changed = False
        for stronger, weaker_types in full_weaker.items():
            for weaker_type in list(weaker_types):
                for transitive in full_weaker.get(weaker_type, set()):
                    if transitive not in weaker_types:
                        weaker_types.add(transitive)
                        changed = True

    subsumptions = []
    for stronger in sorted(state_types):
        weaker = sorted(state_types.intersection(full_weaker.get(stronger, set())))
        if weaker:
            subsumptions.append({"stronger_type": stronger, "weaker_types": weaker})
    return {
        "transformations": {"node_omission": {"rules": specialized}},
        "subsumptions": subsumptions,
    }


__all__ = ["DETAIL_LEVELS", "EDGE_STYLES", "RELATION_SEMANTICS", "build_edge_categories", "build_node_categories", "build_relation_semantics", "category_id"]

#!/usr/bin/env python3
"""Convert resolved mathematical semantics into canonical visualization JSON.

The compiler is intentionally source-blind. It accepts the LLM-authored semantic
IR, the authoritative pooled inventory from the same run, and the skill-owned
presentation base. It expands qualified source handles, derives presentation
fields, and never identifies mathematical blocks or infers edges.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.visualization.base_renderer import BaseRenderer

try:
    from ._ir_validator import validate_extract_reconciliation
    from ._apply_proof_digest import validate_normalized_semantic_profile
except ImportError:  # pragma: no cover - supports direct script execution
    from _ir_validator import validate_extract_reconciliation
    from _apply_proof_digest import validate_normalized_semantic_profile


BASE_PAYLOAD_PATH = Path(__file__).resolve().parents[2] / "resources" / "graph-base.json"


EXTENSION_COLORS = (
    "#884c8c",
    "#287271",
    "#bc6c25",
    "#3a5a99",
    "#9c6644",
    "#4f772d",
    "#7b2cbf",
    "#006d77",
)


def load_json_object(path: Path, label: str) -> dict:
    """Read one UTF-8 JSON object or fail with an artifact-specific message.

    Intent
    ------
    Centralize strict file decoding for semantic IR, schemas, and presentation
    bases used by the command-line interface.

    Rationale
    ---------
    A malformed or non-object artifact must stop deterministic compilation
    before any destination is replaced.

    Pseudocode
    ----------
    - read the selected path as UTF-8
    - decode JSON
    - require a top-level object
    - return the decoded object
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def load_base_payload() -> dict:
    """Load the fixed math-dependency canonical JSON base."""

    return load_json_object(BASE_PAYLOAD_PATH, "math dependency graph base")


def format_location(location: dict) -> str:
    """Format one already-decided source range without consulting source text."""
    source_file = str(location["source_file"])
    start_line = int(location["start_line"])
    end_line = int(location["end_line"])
    if end_line < start_line:
        raise ValueError(
            f"source range ends before it starts: {source_file}:{start_line}-{end_line}"
        )
    if start_line == end_line:
        return f"{source_file}:{start_line}"
    return f"{source_file}:{start_line}-{end_line}"


def stable_extension_color(category_id: str) -> str:
    """Choose a reproducible presentation color for one vocabulary extension.

    Intent
    ------
    Let the LLM introduce an author-visible environment kind without spending
    semantic-extraction effort on arbitrary color selection.

    Rationale
    ---------
    A cryptographic digest is stable across processes and platforms, unlike
    Python's randomized built-in hash.

    Pseudocode
    ----------
    - digest the category id as UTF-8
    - interpret the first digest bytes as an integer
    - select one fixed accessible palette entry modulo palette length
    """
    digest = hashlib.sha256(category_id.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(EXTENSION_COLORS)
    return EXTENSION_COLORS[index]


def unique_extension_color(category_id: str, categories: list[dict]) -> str:
    """Choose a stable color not already assigned to another category.

    The digest-selected palette position is the preferred color. Linear probing
    makes sibling extensions distinct while retaining deterministic results for a
    fixed category order. If the fixed palette is exhausted, salted digest bytes
    provide additional hex colors until an unused value is found.
    """
    used = {str(item.get("color", "")).lower() for item in categories}
    preferred = stable_extension_color(category_id)
    start = EXTENSION_COLORS.index(preferred)
    for offset in range(len(EXTENSION_COLORS)):
        candidate = EXTENSION_COLORS[(start + offset) % len(EXTENSION_COLORS)]
        if candidate.lower() not in used:
            return candidate
    salt = 0
    while True:
        digest = hashlib.sha256(f"{category_id}:{salt}".encode("utf-8")).hexdigest()
        candidate = f"#{digest[:6]}"
        if candidate.lower() not in used:
            return candidate
        salt += 1


def validate_semantic_payload(payload: dict, inventory: dict | None = None) -> None:
    """Validate schema-v2 semantics against their authoritative pooled inventory.

    Semantic IR intentionally retains qualified evidence, reference, unresolved,
    and hint handles instead of copying deterministic source coordinates.  It is
    therefore not certifiable in isolation; compilation must receive the pooled
    artifact from the same run.
    """

    validate_normalized_semantic_profile(payload)
    if inventory is None:
        raise ValueError("pooled inventory is required for semantic validation")
    validate_extract_reconciliation(payload, inventory)


def _expanded_location(
    compact: list[int], files: list[str], *, role: str
) -> dict:
    """Expand one validated pooled coordinate into compiler source metadata."""

    file_index, start_line, end_line = compact
    return {
        "role": role,
        "source_file": files[file_index],
        "start_line": start_line,
        "end_line": end_line,
    }


def _location_role(evidence_role: str) -> str:
    """Map inventory evidence vocabulary to canonical inspector roles."""

    return {
        "statement": "statement",
        "proof-use": "proof",
        "scope": "related",
        "explicit-reference": "evidence",
        "dependency-prose": "evidence",
    }[evidence_role]


def _semantic_locations(
    entity: dict,
    semantic_payload: dict,
    inventory: dict,
) -> list[dict]:
    """Derive complete entity locations from candidate and unresolved evidence."""

    files = inventory["files"]
    candidates = {item["id"]: item for item in inventory["candidates"]}
    evidence = {item["id"]: item for item in inventory["evidence"]}
    unresolved = {item["key"]: item for item in inventory["unresolved_entities"]}
    locations: list[dict] = [
        _expanded_location(entity["statement_location"], files, role="statement")
    ]
    evidence_ids: list[str] = []
    for candidate_id in entity["candidate_ids"]:
        candidate = candidates[candidate_id]
        locations.append(
            _expanded_location(candidate["location"], files, role="statement")
        )
        evidence_ids.extend(candidate["evidence_ids"])
    for resolution in semantic_payload["unresolved_resolutions"]:
        if (
            resolution.get("disposition") in {"matched", "created"}
            and resolution.get("entity_id") == entity["id"]
        ):
            record = unresolved.get(resolution["unresolved_id"])
            if record is not None:
                evidence_ids.extend(record["evidence_ids"])
    for evidence_id in evidence_ids:
        record = evidence[evidence_id]
        locations.append(
            _expanded_location(
                record["location"],
                files,
                role=_location_role(record["role"]),
            )
        )
    deduplicated: list[dict] = []
    seen: set[tuple[str, str, int, int]] = set()
    for location in locations:
        key = (
            location["role"],
            location["source_file"],
            location["start_line"],
            location["end_line"],
        )
        if key not in seen:
            seen.add(key)
            deduplicated.append(location)
    if not deduplicated:
        raise ValueError(f"semantic entity has no registered source location: {entity['id']}")
    return deduplicated


def _source_position(locations: list[dict], files: list[str]) -> tuple[int, int, int]:
    """Return the first deterministic document coordinate for entity ordering."""

    file_indexes = {source_file: index for index, source_file in enumerate(files)}
    return min(
        (
            file_indexes[location["source_file"]],
            location["start_line"],
            location["end_line"],
        )
        for location in locations
    )


def _source_identity(entity: dict, inventory: dict) -> tuple[str | None, str | None]:
    """Return explicit identity or deterministic source-visible candidate fallbacks."""

    candidates = {item["id"]: item for item in inventory["candidates"]}
    ordered = sorted(
        (candidates[candidate_id] for candidate_id in entity["candidate_ids"]),
        key=lambda candidate: (tuple(candidate["location"]), candidate["id"]),
    )
    ref = entity.get("ref")
    title = entity.get("title")
    if not ref:
        ref = next(
            (
                label
                for candidate in ordered
                for label in candidate.get("labels", [])
                if isinstance(label, str) and label
            ),
            None,
        )
    if not title:
        title = next(
            (
                candidate["visible_title"]
                for candidate in ordered
                if isinstance(candidate.get("visible_title"), str)
                and candidate["visible_title"]
            ),
            None,
        )
    return ref, title


def category_for_entity(entity: dict, categories: list[dict]) -> str:
    """Return or deterministically add the category selected by type and kind.

    Intent
    ------
    Translate LLM-owned mathematical classification into presentation vocabulary
    without asking the LLM to reproduce category boilerplate.

    Rationale
    ---------
    Known base kinds map directly. Author-visible extensions inherit their
    family's shape and receive a stable compiler-selected color. A new root uses
    the neutral ellipse shape because no base family supplies one.

    Pseudocode
    ----------
    - construct ``type:kind`` when kind exists, otherwise use type
    - return an existing category when present
    - ensure an extension parent exists, adding a neutral root if necessary
    - append a child extension inheriting the parent shape when kind exists
    - return the appended category id
    """
    entity_type = str(entity["type"])
    kind = entity.get("kind")
    category_id = f"{entity_type}:{kind}" if kind else entity_type
    by_id = {str(item["id"]): item for item in categories}
    if category_id in by_id:
        return category_id

    label = str(entity.get("category_label") or kind or entity_type).replace("-", " ").strip()
    label = label[:1].upper() + label[1:]
    if entity_type not in by_id:
        root = {
            "id": entity_type,
            "label": entity_type.replace("-", " ").strip().title(),
            "shape": "ellipse",
            "color": unique_extension_color(entity_type, categories),
        }
        categories.append(root)
        by_id[entity_type] = root
    if not kind:
        return entity_type

    parent = by_id[entity_type]
    categories.append(
        {
            "id": category_id,
            "label": label,
            "parent": entity_type,
            "shape": parent.get("shape", "ellipse"),
            "color": unique_extension_color(category_id, categories),
        }
    )
    return category_id


def compile_semantic_graph(
    semantic_payload: dict,
    base_payload: dict,
    inventory: dict,
) -> dict:
    """Compile complete source-dependent decisions into canonical graph JSON.

    Intent
    ------
    Remove visualization-schema bookkeeping from LLM extraction while retaining
    a deterministic, auditable handoff into the shared renderer.

    Rationale
    ---------
    The resolved IR already contains every mathematical judgment. Compilation
    therefore consists only of sorting, field derivation, vocabulary expansion,
    edge nesting, presentation-base copying, and mechanical validation.

    Pseudocode
    ----------
    - validate the resolved semantic payload against its pooled inventory
    - derive source locations and sort entities by pooled source coordinates
    - derive categories, positions, inspector provenance, and empty edge lists
    - translate each relationship into its source entity's ``connects_to`` list
    - attach document, audit metadata, and MathJax renderer configuration
    - validate the math profile and canonical visualization schema
    - return the canonical object

    Wraps
    -----
    - validate_semantic_payload
    - officina.visualization.base_renderer.BaseRenderer.validate
    """
    validate_semantic_payload(semantic_payload, inventory)
    canonical = deepcopy(base_payload)
    categories = canonical["categories"]
    compiled_by_id: dict[str, dict] = {}
    compiled_entities: list[dict] = []

    located_entities = [
        (
            semantic_entity,
            _semantic_locations(semantic_entity, semantic_payload, inventory),
        )
        for semantic_entity in semantic_payload["entities"]
    ]
    located_entities.sort(
        key=lambda item: (
            tuple(item[0]["statement_location"]),
            item[0]["id"],
        )
    )
    for position, (semantic_entity, locations) in enumerate(located_entities):
        category_id = category_for_entity(semantic_entity, categories)
        fields: list[dict] = []
        ref, title = _source_identity(semantic_entity, inventory)
        if title:
            fields.append({"label": "Title", "value": title, "format": "text"})
        if ref:
            fields.append(
                {"label": "Reference", "value": ref, "format": "code"}
            )
        for location in locations:
            fields.append(
                {"label": "Location", "value": format_location(location), "format": "path"}
            )
        fields.append(
            {"label": "Provenance", "value": semantic_entity["source"], "format": "text"}
        )
        compiled = {
            "id": semantic_entity["id"],
            "type": semantic_entity["type"],
            "category": category_id,
            "short_title": semantic_entity["short_title"],
            "position": position,
            "description": semantic_entity["description"],
            "source": semantic_entity["source"],
            "details": {"sections": [{"title": "Source", "fields": fields}]},
            "connects_to": [],
        }
        identity = {"title": title, "ref": ref}
        for optional in ("kind", "title", "ref"):
            if optional in identity and identity[optional]:
                compiled[optional] = identity[optional]
                continue
            if semantic_entity.get(optional):
                compiled[optional] = semantic_entity[optional]
        compiled_entities.append(compiled)
        compiled_by_id[compiled["id"]] = compiled

    evidence = {item["id"]: item for item in inventory["evidence"]}
    for relationship in semantic_payload["relationships"]:
        evidence_locations = [
            _expanded_location(
                evidence[evidence_id]["location"],
                inventory["files"],
                role=_location_role(evidence[evidence_id]["role"]),
            )
            for evidence_id in relationship["evidence_ids"]
        ]
        edge = {
            "to": relationship["to"],
            "type": relationship["type"],
            "description": relationship["description"],
            "evidence": ", ".join(format_location(item) for item in evidence_locations),
            "implicit": relationship["implicit"],
        }
        if relationship.get("confidence"):
            edge["confidence"] = relationship["confidence"]
        compiled_by_id[relationship["from"]]["connects_to"].append(edge)

    canonical["document"] = deepcopy(semantic_payload["document"])
    canonical["metadata"] = {
        "extraction": "Compiled from resolved semantic IR; direct dependencies only",
        "semantic_exclusions": deepcopy(semantic_payload["exclusions"]),
        "evidence_gaps": deepcopy(semantic_payload["gaps"]),
    }
    if semantic_payload.get("edgeless_justification"):
        canonical["metadata"]["edgeless_justification"] = semantic_payload[
            "edgeless_justification"
        ]
    canonical["renderer_dependencies"] = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {"input": "tex", "output": "svg"},
        }
    ]
    canonical["entities"] = compiled_entities
    BaseRenderer().validate(canonical)
    return canonical


def write_json_atomic(payload: dict, out_path: Path) -> None:
    """Write one validated JSON object by atomic sibling replacement."""
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            temporary_path = Path(handle.name)
        temporary_path.replace(out_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: Iterable[str] | None = None) -> None:
    """Compile one resolved semantic IR file and print a machine JSON report."""
    parser = argparse.ArgumentParser(
        description="Compile resolved mathematical semantics into canonical graph JSON."
    )
    parser.add_argument("semantic_ir", help="Resolved semantic-graph IR JSON")
    parser.add_argument(
        "--inventory",
        required=True,
        help="Authoritative pooled inventory whose qualified handles the semantic IR reconciles",
    )
    parser.add_argument("--base", help="Presentation base JSON; defaults to the skill base")
    parser.add_argument("--out", required=True, help="Canonical graph JSON destination")
    args = parser.parse_args(list(argv) if argv is not None else None)

    semantic_path = Path(args.semantic_ir).resolve()
    inventory_path = Path(args.inventory).resolve()
    base_path = Path(args.base).resolve() if args.base else BASE_PAYLOAD_PATH
    out_path = Path(args.out).resolve()
    semantic_payload = load_json_object(semantic_path, "semantic graph IR")
    inventory = load_json_object(inventory_path, "pooled inventory")
    base_payload = load_json_object(base_path, "math dependency graph base")
    canonical = compile_semantic_graph(semantic_payload, base_payload, inventory)
    canonical["metadata"]["semantic_ir_sha256"] = hashlib.sha256(
        semantic_path.read_bytes()
    ).hexdigest()
    write_json_atomic(canonical, out_path)
    edge_count = sum(len(entity.get("connects_to", [])) for entity in canonical["entities"])
    print(
        json.dumps(
            {
                "semantic_ir": str(semantic_path),
                "inventory": str(inventory_path),
                "base": str(base_path),
                "out": str(out_path),
                "entities": len(canonical["entities"]),
                "edges": edge_count,
            },
            indent=2,
        )
    )


class Interface(PythonArgvMachineInterface):
    """Expose deterministic semantic compilation through the machine protocol."""

    prog = "semantic_to_canonical_json.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validate and pool completed inventory chunks.

Inventory workers own discovery inside source chunks. This module validates
their fragment vocabulary, checks ownership and accounting mechanically, and
qualifies fragment-local handles for the document-wide semantic worker.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Iterable

import jsonschema

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = SKILL_DIR / "schemas"
INVENTORY_SCHEMA_PATH = SCHEMAS_DIR / "inventory.schema.json"

class ValidationReportError(ValueError):
    """Carry safe structured validation diagnostics across durable handoffs."""

    def __init__(
        self,
        label: str,
        diagnostics: list[dict],
        *,
        repairable: bool,
        display_messages: list[str] | None = None,
    ) -> None:
        self.diagnostics = deepcopy(diagnostics)
        self.repairable = repairable
        summaries = display_messages or [item["message"] for item in diagnostics]
        super().__init__(label + (":\n- " + "\n- ".join(summaries) if summaries else ""))


class WorkerFragmentLoadError(ValidationReportError):
    """Identify one unreadable worker artifact without retaining its contents."""

    def __init__(self, fragment_path: Path, diagnostic: dict) -> None:
        self.fragment_path = fragment_path.resolve()
        super().__init__(
            "invalid worker fragment",
            [diagnostic],
            repairable=False,
        )


class InventoryFragmentValidationError(ValueError):
    """Identify an inventory fragment whose validation failure can be retried."""

    def __init__(self, chunk_id: str, message: str) -> None:
        self.chunk_id = chunk_id
        super().__init__(message)


def _safe_schema_diagnostic(error: jsonschema.ValidationError, *, label: str) -> dict:
    """Project a library validation error without persisting its instance value."""

    path_parts = [str(part) for part in error.absolute_path]
    record_path = ".".join(path_parts) or "<root>"
    keyword = str(error.validator or "schema")
    if (
        keyword == "not"
        and path_parts
        and path_parts[0] == "unresolved_resolutions"
    ):
        summary = f"{label} {record_path}: entity_id is forbidden for a nonretained disposition"
        fields = ["entity_id"]
    elif keyword == "required" and isinstance(error.instance, dict):
        missing = sorted(set(error.validator_value) - set(error.instance))
        summary = f"{label} {record_path}: {', '.join(missing)} is required"
        fields = missing
    else:
        summary = f"{label} {record_path}: violates {keyword}"
        fields = []
    diagnostic = {
        "code": f"schema-{keyword.lower()}",
        "path": path_parts,
        "message": summary,
    }
    if fields:
        diagnostic["fields"] = fields
    return diagnostic


def _safe_cross_diagnostic(message: str) -> dict:
    """Reduce a detailed invariant failure to a stable non-content-bearing code."""

    phrases = (
        "unknown candidate endpoint",
        "unknown unresolved handle",
        "unknown relationship source",
        "unknown relationship target",
        "unknown evidence handle",
        "unknown reference handle",
        "unknown hint handle",
        "unknown inventory gap",
        "unreconciled candidate",
        "unreconciled unresolved handle",
        "unreconciled hint handle",
        "unreconciled reference handle",
        "unreconciled inventory gap",
        "candidate reconciled more than once",
        "unresolved handle reconciled more than once",
        "hint handle reconciled more than once",
        "reference handle reconciled more than once",
        "inventory gap reconciled more than once",
        "inventory candidate_count does not match candidate_ids",
        "semantic inventory candidate_ids do not exactly match pooled inventory",
        "duplicate direct relationship",
        "direct endpoint pair",
        "self-edge is not a direct relationship",
        "low-confidence relationship must be reported as a gap",
        "edgeless multi-entity semantic graph requires a source-grounded justification",
        "isolated semantic entity",
        "description contains a raw TeX environment wrapper",
        "explicit/inferred source is inconsistent",
        "required external-result candidate",
        "candidate-free entity",
        "retains unknown entity",
        "created unresolved handle",
        "does not retain two resolved endpoints",
        "does not retain resolved hint endpoints",
        "explicit/inferred mismatch",
        "has no outgoing supports edge",
        "has no incoming illustrated-by edge",
        "pooled inventory",
        "extract reconciliation requires",
    )
    lowered = message.lower()
    phrase = next(
        (item for item in phrases if item.lower() in lowered),
        "validation invariant failed",
    )
    code = re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")
    return {"code": code, "path": [], "message": phrase}


def canonical_json_bytes(payload: dict) -> bytes:
    """Return the stable serialized form used for diagnostic size measurement."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _inventory_validator() -> jsonschema.protocols.Validator:
    """Build the inventory-fragment schema validator from the owned contract."""

    schema = json.loads(INVENTORY_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def validate_inventory_fragment(fragment: dict) -> None:
    """Reject a worker output that is not an inventory version-3 fragment."""

    _inventory_validator().validate(fragment)


def _qualified(chunk_id: str, local_id: str) -> str:
    """Make one fragment-local handle unambiguous in the pooled artifact."""

    return f"{chunk_id}::{local_id}"


def _require_unique_ids(records: list[dict], *, field: str, label: str) -> set[str]:
    """Return record identifiers while rejecting same-kind local handle reuse."""

    ids = [str(record[field]) for record in records]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate {label} id: {duplicates[0]}")
    return set(ids)


def _chunk_indexes(chunk_manifest: dict) -> tuple[list[dict], dict[str, dict]]:
    """Validate inventory ownership metadata and preserve its source order."""

    if chunk_manifest.get("mode") != "inventory":
        raise ValueError("inventory pooling requires an inventory chunk manifest")
    source_files = chunk_manifest.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise ValueError("inventory chunk manifest requires source file identities")
    for source in source_files:
        if not isinstance(source, dict):
            raise ValueError("inventory source identity must be an object")
        source_path = Path(str(source.get("path", ""))).resolve()
        source_sha256 = source.get("sha256")
        if (
            not source_path.is_file()
            or not isinstance(source_sha256, str)
            or hashlib.sha256(source_path.read_bytes()).hexdigest() != source_sha256
        ):
            raise ValueError("inventory source identity changed after chunk extraction")
    chunks = chunk_manifest.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("inventory chunk manifest requires a nonempty chunks array")
    by_id: dict[str, dict] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("inventory chunk manifest contains a non-object chunk")
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("inventory chunk manifest requires nonempty chunk ids")
        if chunk_id in by_id:
            raise ValueError(f"duplicate inventory chunk id: {chunk_id}")
        spans = chunk.get("spans")
        if not isinstance(spans, list) or not spans:
            raise ValueError(f"inventory chunk {chunk_id!r} requires owned spans")
        if not isinstance(chunk.get("anchors"), list):
            raise ValueError(f"inventory chunk {chunk_id!r} requires an anchors array")
        if not isinstance(chunk.get("chunk_path"), str) or not chunk["chunk_path"]:
            raise ValueError(f"inventory chunk {chunk_id!r} requires chunk_path")
        chunk_path = Path(chunk["chunk_path"]).resolve()
        chunk_sha256 = chunk.get("chunk_sha256")
        if (
            not chunk_path.is_file()
            or not isinstance(chunk_sha256, str)
            or hashlib.sha256(chunk_path.read_bytes()).hexdigest() != chunk_sha256
        ):
            raise ValueError(
                f"inventory chunk identity changed after extraction: {chunk_id}"
            )
        by_id[chunk_id] = chunk
    return chunks, by_id


def _spans_by_file(chunk: dict) -> dict[str, list[tuple[int, int]]]:
    """Project one chunk's declared inclusive source ownership by file."""

    spans_by_file: dict[str, list[tuple[int, int]]] = {}
    for span in chunk["spans"]:
        if not isinstance(span, dict):
            raise ValueError("inventory chunk span must be an object")
        source_file = span.get("source_file")
        start_line = span.get("start_line")
        end_line = span.get("end_line")
        if (
            not isinstance(source_file, str)
            or not source_file
            or not isinstance(start_line, int)
            or not isinstance(end_line, int)
            or start_line < 1
            or end_line < start_line
        ):
            raise ValueError("inventory chunk span is invalid")
        spans_by_file.setdefault(source_file, []).append((start_line, end_line))
    return spans_by_file


def _location_is_owned(location: object, fragment: dict, spans_by_file: dict[str, list[tuple[int, int]]]) -> bool:
    """Return whether a compact fragment location belongs to its assigned span."""

    if not isinstance(location, list) or len(location) != 3:
        return False
    file_index, start_line, end_line = location
    if (
        not isinstance(file_index, int)
        or not isinstance(start_line, int)
        or not isinstance(end_line, int)
        or start_line < 1
        or end_line < start_line
        or file_index < 0
        or file_index >= len(fragment["files"])
    ):
        return False
    source_file = fragment["files"][file_index]
    return any(
        start <= start_line and end_line <= end
        for start, end in spans_by_file.get(source_file, [])
    )


def _check_owned_locations(fragment: dict, chunk: dict) -> None:
    """Reject worker findings that point outside the fragment's source ownership."""

    spans_by_file = _spans_by_file(chunk)
    for source_file in fragment["files"]:
        if source_file not in spans_by_file:
            raise ValueError(
                f"inventory fragment names file outside its owned chunk: {source_file}"
            )
    locations: list[tuple[str, object]] = []

    def collect_scope(label: str, record: dict) -> None:
        scope = record.get("scope_hint")
        if scope is not None:
            locations.append((f"{label} scope start", scope["starts_at"]))
            if "ends_at" in scope:
                locations.append((f"{label} scope end", scope["ends_at"]))

    def collect_endpoint(endpoint: dict) -> None:
        unresolved = endpoint.get("unresolved")
        if unresolved is not None:
            collect_scope("unresolved", unresolved)

    for node in fragment["nodes"]:
        locations.append(("node statement location", node["statement_location"]))
        collect_scope("node", node)
    for edge in fragment["edges"]:
        locations.append(("edge location", edge["location"]))
        collect_endpoint(edge["from"])
        collect_endpoint(edge["to"])
        if "reference" in edge:
            locations.append(("edge reference location", edge["reference"]["location"]))
    for gap in fragment["gaps"]:
        locations.append(("gap location", gap["location"]))
        if "subject" in gap:
            collect_endpoint(gap["subject"])
        if "reference" in gap:
            locations.append(("gap reference location", gap["reference"]["location"]))
    for label, location in locations:
        if not _location_is_owned(location, fragment, spans_by_file):
            raise ValueError(f"{label} is outside its owned chunk span")


def _check_fragment_accounting(fragment: dict) -> None:
    """Validate discovery-local ids and endpoints before deterministic pooling."""

    node_ids = _require_unique_ids(fragment["nodes"], field="local_id", label="node")
    _require_unique_ids(fragment["edges"], field="local_id", label="edge")
    _require_unique_ids(fragment["gaps"], field="local_id", label="gap")

    def check_endpoint(endpoint: dict) -> None:
        if "local_node" in endpoint and endpoint["local_node"] not in node_ids:
            raise ValueError(f"unknown local node endpoint: {endpoint['local_node']}")

    for edge in fragment["edges"]:
        check_endpoint(edge["from"])
        check_endpoint(edge["to"])
    for gap in fragment["gaps"]:
        if "subject" in gap:
            check_endpoint(gap["subject"])


def _check_visible_anchor_coverage(fragment: dict, chunk: dict) -> None:
    """Require every hidden formal-environment anchor after worker discovery."""

    node_anchors = {
        (
            fragment["files"][node["statement_location"][0]],
            node["statement_location"][1],
            node["statement_location"][2],
            node.get("environment"),
        )
        for node in fragment["nodes"]
    }
    for anchor in chunk["anchors"]:
        key = (
            anchor["source_file"],
            anchor["start_line"],
            anchor["end_line"],
            anchor["environment"],
        )
        if key not in node_anchors:
            raise ValueError(
                "inventory fragment misses visible environment anchor: "
                f"{anchor['source_file']}:{anchor['start_line']}-{anchor['end_line']} "
                f"environment={anchor['environment']}"
            )


def _remap_location(location: list[int], file_indexes: dict[tuple[str, int], int], chunk_id: str) -> list[int]:
    """Map one fragment-local location to the pooled document-wide file table."""

    local_index, start_line, end_line = location
    return [file_indexes[(chunk_id, local_index)], start_line, end_line]


def _remap_scope(scope: dict, file_indexes: dict[tuple[str, int], int], chunk_id: str) -> dict:
    """Copy a scope hint while remapping every compact location it contains."""

    result = deepcopy(scope)
    result["starts_at"] = _remap_location(scope["starts_at"], file_indexes, chunk_id)
    if "ends_at" in scope:
        result["ends_at"] = _remap_location(scope["ends_at"], file_indexes, chunk_id)
    return result


def _remap_endpoint(endpoint: dict, chunk_id: str) -> dict:
    """Qualify an unresolved endpoint while retaining document-wide candidate anchors."""

    if "candidate_id" in endpoint:
        return {"candidate_id": endpoint["candidate_id"]}
    return {"unresolved_key": _qualified(chunk_id, endpoint["unresolved_key"])}


def _candidate_anchor_id(
    fragment: dict, node: dict, *, disambiguate_end: bool = False
) -> str:
    """Derive one stable document candidate id from an owned source span."""

    source_file = fragment["files"][node["statement_location"][0]]
    safe_file = re.sub(r"[^A-Za-z0-9._:-]", "_", source_file)
    start_line = node["statement_location"][1]
    if disambiguate_end:
        return f"{safe_file}:{start_line}-{node['statement_location'][2]}"
    return f"{safe_file}:{start_line}"


def pool_inventory_fragments(
    fragments: list[dict],
    *,
    chunk_manifest: dict,
) -> dict:
    """Validate, qualify, and source-order inventory fragments into one IR.

    ``inventory.schema.json`` deliberately remains a fragment-local vocabulary.
    The returned pooled object has the same record layout but qualified local
    handles, so it is validated by this runtime rather than reusing local-id
    JSON Schema patterns that would reject its necessary document-wide keys.
    """

    if not fragments:
        raise ValueError("at least one inventory fragment is required")
    ordered_chunks, chunks_by_id = _chunk_indexes(chunk_manifest)
    fragments_by_id: dict[str, dict] = {}
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise ValueError("inventory fragment must be an object")
        validate_inventory_fragment(fragment)
        chunk_id = fragment["chunk_id"]
        if chunk_id not in chunks_by_id:
            raise ValueError(f"unknown inventory chunk: {chunk_id}")
        if chunk_id in fragments_by_id:
            raise ValueError(f"inventory chunk merged more than once: {chunk_id}")
        _check_owned_locations(fragment, chunks_by_id[chunk_id])
        _check_fragment_accounting(fragment)
        _check_visible_anchor_coverage(fragment, chunks_by_id[chunk_id])
        fragments_by_id[chunk_id] = fragment
    missing = [chunk["chunk_id"] for chunk in ordered_chunks if chunk["chunk_id"] not in fragments_by_id]
    if missing:
        raise ValueError(f"inventory fragments omit chunks: {missing!r}")
    files: list[str] = []
    for chunk in ordered_chunks:
        for span in chunk["spans"]:
            source_file = span["source_file"]
            if source_file not in files:
                files.append(source_file)
    file_indexes: dict[tuple[str, int], int] = {}
    for chunk in ordered_chunks:
        fragment = fragments_by_id[chunk["chunk_id"]]
        for local_index, source_file in enumerate(fragment["files"]):
            try:
                pooled_index = files.index(source_file)
            except ValueError as error:  # Ownership was checked earlier; retain a narrow diagnostic.
                raise ValueError(f"unknown fragment source file: {source_file}") from error
            file_indexes[(chunk["chunk_id"], local_index)] = pooled_index

    pooled = {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": files,
        "evidence": [],
        "references": [],
        "candidates": [],
        "unresolved_entities": [],
        "relationship_hints": [],
        "reference_decisions": [],
        "gaps": [],
    }
    pooled_candidate_ids: set[str] = set()
    for chunk in ordered_chunks:
        chunk_id = chunk["chunk_id"]
        fragment = fragments_by_id[chunk_id]
        candidate_ids_by_local: dict[str, str] = {}
        candidate_start_counts = Counter(
            (
                fragment["files"][node["statement_location"][0]],
                node["statement_location"][1],
            )
            for node in fragment["nodes"]
        )
        evidence_number = 0
        reference_number = 0
        unresolved_number = 0

        def add_evidence(location: list[int], role: str) -> str:
            nonlocal evidence_number
            evidence_number += 1
            local_id = f"e{evidence_number}"
            pooled["evidence"].append(
                {
                    "id": _qualified(chunk_id, local_id),
                    "location": _remap_location(location, file_indexes, chunk_id),
                    "role": role,
                }
            )
            return local_id

        def add_reference(reference: dict) -> str:
            nonlocal reference_number
            reference_number += 1
            local_id = f"r{reference_number}"
            pooled["references"].append(
                {
                    "id": _qualified(chunk_id, local_id),
                    "location": _remap_location(
                        reference["location"], file_indexes, chunk_id
                    ),
                    "locator": deepcopy(reference["locator"]),
                }
            )
            return local_id

        def remap_discovery_endpoint(endpoint: dict, evidence_id: str) -> dict:
            nonlocal unresolved_number
            if "local_node" in endpoint:
                return {"candidate_id": candidate_ids_by_local[endpoint["local_node"]]}
            unresolved_number += 1
            local_id = f"u{unresolved_number}"
            item = deepcopy(endpoint["unresolved"])
            item["key"] = _qualified(chunk_id, local_id)
            item["evidence_ids"] = [_qualified(chunk_id, evidence_id)]
            if "scope_hint" in item:
                item["scope_hint"] = _remap_scope(
                    item["scope_hint"], file_indexes, chunk_id
                )
            pooled["unresolved_entities"].append(item)
            return {"unresolved_key": item["key"]}

        for node in fragment["nodes"]:
            start_key = (
                fragment["files"][node["statement_location"][0]],
                node["statement_location"][1],
            )
            candidate_id = _candidate_anchor_id(
                fragment,
                node,
                disambiguate_end=candidate_start_counts[start_key] > 1,
            )
            if candidate_id in pooled_candidate_ids:
                raise InventoryFragmentValidationError(
                    chunk_id,
                    f"candidate anchor emitted more than once: {candidate_id}",
                )
            pooled_candidate_ids.add(candidate_id)
            candidate_ids_by_local[node["local_id"]] = candidate_id
            evidence_id = add_evidence(node["statement_location"], "statement")
            item = deepcopy(node)
            del item["local_id"]
            item["id"] = candidate_id
            item["location"] = _remap_location(
                node["statement_location"], file_indexes, chunk_id
            )
            item["evidence_ids"] = [_qualified(chunk_id, evidence_id)]
            if "scope_hint" in node:
                item["scope_hint"] = _remap_scope(
                    node["scope_hint"], file_indexes, chunk_id
                )
            if node["type_hint"] == "external-result":
                item["retention_reasons"] = ["named-indispensable-external-result"]
            pooled["candidates"].append(item)
        for hint_number, edge in enumerate(fragment["edges"], 1):
            role = {
                "explicit-reference": "explicit-reference",
                "explicit-prose": "dependency-prose",
                "proof-use": "proof-use",
                "mathematical-inference": "dependency-prose",
            }[edge["basis"]]
            evidence_id = add_evidence(edge["location"], role)
            item = {
                "id": _qualified(chunk_id, f"h{hint_number}"),
                "from": remap_discovery_endpoint(edge["from"], evidence_id),
                "to": remap_discovery_endpoint(edge["to"], evidence_id),
                "type": edge["type"],
                "basis": edge["basis"],
                "assertion": edge["assertion"],
                "evidence_ids": [_qualified(chunk_id, evidence_id)],
                "confidence": edge["confidence"],
                "description": edge["description"],
            }
            if "reason" in edge:
                item["reason"] = edge["reason"]
            if "reference" in edge:
                reference_id = add_reference(edge["reference"])
                item["reference_ids"] = [_qualified(chunk_id, reference_id)]
            pooled["relationship_hints"].append(item)
        for gap_number, gap in enumerate(fragment["gaps"], 1):
            evidence_id = add_evidence(
                gap["location"],
                "explicit-reference" if "reference" in gap else "dependency-prose",
            )
            item = {
                "id": _qualified(chunk_id, f"g{gap_number}"),
                "category": gap["category"],
                "evidence_ids": [_qualified(chunk_id, evidence_id)],
                "description": gap["description"],
            }
            if "subject" in gap:
                item["subject"] = remap_discovery_endpoint(
                    gap["subject"], evidence_id
                )
            if "reference" in gap:
                reference_id = add_reference(gap["reference"])
                item["reference_id"] = _qualified(chunk_id, reference_id)
            pooled["gaps"].append(item)
    return pooled


def _load_fragment_manifest(path: Path) -> tuple[dict, list[dict]]:
    """Load an ordered worker-fragment manifest without interpreting its contents."""

    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("chunk inventory manifest must be a JSON object")
    fragment_paths = payload.get("fragments")
    if not isinstance(fragment_paths, list) or not fragment_paths:
        raise ValueError("chunk inventory manifest requires a nonempty fragments array")
    fragments: list[dict] = []
    for raw_path in fragment_paths:
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("chunk inventory paths must be nonempty strings")
        fragment_path = Path(raw_path).resolve()
        try:
            fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkerFragmentLoadError(
                fragment_path,
                {
                    "code": "invalid-fragment-json",
                    "path": [],
                    "message": "extract fragment is not valid JSON",
                },
            ) from error
        if not isinstance(fragment, dict):
            raise WorkerFragmentLoadError(
                fragment_path,
                {
                    "code": "invalid-fragment-shape",
                    "path": [],
                    "message": "extract fragment must be a JSON object",
                },
            )
        fragments.append(fragment)
    return payload, fragments


def write_json_atomic(payload: dict, out_path: Path) -> None:
    """Write one JSON artifact through atomic sibling replacement."""

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
    """Pool one inventory-fragment manifest and print its machine-readable summary."""

    parser = argparse.ArgumentParser(description="Pool mathematical inventory fragments.")
    parser.add_argument("fragment_manifest", help="JSON object listing fragment paths")
    parser.add_argument("--chunk-manifest", required=True, help="Inventory ownership manifest")
    parser.add_argument("--out", required=True, help="Pooled inventory destination")
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest_path = Path(args.fragment_manifest).resolve()
    _fragment_manifest, fragments = _load_fragment_manifest(manifest_path)
    chunk_manifest = json.loads(Path(args.chunk_manifest).resolve().read_text(encoding="utf-8"))
    if not isinstance(chunk_manifest, dict):
        raise ValueError("inventory chunk manifest must be a JSON object")
    pooled = pool_inventory_fragments(
        fragments,
        chunk_manifest=chunk_manifest,
    )
    out_path = Path(args.out).resolve()
    write_json_atomic(pooled, out_path)
    print(json.dumps({"fragments": len(fragments), "out": str(out_path)}, indent=2))


class Interface(PythonArgvMachineInterface):
    """Expose non-semantic inventory pooling through the machine protocol."""

    prog = "inventory_chunk_pooler.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()

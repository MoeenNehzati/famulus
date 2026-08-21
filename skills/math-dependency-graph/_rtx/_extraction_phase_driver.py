#!/usr/bin/env python3
"""Advance the inventory-to-extract mathematical graph pipeline.

The driver owns deterministic handoffs only: source preparation, inventory
pooling, one extract packet, bounded proof reconciliation when present, and final
deterministic compilation. Inventory, extract, and proof workers retain every
mathematical decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._batch_ir_merger import (
        _load_fragment_manifest,
        ValidationReportError,
        WorkerFragmentLoadError,
        apply_semantic_repair,
        canonical_json_bytes,
        pool_inventory_fragments,
        validate_extract_reconciliation,
        write_json_atomic,
    )
    from ._extraction_chunk_planner import plan_extract_packet
    from ._graph_builder import main as render_graph
    from ._inventory_unit_iterator import (
        load_iterator_diagnostics,
        load_iterator_summary,
        verify_completed_inventories,
    )
    from ._run_diagnostics import RunDiagnostics, _measure_file
    from ._proof_normalizer import normalize_files, validate_transitional_proof_ownership
    from ._semantic_graph_compiler import compile_semantic_graph, validate_semantic_payload
    from ._source_packet import collect_source_packet, write_source_packet
    from ._tex_macro_reader import extract_macros, write_macros
except ImportError:  # pragma: no cover - direct script execution
    from _batch_ir_merger import (
        _load_fragment_manifest,
        ValidationReportError,
        WorkerFragmentLoadError,
        apply_semantic_repair,
        canonical_json_bytes,
        pool_inventory_fragments,
        validate_extract_reconciliation,
        write_json_atomic,
    )
    from _extraction_chunk_planner import plan_extract_packet
    from _graph_builder import main as render_graph
    from _inventory_unit_iterator import (
        load_iterator_diagnostics,
        load_iterator_summary,
        verify_completed_inventories,
    )
    from _run_diagnostics import RunDiagnostics, _measure_file
    from _proof_normalizer import normalize_files, validate_transitional_proof_ownership
    from _semantic_graph_compiler import compile_semantic_graph, validate_semantic_payload
    from _source_packet import collect_source_packet, write_source_packet
    from _tex_macro_reader import extract_macros, write_macros


SKILL_DIR = Path(__file__).resolve().parents[1]
BASE_PATH = SKILL_DIR / "base.json"


class InventoryIncompleteError(ValueError):
    """Signal that durable inventory workers have not all reached completion."""


def _iterator_setup_job(source_packet: Path, state_dir: Path) -> dict:
    """Return the sole deterministic handoff created by source preparation."""

    return {
        "operation": "setup-inventory-iterator",
        "source_packet": str(source_packet.resolve()),
        "state_dir": str(state_dir.resolve()),
    }


def _extract_job(chunk: dict) -> dict:
    """Return every immutable path required by the sole extract worker."""

    return {
        "chunk_id": chunk["chunk_id"],
        "instruction": str(SKILL_DIR / "instructions" / "extract.md"),
        "schema": str(SKILL_DIR / "semantic-graph.schema.json"),
        "base": str(BASE_PATH),
        "packet": chunk["packet_path"],
        "sidecar": chunk["sidecar_path"],
        "source_packet": chunk["source_packet_path"],
        "entrypoint": chunk["entrypoint_path"],
        "progress_path": chunk["progress_path"],
        "output": chunk["fragment_path"],
    }


def _correction_job(
    chunk: dict,
    *,
    repair_base_path: Path,
    inventory_path: Path,
    validation_diagnostics: list[dict],
    output_path: Path,
) -> dict:
    """Return one record-local correction assignment from immutable run inputs."""

    return {
        "chunk_id": "extract-001-correction-001",
        "instruction": str(SKILL_DIR / "instructions" / "extract.md"),
        "schema": str(SKILL_DIR / "semantic-repair.schema.json"),
        "base": str(BASE_PATH),
        "repair_base": str(repair_base_path.resolve()),
        "inventory": str(inventory_path.resolve()),
        "packet": chunk["packet_path"],
        "sidecar": chunk["sidecar_path"],
        "source_packet": chunk["source_packet_path"],
        "entrypoint": chunk["entrypoint_path"],
        "progress_path": chunk["progress_path"],
        "validation_diagnostics": validation_diagnostics,
        "output": str(output_path.resolve()),
    }


def _extract_retry_job(chunk: dict, *, output_path: Path) -> dict:
    """Return a fresh normal extract assignment after a global validation failure."""

    job = _extract_job(chunk)
    job["output"] = str(output_path.resolve())
    return job


def _proof_entities(semantic: dict) -> list[dict]:
    """Return transitional proof entities in source-stable extract order."""

    return [item for item in semantic["entities"] if item.get("type") == "proof"]


def _registered_source_evidence(
    source_packet_path: Path, files: list[str], evidence: list[dict]
) -> list[dict]:
    """Embed only exact registered source ranges in the bounded proof packet."""

    source_lines: dict[tuple[str, int], str] = {}
    current_file: str | None = None
    for raw_line in source_packet_path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("@@ source: "):
            current_file = raw_line.removeprefix("@@ source: ")
            continue
        if current_file is None or " | " not in raw_line:
            continue
        ordinal, text = raw_line.split(" | ", 1)
        if ordinal.isdigit():
            source_lines[(current_file, int(ordinal))] = text
    records: list[dict] = []
    for item in evidence:
        file_index, start_line, end_line = item["location"]
        source_file = files[file_index]
        lines = [source_lines.get((source_file, line)) for line in range(start_line, end_line + 1)]
        if any(line is None for line in lines):
            raise ValueError(f"registered proof evidence is absent from immutable source: {item['id']}")
        records.append(
            {
                "evidence_id": item["id"],
                "source_file": source_file,
                "start_line": start_line,
                "end_line": end_line,
                "text": "\n".join(line for line in lines if line is not None),
            }
        )
    return records


def _proof_packet(
    semantic: dict,
    inventory: dict,
    *,
    semantic_sha256: str,
    inventory_sha256: str,
    source_sha256: str,
    source_packet_path: Path,
) -> dict:
    """Project only registered proof-centered semantic and inventory evidence."""

    proofs = _proof_entities(semantic)
    proof_ids = {item["id"] for item in proofs}
    incident = [
        item
        for item in semantic["relationships"]
        if item["from"] in proof_ids or item["to"] in proof_ids
    ]
    target_ids = {
        item["to"]
        for item in incident
        if item["type"] == "proves" and item["from"] in proof_ids
    }
    targets = [item for item in semantic["entities"] if item["id"] in target_ids]
    neighboring_ids = {
        endpoint
        for relationship in incident
        for endpoint in (relationship["from"], relationship["to"])
        if endpoint not in proof_ids and endpoint not in target_ids
    }
    neighbors = [item for item in semantic["entities"] if item["id"] in neighboring_ids]
    candidate_ids = {
        candidate_id
        for entity in (*proofs, *targets, *neighbors)
        for candidate_id in entity.get("candidate_ids", [])
    }
    hint_ids = {
        hint_id for relationship in incident for hint_id in relationship.get("hint_ids", [])
    }
    evidence_ids = {
        evidence_id
        for relationship in incident
        for evidence_id in relationship.get("evidence_ids", [])
    }
    reference_ids = {
        reference_id
        for relationship in incident
        for reference_id in relationship.get("reference_ids", [])
    }
    evidence_ids.update(
        evidence_id
        for candidate in inventory["candidates"]
        if candidate["id"] in candidate_ids
        for evidence_id in candidate.get("evidence_ids", [])
    )
    evidence = [item for item in inventory["evidence"] if item["id"] in evidence_ids]
    payload = {
        "proof_entities": proofs,
        "target_entities": targets,
        "neighboring_entities": neighbors,
        "incident_relationships": incident,
        "candidates": [item for item in inventory["candidates"] if item["id"] in candidate_ids],
        "relationship_hints": [item for item in inventory["relationship_hints"] if item["id"] in hint_ids],
        "evidence": evidence,
        "source_evidence": _registered_source_evidence(
            source_packet_path, inventory["files"], evidence
        ),
        "references": [item for item in inventory["references"] if item["id"] in reference_ids],
    }
    input_identity = {
        "semantic_ir_sha256": semantic_sha256,
        "inventory_sha256": inventory_sha256,
        "source_sha256": source_sha256,
        "packet_payload_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }
    return {
        "document_kind": "proof-reconciliation-packet",
        "ir_version": 1,
        "input_identity": input_identity,
        **payload,
    }


def _proof_reconciliation_job(
    *,
    run_dir: Path,
    state: dict,
    output_path: Path,
) -> dict:
    """Return one immutable bounded proof-reconciliation assignment."""

    original = state.get("proof_reconciliation_original")
    if not isinstance(original, dict):
        raise ValueError("proof reconciliation requires persisted original identity")
    return {
        "chunk_id": "proof-reconciliation-001",
        "instruction": str(SKILL_DIR / "instructions" / "proof-reconciliation.md"),
        "schema": str(SKILL_DIR / "proof-normalization.schema.json"),
        "packet": original["artifacts"]["packet"]["path"],
        "input_identity": original["input_identity"],
        "progress_path": str(
            (run_dir / "progress" / "proof-reconciliation-001.progress.md").resolve()
        ),
        "output": str(output_path.resolve()),
    }


def _verify_proof_reconciliation_original(
    state: dict,
    *,
    semantic_path: Path,
    inventory_path: Path,
    source_path: Path,
    packet_path: Path,
) -> dict:
    """Verify every persisted proof input against its first assigned identity."""

    original = state.get("proof_reconciliation_original")
    if not isinstance(original, dict):
        raise ValueError("proof reconciliation original identity is absent")
    input_identity = original.get("input_identity")
    artifacts = original.get("artifacts")
    if not isinstance(input_identity, dict) or not isinstance(artifacts, dict):
        raise ValueError("proof reconciliation original identity is malformed")
    expected_names = {"semantic_ir", "inventory", "source", "packet"}
    if set(artifacts) != expected_names:
        raise ValueError("proof reconciliation original artifact identity is malformed")
    expected_paths = {
        "semantic_ir": semantic_path.resolve(),
        "inventory": inventory_path.resolve(),
        "source": source_path.resolve(),
        "packet": packet_path.resolve(),
    }
    for name in sorted(expected_names):
        record = artifacts[name]
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ValueError(f"proof reconciliation {name} identity is malformed")
        path = Path(record["path"]).resolve()
        if path != expected_paths[name]:
            raise ValueError(f"proof reconciliation {name} artifact path identity changed")
        if not path.is_file() or _measure_file(path)[1] != record["sha256"]:
            raise ValueError(f"proof reconciliation {name} identity changed")
    packet = _read_json(Path(artifacts["packet"]["path"]))
    if packet.get("input_identity") != input_identity:
        raise ValueError("proof reconciliation packet identity changed")
    packet_payload = {
        key: value
        for key, value in packet.items()
        if key not in {"document_kind", "ir_version", "input_identity"}
    }
    if hashlib.sha256(canonical_json_bytes(packet_payload)).hexdigest() != input_identity.get(
        "packet_payload_sha256"
    ):
        raise ValueError("proof reconciliation packet payload identity changed")
    return original


def _read_json(path: Path) -> dict:
    """Read one expected JSON object with a narrow diagnostic on malformed input."""

    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _assignment_units(summary: dict, assignment: dict) -> list[dict]:
    """Return the persisted contiguous unit range owned by one assignment."""

    units = summary.get("units")
    if not isinstance(units, list) or not units:
        raise ValueError("iterator state requires durable source units")
    unit_ids = [unit.get("id") for unit in units]
    try:
        first = unit_ids.index(assignment["first_unit_id"])
        last = unit_ids.index(assignment["last_unit_id"])
    except (KeyError, ValueError) as error:
        raise ValueError("iterator assignment names an unknown durable unit") from error
    if first > last:
        raise ValueError("iterator assignment unit range is reversed")
    owned = units[first : last + 1]
    if len(owned) != assignment.get("unit_count"):
        raise ValueError("iterator assignment unit count does not match durable state")
    return owned


def _owned_spans(units: list[dict]) -> list[dict]:
    """Coalesce persisted worker coordinates into inclusive source spans."""

    spans: list[dict] = []
    for unit in units:
        coordinates = unit.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("durable iterator unit requires source coordinates")
        for coordinate in coordinates:
            source_file = coordinate.get("source")
            line_number = coordinate.get("line")
            if (
                not isinstance(source_file, str)
                or not source_file
                or not isinstance(line_number, int)
                or line_number < 1
            ):
                raise ValueError("durable iterator coordinate is invalid")
            if (
                spans
                and spans[-1]["source_file"] == source_file
                and spans[-1]["end_line"] + 1 == line_number
            ):
                spans[-1]["end_line"] = line_number
            else:
                spans.append(
                    {
                        "source_file": source_file,
                        "start_line": line_number,
                        "end_line": line_number,
                    }
                )
    return spans


def _iterator_pool_inputs(state_dir: Path, expected_source: Path) -> tuple[dict, list[dict]]:
    """Build pooler ownership from private controller packets and durable units."""

    summary = load_iterator_summary(state_dir)
    if Path(str(summary.get("source_packet_path", ""))).resolve() != expected_source:
        raise ValueError("iterator source packet does not match prepared run state")
    assignments = summary.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise ValueError("iterator state requires effective worker assignments")
    verification = verify_completed_inventories(state_dir)
    workers = verification["worker_indices"]
    incomplete = verification["incomplete_workers"]
    expected_workers = [assignment.get("worker_index") for assignment in assignments]
    if workers != expected_workers or summary.get("effective_workers") != len(workers):
        raise ValueError("iterator assignment state does not match durable worker cursors")
    if incomplete:
        raise InventoryIncompleteError(f"inventory workers are incomplete: {incomplete!r}")
    authenticated_fragments = verification["fragments"]
    if len(authenticated_fragments) != len(assignments):
        raise ValueError("authenticated inventories do not match worker assignments")

    source_sha256 = summary.get("configuration", {}).get("source_sha256")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ValueError("iterator state requires a durable source identity")
    chunks: list[dict] = []
    fragments: list[dict] = []
    for assignment, fragment in zip(
        assignments, authenticated_fragments, strict=True
    ):
        worker_index = assignment["worker_index"]
        chunk_id = f"iterator-worker-{worker_index:03d}"
        units = _assignment_units(summary, assignment)
        controller_path = Path(assignment["controller_packet_path"]).resolve()
        controller = _read_json(controller_path)
        expected_unit_ids = [unit["id"] for unit in units]
        if controller != {
            "worker_index": worker_index,
            "unit_ids": expected_unit_ids,
            "source_packet_path": str(expected_source),
            "source_sha256": source_sha256,
        }:
            raise ValueError(f"controller packet does not match worker {worker_index}")
        if fragment.get("chunk_id") != chunk_id:
            raise ValueError(f"inventory fragment does not match worker {worker_index}")
        controller_bytes = controller_path.read_bytes()
        chunks.append(
            {
                "chunk_id": chunk_id,
                "packet_path": str(controller_path),
                "packet_sha256": hashlib.sha256(controller_bytes).hexdigest(),
                "owned_bytes": sum(int(unit["character_count"]) for unit in units),
                "anchors": [],
                "spans": _owned_spans(units),
            }
        )
        fragments.append(fragment)

    assignments_path = state_dir / "inventory-assignments.json"
    manifest_bytes = assignments_path.read_bytes()
    return (
        {
            "mode": "inventory",
            "source": str(assignments_path),
            "source_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "chunks": chunks,
        },
        fragments,
    )


def _structured_record_counts(payload: dict) -> dict:
    """Project in-memory structured artifacts onto diagnostics count fields."""

    if payload.get("repair_version") == 2:
        repair_fields = {
            "entities": ("remove_entity_ids", "upsert_entities"),
            "exclusions": (
                "remove_exclusion_candidate_ids",
                "upsert_exclusions",
            ),
            "unresolved_resolutions": (
                "remove_unresolved_ids",
                "upsert_unresolved_resolutions",
            ),
            "relationships": ("remove_relationships", "upsert_relationships"),
            "hint_decisions": ("remove_hint_ids", "upsert_hint_decisions"),
            "reference_decisions": (
                "remove_reference_ids",
                "upsert_reference_decisions",
            ),
            "gaps": ("remove_gap_ids", "upsert_gaps"),
            "gap_decisions": (
                "remove_inventory_gap_ids",
                "upsert_gap_decisions",
            ),
        }
        return {
            target: sum(
                len(payload.get(source, []))
                for source in sources
                if isinstance(payload.get(source, []), list)
            )
            for target, sources in repair_fields.items()
        }
    source_fields = (
        ("files", "files"),
        ("nodes", "nodes"),
        ("edges", "edges"),
        ("evidence", "evidence"),
        ("references", "references"),
        ("candidates", "candidates"),
        ("unresolved_entities", "unresolved_entities"),
        ("relationship_hints", "relationship_hints"),
        ("entities", "entities"),
        ("exclusions", "exclusions"),
        ("unresolved_resolutions", "unresolved_resolutions"),
        ("hint_decisions", "hint_decisions"),
        ("reference_decisions", "reference_decisions"),
        ("gap_decisions", "gap_decisions"),
        ("gaps", "gaps"),
    )
    counts = {
        target: len(payload[source])
        for source, target in source_fields
        if isinstance(payload.get(source), list)
    }
    resolutions = payload.get("unresolved_resolutions")
    if isinstance(resolutions, list):
        counts["unresolved"] = sum(
            isinstance(item, dict) and item.get("disposition") == "unresolved"
            for item in resolutions
        )
    relationships = payload.get("relationships")
    if isinstance(relationships, list):
        counts["relationships"] = len(relationships)
    elif isinstance(payload.get("entities"), list):
        counts["relationships"] = sum(
            len(item.get("connects_to", []))
            for item in payload["entities"]
            if isinstance(item, dict) and isinstance(item.get("connects_to", []), list)
        )
    return counts


def _proof_metrics(provenance: dict) -> dict:
    """Derive durable coverage metrics from complete normalization provenance."""

    bundles_by_target: dict[str, set[str]] = {}
    accepted_proof_ids: set[str] = set()
    for bundle in provenance["bundles"]:
        bundles_by_target.setdefault(bundle["target_id"], set()).add(bundle["bundle_id"])
        accepted_proof_ids.update(bundle["proof_ids"])
    alternative_bundle_ids = {
        bundle_id
        for bundle_ids in bundles_by_target.values()
        if len(bundle_ids) > 1
        for bundle_id in bundle_ids
    }
    redirected: list[dict] = []
    for relationship in provenance["relationships"]:
        if not relationship["proof_ids"]:
            continue
        routes = [
            {
                "proof_id": route["proof_id"],
                "bundle_id": route["bundle_id"],
                "evidence_ids": route["relationship"]["evidence_ids"],
            }
            for route in relationship["source_relationships"]
            if "proof_id" in route
        ]
        redirected.append(
            {
                "from": relationship["from"],
                "to": relationship["to"],
                "type": relationship["type"],
                "proof_ids": relationship["proof_ids"],
                "bundle_ids": relationship["bundle_ids"],
                "routes": routes,
            }
        )
    return {
        "accepted_proofs": len(accepted_proof_ids),
        "alternative_bundles": len(alternative_bundle_ids),
        "total_redirected_relationships": len(redirected),
        "redirected_relationships": redirected,
    }


def _open_phase_diagnostics(run_dir: Path) -> RunDiagnostics:
    """Open a live report or explicitly resume its preserved failure history."""

    diagnostics = RunDiagnostics.open(run_dir)
    status = diagnostics.payload["run"]["status"]
    if status == "failure":
        diagnostics.resume()
    elif status == "success":
        raise ValueError("extraction run diagnostics are already complete")
    return diagnostics


def prepare(entrypoint: Path, run_dir: Path) -> dict:
    """Prepare active source and return one durable iterator setup assignment."""

    entrypoint = entrypoint.resolve()
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = RunDiagnostics.initialize(run_dir, entrypoint=entrypoint)
    source_path = run_dir / "source-packet.txt"
    iterator_state_dir = run_dir / "inventory-iterator"
    state_path = run_dir / "run-state.json"
    try:
        with diagnostics.stage(
            "source-preparation", inputs=[entrypoint], outputs=[source_path]
        ):
            packet = collect_source_packet(entrypoint)
            write_source_packet(packet, source_path)
        diagnostics.record_artifact(
            source_path,
            kind="active-source",
            phase="source-preparation",
        )
        with diagnostics.stage(
            "planning",
            inputs=[source_path],
            outputs=[state_path],
        ):
            setup_job = _iterator_setup_job(source_path, iterator_state_dir)
            state = {
                "entrypoint": str(entrypoint),
                "source_packet": str(source_path),
                "inventory_iterator_state": str(iterator_state_dir),
            }
            write_json_atomic(state, state_path)
        return {
            **state,
            "next_job": setup_job,
            "diagnostics": str(diagnostics.path),
        }
    except BaseException as exc:
        diagnostics.finish(status="failure", error=exc)
        raise


def advance_inventory(iterator_state_dir: Path, run_dir: Path) -> dict:
    """Pool completed iterator inventories and materialize one extract handoff."""

    run_dir = run_dir.resolve()
    iterator_state_dir = iterator_state_dir.resolve()
    diagnostics = _open_phase_diagnostics(run_dir)
    inventory_path = run_dir / "inventory-ir.json"
    extract_manifest_path = run_dir / "extract-chunks.json"
    state_path = run_dir / "run-state.json"
    recoverable_pooling_failure = False
    try:
        state = _read_json(state_path)
        prepared_state_dir = Path(state.get("inventory_iterator_state", "")).resolve()
        if iterator_state_dir != prepared_state_dir:
            raise ValueError("iterator state does not match prepared iterator state")
        diagnostics.record_iterator_summary(
            load_iterator_diagnostics(iterator_state_dir)
        )
        try:
            chunk_manifest, fragments = _iterator_pool_inputs(
                iterator_state_dir,
                Path(state["source_packet"]).resolve(),
            )
        except InventoryIncompleteError:
            recoverable_pooling_failure = True
            raise
        chunks = chunk_manifest["chunks"]
        authenticated_fragment_dir = run_dir / "authenticated-inventory-fragments"
        for chunk, fragment in zip(chunks, fragments, strict=True):
            fragment_path = authenticated_fragment_dir / f"{chunk['chunk_id']}.json"
            write_json_atomic(fragment, fragment_path)
            chunk["fragment_path"] = str(fragment_path.resolve())
        fragment_paths = [Path(chunk["fragment_path"]) for chunk in chunks]
        controller_paths = [Path(chunk["packet_path"]) for chunk in chunks]
        fragments_by_chunk = {
            str(fragment["chunk_id"]): fragment for fragment in fragments
        }
        try:
            with diagnostics.stage(
                "pooling",
                inputs=[*fragment_paths, *controller_paths],
                outputs=[inventory_path],
                validation=True,
            ):
                inventory = pool_inventory_fragments(
                    fragments,
                    chunk_manifest=chunk_manifest,
                )
                write_json_atomic(inventory, inventory_path)
        except ValueError:
            recoverable_pooling_failure = True
            raise
        for chunk, fragment_path in zip(chunks, fragment_paths, strict=True):
            controller_artifact = diagnostics.record_artifact(
                Path(chunk["packet_path"]),
                kind="inventory-packet",
                phase="planning",
            )
            fragment = fragments_by_chunk[str(chunk["chunk_id"])]
            fragment_artifact = diagnostics.record_artifact(
                fragment_path,
                kind="inventory-fragment",
                phase="inventory",
                counts=_structured_record_counts(fragment),
            )
            diagnostics.record_ratio(
                "inventory-fragment-to-owned-packet",
                numerator=fragment_artifact,
                denominator=controller_artifact,
                job_id=str(chunk["chunk_id"]),
                numerator_bytes=len(canonical_json_bytes(fragment)),
                denominator_bytes=int(chunk["owned_bytes"]),
                measurement_basis="canonical-fragment-to-owned-lines",
                stage_attempt=f"{chunk['chunk_id']}:initial",
            )
        source_artifact = diagnostics.record_artifact(
            Path(state["source_packet"]),
            kind="active-source",
            phase="source-preparation",
        )
        inventory_artifact = diagnostics.record_artifact(
            inventory_path,
            kind="pooled-inventory",
            phase="pooling",
            counts=_structured_record_counts(inventory),
        )
        diagnostics.record_ratio(
            "pooled-canonical-fragments-to-owned-packets",
            numerator=inventory_artifact,
            denominator=source_artifact,
            numerator_bytes=sum(len(canonical_json_bytes(fragment)) for fragment in fragments),
            denominator_bytes=sum(int(chunk["owned_bytes"]) for chunk in chunks),
            measurement_basis="canonical-fragments-to-owned-lines",
        )
        diagnostics.record_ratio(
            "pooled-inventory-to-active-source",
            numerator=inventory_artifact,
            denominator=source_artifact,
        )
        with diagnostics.stage(
            "planning",
            inputs=[inventory_path, Path(state["source_packet"])],
            outputs=[
                extract_manifest_path,
                state_path,
                run_dir / "extract-packets",
                run_dir / "coordinate-sidecars",
            ],
        ):
            manifest = plan_extract_packet(
                inventory,
                source_packet_path=Path(state["source_packet"]),
                entrypoint_path=Path(state["entrypoint"]),
                output_dir=run_dir,
            )
            state["inventory_ir"] = str(inventory_path.resolve())
            state["extract_manifest"] = str(extract_manifest_path.resolve())
            write_json_atomic(state, state_path)
        extract_chunk = manifest["chunks"][0]
        diagnostics.record_artifact(
            Path(extract_chunk["packet_path"]),
            kind="extract-packet",
            phase="planning",
        )
        diagnostics.record_artifact(
            Path(extract_chunk["sidecar_path"]),
            kind="coordinate-sidecar",
            phase="planning",
        )
        return {
            "inventory_ir": str(inventory_path.resolve()),
            "candidates": len(inventory["candidates"]),
            "next_job": _extract_job(manifest["chunks"][0]),
            "diagnostics": str(diagnostics.path),
        }
    except BaseException as exc:
        if not recoverable_pooling_failure:
            diagnostics.finish(status="failure", error=exc)
        raise


def finalize_extract(
    fragment_manifest_path: Path, run_dir: Path, html: Path | None
) -> dict:
    """Reconcile one extract output, compile it, resolve macros, and render HTML."""

    run_dir = run_dir.resolve()
    diagnostics = _open_phase_diagnostics(run_dir)
    semantic_path = run_dir / "semantic-ir.json"
    try:
        state = _read_json(run_dir / "run-state.json")
        inventory_path = Path(state.get("inventory_ir", "")).resolve()
        if not inventory_path.is_file():
            raise ValueError("extract finalization requires the saved pooled inventory")
        inventory = _read_json(inventory_path)
        try:
            manifest, fragments = _load_fragment_manifest(
                fragment_manifest_path.resolve()
            )
        except WorkerFragmentLoadError as error:
            try:
                with diagnostics.stage(
                    "validation",
                    inputs=[error.fragment_path, inventory_path],
                    outputs=[],
                    validation=True,
                ):
                    raise error
            except WorkerFragmentLoadError:
                pass
            diagnostics.record_validation_diagnostics(error.diagnostics)
            return _prepare_extract_retry(
                fragment_path=error.fragment_path,
                inventory_path=inventory_path,
                state=state,
                run_dir=run_dir,
                diagnostics=diagnostics,
                validation_diagnostics=error.diagnostics,
            )
        if len(fragments) != 1:
            raise ValueError("extract finalization requires exactly one fragment")
        fragment_path = Path(manifest["fragments"][0])
        inventory_artifact = diagnostics.record_artifact(
            inventory_path,
            kind="pooled-inventory",
            phase="pooling",
            counts=_structured_record_counts(inventory),
        )
        diagnostics.record_artifact(
            fragment_path,
            kind="semantic-fragment",
            phase="extract",
            counts=_structured_record_counts(fragments[0]),
        )
        fragment = fragments[0]
        if fragment.get("repair_version") == 2:
            repair_base_path = Path(
                state.get("semantic_repair_base", str(semantic_path))
            ).resolve()
            if not repair_base_path.is_file():
                raise ValueError("semantic correction requires a persisted repair base")
            with diagnostics.stage(
                "correction-application",
                inputs=[repair_base_path, fragment_path, inventory_path],
                outputs=[semantic_path],
                validation=True,
            ):
                semantic = apply_semantic_repair(
                    _read_json(repair_base_path),
                    fragment,
                    inventory,
                )
                write_json_atomic(semantic, semantic_path)
        else:
            semantic = fragment
            try:
                with diagnostics.stage(
                    "validation",
                    inputs=[fragment_path, inventory_path],
                    outputs=[semantic_path],
                    validation=True,
                ):
                    try:
                        validate_transitional_proof_ownership(semantic)
                    except ValueError as error:
                        raise ValidationReportError(
                            "extract proof ownership failed",
                            [{"code": "proof-ownership", "path": ["relationships"]}],
                            repairable=True,
                            display_messages=[str(error)],
                        ) from error
                    validate_extract_reconciliation(semantic, inventory)
                    write_json_atomic(semantic, semantic_path)
            except ValidationReportError as error:
                diagnostics.record_validation_diagnostics(error.diagnostics)
                if not error.repairable:
                    return _prepare_extract_retry(
                        fragment_path=fragment_path,
                        inventory_path=inventory_path,
                        state=state,
                        run_dir=run_dir,
                        diagnostics=diagnostics,
                        validation_diagnostics=error.diagnostics,
                    )
                return _prepare_semantic_correction(
                    semantic,
                    fragment_path=fragment_path,
                    inventory_path=inventory_path,
                    state=state,
                    run_dir=run_dir,
                    diagnostics=diagnostics,
                    validation_diagnostics=error.diagnostics,
                )
        diagnostics.record_semantic_counts(semantic)
        semantic_artifact = diagnostics.record_artifact(
            semantic_path,
            kind="semantic-ir",
            phase=(
                "correction-application"
                if fragment.get("repair_version") == 2
                else "validation"
            ),
            counts=_structured_record_counts(semantic),
        )
        diagnostics.record_ratio(
            "semantic-ir-to-pooled-inventory",
            numerator=semantic_artifact,
            denominator=inventory_artifact,
        )
        if _proof_entities(semantic):
            packet_path = run_dir / "proof-reconciliation-packet.json"
            output_path = run_dir / "proof-reconciliation-001.json"
            report_path = run_dir / "proof-reconciliation-required.json"
            state_path = run_dir / "run-state.json"
            with diagnostics.stage(
                "proof-reconciliation-planning",
                inputs=[semantic_path, inventory_path],
                outputs=[packet_path, report_path, state_path],
            ):
                _, semantic_sha256 = _measure_file(semantic_path)
                _, inventory_sha256 = _measure_file(inventory_path)
                source_packet_path = Path(state["source_packet"]).resolve()
                _, source_sha256 = _measure_file(source_packet_path)
                packet = _proof_packet(
                    semantic,
                    inventory,
                    semantic_sha256=semantic_sha256,
                    inventory_sha256=inventory_sha256,
                    source_sha256=source_sha256,
                    source_packet_path=source_packet_path,
                )
                write_json_atomic(packet, packet_path)
                _, packet_sha256 = _measure_file(packet_path)
                state["proof_reconciliation_original"] = {
                    "input_identity": packet["input_identity"],
                    "artifacts": {
                        "semantic_ir": {
                            "path": str(semantic_path.resolve()),
                            "sha256": semantic_sha256,
                        },
                        "inventory": {
                            "path": str(inventory_path.resolve()),
                            "sha256": inventory_sha256,
                        },
                        "source": {
                            "path": str(source_packet_path),
                            "sha256": source_sha256,
                        },
                        "packet": {
                            "path": str(packet_path.resolve()),
                            "sha256": packet_sha256,
                        },
                    },
                }
                next_job = _proof_reconciliation_job(
                    run_dir=run_dir,
                    state=state,
                    output_path=output_path,
                )
                report = {
                    "status": "proof-reconciliation-required",
                    "semantic_ir": str(semantic_path.resolve()),
                    "next_job": next_job,
                    "diagnostics": str(diagnostics.path),
                }
                write_json_atomic(report, report_path)
                state["proof_reconciliation_report"] = str(report_path.resolve())
                state["proof_reconciliation_attempts"] = 0
                write_json_atomic(state, state_path)
            diagnostics.record_artifact(
                packet_path,
                kind="proof-reconciliation-packet",
                phase="proof-reconciliation-planning",
                counts={"proof_entities": len(_proof_entities(semantic))},
            )
            return report
        result = _compile_and_render(
            semantic,
            semantic_path,
            inventory,
            state,
            run_dir,
            html,
            diagnostics,
            semantic_artifact,
        )
        diagnostics.finish(status="success")
        result["diagnostics"] = str(diagnostics.path)
        return result
    except BaseException as exc:
        diagnostics.finish(status="failure", error=exc)
        raise


def finalize_proofs(
    fragment_manifest_path: Path, run_dir: Path, html: Path | None
) -> dict:
    """Validate exhaustive proof decisions, normalize, compile, render, and finish."""

    run_dir = run_dir.resolve()
    diagnostics = _open_phase_diagnostics(run_dir)
    state_path = run_dir / "run-state.json"
    state = _read_json(state_path)
    semantic_path = run_dir / "semantic-ir.json"
    inventory_path = Path(state.get("inventory_ir", "")).resolve()
    packet_path = run_dir / "proof-reconciliation-packet.json"
    normalized_path = run_dir / "semantic-ir-normalized.json"
    provenance_path = run_dir / "proof-provenance.json"
    projected_inventory_path = run_dir / "inventory-ir-normalized.json"
    try:
        if not semantic_path.is_file() or not inventory_path.is_file() or not packet_path.is_file():
            raise ValueError("proof finalization requires immutable semantic, inventory, and proof packet inputs")
        original = _verify_proof_reconciliation_original(
            state,
            semantic_path=semantic_path,
            inventory_path=inventory_path,
            source_path=Path(state.get("source_packet", "")),
            packet_path=packet_path,
        )
        try:
            manifest, fragments = _load_fragment_manifest(
                fragment_manifest_path.resolve()
            )
        except WorkerFragmentLoadError as error:
            try:
                with diagnostics.stage(
                    "proof-normalization",
                    inputs=[error.fragment_path, semantic_path, inventory_path],
                    outputs=[],
                    validation=True,
                ):
                    raise error
            except WorkerFragmentLoadError:
                pass
            diagnostics.record_validation_diagnostics(error.diagnostics)
            return _prepare_proof_retry(
                decision_path=error.fragment_path,
                semantic_path=semantic_path,
                inventory_path=inventory_path,
                packet_path=packet_path,
                state=state,
                state_path=state_path,
                run_dir=run_dir,
                diagnostics=diagnostics,
            )
        if len(fragments) != 1:
            raise ValueError("proof finalization requires exactly one decisions fragment")
        decision_path = Path(manifest["fragments"][0]).resolve()
        decisions = fragments[0]
        if decisions.get("input_identity") != original["input_identity"]:
            raise ValueError("proof reconciliation decision input identity does not match original")
        try:
            with diagnostics.stage(
                "proof-normalization",
                inputs=[semantic_path, decision_path, inventory_path],
                outputs=[normalized_path, provenance_path, projected_inventory_path],
                validation=True,
            ):
                normalize_files(
                    semantic_path,
                    decision_path,
                    normalized_path,
                    provenance_path,
                    inventory_path,
                    projected_inventory_path,
                )
        except (ValueError, ValidationReportError) as error:
            return _prepare_proof_retry(
                decision_path=decision_path,
                semantic_path=semantic_path,
                inventory_path=inventory_path,
                packet_path=packet_path,
                state=state,
                state_path=state_path,
                run_dir=run_dir,
                diagnostics=diagnostics,
            )

        normalized = _read_json(normalized_path)
        projected_inventory = _read_json(projected_inventory_path)
        semantic_artifact = diagnostics.record_artifact(
            normalized_path,
            kind="normalized-semantic-ir",
            phase="proof-normalization",
            counts=_structured_record_counts(normalized),
        )
        diagnostics.record_artifact(
            decision_path,
            kind="proof-normalization-decisions",
            phase="proof-reconciliation",
        )
        provenance = _read_json(provenance_path)
        proof_metrics = _proof_metrics(provenance)
        diagnostics.record_proof_metrics(proof_metrics)
        diagnostics.record_artifact(
            provenance_path,
            kind="proof-provenance",
            phase="proof-normalization",
            counts={
                "proof_entities": len(provenance["proof_entities"]),
                "accepted_proofs": proof_metrics["accepted_proofs"],
                "proof_bundles": len(provenance["bundles"]),
                "alternative_bundles": proof_metrics["alternative_bundles"],
                "proof_targets": len(
                    {item["target_id"] for item in provenance["bundles"]}
                ),
                "proof_exclusions": len(provenance["exclusions"]),
                "redirected_relationships": sum(
                    bool(item["proof_ids"])
                    for item in provenance["relationships"]
                ),
                "total_redirected_relationships": proof_metrics[
                    "total_redirected_relationships"
                ],
            },
        )
        diagnostics.record_artifact(
            projected_inventory_path,
            kind="projected-inventory",
            phase="proof-normalization",
            counts=_structured_record_counts(projected_inventory),
        )
        result = _compile_and_render(
            normalized,
            normalized_path,
            projected_inventory,
            state,
            run_dir,
            html,
            diagnostics,
            semantic_artifact,
        )
        result["transitional_semantic_ir"] = str(semantic_path.resolve())
        result["proof_provenance"] = str(provenance_path.resolve())
        result["projected_inventory"] = str(projected_inventory_path.resolve())
        diagnostics.finish(status="success")
        result["diagnostics"] = str(diagnostics.path)
        return result
    except BaseException as exc:
        diagnostics.finish(status="failure", error=exc)
        raise


def _prepare_semantic_correction(
    semantic: dict,
    *,
    fragment_path: Path,
    inventory_path: Path,
    state: dict,
    run_dir: Path,
    diagnostics: RunDiagnostics,
    validation_diagnostics: list[dict],
) -> dict:
    """Persist one rejected extract and return its bounded correction assignment."""

    extract_manifest_path = Path(state.get("extract_manifest", "")).resolve()
    if not extract_manifest_path.is_file():
        raise ValueError("semantic correction requires the saved extract manifest")
    extract_manifest = _read_json(extract_manifest_path)
    chunks = extract_manifest.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 1:
        raise ValueError("semantic correction requires exactly one saved extract chunk")
    repair_base_path = run_dir / "semantic-repair-base.json"
    correction_report_path = run_dir / "semantic-correction.json"
    correction_output_path = (
        run_dir / "extract-fragments" / "extract-001-correction-001.json"
    )
    next_job = _correction_job(
        chunks[0],
        repair_base_path=repair_base_path,
        inventory_path=inventory_path,
        validation_diagnostics=validation_diagnostics,
        output_path=correction_output_path,
    )
    report = {
        "status": "correction-required",
        "validation_diagnostics": validation_diagnostics,
        "repair_base": str(repair_base_path.resolve()),
        "correction_report": str(correction_report_path.resolve()),
        "next_job": next_job,
        "diagnostics": str(diagnostics.path),
    }
    state_path = run_dir / "run-state.json"
    with diagnostics.stage(
        "planning",
        inputs=[fragment_path, inventory_path, extract_manifest_path],
        outputs=[repair_base_path, correction_report_path, state_path],
    ):
        write_json_atomic(semantic, repair_base_path)
        write_json_atomic(report, correction_report_path)
        state["semantic_repair_base"] = str(repair_base_path.resolve())
        state["semantic_correction_report"] = str(correction_report_path.resolve())
        write_json_atomic(state, state_path)
    diagnostics.record_artifact(
        repair_base_path,
        kind="semantic-fragment",
        phase="extract",
        counts=_structured_record_counts(semantic),
    )
    diagnostics.record_correction()
    return report


def _prepare_extract_retry(
    *,
    fragment_path: Path,
    inventory_path: Path,
    state: dict,
    run_dir: Path,
    diagnostics: RunDiagnostics,
    validation_diagnostics: list[dict],
) -> dict:
    """Route structural/global failures to a fresh whole-document extract."""

    extract_manifest_path = Path(state.get("extract_manifest", "")).resolve()
    extract_manifest = _read_json(extract_manifest_path)
    chunks = extract_manifest.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 1:
        raise ValueError("extract retry requires exactly one saved extract chunk")
    retry_report_path = run_dir / "semantic-retry.json"
    retry_output_path = run_dir / "extract-fragments" / "extract-001-retry-001.json"
    report = {
        "status": "retry-required",
        "validation_diagnostics": validation_diagnostics,
        "retry_report": str(retry_report_path.resolve()),
        "next_job": _extract_retry_job(chunks[0], output_path=retry_output_path),
        "diagnostics": str(diagnostics.path),
    }
    with diagnostics.stage(
        "planning",
        inputs=[fragment_path, inventory_path, extract_manifest_path],
        outputs=[retry_report_path],
    ):
        write_json_atomic(report, retry_report_path)
    return report


def _prepare_proof_retry(
    *,
    decision_path: Path,
    semantic_path: Path,
    inventory_path: Path,
    packet_path: Path,
    state: dict,
    state_path: Path,
    run_dir: Path,
    diagnostics: RunDiagnostics,
) -> dict:
    """Return the sole bounded proof retry without changing controller inputs."""

    attempts = state.get("proof_reconciliation_attempts", 0)
    if not isinstance(attempts, int) or attempts < 0:
        raise ValueError("proof reconciliation retry state is invalid")
    if attempts >= 1:
        raise ValueError("proof reconciliation exhausted its bounded retry")
    retry_output = run_dir / "proof-reconciliation-001-retry-001.json"
    next_job = _proof_reconciliation_job(
        run_dir=run_dir,
        state=state,
        output_path=retry_output,
    )
    report = {
        "status": "proof-reconciliation-retry-required",
        "retry_code": "validation-failed",
        "next_job": next_job,
        "diagnostics": str(diagnostics.path),
    }
    report_path = run_dir / "proof-reconciliation-retry.json"
    with diagnostics.stage(
        "proof-reconciliation-planning",
        inputs=[decision_path, semantic_path, inventory_path, packet_path],
        outputs=[report_path, state_path],
    ):
        state["proof_reconciliation_attempts"] = attempts + 1
        write_json_atomic(report, report_path)
        write_json_atomic(state, state_path)
    return report


def _compile_and_render(
    semantic: dict,
    semantic_path: Path,
    inventory: dict,
    state: dict,
    run_dir: Path,
    html: Path | None,
    diagnostics: RunDiagnostics,
    semantic_artifact: dict,
) -> dict:
    """Compile and render one already validated whole-document semantic IR."""

    canonical_path = run_dir / "dependency-graph.json"
    with diagnostics.stage(
        "compilation",
        inputs=[semantic_path, BASE_PATH],
        outputs=[canonical_path],
    ):
        canonical = compile_semantic_graph(semantic, _read_json(BASE_PATH), inventory)
        _, semantic_sha256 = _measure_file(semantic_path)
        canonical["metadata"]["semantic_ir_sha256"] = semantic_sha256
        write_json_atomic(canonical, canonical_path)
    canonical_artifact = diagnostics.record_artifact(
        canonical_path,
        kind="renderer-json",
        phase="compilation",
        counts=_structured_record_counts(canonical),
    )
    diagnostics.record_ratio(
        "renderer-json-to-semantic-ir",
        numerator=canonical_artifact,
        denominator=semantic_artifact,
    )
    entrypoint = Path(state["entrypoint"])
    macro_path = run_dir / "mathjax-macros.json"
    with diagnostics.stage(
        "macro-extraction",
        inputs=[entrypoint],
        outputs=[macro_path],
    ):
        macros = extract_macros(entrypoint)
        write_macros(macros, macro_path)
    diagnostics.record_artifact(
        macro_path,
        kind="macro-file",
        phase="macro-extraction",
        counts={"macros": len(macros)},
    )
    html_path = html.resolve() if html else run_dir / "dependency-graph.html"
    with diagnostics.stage(
        "rendering",
        inputs=[canonical_path, semantic_path, macro_path],
        outputs=[html_path],
    ):
        render_graph(
            [
                str(canonical_path),
                "--semantic-ir",
                str(semantic_path),
                "--html-out",
                str(html_path),
                "--macro-file",
                str(macro_path),
            ]
        )
        if not html_path.is_file() or html_path.stat().st_size == 0:
            raise ValueError("renderer did not create a nonempty HTML artifact")
    diagnostics.record_artifact(
        html_path,
        kind="html",
        phase="rendering",
    )
    return {
        "semantic_ir": str(semantic_path),
        "canonical_json": str(canonical_path),
        "macro_file": str(macro_path),
        "html": str(html_path),
        "entities": len(semantic["entities"]),
        "relationships": len(semantic["relationships"]),
    }


def main(argv: Iterable[str] | None = None) -> None:
    """Advance one deterministic inventory/extract phase from the command line."""

    parser = argparse.ArgumentParser(description="Advance graph extraction phases.")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("entrypoint")
    prepare_parser.add_argument("--run-dir", required=True)
    for mode in ("advance-inventory", "finalize-extract", "finalize-proofs"):
        phase = subparsers.add_parser(mode)
        phase.add_argument("fragment_manifest")
        phase.add_argument("--run-dir", required=True)
        if mode in {"finalize-extract", "finalize-proofs"}:
            phase.add_argument("--html")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.mode == "prepare":
        report = prepare(Path(args.entrypoint), Path(args.run_dir))
    elif args.mode == "advance-inventory":
        report = advance_inventory(Path(args.fragment_manifest), Path(args.run_dir))
    elif args.mode == "finalize-extract":
        report = finalize_extract(
            Path(args.fragment_manifest),
            Path(args.run_dir),
            Path(args.html) if args.html else None,
        )
    else:
        report = finalize_proofs(
            Path(args.fragment_manifest),
            Path(args.run_dir),
            Path(args.html) if args.html else None,
        )
    print(json.dumps(report, indent=2))


class Interface(PythonArgvMachineInterface):
    """Expose deterministic inventory/extract phase advancement to the runtime."""

    prog = "extraction_phase_driver.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Advance the inventory-to-extract mathematical graph pipeline.

The driver owns deterministic handoffs only: source preparation, inventory
pooling, one extract packet, and final deterministic compilation.  Inventory
and extract workers retain every mathematical decision.
"""

from __future__ import annotations

import argparse
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
        _owned_packet_bytes,
        pool_inventory_fragments,
        write_json_atomic,
    )
    from ._extraction_chunk_planner import plan_extract_packet, plan_inventory_chunks
    from ._graph_builder import main as render_graph
    from ._run_diagnostics import RunDiagnostics, _measure_file
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
        _owned_packet_bytes,
        pool_inventory_fragments,
        write_json_atomic,
    )
    from _extraction_chunk_planner import plan_extract_packet, plan_inventory_chunks
    from _graph_builder import main as render_graph
    from _run_diagnostics import RunDiagnostics, _measure_file
    from _semantic_graph_compiler import compile_semantic_graph, validate_semantic_payload
    from _source_packet import collect_source_packet, write_source_packet
    from _tex_macro_reader import extract_macros, write_macros


SKILL_DIR = Path(__file__).resolve().parents[1]
BASE_PATH = SKILL_DIR / "base.json"


def _inventory_job(chunk: dict) -> dict:
    """Return every immutable path required by one inventory worker."""

    return {
        "chunk_id": chunk["chunk_id"],
        "instruction": str(SKILL_DIR / "instructions" / "inventory.md"),
        "schema": str(SKILL_DIR / "inventory.schema.json"),
        "packet": chunk["packet_path"],
        "progress_path": chunk["progress_path"],
        "output": chunk["fragment_path"],
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


def _read_json(path: Path) -> dict:
    """Read one expected JSON object with a narrow diagnostic on malformed input."""

    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


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
    """Prepare active source and its minimum deterministic inventory chunk plan."""

    entrypoint = entrypoint.resolve()
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = RunDiagnostics.initialize(run_dir, entrypoint=entrypoint)
    source_path = run_dir / "source-packet.txt"
    inventory_manifest_path = run_dir / "inventory-chunks.json"
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
            outputs=[inventory_manifest_path, state_path, run_dir / "inventory-packets"],
        ):
            manifest = plan_inventory_chunks(
                packet.text,
                source_packet_path=source_path,
                output_dir=run_dir,
            )
            state = {
                "entrypoint": str(entrypoint),
                "source_packet": str(source_path),
                "inventory_manifest": str(inventory_manifest_path),
            }
            write_json_atomic(state, state_path)
        for chunk in manifest["chunks"]:
            diagnostics.record_artifact(
                Path(chunk["packet_path"]),
                kind="inventory-packet",
                phase="planning",
            )
        return {
            **state,
            "next_jobs": [_inventory_job(item) for item in manifest["chunks"]],
            "diagnostics": str(diagnostics.path),
        }
    except BaseException as exc:
        diagnostics.finish(status="failure", error=exc)
        raise


def advance_inventory(fragment_manifest_path: Path, run_dir: Path) -> dict:
    """Pool inventory fragments and materialize exactly one extract handoff."""

    run_dir = run_dir.resolve()
    diagnostics = _open_phase_diagnostics(run_dir)
    inventory_path = run_dir / "inventory-ir.json"
    extract_manifest_path = run_dir / "extract-chunks.json"
    state_path = run_dir / "run-state.json"
    recoverable_pooling_failure = False
    try:
        state = _read_json(state_path)
        fragment_manifest, fragments = _load_fragment_manifest(
            fragment_manifest_path.resolve()
        )
        chunk_manifest = _read_json(run_dir / "inventory-chunks.json")
        manifest_paths = [Path(path) for path in fragment_manifest["fragments"]]
        fragments_by_chunk: dict[str, dict] = {}
        paths_by_chunk: dict[str, Path] = {}
        for fragment_path, fragment in zip(manifest_paths, fragments, strict=True):
            chunk_id = fragment.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ValueError("inventory fragment requires a nonempty chunk_id")
            if chunk_id in fragments_by_chunk:
                raise ValueError(f"duplicate inventory fragment chunk id: {chunk_id}")
            fragments_by_chunk[chunk_id] = fragment
            paths_by_chunk[chunk_id] = fragment_path
        ordered_chunk_ids = [str(chunk["chunk_id"]) for chunk in chunk_manifest["chunks"]]
        unknown = sorted(set(fragments_by_chunk) - set(ordered_chunk_ids))
        missing = sorted(set(ordered_chunk_ids) - set(fragments_by_chunk))
        if unknown or missing:
            raise ValueError(
                f"inventory fragment ownership mismatch: missing={missing!r}, unknown={unknown!r}"
            )
        fragments = [fragments_by_chunk[chunk_id] for chunk_id in ordered_chunk_ids]
        fragment_paths = [paths_by_chunk[chunk_id] for chunk_id in ordered_chunk_ids]
        packet_paths = [Path(chunk["packet_path"]) for chunk in chunk_manifest["chunks"]]
        try:
            with diagnostics.stage(
                "pooling",
                inputs=[*fragment_paths, *packet_paths],
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
        for chunk, fragment_path in zip(
            chunk_manifest["chunks"], fragment_paths, strict=True
        ):
            packet_artifact = diagnostics.record_artifact(
                Path(chunk["packet_path"]),
                kind="inventory-packet",
                phase="planning",
            )
            fragment_artifact = diagnostics.record_artifact(
                fragment_path,
                kind="inventory-fragment",
                phase="inventory",
                counts=_structured_record_counts(
                    fragments_by_chunk[str(chunk["chunk_id"])]
                ),
            )
            diagnostics.record_ratio(
                "inventory-fragment-to-owned-packet",
                numerator=fragment_artifact,
                denominator=packet_artifact,
                job_id=str(chunk["chunk_id"]),
                numerator_bytes=len(
                    canonical_json_bytes(
                        fragments_by_chunk[str(chunk["chunk_id"])]
                    )
                ),
                denominator_bytes=_owned_packet_bytes(chunk),
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
            numerator_bytes=sum(
                len(canonical_json_bytes(fragments_by_chunk[chunk_id]))
                for chunk_id in ordered_chunk_ids
            ),
            denominator_bytes=sum(
                _owned_packet_bytes(chunk) for chunk in chunk_manifest["chunks"]
            ),
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
                    validate_semantic_payload(semantic, inventory)
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
    for mode in ("advance-inventory", "finalize-extract"):
        phase = subparsers.add_parser(mode)
        phase.add_argument("fragment_manifest")
        phase.add_argument("--run-dir", required=True)
        if mode == "finalize-extract":
            phase.add_argument("--html")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.mode == "prepare":
        report = prepare(Path(args.entrypoint), Path(args.run_dir))
    elif args.mode == "advance-inventory":
        report = advance_inventory(Path(args.fragment_manifest), Path(args.run_dir))
    else:
        report = finalize_extract(
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

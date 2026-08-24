#!/usr/bin/env python3
"""Declare inventory Rutter logic and dispense one Voyage per worker chunk."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Mapping, Sequence
from uuid import uuid4

from jsonschema import ValidationError
from officina.rutter import (
    DiagnosisCase,
    DiagnoseAnswer,
    LLMStep,
    MachineStep,
    QuestionCase,
    Rutter,
    RutterDefinitionError,
    Terminal,
    TransitionContext,
    TransitionHook,
    Turn,
    VoyageDispenser,
    after,
    voyage_dispenser_cli,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from . import _voyage_support as _support
from ._chunk_extractor import extract_inventory_chunks, load_chunk_manifest


_RUTTER_ID = "math-graph-inventory-voyage"
_DEBUG_RUTTER_ID = "math-graph-inventory-voyage-debug"
_DEFAULT_RUTTER_NAME = "inventory-voyage"
_DEBUG_RUTTER_NAME = "inventory-voyage-debug"
_DIAGNOSIS_HOOK_ID = "inventory-diagnosis"
_REPORT_EVOLUTION = "report"
_RECORD_EVOLUTION = "record"

_SKILL_ROOT = Path(__file__).resolve().parents[2]
_STATE_ROOT = Path(__file__).resolve().parent
_INVENTORY_INSTRUCTION_PATH = _SKILL_ROOT / "instructions" / "inventory.md"
_INVENTORY_SCHEMA_PATH = _SKILL_ROOT / "schemas" / "inventory.schema.json"
_REPORT_REQUEST = (
    "Read the displayed packet from your immutable chunk. Update the prior "
    "cumulative inventory and return the complete inventory snapshot for the "
    "chunk through this packet."
)
_REPORT_PROMPT = (
    f"{_INVENTORY_INSTRUCTION_PATH.read_text(encoding='utf-8')}\n\n"
    f"{_REPORT_REQUEST}"
)
_PACKET_CHARS = 3_000
_DOC_ENTRYPOINT_DESCRIPTION = "Path to the root TeX or Markdown document."
_CHUNK_COUNT_DESCRIPTION = "Requested positive number of inventory chunks."
_MODES = {
    "default": {
        "description": "Run inventory Voyages without diagnostic hooks.",
        "arguments": {
            "doc_entrypoint": _DOC_ENTRYPOINT_DESCRIPTION,
            "chunk_count": _CHUNK_COUNT_DESCRIPTION,
        },
    },
    "debug": {
        "description": (
            "Attach inventory diagnosis hooks using a supplied gold standard."
        ),
        "arguments": {
            "doc_entrypoint": _DOC_ENTRYPOINT_DESCRIPTION,
            "chunk_count": _CHUNK_COUNT_DESCRIPTION,
            "inventory_gold_standard": (
                "Path to the inventory gold-standard JSON used by diagnosis hooks."
            ),
        },
    },
}
_DIAGNOSIS_GUIDANCE = (
    "Compare only gold records whose primary source location overlaps the packets "
    "processed so far.",
    "Treat local IDs as opaque; compare mathematical entities and dependencies.",
    "Make any proposed fix paper-independent and target inventory.md.",
)


def _evolutions() -> dict[str, object]:
    return {
        _REPORT_EVOLUTION: LLMStep(
            _REPORT_PROMPT,
            response_schema={
                "type": "object",
                "properties": {
                    "outcome": {"const": "reported"},
                    "packet_id": {"type": "string", "minLength": 1},
                    "inventory": {"type": "object"},
                },
                "required": ["outcome", "packet_id", "inventory"],
                "additionalProperties": False,
            },
            data=_support.report_data,
            assess_response=_support.assess_report,
            next_on_outcome=_RECORD_EVOLUTION,
        ),
        _RECORD_EVOLUTION: MachineStep(
            _support.record_iteration,
            mode="repeat-safe",
            next_on_outcome={"more": _REPORT_EVOLUTION, "done": "complete"},
        ),
        "complete": Terminal(result_constructor=_support.complete_result),
    }


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _canonical_text(value: object) -> str:
    return json.dumps(
        _plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _frozen_gold(context: TransitionContext) -> dict[str, object]:
    text = context.evolution.charter.data.get("inventory_gold_standard_text")
    digest = context.evolution.charter.data.get(
        "inventory_gold_standard_sha256"
    )
    if type(text) is not str or type(digest) is not str:
        raise RutterDefinitionError("debug inventory Voyage has no frozen gold")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise RutterDefinitionError("debug inventory gold snapshot hash is invalid")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RutterDefinitionError("debug inventory gold snapshot is invalid") from error
    if not isinstance(value, dict):
        raise RutterDefinitionError("debug inventory gold must be a JSON object")
    return value


def _same_source(left: str, right: str) -> bool:
    return (
        left == right
        or left.endswith("/" + right)
        or right.endswith("/" + left)
    )


def _project_gold(
    gold: Mapping[str, object],
    coverage: set[tuple[str, int]],
) -> dict[str, object]:
    files = gold.get("files")
    if not isinstance(files, list) or any(type(path) is not str for path in files):
        raise RutterDefinitionError("debug inventory gold files are invalid")

    def overlaps(record: object, location_key: str) -> bool:
        if not isinstance(record, Mapping):
            raise RutterDefinitionError("debug inventory gold record is invalid")
        location = record.get(location_key)
        if (
            not isinstance(location, list)
            or len(location) != 3
            or any(type(value) is not int for value in location)
        ):
            raise RutterDefinitionError(
                "debug inventory gold record location is invalid"
            )
        file_index, start, end = location
        if file_index < 0 or file_index >= len(files) or start < 1 or end < start:
            raise RutterDefinitionError(
                "debug inventory gold record location is outside its files"
            )
        source = files[file_index]
        return any(
            _same_source(source, covered_source) and start <= line <= end
            for covered_source, line in coverage
        )

    sections = (("nodes", "statement_location"), ("edges", "location"), ("gaps", "location"))
    projected: dict[str, object] = {
        "ir_version": gold.get("ir_version"),
        "chunk_id": gold.get("chunk_id"),
        "files": list(files),
    }
    for section, location_key in sections:
        records = gold.get(section)
        if not isinstance(records, list):
            raise RutterDefinitionError(
                f"debug inventory gold {section} must be an array"
            )
        projected[section] = [
            dict(record)
            for record in records
            if overlaps(record, location_key)
        ]
    return projected


_DIAGNOSIS_RUTTER = DiagnoseAnswer()


def _diagnosis_rutter(context: TransitionContext) -> Rutter:
    del context
    return _DIAGNOSIS_RUTTER


def _diagnosis_charter(context: TransitionContext):
    if not isinstance(context.record, Turn) or context.record.response is None:
        raise RutterDefinitionError("inventory diagnosis requires an accepted report")
    chunk = context.evolution.charter.data.get("chunk")
    if not isinstance(chunk, Mapping):
        raise RutterDefinitionError("inventory diagnosis chunk is unavailable")
    packets = chunk.get("packets")
    if not isinstance(packets, (list, tuple)):
        raise RutterDefinitionError("inventory diagnosis packets are unavailable")
    completed = len(context.evolution.history.turns(_REPORT_EVOLUTION)) + 1
    coverage: set[tuple[str, int]] = set()
    for packet in packets[:completed]:
        if not isinstance(packet, Mapping):
            raise RutterDefinitionError("inventory diagnosis packet is invalid")
        coordinates = packet.get("coordinates")
        if not isinstance(coordinates, (list, tuple)):
            raise RutterDefinitionError(
                "inventory diagnosis packet coordinates are unavailable"
            )
        for coordinate in coordinates:
            if not isinstance(coordinate, Mapping):
                raise RutterDefinitionError(
                    "inventory diagnosis coordinate is invalid"
                )
            source = coordinate.get("source_file")
            line = coordinate.get("line")
            if type(source) is not str or type(line) is not int:
                raise RutterDefinitionError(
                    "inventory diagnosis coordinate is invalid"
                )
            coverage.add((source, line))
    expected = _project_gold(_frozen_gold(context), coverage)
    actual = context.record.response.get("inventory")
    if not isinstance(actual, Mapping):
        raise RutterDefinitionError("inventory diagnosis requires reported inventory")
    packet_id = context.record.response.get("packet_id")
    question = QuestionCase(
        f"inventory-{packet_id}",
        "Which inventory entities and direct dependency edges are recovered so far?",
        _canonical_text(expected),
        format_hint={
            "ir_version": 3,
            "chunk_id": "...",
            "files": [],
            "nodes": [],
            "edges": [],
            "gaps": [],
        },
        metadata={
            "packet_id": packet_id,
            "covered_coordinates": [
                {"source_file": source, "line": line}
                for source, line in sorted(coverage)
            ],
            "diagnosis_guidance": _DIAGNOSIS_GUIDANCE,
        },
    )
    return DiagnosisCase(
        question,
        _canonical_text(actual),
        ask_for_fix=True,
    ).to_json()


INVENTORY_VOYAGE = Rutter(
    id=_RUTTER_ID,
    version=1,
    start=_REPORT_EVOLUTION,
    evolutions=_evolutions(),
)

DEBUG_INVENTORY_VOYAGE = Rutter(
    id=_DEBUG_RUTTER_ID,
    version=1,
    start=_REPORT_EVOLUTION,
    evolutions=_evolutions(),
    hooks=(
        TransitionHook(
            _DIAGNOSIS_HOOK_ID,
            on=after(_REPORT_EVOLUTION),
            rutter_constructor=_diagnosis_rutter,
            charter_constructor=_diagnosis_charter,
        ),
    ),
)

_RUTTERS = {
    _DEFAULT_RUTTER_NAME: INVENTORY_VOYAGE,
    _DEBUG_RUTTER_NAME: DEBUG_INVENTORY_VOYAGE,
}


def _write_json(stream: object, value: object) -> None:
    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")


def _load_chunk(record: dict[str, object]) -> dict[str, object]:
    chunk_path = Path(str(record.get("chunk_path", ""))).resolve()
    expected = record.get("chunk_sha256")
    if (
        not chunk_path.is_file()
        or type(expected) is not str
        or hashlib.sha256(chunk_path.read_bytes()).hexdigest() != expected
    ):
        raise ValueError(
            f"inventory chunk identity is invalid: {record.get('chunk_id')}"
        )
    value = json.loads(chunk_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("chunk_id") != record.get("chunk_id"):
        raise ValueError("inventory chunk file does not match its manifest")
    return value


def _gold_charter_data(path: Path) -> dict[str, object]:
    try:
        text = path.read_bytes().decode("utf-8")
        value = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "inventory gold standard must be readable UTF-8 JSON"
        ) from error
    if not isinstance(value, dict):
        raise ValueError("inventory gold standard must be a JSON object")
    try:
        _support.validate_inventory_fragment(value)
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError("inventory gold standard is schema-invalid") from error
    return {
        "inventory_gold_standard_text": text,
        "inventory_gold_standard_sha256": hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest(),
    }


def setup_run(
    chunk_manifest: Path,
    run_dir: Path,
    *,
    run_prefix: str,
    mode: str = "default",
    inventory_gold_standard: Path | None = None,
) -> dict[str, object]:
    """Create one durable inventory Voyage for every manifest chunk."""

    if mode not in _MODES:
        raise ValueError(f"unknown inventory Voyage mode {mode!r}")
    if mode == "debug" and inventory_gold_standard is None:
        raise ValueError("debug mode requires an inventory gold standard")
    if mode == "default" and inventory_gold_standard is not None:
        raise ValueError("default mode does not accept an inventory gold standard")
    extra_charter_data = (
        {}
        if inventory_gold_standard is None
        else _gold_charter_data(inventory_gold_standard.resolve())
    )
    definition = (
        INVENTORY_VOYAGE if mode == "default" else DEBUG_INVENTORY_VOYAGE
    )
    rutter_name = (
        _DEFAULT_RUTTER_NAME if mode == "default" else _DEBUG_RUTTER_NAME
    )

    manifest = load_chunk_manifest(chunk_manifest)
    run_dir = run_dir.resolve()
    voyages_dir = run_dir / "voyages" / run_prefix
    if _voyage_paths(run_dir, run_prefix):
        raise FileExistsError(
            f"inventory Voyages already exist for run prefix {run_prefix!r}"
        )
    voyages_dir.mkdir(parents=True, exist_ok=True)
    voyage_ids: list[str] = []
    for chunk_value in manifest["chunks"]:
        if not isinstance(chunk_value, dict):
            raise ValueError("inventory chunk manifest contains a non-object chunk")
        chunk_id = chunk_value.get("chunk_id")
        if (
            type(chunk_id) is not str
            or type(chunk_value.get("fragment_path")) is not str
        ):
            raise ValueError("inventory chunk manifest record is incomplete")
        voyage_id = f"{run_prefix}-voyage-{uuid4().hex}"
        _support.setup_voyage(
            definition,
            _load_chunk(chunk_value),
            voyages_dir / voyage_id,
            run_dir
            / "artifacts"
            / run_prefix
            / "inventories"
            / f"{voyage_id}.json",
            rutter_name=rutter_name,
            run_dir=run_dir,
            inventory_instruction_path=_INVENTORY_INSTRUCTION_PATH,
            inventory_schema_path=_INVENTORY_SCHEMA_PATH,
            extra_charter_data=extra_charter_data,
        )
        voyage_ids.append(voyage_id)
    return {
        "run_dir": str(run_dir),
        "chunk_manifest": str(chunk_manifest.resolve()),
        "mode": mode,
        "run_prefix": run_prefix,
        "voyages": voyage_ids,
        "voyage_count": len(voyage_ids),
    }


def _voyage_paths(
    root: Path,
    run_prefix: str | None = None,
) -> dict[str, Path]:
    root = root.resolve()
    reckoning = _support.reckoning_name()
    if (root / reckoning).is_file():
        return {root.name: root}
    voyages = root / "voyages"
    if run_prefix is not None:
        collection = voyages / run_prefix
        if not collection.is_dir():
            return {}
        return {
            child.name: child
            for child in sorted(collection.iterdir(), key=lambda path: path.name)
            if child.is_dir() and (child / reckoning).is_file()
        }
    if not voyages.is_dir():
        return {}
    paths: dict[str, Path] = {}
    for child in sorted(voyages.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        if (child / reckoning).is_file():
            paths[child.name] = child
            continue
        for voyage_path in sorted(child.iterdir(), key=lambda path: path.name):
            if voyage_path.is_dir() and (voyage_path / reckoning).is_file():
                paths[voyage_path.name] = voyage_path
    return paths


def make_voyage_dispenser() -> VoyageDispenser:
    """Chunk one document and expose its inventory Voyages under module-owned state."""

    root = _STATE_ROOT.resolve()

    def initiate(
        mode: str,
        *,
        run_prefix: str,
        doc_entrypoint: str,
        chunk_count: str,
        inventory_gold_standard: str | None = None,
    ) -> None:
        try:
            requested_chunks = int(chunk_count)
        except (TypeError, ValueError) as error:
            raise ValueError("chunk_count must be a positive integer") from error
        if requested_chunks < 1:
            raise ValueError("chunk_count must be a positive integer")
        source = Path(doc_entrypoint).resolve()
        if source.suffix.lower() not in {".tex", ".md"}:
            raise ValueError("doc_entrypoint must be a .tex or .md file")
        report = extract_inventory_chunks(
            source,
            root / "artifacts" / run_prefix,
            workers=requested_chunks,
            packet_chars=_PACKET_CHARS,
        )
        setup_run(
            Path(str(report["manifest_path"])),
            root,
            run_prefix=run_prefix,
            mode=mode,
            inventory_gold_standard=(
                None
                if inventory_gold_standard is None
                else Path(inventory_gold_standard)
            ),
        )

    def paths(run_prefix: str | None = None) -> dict[str, Path]:
        return _voyage_paths(root, run_prefix)

    def release(voyage_id: str) -> None:
        voyage_path = paths()[voyage_id]
        voyages_dir = (root / "voyages").resolve()
        relative = voyage_path.resolve().relative_to(voyages_dir)
        if (
            voyage_path.is_symlink()
            or voyage_path.parent.is_symlink()
            or len(relative.parts) not in {1, 2}
        ):
            raise ValueError("inventory Voyage working directory is unsafe to release")
        shutil.rmtree(voyage_path)

    return VoyageDispenser(
        modes=_MODES,
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix: tuple(paths(run_prefix)),
        open_voyage=lambda voyage_id: _support.open_voyage(
            _RUTTERS, paths()[voyage_id]
        ),
        release_voyage=release,
    )


def _run_dispenser(argv: Sequence[str]) -> int:
    return voyage_dispenser_cli(make_voyage_dispenser(), argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch discovery, initialization, or a standard Voyage operation."""

    raw = list(argv) if argv is not None else list(sys.argv[1:])
    try:
        return _run_dispenser(raw)
    except FileExistsError as error:
        payload = {"error": {"code": "state-error", "message": str(error)}}
        _write_json(sys.stderr, payload)
        return 5
    except (OSError, ValueError, json.JSONDecodeError) as error:
        payload = {"error": {"code": "input-error", "message": str(error)}}
        _write_json(sys.stderr, payload)
        return 3


class Interface(PythonArgvMachineInterface):
    """Expose inventory initialization and standard multi-Voyage operations."""

    prog = "inventory_voyage_dispenser.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

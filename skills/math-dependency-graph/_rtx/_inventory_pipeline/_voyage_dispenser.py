#!/usr/bin/env python3
"""Declare inventory Rutter logic and dispense one Voyage per worker chunk."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Sequence

from officina.rutter import (
    LLMStep,
    MachineStep,
    Rutter,
    Terminal,
    TransitionHook,
    VoyageDispenser,
    after,
    voyage_dispenser_cli,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from . import _voyage_support as _support


_RUTTER_ID = "math-graph-inventory-voyage"
_DEBUG_RUTTER_ID = "math-graph-inventory-voyage-debug"
_DEFAULT_RUTTER_NAME = "inventory-voyage"
_DEBUG_RUTTER_NAME = "inventory-voyage-debug"
_DIAGNOSIS_HOOK_ID = "inventory-diagnosis"
_INTRODUCE_EVOLUTION = "introduce"
_PREPARE_EVOLUTION = "prepare-packet"
_REPORT_EVOLUTION = "report"
_RECORD_EVOLUTION = "record"

_SKILL_ROOT = Path(__file__).resolve().parents[2]
_STATE_ROOT = Path(__file__).resolve().parent
_INVENTORY_INSTRUCTION_PATH = _SKILL_ROOT / "instructions" / "inventory.md"
_INVENTORY_SCHEMA_PATH = _SKILL_ROOT / "schemas" / "inventory.schema.json"
_INVENTORY_REPORT_SCHEMA = _support.inventory_report_schema(
    json.loads(_INVENTORY_SCHEMA_PATH.read_text(encoding="utf-8"))
)
_REPORT_REQUEST = (
    "Read the displayed packet string, update inventory_file, and return only "
    "the nodes, edges, and gaps newly appended for this packet."
)
_INTRODUCTION_PROMPT = (
    "Read and retain the supplied inventory_instruction and "
    "the cumulative_packets_file and inventory_file paths for all subsequent "
    "packet reports."
)
_REPORT_PROMPT = f"Apply the retained inventory instruction. {_REPORT_REQUEST}"
_DEBUG_REPORT_PROMPT = (
    f"{_REPORT_PROMPT}\n\n"
    "Before any comparison with a reference answer, include decision_basis: one "
    "concise account of the source-visible evidence and inventory rules that "
    "determined this snapshot."
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
            "inventory_source_aliases": (
                "Path to a JSON object mapping gold source paths to inventory "
                "source paths; use an empty object when paths already align."
            ),
        },
    },
}
INVENTORY_VOYAGE = Rutter(
    id=_RUTTER_ID,
    version=7,
    start=_INTRODUCE_EVOLUTION,
    evolutions={
        _INTRODUCE_EVOLUTION: LLMStep(
            _INTRODUCTION_PROMPT,
            response_schema={
                "type": "object",
                "properties": {"outcome": {"const": "ready"}},
                "required": ["outcome"],
                "additionalProperties": False,
            },
            data=_support.introduction_data,
            next_on_outcome=_PREPARE_EVOLUTION,
        ),
        _PREPARE_EVOLUTION: MachineStep(
            _support.prepare_packet,
            mode="repeat-safe",
            next_on_outcome=_REPORT_EVOLUTION,
        ),
        _REPORT_EVOLUTION: LLMStep(
            _REPORT_PROMPT,
            response_schema=_INVENTORY_REPORT_SCHEMA,
            data=_support.report_data,
            assess_response=_support.assess_report,
            next_on_outcome=_RECORD_EVOLUTION,
        ),
        _RECORD_EVOLUTION: MachineStep(
            _support.record_iteration,
            mode="repeat-safe",
            next_on_outcome={"more": _PREPARE_EVOLUTION, "done": "complete"},
        ),
        "complete": Terminal(result_constructor=_support.complete_result),
    },
)

DEBUG_INVENTORY_VOYAGE = Rutter(
    id=_DEBUG_RUTTER_ID,
    version=7,
    start=_INTRODUCE_EVOLUTION,
    evolutions={
        _INTRODUCE_EVOLUTION: LLMStep(
            _INTRODUCTION_PROMPT,
            response_schema={
                "type": "object",
                "properties": {"outcome": {"const": "ready"}},
                "required": ["outcome"],
                "additionalProperties": False,
            },
            data=_support.introduction_data,
            next_on_outcome=_PREPARE_EVOLUTION,
        ),
        _PREPARE_EVOLUTION: MachineStep(
            _support.prepare_packet,
            mode="repeat-safe",
            next_on_outcome=_REPORT_EVOLUTION,
        ),
        _REPORT_EVOLUTION: LLMStep(
            _DEBUG_REPORT_PROMPT,
            response_schema={
                **_INVENTORY_REPORT_SCHEMA,
                "properties": {
                    **_INVENTORY_REPORT_SCHEMA["properties"],
                    "decision_basis": {"type": "string", "minLength": 1},
                },
                "required": [*_INVENTORY_REPORT_SCHEMA["required"], "decision_basis"],
            },
            data=_support.report_data,
            assess_response=_support.assess_debug_report,
            next_on_outcome=_RECORD_EVOLUTION,
        ),
        _RECORD_EVOLUTION: MachineStep(
            _support.record_iteration,
            mode="repeat-safe",
            next_on_outcome={"more": _PREPARE_EVOLUTION, "done": "complete"},
        ),
        "complete": Terminal(result_constructor=_support.complete_result),
    },
    hooks=(
        TransitionHook(
            _DIAGNOSIS_HOOK_ID,
            on=after(_REPORT_EVOLUTION),
            rutter_constructor=_support.diagnosis_rutter,
            charter_constructor=_support.diagnosis_charter,
        ),
    ),
)

_RUTTERS_BY_NAME = {
    _DEFAULT_RUTTER_NAME: INVENTORY_VOYAGE,
    _DEBUG_RUTTER_NAME: DEBUG_INVENTORY_VOYAGE,
}


def make_voyage_dispenser() -> VoyageDispenser:
    """Chunk one document and expose its inventory Voyages under module-owned state."""

    root = _STATE_ROOT.resolve()

    def initiate(
        mode: str,
        *,
        run_id: str,
        doc_entrypoint: str,
        chunk_count: str,
        inventory_gold_standard: str | None = None,
        inventory_source_aliases: str | None = None,
    ) -> None:
        _support.initiate_run(
            root,
            mode=mode,
            run_id=run_id,
            doc_entrypoint=doc_entrypoint,
            chunk_count=chunk_count,
            packet_chars=_PACKET_CHARS,
            definitions={
                "default": (INVENTORY_VOYAGE, _DEFAULT_RUTTER_NAME),
                "debug": (DEBUG_INVENTORY_VOYAGE, _DEBUG_RUTTER_NAME),
            },
            inventory_instruction_path=_INVENTORY_INSTRUCTION_PATH,
            inventory_schema_path=_INVENTORY_SCHEMA_PATH,
            inventory_gold_standard=inventory_gold_standard,
            inventory_source_aliases=inventory_source_aliases,
        )

    def paths(run_prefix: str | None = None) -> dict[str, Path]:
        return _support.voyage_paths(root, run_prefix)

    return VoyageDispenser(
        modes=_MODES,
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix: tuple(paths(run_prefix)),
        open_voyage=lambda voyage_id: _support.open_voyage(
            _RUTTERS_BY_NAME, paths()[voyage_id]
        ),
        release_voyage=lambda voyage_id: _support.release_voyage(
            root, voyage_id, debug_rutter_id=_DEBUG_RUTTER_ID
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch discovery, initialization, or a standard Voyage operation."""

    raw = list(argv) if argv is not None else list(sys.argv[1:])
    try:
        return voyage_dispenser_cli(make_voyage_dispenser(), raw)
    except FileExistsError as error:
        payload = {"error": {"code": "state-error", "message": str(error)}}
        _support.write_json(sys.stderr, payload)
        return 5
    except (OSError, ValueError, json.JSONDecodeError) as error:
        payload = {"error": {"code": "input-error", "message": str(error)}}
        _support.write_json(sys.stderr, payload)
        return 3


class Interface(PythonArgvMachineInterface):
    """Expose inventory initialization and standard multi-Voyage operations."""

    prog = "inventory_voyage_dispenser.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

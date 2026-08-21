#!/usr/bin/env python3
"""Materialize the sole bounded LLM packet for graph extraction."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Iterable

import jsonschema

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._semantic_graph_compiler import load_json_object
except ImportError:  # pragma: no cover - supports direct script execution
    from _semantic_graph_compiler import load_json_object


TOTAL_CONTEXT_CEILING_TOKENS = 95_000
DEFAULT_TARGET_TOKENS = 60_000
DEFAULT_HARD_MAX_TOKENS = 60_700
CHUNK_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "chunk-plan.schema.json"


def validate_chunk_manifest(payload: dict) -> None:
    """Validate manifest shape, mode fields, and exactly-once assignments."""

    schema = json.loads(CHUNK_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    validator_class(schema).validate(payload)
    if payload["target_tokens"] > payload["hard_max_tokens"]:
        raise ValueError("chunk target tokens exceed hard maximum")
    chunk_ids = [chunk["chunk_id"] for chunk in payload["chunks"]]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("chunk ids must be unique")
    if chunk_ids != ["extract-001"]:
        raise ValueError("extract planning requires exactly one extract-001 chunk")
    chunk = payload["chunks"][0]
    required_paths = (
        "packet_path",
        "fragment_path",
        "progress_path",
        "sidecar_path",
        "source_packet_path",
        "entrypoint_path",
    )
    if any(not isinstance(chunk.get(path), str) or not chunk[path] for path in required_paths):
        raise ValueError(
            "extract chunk requires packet, fragment, sidecar, source, and entrypoint paths"
        )


def estimate_tokens(value: str | object) -> int:
    """Return a conservative deterministic token estimate from UTF-8 text."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, math.ceil(len(text) / 4))


def _write_text_atomic(text: str, path: Path) -> None:
    """Replace one UTF-8 packet atomically beside its final destination."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _write_json_atomic(payload: dict, path: Path, *, compact: bool = False) -> None:
    """Write one JSON artifact atomically, optionally without presentation space."""

    kwargs = {"ensure_ascii": False}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    _write_text_atomic(json.dumps(payload, **kwargs) + "\n", path)


def plan_extract_packet(
    inventory: dict,
    *,
    source_packet_path: Path,
    entrypoint_path: Path,
    output_dir: Path,
) -> dict:
    """Materialize the sole document-wide extract packet from pooled inventory.

    The inventory is an index of graph-relevant findings, not a source-text
    substitute.  The packet therefore embeds the pooled records but carries the
    immutable source and coordinate sidecar by path only.  The worker may reopen
    only registered locations with the stated bounded lookup limits.
    """

    if inventory.get("ir_version") != 2 or inventory.get("chunk_id") != "pooled":
        raise ValueError("extract planning requires pooled inventory version 2")
    files = inventory.get("files")
    if not isinstance(files, list) or not files or not all(
        isinstance(item, str) and item for item in files
    ):
        raise ValueError("pooled inventory requires a nonempty file table")
    source_path = source_packet_path.resolve()
    if not source_path.is_file():
        raise ValueError("extract planning requires an existing immutable source packet")
    entrypoint = entrypoint_path.resolve()
    if not entrypoint.is_file():
        raise ValueError("extract planning requires the retained TeX entrypoint")
    output_dir = output_dir.resolve()
    chunk_id = "extract-001"
    packet_path = output_dir / "extract-packets" / f"{chunk_id}.json"
    fragment_path = output_dir / "extract-fragments" / f"{chunk_id}.json"
    progress_path = output_dir / "progress" / f"{chunk_id}.progress.md"
    sidecar_path = output_dir / "extract-sidecars" / f"{chunk_id}.json"
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    lookup_rules = {
        "max_context_lines": 20,
        "max_locations_per_request": 32,
        "require_registered_locations": True,
    }
    sidecar = {
        "sidecar_version": 1,
        "source_packet_path": str(source_path),
        "source_sha256": source_sha256,
        "files": deepcopy(files),
        "lookup_rules": deepcopy(lookup_rules),
    }
    packet = {
        "packet_version": 1,
        "mode": "extract",
        "chunk_id": chunk_id,
        "inventory": deepcopy(inventory),
        "entrypoint_path": str(entrypoint),
        "source_packet_path": str(source_path),
        "coordinate_sidecar_path": str(sidecar_path),
        "lookup_rules": lookup_rules,
    }
    packet_tokens = estimate_tokens(packet) + estimate_tokens(sidecar)
    projected_total_tokens = packet_tokens + 12_000
    if projected_total_tokens > TOTAL_CONTEXT_CEILING_TOKENS:
        raise ValueError("essential whole-document extract packet exceeds context ceiling")
    _write_json_atomic(sidecar, sidecar_path, compact=True)
    _write_json_atomic(packet, packet_path, compact=True)
    manifest = {
        "plan_version": 1,
        "mode": "extract",
        "source": str(source_path),
        "target_tokens": DEFAULT_TARGET_TOKENS,
        "hard_max_tokens": DEFAULT_HARD_MAX_TOKENS,
        "chunks": [
            {
                "chunk_id": chunk_id,
                "estimated_tokens": packet_tokens,
                "projected_total_tokens": projected_total_tokens,
                "packet_path": str(packet_path),
                "fragment_path": str(fragment_path),
                "progress_path": str(progress_path),
                "sidecar_path": str(sidecar_path),
                "source_packet_path": str(source_path),
                "entrypoint_path": str(entrypoint),
            }
        ],
    }
    validate_chunk_manifest(manifest)
    _write_json_atomic(manifest, output_dir / "extract-chunks.json")
    return manifest


def main(argv: Iterable[str] | None = None) -> None:
    """Plan the extraction phase and emit a concise machine report."""

    parser = argparse.ArgumentParser(description="Plan bounded mathematical extraction chunks.")
    parser.add_argument("mode", choices=("extract",))
    parser.add_argument("source", help="Pooled inventory")
    parser.add_argument("--source-packet", required=True, help="Immutable source packet")
    parser.add_argument("--entrypoint", required=True, help="Retained TeX entrypoint")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    parser.add_argument("--hard-max-tokens", type=int, default=DEFAULT_HARD_MAX_TOKENS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    source_path = Path(args.source).resolve()
    output_dir = Path(args.out_dir).resolve()
    manifest = plan_extract_packet(
        load_json_object(source_path, "pooled inventory"),
        source_packet_path=Path(args.source_packet),
        entrypoint_path=Path(args.entrypoint),
        output_dir=output_dir,
    )
    print(
        json.dumps(
            {
                "mode": args.mode,
                "chunks": len(manifest["chunks"]),
                "manifest": str(output_dir / f"{args.mode}-chunks.json"),
            },
            indent=2,
        )
    )


class Interface(PythonArgvMachineInterface):
    """Expose deterministic extraction chunk planning through the machine protocol."""

    prog = "extraction_chunk_planner.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()

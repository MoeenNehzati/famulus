#!/usr/bin/env python3
"""Extract immutable inventory chunks and their iterative packets from TeX."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


SOURCE_INCLUDE_RE = re.compile(
    r"\\(?P<command>input|include)\s*"
    r"(?:\{(?P<braced>[^{}]+)\}|(?P<plain>[^\s%{}]+))"
)
VISIBLE_ENVIRONMENT_TYPES = (
    "assumption",
    "definition",
    "notation",
    "lemma",
    "proposition",
    "theorem",
    "corollary",
    "remark",
    "example",
)
_VISIBLE_ALTERNATION = "|".join(VISIBLE_ENVIRONMENT_TYPES)
NEW_THEOREM_RE = re.compile(
    r"\\newtheorem\*?\s*\{(?P<environment>[A-Za-z@][A-Za-z0-9@*:-]*)\}"
)
DECLARE_THEOREM_RE = re.compile(
    r"\\declaretheorem(?:\s*\[[^]]*\])?\s*"
    r"\{(?P<environment>[A-Za-z@][A-Za-z0-9@*:-]*)\}"
)
NEW_ENVIRONMENT_WRAPPER_RE = re.compile(
    r"\\newenvironment\*?\s*\{(?P<environment>[A-Za-z@][A-Za-z0-9@*:-]*)\}"
    r"(?P<body>.{0,1200}?)\\begin\{(?:" + _VISIBLE_ALTERNATION + r")\}",
    re.DOTALL,
)


def strip_comments(text: str) -> str:
    """Remove TeX comments while preserving escaped percent signs."""

    cleaned_lines: list[str] = []
    for line in text.splitlines():
        index = 0
        cut = len(line)
        while True:
            position = line.find("%", index)
            if position == -1:
                break
            backslashes = 0
            cursor = position - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                cut = position
                break
            index = position + 1
        cleaned_lines.append(line[:cut])
    return "\n".join(cleaned_lines)


def read_tex_text(path: Path) -> str:
    """Read TeX without assuming every legacy source is UTF-8."""

    payload = path.read_bytes()
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("latin-1")


def visible_environment_ranges(
    numbered_lines: Iterable[tuple[int, str]],
    *,
    declaration_text: str,
) -> tuple[tuple[int, int, str, str | None], ...]:
    """Find complete theorem-like source ranges by syntactic declarations."""

    lines = tuple(numbered_lines)
    declared = {
        match.group("environment")
        for pattern in (NEW_THEOREM_RE, DECLARE_THEOREM_RE)
        for match in pattern.finditer(declaration_text)
    }
    declared.update(
        match.group("environment")
        for match in NEW_ENVIRONMENT_WRAPPER_RE.finditer(declaration_text)
    )
    names = (*VISIBLE_ENVIRONMENT_TYPES, *sorted(declared))
    alternation = "|".join(re.escape(name) for name in names)
    begin_re = re.compile(rf"\\begin\{{(?P<environment>{alternation})\}}")
    restatable_re = re.compile(
        rf"\\begin\{{restatable\}}(?:\[[^]]*\])?"
        rf"\{{(?P<environment>{alternation})\}}"
    )
    ranges: list[tuple[int, int, str, str | None]] = []
    for start_index, (start_line, text) in enumerate(lines):
        anchors = [
            (match.group("environment"), None) for match in begin_re.finditer(text)
        ]
        anchors.extend(
            (match.group("environment"), "restatable")
            for match in restatable_re.finditer(text)
        )
        for environment, wrapper in anchors:
            closing = wrapper or environment
            end_re = re.compile(rf"\\end\{{{re.escape(closing)}\}}")
            for end_line, end_text in lines[start_index:]:
                if end_re.search(end_text):
                    ranges.append((start_line, end_line, environment, wrapper))
                    break
    return tuple(ranges)


@dataclass(frozen=True)
class SourceRow:
    """One active source line in TeX expansion order."""

    source_file: str
    line: int
    text: str
    chunk_row: int


@dataclass(frozen=True)
class DocumentSource:
    """Deterministic source material from which worker chunks are extracted."""

    entrypoint: Path
    files: tuple[Path, ...]
    rows: tuple[SourceRow, ...]
    anchors: tuple[dict[str, object], ...]
    packet_anchors: tuple[dict[str, object], ...]
    unresolved: tuple[str, ...]
    cycles: tuple[str, ...]


def source_label(path: Path, project_root: Path) -> str:
    """Return a stable project-relative source label."""

    try:
        return path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_source_include(
    name: str, current_file: Path, project_root: Path
) -> Path | None:
    """Resolve one authored TeX input against its local and project roots."""

    candidate = Path(name.strip())
    if not candidate.suffix:
        candidate = candidate.with_suffix(".tex")
    for root in (current_file.parent, project_root):
        path = candidate if candidate.is_absolute() else root / candidate
        resolved = path.resolve()
        if resolved.is_file():
            return resolved
    return None


def collect_document_source(entrypoint: Path) -> DocumentSource:
    """Expand active TeX inputs into ordered coordinate-bearing source rows."""

    entrypoint = entrypoint.resolve()
    if not entrypoint.is_file():
        raise FileNotFoundError(f"TeX entrypoint not found: {entrypoint}")
    project_root = entrypoint.parent
    discovered: list[Path] = []
    discovered_set: set[Path] = set()
    searchable_by_path: dict[Path, list[str]] = {}
    rows: list[SourceRow] = []
    unresolved: list[str] = []
    cycles: list[str] = []

    def walk(path: Path, active: tuple[Path, ...]) -> None:
        if path not in discovered_set:
            discovered.append(path)
            discovered_set.add(path)
        raw_lines = read_tex_text(path).splitlines()
        searchable = strip_comments("\n".join(raw_lines)).splitlines()
        if len(searchable) < len(raw_lines):
            searchable.extend([""] * (len(raw_lines) - len(searchable)))
        searchable_by_path.setdefault(path, searchable)
        label = source_label(path, project_root)
        for line_number, text in enumerate(searchable, start=1):
            rows.append(SourceRow(label, line_number, text, len(rows) + 1))
            for match in SOURCE_INCLUDE_RE.finditer(text):
                include_name = match.group("braced") or match.group("plain") or ""
                child = resolve_source_include(include_name, path, project_root)
                location = f"{label}:{line_number}"
                if child is None:
                    unresolved.append(f"{location} -> {include_name}")
                elif child in active or child == path:
                    cycles.append(
                        f"{location} -> {source_label(child, project_root)}"
                    )
                else:
                    walk(child, (*active, path))

    walk(entrypoint, ())
    declaration_text = "\n".join(
        line for path in discovered for line in searchable_by_path[path]
    )
    anchors: list[dict[str, object]] = []
    for path in discovered:
        label = source_label(path, project_root)
        for start, end, environment, wrapper in visible_environment_ranges(
            enumerate(searchable_by_path[path], start=1),
            declaration_text=declaration_text,
        ):
            anchor: dict[str, object] = {
                "source_file": label,
                "start_line": start,
                "end_line": end,
                "environment": environment,
            }
            if wrapper is not None:
                anchor["wrapper"] = wrapper
            anchors.append(anchor)
    packet_anchors = list(anchors)
    for path in discovered:
        label = source_label(path, project_root)
        lines = searchable_by_path[path]
        starts: list[int] = []
        for line_number, text in enumerate(lines, start=1):
            if re.search(r"\\begin\{proof\}", text):
                starts.append(line_number)
            if re.search(r"\\end\{proof\}", text) and starts:
                packet_anchors.append(
                    {
                        "source_file": label,
                        "start_line": starts.pop(),
                        "end_line": line_number,
                        "environment": "proof",
                    }
                )
    return DocumentSource(
        entrypoint=entrypoint,
        files=tuple(discovered),
        rows=tuple(rows),
        anchors=tuple(anchors),
        packet_anchors=tuple(packet_anchors),
        unresolved=tuple(unresolved),
        cycles=tuple(cycles),
    )


def _inside_anchor_boundary(
    previous: SourceRow,
    current: SourceRow,
    anchors: tuple[dict[str, object], ...],
) -> bool:
    if previous.source_file != current.source_file:
        return False
    return any(
        anchor["source_file"] == previous.source_file
        and int(anchor["start_line"]) <= previous.line
        and current.line <= int(anchor["end_line"])
        for anchor in anchors
    )


def _at_anchor_boundary(
    previous: SourceRow,
    current: SourceRow,
    anchors: tuple[dict[str, object], ...],
) -> bool:
    if previous.source_file != current.source_file:
        return False
    return any(
        anchor["source_file"] == current.source_file
        and (
            int(anchor["start_line"]) == current.line
            or int(anchor["end_line"]) == previous.line
        )
        for anchor in anchors
    )


def _packet_rows(
    rows: tuple[SourceRow, ...],
    anchors: tuple[dict[str, object], ...],
    packet_chars: int,
) -> list[list[SourceRow]]:
    """Partition source into bounded packets without bisecting anchors."""

    if packet_chars < 1:
        raise ValueError("packet_chars must be positive")
    packets: list[list[SourceRow]] = []
    active: list[SourceRow] = []
    active_chars = 0
    for row in rows:
        row_chars = len(row.text) + 1
        file_boundary = bool(active and active[-1].source_file != row.source_file)
        size_boundary = bool(
            active
            and active_chars + row_chars > packet_chars
            and (
                not active[-1].text.strip()
                or _at_anchor_boundary(active[-1], row, anchors)
                or bool(re.match(r"\s*(?:#{1,6}\s|\\(?:sub)*section\b)", row.text))
            )
            and not _inside_anchor_boundary(active[-1], row, anchors)
        )
        if file_boundary or size_boundary:
            packets.append(active)
            active = []
            active_chars = 0
        active.append(row)
        active_chars += row_chars
    if active:
        packets.append(active)
    if not packets:
        raise ValueError("active TeX source contains no lines")
    return packets


def _render_packet(rows: list[SourceRow]) -> str:
    rendered = [f"{row.chunk_row:06d} | {row.text}" for row in rows]
    return "\n".join(rendered) + "\n"


def _partition_packets(
    packets: list[list[SourceRow]], requested_workers: int
) -> list[list[list[SourceRow]]]:
    """Assign contiguous packets to no more than the requested workers."""

    if requested_workers < 1:
        raise ValueError("workers must be positive")
    worker_count = min(requested_workers, len(packets))
    remaining_chars = sum(
        sum(len(row.text) + 1 for row in packet) for packet in packets
    )
    assignments: list[list[list[SourceRow]]] = []
    cursor = 0
    for worker_index in range(worker_count):
        workers_left = worker_count - worker_index
        packets_left = len(packets) - cursor
        target = max(1, remaining_chars // workers_left)
        assigned: list[list[SourceRow]] = []
        assigned_chars = 0
        while cursor < len(packets):
            must_leave = workers_left - 1
            if assigned and assigned_chars >= target and packets_left > must_leave:
                break
            packet = packets[cursor]
            packet_size = sum(len(row.text) + 1 for row in packet)
            assigned.append(packet)
            assigned_chars += packet_size
            remaining_chars -= packet_size
            cursor += 1
            packets_left -= 1
            if packets_left == must_leave:
                break
        assignments.append(assigned)
    return assignments


def _spans(rows: list[SourceRow]) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    for row in rows:
        if (
            spans
            and spans[-1]["source_file"] == row.source_file
            and int(spans[-1]["end_line"]) + 1 == row.line
        ):
            spans[-1]["end_line"] = row.line
        else:
            spans.append(
                {
                    "source_file": row.source_file,
                    "start_line": row.line,
                    "end_line": row.line,
                }
            )
    return spans


def _owned(anchor: dict[str, object], spans: list[dict[str, object]]) -> bool:
    return any(
        span["source_file"] == anchor["source_file"]
        and int(span["start_line"]) <= int(anchor["start_line"])
        and int(anchor["end_line"]) <= int(span["end_line"])
        for span in spans
    )


def _write_bytes_atomic(payload: bytes, path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def extract_inventory_chunks(
    entrypoint: Path,
    output_dir: Path,
    *,
    workers: int,
    packet_chars: int,
) -> dict[str, object]:
    """Create immutable worker chunks containing ordered iterative packets."""

    source = collect_document_source(entrypoint)
    if source.unresolved:
        raise ValueError("unresolved TeX inputs: " + "; ".join(source.unresolved))
    packets = _packet_rows(source.rows, source.packet_anchors, packet_chars)
    assignments = _partition_packets(packets, workers)
    output_dir = output_dir.resolve()
    chunks: list[dict[str, object]] = []
    for chunk_number, assigned_packets in enumerate(assignments, start=1):
        chunk_id = f"inventory-{chunk_number:03d}"
        next_chunk_row = 1
        normalized_packets: list[list[SourceRow]] = []
        for packet in assigned_packets:
            normalized_packet: list[SourceRow] = []
            for row in packet:
                normalized_packet.append(
                    SourceRow(
                        row.source_file,
                        row.line,
                        row.text,
                        next_chunk_row,
                    )
                )
                next_chunk_row += 1
            normalized_packets.append(normalized_packet)
        assigned_packets = normalized_packets
        all_rows = [row for packet in assigned_packets for row in packet]
        spans = _spans(all_rows)
        files = list(dict.fromkeys(row.source_file for row in all_rows))
        packet_records: list[dict[str, object]] = []
        for packet_number, packet_rows in enumerate(assigned_packets, start=1):
            text = _render_packet(packet_rows)
            packet_records.append(
                {
                    "packet_id": f"{chunk_id}-packet-{packet_number:03d}",
                    "packet_index": packet_number,
                    "text": text,
                    "coordinates": [
                        {
                            "chunk_row": row.chunk_row,
                            "source_file": row.source_file,
                            "line": row.line,
                        }
                        for row in packet_rows
                    ],
                    "source_bytes": len(text.encode("utf-8")),
                }
            )
        chunk_payload = {
            "chunk_version": 1,
            "chunk_id": chunk_id,
            "files": files,
            "spans": spans,
            "anchors": [
                dict(anchor) for anchor in source.anchors if _owned(anchor, spans)
            ],
            "packets": packet_records,
        }
        chunk_path = output_dir / "chunks" / f"{chunk_id}.json"
        chunk_bytes = _json_bytes(chunk_payload)
        _write_bytes_atomic(chunk_bytes, chunk_path)
        chunks.append(
            {
                "chunk_id": chunk_id,
                "chunk_path": str(chunk_path.resolve()),
                "chunk_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
                "files": files,
                "spans": spans,
                "anchors": list(chunk_payload["anchors"]),
                "owned_bytes": sum(
                    int(packet["source_bytes"]) for packet in packet_records
                ),
                "packet_count": len(packet_records),
            }
        )
    manifest = {
        "manifest_version": 1,
        "mode": "inventory",
        "entrypoint": str(source.entrypoint),
        "source_files": [
            {
                "path": str(path.resolve()),
                "source_file": source_label(path, source.entrypoint.parent),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in source.files
        ],
        "requested_workers": workers,
        "effective_workers": len(chunks),
        "packet_chars": packet_chars,
        "chunks": chunks,
    }
    manifest_path = output_dir / "inventory-chunks.json"
    _write_bytes_atomic(_json_bytes(manifest), manifest_path)
    return {**manifest, "manifest_path": str(manifest_path.resolve())}


def load_chunk_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("mode") != "inventory":
        raise ValueError("inventory chunk manifest is invalid")
    if not isinstance(value.get("chunks"), list) or not value["chunks"]:
        raise ValueError("inventory chunk manifest has no chunks")
    return value


class Interface(PythonArgvMachineInterface):
    """Expose deterministic inventory chunk extraction."""

    prog = "inventory_chunk_extractor.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract immutable worker chunks and their iterative packets."
    )
    parser.add_argument("entrypoint", help="Root TeX document")
    parser.add_argument("--out-dir", required=True, help="Fresh run directory")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--packet-chars", type=int, default=3000)
    args = parser.parse_args(list(argv) if argv is not None else None)
    started = time.monotonic()
    report = extract_inventory_chunks(
        Path(args.entrypoint),
        Path(args.out_dir),
        workers=args.workers,
        packet_chars=args.packet_chars,
    )
    print(
        json.dumps(
            {
                "manifest": report["manifest_path"],
                "chunks": report["effective_workers"],
                "packets": sum(
                    int(chunk["packet_count"]) for chunk in report["chunks"]
                ),
                "elapsed_seconds": round(time.monotonic() - started, 6),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

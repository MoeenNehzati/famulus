"""Tests for immutable chunk extraction and packet subdivision."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

extractor = importlib.import_module(
    "skills.math-dependency-graph._rtx._inventory_pipeline._chunk_extractor"
)


def test_extractor_assigns_chunks_and_packets(tmp_path: Path) -> None:
    entrypoint = tmp_path / "main.tex"
    entrypoint.write_text(
        "\\newtheorem{theorem}{Theorem}\n"
        "preface\n"
        "\\begin{theorem}\n"
        "A sufficiently long theorem body must remain together.\n"
        "\\end{theorem}\n"
        "tail\n",
        encoding="utf-8",
    )

    result = extractor.extract_inventory_chunks(
        entrypoint, tmp_path / "run", workers=2, packet_chars=20
    )

    assert result["effective_workers"] == 2
    assert sum(chunk["packet_count"] for chunk in result["chunks"]) >= 2
    for chunk_record in result["chunks"]:
        assert "fragment_path" not in chunk_record
        assert "progress_path" not in chunk_record
        chunk = json.loads(
            Path(chunk_record["chunk_path"]).read_text(encoding="utf-8")
        )
        assert chunk["chunk_id"] == chunk_record["chunk_id"]
        assert chunk["packets"]
        for packet in chunk["packets"]:
            assert "@@ source:" not in packet["text"]
            assert all(
                set(coordinate) == {"chunk_row", "source_file", "line"}
                for coordinate in packet["coordinates"]
            )
            assert len({row["source_file"] for row in packet["coordinates"]}) == 1


def test_packets_use_chunk_rows_and_never_split_a_proof(tmp_path: Path) -> None:
    entrypoint = tmp_path / "main.tex"
    child = tmp_path / "child.tex"
    entrypoint.write_text("\\input{child}\n", encoding="utf-8")
    child.write_text(
        "preface\n\n"
        "\\begin{proof}\n"
        "first proof line\n\n"
        "second proof line\n"
        "\\end{proof}\n\n"
        "tail\n",
        encoding="utf-8",
    )

    result = extractor.extract_inventory_chunks(
        entrypoint, tmp_path / "run", workers=1, packet_chars=12
    )
    chunk = json.loads(Path(result["chunks"][0]["chunk_path"]).read_text())

    proof_packets = [
        packet for packet in chunk["packets"] if "\\begin{proof}" in packet["text"]
    ]
    assert len(proof_packets) == 1
    assert "\\end{proof}" in proof_packets[0]["text"]
    rendered_rows = [
        int(line.split(" | ", 1)[0])
        for packet in chunk["packets"]
        for line in packet["text"].splitlines()
    ]
    assert rendered_rows == list(range(1, len(rendered_rows) + 1))


def test_extractor_rejects_unresolved_include(tmp_path: Path) -> None:
    entrypoint = tmp_path / "main.tex"
    entrypoint.write_text("\\input{missing}\n", encoding="utf-8")

    try:
        extractor.extract_inventory_chunks(
            entrypoint, tmp_path / "run", workers=1, packet_chars=100
        )
    except ValueError as error:
        assert "unresolved TeX inputs" in str(error)
    else:
        raise AssertionError("an unresolved authored include must fail extraction")

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
        chunk = json.loads(
            Path(chunk_record["chunk_path"]).read_text(encoding="utf-8")
        )
        assert chunk["chunk_id"] == chunk_record["chunk_id"]
        assert chunk["packets"]


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

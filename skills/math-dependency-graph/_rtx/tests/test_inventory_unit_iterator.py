#!/usr/bin/env python3
"""Behavioral tests for durable, source-ordered inventory unit setup."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR))

from _inventory_unit_iterator import (  # noqa: E402
    load_iterator_summary,
    setup_inventory_iterator,
)


def _write_packet(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _units(state_dir: Path) -> list[dict]:
    return load_iterator_summary(state_dir)["units"]


def _covered_coordinates(summary: dict) -> list[tuple[int, str, int]]:
    entries = [
        (coordinate["packet_index"], coordinate["source"], coordinate["line"])
        for unit in summary["units"]
        for coordinate in unit["coordinates"]
    ]
    entries.extend(
        (coordinate["packet_index"], coordinate["source"], coordinate["line"])
        for coordinate in summary["structural_context"]
    )
    return sorted(entries)


def test_setup_uses_expanded_include_order_and_global_unit_ids(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: main.tex\n"
        "0001 | Root before.\n"
        "0002 | \\input{first}\n"
        "@@ source: first.tex\n"
        "0001 | First text.\n"
        "@@ source: main.tex\n"
        "0003 | Root after.\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=80
    )

    units = _units(tmp_path / "iterator")
    assert [unit["id"] for unit in units] == ["u000001", "u000002", "u000003"]
    assert [unit["coordinates"][0]["source"] for unit in units] == [
        "main.tex",
        "first.tex",
        "main.tex",
    ]
    assert [unit["text"] for unit in units] == [
        "Root before.\n\\input{first}",
        "First text.",
        "Root after.",
    ]


def test_setup_keeps_small_latex_and_markdown_blocks_whole(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.tex\n"
        "0001 | \\begin{theorem}\n"
        "0002 | The result holds.\n"
        "0003 | \\end{theorem}\n"
        "0004 | \n"
        "0005 | ## Proposition\n"
        "0006 | $$ x = y $$\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=200
    )

    units = _units(tmp_path / "iterator")
    theorem = next(unit for unit in units if unit["environment"] == "theorem")
    markdown = next(unit for unit in units if unit["environment"] == "markdown-math")
    assert theorem["text"] == "\\begin{theorem}\nThe result holds.\n\\end{theorem}"
    assert theorem["part"] == 1
    assert theorem["oversize"] is False
    assert markdown["text"] == "$$ x = y $$"
    assert markdown["owner"] == "paper.tex:5"


def test_setup_splits_oversize_environment_at_complete_paragraph_boundaries(
    tmp_path: Path,
) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.tex\n"
        "0001 | \\begin{lemma}\n"
        "0002 | First paragraph has enough text.\n"
        "0003 | \n"
        "0004 | Second paragraph has enough text.\n"
        "0005 | \\end{lemma}\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=45
    )

    units = _units(tmp_path / "iterator")
    parts = [unit for unit in units if unit["environment"] == "lemma"]
    assert [unit["part"] for unit in parts] == [1, 2]
    assert [unit["owner"] for unit in parts] == ["paper.tex:1", "paper.tex:1"]
    assert [unit["oversize"] for unit in parts] == [False, False]
    assert [
        [coordinate["line"] for coordinate in unit["coordinates"]] for unit in parts
    ] == [[1, 2, 3], [4, 5]]


def test_setup_splits_oversize_environment_after_a_complete_nested_environment(
    tmp_path: Path,
) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.tex\n"
        "0001 | \\begin{theorem}\n"
        "0002 | The statement starts here.\n"
        "0003 | \\begin{proof}\n"
        "0004 | Nested argument.\n"
        "0005 | \\end{proof}\n"
        "0006 | The statement closes here.\n"
        "0007 | \\end{theorem}\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=90
    )

    parts = [unit for unit in _units(tmp_path / "iterator") if unit["environment"] == "theorem"]
    assert [unit["part"] for unit in parts] == [1, 2]
    assert [
        [coordinate["line"] for coordinate in unit["coordinates"]] for unit in parts
    ] == [[1, 2, 3, 4, 5], [6, 7]]


def test_setup_groups_outside_paragraphs_without_crossing_window(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.md\n"
        "0001 | alpha\n"
        "0002 | beta\n"
        "0003 | \n"
        "0004 | gamma\n"
        "0005 | delta\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=12
    )

    units = _units(tmp_path / "iterator")
    assert [unit["text"] for unit in units] == ["alpha\nbeta\n", "gamma\ndelta"]
    assert [unit["environment"] for unit in units] == [None, None]


def test_setup_marks_an_indivisible_oversize_unit(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.tex\n0001 | " + "x" * 64 + "\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=12
    )

    unit = _units(tmp_path / "iterator")[0]
    assert unit["text"] == "x" * 64
    assert unit["oversize"] is True
    assert unit["part"] == 1


def test_setup_covers_every_source_coordinate_once_with_heading_context(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.tex\n"
        "0001 | \\section{Results}\n"
        "0002 | Opening text.\n"
        "0003 | \n"
        "0004 | \\begin{proof}\n"
        "0005 | The argument.\n"
        "0006 | \\end{proof}\n",
    )

    summary = setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=80
    )

    assert _covered_coordinates(summary) == [
        (1, "paper.tex", 1),
        (2, "paper.tex", 2),
        (3, "paper.tex", 3),
        (4, "paper.tex", 4),
        (5, "paper.tex", 5),
        (6, "paper.tex", 6),
    ]
    assert summary["structural_context"] == [
        {
            "packet_index": 1,
            "source": "paper.tex",
            "line": 1,
            "text": "\\section{Results}",
        }
    ]


def test_setup_partitions_units_into_deterministic_contiguous_character_ranges(
    tmp_path: Path,
) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.md\n"
        "0001 | aaaaaaaa\n0002 | \n"
        "0003 | bbbbbbbbbb\n0004 | \n"
        "0005 | ccccccccccc\n0006 | \n"
        "0007 | ddddddddd\n",
    )

    summary = setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=2, window_chars=12
    )

    assert summary["effective_workers"] == 2
    assert [
        (assignment["first_unit_id"], assignment["last_unit_id"])
        for assignment in summary["assignments"]
    ] == [("u000001", "u000002"), ("u000003", "u000004")]
    assert [assignment["character_count"] for assignment in summary["assignments"]] == [18, 20]
    assert all(assignment["unit_count"] == 2 for assignment in summary["assignments"])


def test_setup_caps_effective_workers_and_creates_owned_worker_paths(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.md\n0001 | firstx\n0002 | \n0003 | second\n",
    )

    summary = setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=8, window_chars=8
    )

    assert summary["effective_workers"] == 2
    assert [assignment["worker_index"] for assignment in summary["assignments"]] == [1, 2]
    for assignment in summary["assignments"]:
        assert Path(assignment["inventory_path"]).is_file()
        assert Path(assignment["progress_path"]).is_file()
        assert Path(assignment["controller_packet_path"]).is_file()


def test_setup_publishes_complete_state_or_nothing_when_validation_fails(tmp_path: Path) -> None:
    packet = _write_packet(tmp_path / "source-packet.txt", "not a source packet\n")
    state_dir = tmp_path / "iterator"

    with pytest.raises(ValueError, match="source packet"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=1, window_chars=20
        )

    assert not state_dir.exists()
    assert not list(tmp_path.glob(".iterator.*.tmp"))


def test_setup_reuses_only_an_exact_matching_configuration(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt", "@@ source: paper.md\n0001 | text\n"
    )
    state_dir = tmp_path / "iterator"

    first = setup_inventory_iterator(
        packet, state_dir, requested_workers=1, window_chars=20
    )
    second = setup_inventory_iterator(
        packet, state_dir, requested_workers=1, window_chars=20
    )

    assert second == first == load_iterator_summary(state_dir)
    with pytest.raises(ValueError, match="existing iterator state does not match"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=2, window_chars=20
        )
    assert load_iterator_summary(state_dir) == first
    assert json.loads((state_dir / "inventory-assignments.json").read_text(encoding="utf-8"))[
        "requested_workers"
    ] == 1


def test_setup_records_each_substage_time_with_the_injected_clock(tmp_path: Path) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt", "@@ source: paper.md\n0001 | text\n"
    )
    ticks = iter(range(0, 20_000_000, 1_000_000))

    summary = setup_inventory_iterator(
        packet,
        tmp_path / "iterator",
        requested_workers=1,
        window_chars=20,
        clock_ns=lambda: next(ticks),
        utc_now=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
    )

    assert summary["timings_ms"]["scan"] == 1
    assert summary["timings_ms"]["partition"] == 1
    assert summary["timings_ms"]["database"] == 1
    assert summary["timings_ms"]["validation"] == 1
    assert summary["timings_ms"]["publication"] == 1

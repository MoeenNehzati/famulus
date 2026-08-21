#!/usr/bin/env python3
"""Behavioral tests for durable, source-ordered inventory unit setup."""

from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR))

import _inventory_unit_iterator as iterator  # noqa: E402
from _inventory_unit_iterator import (  # noqa: E402
    load_iterator_summary,
    next_inventory_unit,
    setup_inventory_iterator,
)


def _write_packet(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _units(state_dir: Path) -> list[dict]:
    return load_iterator_summary(state_dir)["units"]


def _iterator_with_units(tmp_path: Path, *, workers: int = 1, units: int = 3) -> Path:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.md\n"
        + "\n".join(
            f"{line:04d} | unit {ordinal}\n{line + 1:04d} | "
            for ordinal, line in enumerate(range(1, units * 2, 2), start=1)
        ),
    )
    state_dir = tmp_path / "iterator"
    setup_inventory_iterator(packet, state_dir, requested_workers=workers, window_chars=6)
    return state_dir


def _inventory_path(state_dir: Path, worker_index: int) -> Path:
    return state_dir / "workers" / f"worker-{worker_index}" / "inventory.json"


def _valid_inventory(state_dir: Path, worker_index: int) -> dict:
    return {
        "ir_version": 3,
        "chunk_id": f"iterator-worker-{worker_index:03d}",
        "files": ["paper.md"],
        "nodes": [],
        "edges": [],
        "gaps": [],
    }


def _write_valid_inventory(state_dir: Path, worker_index: int) -> None:
    _inventory_path(state_dir, worker_index).write_text(
        json.dumps(_valid_inventory(state_dir, worker_index)), encoding="utf-8"
    )


def _ack_rows(state_dir: Path) -> list[tuple]:
    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
        return connection.execute(
            "SELECT worker_index, unit_id, wrapped FROM acknowledgements ORDER BY id"
        ).fetchall()


def _sequence_rows(state_dir: Path) -> list[tuple]:
    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
        return connection.execute(
            "SELECT worker_index, first_unit_id, last_unit_id, closure_reason "
            "FROM attention_sequences ORDER BY id"
        ).fetchall()


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


@pytest.mark.parametrize(
    ("opener", "closer"),
    [(r"\[", r"\]"), ("$$", "$$")],
)
def test_setup_keeps_small_multiline_display_math_whole(
    tmp_path: Path, opener: str, closer: str
) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.md\n"
        f"0001 | {opener}\n"
        "0002 | x = y\n"
        f"0003 | {closer}\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=80
    )

    units = _units(tmp_path / "iterator")
    assert [unit["text"] for unit in units] == [f"{opener}\nx = y\n{closer}"]
    assert [unit["environment"] for unit in units] == ["markdown-math"]
    assert [coordinate["line"] for coordinate in units[0]["coordinates"]] == [1, 2, 3]


@pytest.mark.parametrize(
    ("opener", "closer"),
    [(r"\[", r"\]"), ("$$", "$$")],
)
def test_setup_splits_oversize_multiline_display_math_only_at_valid_boundaries(
    tmp_path: Path, opener: str, closer: str
) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt",
        "@@ source: paper.md\n"
        f"0001 | {opener}\n"
        "0002 | First display paragraph.\n"
        "0003 | \n"
        "0004 | Second display paragraph.\n"
        f"0005 | {closer}\n",
    )

    setup_inventory_iterator(
        packet, tmp_path / "iterator", requested_workers=1, window_chars=30
    )

    units = _units(tmp_path / "iterator")
    assert [unit["environment"] for unit in units] == ["markdown-math", "markdown-math"]
    assert [unit["part"] for unit in units] == [1, 2]
    assert [unit["oversize"] for unit in units] == [False, False]
    assert [
        [coordinate["line"] for coordinate in unit["coordinates"]] for unit in units
    ] == [[1, 2, 3], [4, 5]]


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


def test_setup_publishes_an_immutable_complete_state_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt", "@@ source: paper.md\n0001 | text\n"
    )
    state_dir = tmp_path / "iterator"
    original_replace = iterator.os.replace
    published: dict[str, bytes] = {}

    def capture_publish(source: Path, destination: Path) -> None:
        assert destination == state_dir
        assert not destination.exists()
        assert (source / "iterator.sqlite3").is_file()
        assert (source / "inventory-assignments.json").is_file()
        assert (source / "workers/worker-1/inventory.json").is_file()
        assert (source / "workers/worker-1/progress.md").is_file()
        assert (source / "controller/worker-1-packet.json").is_file()
        original_replace(source, destination)
        published["manifest"] = (destination / "inventory-assignments.json").read_bytes()

    monkeypatch.setattr(iterator.os, "replace", capture_publish)

    setup_inventory_iterator(
        packet, state_dir, requested_workers=1, window_chars=20
    )

    assert (state_dir / "inventory-assignments.json").read_bytes() == published["manifest"]


def test_setup_removes_temporary_state_when_atomic_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = _write_packet(
        tmp_path / "source-packet.txt", "@@ source: paper.md\n0001 | text\n"
    )
    state_dir = tmp_path / "iterator"

    def fail_publish(source: Path, destination: Path) -> None:
        assert source.name.startswith(".iterator.")
        assert (source / "iterator.sqlite3").is_file()
        assert (source / "inventory-assignments.json").is_file()
        assert destination == state_dir
        raise OSError("injected publication failure")

    monkeypatch.setattr(iterator.os, "replace", fail_publish)

    with pytest.raises(OSError, match="injected publication failure"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=1, window_chars=20
        )

    assert not state_dir.exists()
    assert not list(tmp_path.glob(".iterator.*.tmp"))


def test_setup_reuses_only_an_exact_matching_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    with pytest.raises(ValueError, match="existing iterator state does not match"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=1, window_chars=21
        )
    packet.write_text("@@ source: paper.md\n0001 | changed text\n", encoding="utf-8")
    with pytest.raises(ValueError, match="existing iterator state does not match"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=1, window_chars=20
        )
    packet.write_text("@@ source: paper.md\n0001 | text\n", encoding="utf-8")
    monkeypatch.setattr(iterator, "SCANNER_VERSION", iterator.SCANNER_VERSION + 1)
    with pytest.raises(ValueError, match="existing iterator state does not match"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=1, window_chars=20
        )
    monkeypatch.setattr(iterator, "SCANNER_VERSION", iterator.SCANNER_VERSION - 1)
    monkeypatch.setattr(iterator, "SCHEMA_VERSION", iterator.SCHEMA_VERSION + 1)
    with pytest.raises(ValueError, match="existing iterator state does not match"):
        setup_inventory_iterator(
            packet, state_dir, requested_workers=1, window_chars=20
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
    assert "publication" not in summary["timings_ms"]


def test_next_leases_first_unit_and_replays_it_until_acknowledged(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path)

    first = next_inventory_unit(state_dir, 1)
    replay = next_inventory_unit(state_dir, 1)

    assert first["state"] == replay["state"] == "unit"
    assert first["unit"]["id"] == replay["unit"]["id"] == "u000001"


def test_next_uses_independent_worker_cursors(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path, workers=2, units=4)

    worker_one = next_inventory_unit(state_dir, 1)
    worker_two = next_inventory_unit(state_dir, 2)
    _write_valid_inventory(state_dir, 1)
    worker_one_next = next_inventory_unit(state_dir, 1, ack=worker_one["unit"]["id"])

    assert worker_one["unit"]["id"] == "u000001"
    assert worker_two["unit"]["id"] == "u000003"
    assert worker_one_next["unit"]["id"] == "u000002"
    assert next_inventory_unit(state_dir, 2)["unit"]["id"] == "u000003"


def test_next_validates_schema_before_advancing_the_lease(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path)
    leased = next_inventory_unit(state_dir, 1)
    _inventory_path(state_dir, 1).write_text("{}", encoding="utf-8")

    rejected = next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"])

    assert rejected["state"] == "failure"
    assert next_inventory_unit(state_dir, 1)["unit"]["id"] == leased["unit"]["id"]
    assert _ack_rows(state_dir) == []


@pytest.mark.parametrize("breakage", ["missing", "invalid-json", "wrong-worker"])
def test_next_rolls_back_when_owned_inventory_cannot_be_validated(
    tmp_path: Path, breakage: str
) -> None:
    state_dir = _iterator_with_units(tmp_path)
    leased = next_inventory_unit(state_dir, 1)
    inventory_path = _inventory_path(state_dir, 1)
    if breakage == "missing":
        inventory_path.unlink()
    elif breakage == "invalid-json":
        inventory_path.write_text("{", encoding="utf-8")
    else:
        inventory_path.write_text(
            json.dumps({**_valid_inventory(state_dir, 1), "chunk_id": "iterator-worker-002"}),
            encoding="utf-8",
        )

    rejected = next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"])

    assert rejected["state"] == "failure"
    assert next_inventory_unit(state_dir, 1)["unit"]["id"] == leased["unit"]["id"]
    assert _ack_rows(state_dir) == []


@pytest.mark.parametrize("ack_kind", ["stale", "future", "cross-worker"])
def test_next_rejects_acknowledgements_that_do_not_match_the_outstanding_lease(
    tmp_path: Path, ack_kind: str
) -> None:
    state_dir = _iterator_with_units(tmp_path, workers=2, units=4)
    leased = next_inventory_unit(state_dir, 1)
    _write_valid_inventory(state_dir, 1)
    if ack_kind == "stale":
        next_unit = next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"])
        _write_valid_inventory(state_dir, 1)
        next_inventory_unit(state_dir, 1, ack=next_unit["unit"]["id"])
        invalid_ack = leased["unit"]["id"]
    elif ack_kind == "future":
        invalid_ack = "u000002"
    else:
        invalid_ack = "u000003"

    rejected = next_inventory_unit(state_dir, 1, ack=invalid_ack)

    assert rejected["state"] == "failure"
    if ack_kind == "stale":
        assert next_inventory_unit(state_dir, 1)["state"] == "complete"
    else:
        assert next_inventory_unit(state_dir, 1)["unit"]["id"] == leased["unit"]["id"]


def test_next_replays_the_most_recent_matching_acknowledgement_idempotently(
    tmp_path: Path,
) -> None:
    state_dir = _iterator_with_units(tmp_path)
    leased = next_inventory_unit(state_dir, 1)
    _write_valid_inventory(state_dir, 1)

    advanced = next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"])
    retried = next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"])

    assert advanced == retried
    assert advanced["unit"]["id"] == "u000002"
    assert _ack_rows(state_dir) == [(1, "u000001", 0)]


def test_next_rejects_idempotent_retry_when_wrap_intent_differs(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path)
    leased = next_inventory_unit(state_dir, 1)
    _write_valid_inventory(state_dir, 1)
    next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"])

    rejected = next_inventory_unit(state_dir, 1, ack=leased["unit"]["id"], wrap=True)

    assert rejected["state"] == "failure"
    assert _ack_rows(state_dir) == [(1, "u000001", 0)]
    assert next_inventory_unit(state_dir, 1)["unit"]["id"] == "u000002"


def test_next_closes_an_attention_sequence_when_acknowledgement_wraps(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path)
    first = next_inventory_unit(state_dir, 1)
    _write_valid_inventory(state_dir, 1)
    second = next_inventory_unit(state_dir, 1, ack=first["unit"]["id"])
    _write_valid_inventory(state_dir, 1)

    next_inventory_unit(state_dir, 1, ack=second["unit"]["id"], wrap=True)

    assert _sequence_rows(state_dir) == [
        (1, "u000001", "u000002", "worker-wrap"),
    ]


def test_next_closes_the_final_open_attention_sequence_automatically(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path, units=2)
    first = next_inventory_unit(state_dir, 1)
    _write_valid_inventory(state_dir, 1)
    second = next_inventory_unit(state_dir, 1, ack=first["unit"]["id"])
    _write_valid_inventory(state_dir, 1)

    complete = next_inventory_unit(state_dir, 1, ack=second["unit"]["id"])

    assert complete["state"] == "complete"
    assert _sequence_rows(state_dir) == [
        (1, "u000001", "u000002", "end-of-source"),
    ]


def test_next_concurrently_leases_separate_worker_indices_with_real_sqlite(tmp_path: Path) -> None:
    state_dir = _iterator_with_units(tmp_path, workers=2, units=4)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda worker: next_inventory_unit(state_dir, worker), (1, 2)))

    assert [result["state"] for result in results] == ["unit", "unit"]
    assert [result["unit"]["id"] for result in results] == ["u000001", "u000003"]

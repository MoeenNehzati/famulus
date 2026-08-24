"""Observable workflow tests for the inventory Voyage dispenser."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from officina import rutter


dispenser = importlib.import_module(
    "skills.math-dependency-graph._rtx._inventory_pipeline._voyage_dispenser"
)


def test_cli_describes_modes_and_rejects_incomplete_debug_setup(
    inventory_run: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)

    assert dispenser.main(["modes"]) == 0
    modes = json.loads(capsys.readouterr().out)
    assert modes["default_mode"] == "default"
    assert modes["modes"]["default"]["arguments"] == {
        "doc_entrypoint": "Path to the root TeX or Markdown document.",
        "chunk_count": "Requested positive number of inventory chunks.",
    }
    assert "chunk_manifest" not in json.dumps(modes)
    assert "inventory_gold_standard" in modes["modes"]["debug"]["arguments"]

    assert dispenser.main(
        [
            "initiate",
            "debug",
            "--doc-entrypoint",
            str(doc_entrypoint),
            "--chunk-count",
            "1",
        ]
    ) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "usage-error"
    assert "--inventory-gold-standard" in error["message"]
    assert not (run_dir / "voyages").exists()


def test_default_voyage_iterates_packets_and_writes_cumulative_inventory(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
    )
    assert voyage_id.startswith("default-voyage-")
    assert dispenser.make_voyage_dispenser().get_voyage_ids() == (voyage_id,)
    assert (
        run_dir / "artifacts" / "default" / "inventory-chunks.json"
    ).is_file()

    seen_packets: list[str] = []
    while True:
        status = inventory_dispenser.get_status(voyage_id)
        if status.terminal_result is not None:
            break
        if status.instruction is None:
            inventory_dispenser.advance(voyage_id)
            continue
        assert isinstance(status.instruction, rutter.Message)
        packet_id = status.instruction.data["payload"]["packet"]["packet_id"]
        seen_packets.append(packet_id)
        inventory_dispenser.advance(
            voyage_id,
            {
                "outcome": "reported",
                "packet_id": packet_id,
                "inventory": {
                    "ir_version": 3,
                    "chunk_id": "inventory-001",
                        "files": ["main.md"],
                    "nodes": [],
                    "edges": [],
                    "gaps": [],
                },
            },
            responding_to=status.current_evolution.evolution_entry_id,
        )

    assert seen_packets == [
        "inventory-001-packet-001",
        "inventory-001-packet-002",
        "inventory-001-packet-003",
        "inventory-001-packet-004",
    ]
    assert status.terminal_result.outcome == "complete"
    inventory_path = Path(status.terminal_result.value["inventory_path"])
    assert inventory_path.is_file()

    inventory_dispenser.release(voyage_id)

    assert not (run_dir / "voyages" / "default" / voyage_id).exists()
    assert inventory_path.is_file()


def test_debug_setup_freezes_supplied_gold_and_attaches_diagnosis(
    inventory_run: tuple[Path, Path],
    inventory_gold_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()

    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
        inventory_gold_standard=str(inventory_gold_path),
    )
    assert [
        hook.id
        for hook in dispenser.DEBUG_INVENTORY_VOYAGE.define_transition_hooks()
    ] == ["inventory-diagnosis"]

    reckoning = json.loads(
        (
            run_dir
            / "voyages"
            / "debug"
            / voyage_id
            / "inventory-voyage.reckoning.json"
        ).read_text(encoding="utf-8")
    )
    charter = reckoning["root"]["charter"]
    assert charter["inventory_gold_standard_text"] == inventory_gold_path.read_text(
        encoding="utf-8"
    )
    assert len(charter["inventory_gold_standard_sha256"]) == 64


def test_requested_chunk_count_controls_dispensed_voyages(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()

    voyage_ids = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="2",
    )
    assert len(voyage_ids) == 2
    assert len(set(voyage_ids)) == 2
    assert all(
        voyage_id.startswith("default-voyage-") for voyage_id in voyage_ids
    )


def test_run_prefixes_isolate_voyages_and_artifacts(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new prefixed run must coexist with unfinished Voyages from another run."""

    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()

    (baseline_id,) = inventory_dispenser.initiate_voyages(
        run_prefix="baseline",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
    )
    (retry_id,) = inventory_dispenser.initiate_voyages(
        run_prefix="retry",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
    )

    assert baseline_id.startswith("baseline-voyage-")
    assert retry_id.startswith("retry-voyage-")
    assert inventory_dispenser.get_voyage_ids("baseline") == (baseline_id,)
    assert inventory_dispenser.get_voyage_ids("retry") == (retry_id,)
    assert inventory_dispenser.get_voyage_ids() == (baseline_id, retry_id)
    assert (run_dir / "voyages" / "baseline" / baseline_id).is_dir()
    assert (run_dir / "voyages" / "retry" / retry_id).is_dir()
    assert (run_dir / "artifacts" / "baseline" / "inventory-chunks.json").is_file()
    assert (run_dir / "artifacts" / "retry" / "inventory-chunks.json").is_file()


@pytest.mark.parametrize("chunk_count", ["0", "-1", "many"])
def test_chunk_count_must_be_a_positive_integer(
    tmp_path: Path,
    chunk_count: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "main.md"
    source.write_text("# Result\n", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", tmp_path / "run")
    inventory_dispenser = dispenser.make_voyage_dispenser()

    with pytest.raises(ValueError, match="chunk_count must be a positive integer"):
        inventory_dispenser.initiate_voyages(
            doc_entrypoint=str(source),
            chunk_count=chunk_count,
        )

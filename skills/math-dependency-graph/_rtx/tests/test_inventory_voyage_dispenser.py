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
support = importlib.import_module(
    "skills.math-dependency-graph._rtx._inventory_pipeline._voyage_support"
)


def _empty_inventory(**extra: object) -> dict[str, object]:
    return {
        "outcome": "reported",
        "nodes": [],
        "edges": [],
        "gaps": [],
        **extra,
    }


def _worker_inventory_path(run_dir: Path, voyage_id: str, prefix: str = "default") -> Path:
    return run_dir / "artifacts" / prefix / "inventories" / f"{voyage_id}.json"


def _write_worker_inventory(path: Path, inventory: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory) + "\n", encoding="utf-8")


def test_worker_owns_cumulative_inventory_file_and_reports_only_new_records(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint), chunk_count="1"
    )

    introduction = inventory_dispenser.get_status(voyage_id)
    inventory_file = Path(introduction.instruction.data["payload"]["inventory_file"])
    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "ready"},
        responding_to=introduction.current_evolution.evolution_entry_id,
    )
    report = _advance_to_message(inventory_dispenser, voyage_id)
    row = int(report.instruction.data["payload"].split(" | ", 1)[0])
    node = {
        "local_id": "n1",
        "statement_location": [row, row],
        "provenance": "explicit",
        "type_hint": "result",
        "summary": "The packet states a result.",
    }
    inventory_file.parent.mkdir(parents=True, exist_ok=True)
    inventory_file.write_text(
        json.dumps({"nodes": [node], "edges": [], "gaps": []}) + "\n",
        encoding="utf-8",
    )

    validation = inventory_dispenser.validate(
        voyage_id,
        {"outcome": "reported", "nodes": [node], "edges": [], "gaps": []},
        responding_to=report.current_evolution.evolution_entry_id,
    )

    assert validation.valid, validation.issues


def test_report_accepts_packet_local_diagnostic_while_payload_stays_text(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable file, not the diagnostic response, owns cumulative state."""

    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint), chunk_count="1"
    )
    report = _advance_to_message(inventory_dispenser, voyage_id)

    assert isinstance(report.instruction.data["payload"], str)
    validation = inventory_dispenser.validate(
        voyage_id,
        _empty_inventory(),
        responding_to=report.current_evolution.evolution_entry_id,
    )
    assert validation.valid, validation.issues


def _advance_to_message(
    inventory_dispenser: rutter.VoyageDispenser,
    voyage_id: str,
) -> rutter.VoyageStatus:
    """Settle engine-owned machine work and return the next public message."""

    while True:
        status = inventory_dispenser.get_status(voyage_id)
        if status.terminal_result is not None or isinstance(
            status.instruction, rutter.Message
        ):
            if (
                status.terminal_result is None
                and status.current_evolution.evolution_id
                == dispenser._INTRODUCE_EVOLUTION
            ):
                inventory_file = Path(
                    status.instruction.data["payload"]["inventory_file"]
                )
                _write_worker_inventory(
                    inventory_file, {"nodes": [], "edges": [], "gaps": []}
                )
                inventory_dispenser.advance(
                    voyage_id,
                    {"outcome": "ready"},
                    responding_to=status.current_evolution.evolution_entry_id,
                )
                continue
            return status
        assert status.instruction is None or isinstance(
            status.instruction, rutter.MachineInstruction
        )
        inventory_dispenser.advance(voyage_id)


def _gold_inventory(
    *,
    files: list[str],
    with_node: bool = False,
) -> dict[str, object]:
    return {
        "ir_version": 3,
        "chunk_id": "gold",
        "files": files,
        "nodes": (
            [
                {
                    "local_id": "n1",
                    "statement_location": [0, 1, 1],
                    "provenance": "explicit",
                    "type_hint": "result",
                    "summary": "The first result holds.",
                }
            ]
            if with_node
            else []
        ),
        "edges": [],
        "gaps": [],
    }


def test_diagnosis_expected_answer_contains_only_current_packet_additions() -> None:
    previous = {
        "ir_version": 3,
        "chunk_id": "inventory-001",
        "files": ["main.md"],
        "nodes": [{"local_id": "n1"}],
        "edges": [],
        "gaps": [],
    }
    current = {
        **previous,
        "nodes": [{"local_id": "n1"}, {"local_id": "n2"}],
        "edges": [{"local_id": "d1"}],
    }

    assert support._new_projection_records(current, previous) == {
        "ir_version": 3,
        "chunk_id": "inventory-001",
        "files": ["main.md"],
        "nodes": [{"local_id": "n2"}],
        "edges": [{"local_id": "d1"}],
        "gaps": [],
    }


def _finish_empty_debug_voyage(
    inventory_dispenser: rutter.VoyageDispenser,
    voyage_id: str,
) -> None:
    """Advance one real debug Voyage through one empty report and diagnosis."""

    while True:
        status = inventory_dispenser.get_status(voyage_id)
        if status.terminal_result is not None:
            return
        if status.instruction is None or isinstance(
            status.instruction, rutter.MachineInstruction
        ):
            inventory_dispenser.advance(voyage_id)
            continue
        if status.current_evolution.evolution_id == dispenser._INTRODUCE_EVOLUTION:
            _write_worker_inventory(
                Path(status.instruction.data["payload"]["inventory_file"]),
                {"nodes": [], "edges": [], "gaps": []},
            )
            inventory_dispenser.advance(
                voyage_id,
                {"outcome": "ready"},
                responding_to=status.current_evolution.evolution_entry_id,
            )
            continue
        if status.current_evolution.rutter_id == dispenser._DEBUG_RUTTER_ID:
            response = _empty_inventory(
                decision_basis=(
                    "No source-visible graph entity appears in this packet."
                )
            )
        else:
            assert status.current_evolution.rutter_id == "diagnose-answer"
            assert status.current_evolution.evolution_id == "compare"
            response = {"outcome": "yes"}
        inventory_dispenser.advance(
            voyage_id,
            response,
            responding_to=status.current_evolution.evolution_entry_id,
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


def test_voyage_introduces_inventory_contract_once_before_packet_reports(
    inventory_run: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeating the instruction or cumulative path in report payloads wastes context."""

    doc_entrypoint, run_dir = inventory_run
    instruction_path = tmp_path / "inventory.md"
    instruction_text = "# Inventory contract\nRead cumulative source evidence.\n"
    instruction_path.write_text(instruction_text, encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    monkeypatch.setattr(dispenser, "_INVENTORY_INSTRUCTION_PATH", instruction_path)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
    )

    introduction = inventory_dispenser.get_status(voyage_id)
    assert isinstance(introduction.instruction, rutter.Message)
    assert introduction.instruction.data["payload"] == {
        "inventory_instruction": instruction_text,
        "cumulative_packets_file": str(
            run_dir
            / "artifacts"
            / "default"
            / "source-packets"
            / f"{voyage_id}.txt"
        ),
        "inventory_file": str(
            run_dir
            / "artifacts"
            / "default"
            / "inventories"
            / f"{voyage_id}.json"
        ),
    }
    _write_worker_inventory(
        Path(introduction.instruction.data["payload"]["inventory_file"]),
        {"nodes": [], "edges": [], "gaps": []},
    )
    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "ready"},
        responding_to=introduction.current_evolution.evolution_entry_id,
    )

    report = _advance_to_message(inventory_dispenser, voyage_id)
    payload = report.instruction.data["payload"]
    assert isinstance(payload, str)
    validation = inventory_dispenser.validate(
        voyage_id,
        _empty_inventory(),
        responding_to=report.current_evolution.evolution_entry_id,
    )
    assert validation.valid, validation.issues
    inventory_dispenser.advance(
        voyage_id,
        _empty_inventory(),
        responding_to=report.current_evolution.evolution_entry_id,
    )
    second_report = _advance_to_message(inventory_dispenser, voyage_id)
    second_payload = second_report.instruction.data["payload"]
    assert isinstance(second_payload, str)


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
    cumulative_source = ""
    source_packets_path: Path | None = None
    inventory_file: Path | None = None
    while True:
        status = inventory_dispenser.get_status(voyage_id)
        if status.terminal_result is not None:
            break
        if status.instruction is None or isinstance(
            status.instruction, rutter.MachineInstruction
        ):
            inventory_dispenser.advance(voyage_id)
            continue
        assert isinstance(status.instruction, rutter.Message)
        if status.current_evolution.evolution_id == dispenser._INTRODUCE_EVOLUTION:
            source_packets_path = Path(
                status.instruction.data["payload"]["cumulative_packets_file"]
            )
            inventory_file = Path(status.instruction.data["payload"]["inventory_file"])
            _write_worker_inventory(
                inventory_file, {"nodes": [], "edges": [], "gaps": []}
            )
            inventory_dispenser.advance(
                voyage_id,
                {"outcome": "ready"},
                responding_to=status.current_evolution.evolution_entry_id,
            )
            continue
        payload = status.instruction.data["payload"]
        assert isinstance(payload, str)
        packet = payload
        seen_packets.append(packet)
        cumulative_source += packet
        assert source_packets_path is not None
        assert source_packets_path.read_text(encoding="utf-8") == cumulative_source
        inventory_dispenser.advance(
            voyage_id,
            _empty_inventory(),
            responding_to=status.current_evolution.evolution_entry_id,
        )

    assert len(seen_packets) == 2
    assert all("@@ source:" not in packet for packet in seen_packets)
    assert status.terminal_result.outcome == "complete"
    inventory_path = Path(status.terminal_result.value["inventory_path"])
    assert inventory_path.is_file()
    assert source_packets_path is not None
    assert status.terminal_result.value["source_packets_path"] == str(
        source_packets_path
    )

    inventory_dispenser.release(voyage_id)

    assert not (run_dir / "voyages" / "default" / voyage_id).exists()
    assert inventory_path.is_file()
    assert source_packets_path.read_text(encoding="utf-8") == cumulative_source
    assert not (run_dir / "artifacts" / "default" / "diagnostics").exists()


def test_cumulative_inventory_keeps_cross_packet_ids_and_maps_coordinates(
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

    first = _advance_to_message(inventory_dispenser, voyage_id)
    first_row = int(first.instruction.data["payload"].split(" | ", 1)[0])
    first_node = {
        "local_id": "n1",
        "statement_location": [first_row, first_row],
        "provenance": "explicit",
        "type_hint": "result",
        "summary": "The first packet states a result.",
    }
    inventory_path = _worker_inventory_path(run_dir, voyage_id)
    _write_worker_inventory(
        inventory_path, {"nodes": [first_node], "edges": [], "gaps": []}
    )
    inventory_dispenser.advance(
        voyage_id,
        {
            "outcome": "reported",
            "nodes": [first_node],
            "edges": [],
            "gaps": [],
        },
        responding_to=first.current_evolution.evolution_entry_id,
    )
    second = _advance_to_message(inventory_dispenser, voyage_id)
    second_row = int(second.instruction.data["payload"].split(" | ", 1)[0])
    second_node = {
        "local_id": "n2",
        "statement_location": [second_row, second_row],
        "provenance": "explicit",
        "type_hint": "result",
        "summary": "The second packet states a result.",
    }
    edge = {
                        "local_id": "d1",
                        "from": {"local_node": "n1"},
                        "to": {"local_node": "n2"},
                        "type": "supports",
                        "basis": "explicit-prose",
                        "assertion": "explicit",
                        "location": [second_row, second_row],
                        "description": "The first result supports the second.",
                        "confidence": "High",
                    }
    _write_worker_inventory(
        inventory_path,
        {"nodes": [first_node, second_node], "edges": [edge], "gaps": []},
    )
    inventory_dispenser.advance(
        voyage_id,
        {
            "outcome": "reported",
            "nodes": [second_node],
            "edges": [edge],
            "gaps": [],
        },
        responding_to=second.current_evolution.evolution_entry_id,
    )
    terminal = _advance_to_message(inventory_dispenser, voyage_id)
    assert terminal.terminal_result is not None
    inventory = json.loads(
        Path(terminal.terminal_result.value["inventory_path"]).read_text(
            encoding="utf-8"
        )
    )

    assert [node["local_id"] for node in inventory["nodes"]] == ["n1", "n2"]
    assert inventory["edges"][0]["local_id"] == "d1"
    assert inventory["edges"][0]["to"] == {"local_node": "n2"}
    assert inventory["edges"][0]["from"] == {"local_node": "n1"}
    assert inventory["nodes"][0]["statement_location"][0] == 0


def test_later_report_cannot_drop_prior_cumulative_records(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        doc_entrypoint=str(doc_entrypoint), chunk_count="1"
    )

    first = _advance_to_message(inventory_dispenser, voyage_id)
    first_row = int(first.instruction.data["payload"].split(" | ", 1)[0])
    first_inventory = {
        "outcome": "reported",
        "nodes": [
                {
                    "local_id": "n1",
                    "statement_location": [first_row, first_row],
                    "provenance": "explicit",
                    "type_hint": "result",
                    "summary": "The first packet states a result.",
                }
        ],
        "edges": [],
        "gaps": [],
    }
    inventory_path = _worker_inventory_path(run_dir, voyage_id)
    _write_worker_inventory(
        inventory_path,
        {
            "nodes": list(first_inventory["nodes"]),
            "edges": [],
            "gaps": [],
        },
    )
    inventory_dispenser.advance(
        voyage_id,
        first_inventory,
        responding_to=first.current_evolution.evolution_entry_id,
    )
    second = _advance_to_message(inventory_dispenser, voyage_id)
    _write_worker_inventory(
        inventory_path, {"nodes": [], "edges": [], "gaps": []}
    )

    validation = inventory_dispenser.validate(
        voyage_id,
        _empty_inventory(),
        responding_to=second.current_evolution.evolution_entry_id,
    )

    assert not validation.valid
    assert validation.issues[0].code == "invalid-inventory"
    assert "dropped prior nodes" in validation.issues[0].message


def test_prepared_report_reload_does_not_duplicate_source_content(
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
    introduction = inventory_dispenser.get_status(voyage_id)
    source_packets_path = Path(
        introduction.instruction.data["payload"]["cumulative_packets_file"]
    )
    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "ready"},
        responding_to=introduction.current_evolution.evolution_entry_id,
    )
    first = inventory_dispenser.get_status(voyage_id)
    assert isinstance(first.instruction, rutter.Message)
    expected = source_packets_path.read_bytes()

    second = dispenser.make_voyage_dispenser().get_status(voyage_id)

    assert second.instruction == first.instruction
    assert source_packets_path.read_bytes() == expected


def test_default_report_uses_sealed_instruction_after_live_file_drift(
    inventory_run: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading the live instruction after setup would change a Voyage in place."""

    doc_entrypoint, run_dir = inventory_run
    instruction_path = tmp_path / "inventory.md"
    sealed_instruction = "# Sealed inventory instruction\nUse visible evidence.\n"
    instruction_path.write_text(sealed_instruction, encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    monkeypatch.setattr(
        dispenser,
        "_INVENTORY_INSTRUCTION_PATH",
        instruction_path,
    )
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "default",
        run_prefix="sealed-instruction",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
    )
    instruction_path.write_text("# Drifted instruction\n", encoding="utf-8")

    status = inventory_dispenser.get_status(voyage_id)

    assert status.current_evolution.definition_version == 7
    assert status.instruction.data["payload"]["inventory_instruction"] == (
        sealed_instruction
    )
    _write_worker_inventory(
        Path(status.instruction.data["payload"]["inventory_file"]),
        {"nodes": [], "edges": [], "gaps": []},
    )
    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "ready"},
        responding_to=status.current_evolution.evolution_entry_id,
    )
    status = _advance_to_message(inventory_dispenser, voyage_id)
    assert "inventory_instruction" not in status.instruction.data["payload"]
    assert status.instruction.instructions["response_schema"]["required"] == (
        "outcome",
        "nodes",
        "edges",
        "gaps",
    )
    payload = status.instruction.data["payload"]
    assert isinstance(payload, str)
    inventory_dispenser.advance(
        voyage_id,
        _empty_inventory(),
        responding_to=status.current_evolution.evolution_entry_id,
    )
    continued = _advance_to_message(inventory_dispenser, voyage_id)
    assert "inventory_instruction" not in continued.instruction.data["payload"]


@pytest.mark.parametrize(
    "current",
    (dispenser.INVENTORY_VOYAGE, dispenser.DEBUG_INVENTORY_VOYAGE),
)
def test_v6_inventory_roots_reject_v5_reckonings(
    tmp_path: Path,
    current: rutter.Rutter,
) -> None:
    """A persisted v5 root must not silently acquire string packet transport."""

    legacy = rutter.Rutter(
        id=current.rutter_id,
        version=5,
        start="complete",
        evolutions={
            "complete": rutter.Terminal(result=rutter.VoyageResult("complete", None))
        },
    )
    path = Path(f"{current.rutter_id}-v5.reckoning.json")
    rutter.RutterRegistry({"inventory": legacy}, tmp_path).create(
        "inventory", path, {}
    )

    registry = rutter.RutterRegistry({"inventory": current}, tmp_path)
    with pytest.raises(rutter.RutterStateError):
        registry.open(path)


def test_debug_release_archives_the_exact_terminal_reckoning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "main.md"
    source.write_text("# Result\nThe result holds.\n", encoding="utf-8")
    gold_path = run_dir / "gold.json"
    gold_path.write_text(
        json.dumps(_gold_inventory(files=["main.md"])),
        encoding="utf-8",
    )
    aliases_path = run_dir / "source-aliases.json"
    aliases_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    run_prefix = "debug-release"

    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        run_prefix=run_prefix,
        doc_entrypoint=str(source),
        chunk_count="1",
        inventory_gold_standard=str(gold_path),
        inventory_source_aliases=str(aliases_path),
    )
    _finish_empty_debug_voyage(inventory_dispenser, voyage_id)
    voyage_dir = run_dir / "voyages" / run_prefix / voyage_id
    reckoning_path = voyage_dir / "inventory-voyage.reckoning.json"
    expected = reckoning_path.read_bytes()

    inventory_dispenser.release(voyage_id)

    archive = (
        run_dir
        / "artifacts"
        / run_prefix
        / "diagnostics"
        / f"{voyage_id}.reckoning.json"
    )
    assert archive.read_bytes() == expected
    assert not voyage_dir.exists()


def test_debug_release_archives_full_attributed_unequal_diagnosis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping attribution or its gold challenge during release loses the diagnosis."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "main.md"
    source.write_text("# Result\nThe result holds.\n", encoding="utf-8")
    gold_path = run_dir / "gold.json"
    gold_path.write_text(
        json.dumps(_gold_inventory(files=["main.md"])),
        encoding="utf-8",
    )
    aliases_path = run_dir / "source-aliases.json"
    aliases_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    run_prefix = "debug-attributed-release"
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        run_prefix=run_prefix,
        doc_entrypoint=str(source),
        chunk_count="1",
        inventory_gold_standard=str(gold_path),
        inventory_source_aliases=str(aliases_path),
    )

    report = _advance_to_message(inventory_dispenser, voyage_id)
    payload = report.instruction.data["payload"]
    assert isinstance(payload, str)
    inventory_dispenser.advance(
        voyage_id,
        _empty_inventory(
            decision_basis="Only source-visible content was inventoried."
        ),
        responding_to=report.current_evolution.evolution_entry_id,
    )
    comparison = inventory_dispenser.get_status(voyage_id)
    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "no"},
        responding_to=comparison.current_evolution.evolution_entry_id,
    )
    explanation = inventory_dispenser.get_status(voyage_id)
    challenge = {
        "target": "expected hidden theorem expansion",
        "coordinates": [{"source_file": "main.md", "line": 2}],
        "policy": "Inventory workers use only visible source content.",
        "actual_support": "The covered source contains no expanded theorem text.",
        "owner": "gold",
    }
    inventory_dispenser.advance(
        voyage_id,
        {
            "outcome": "diagnosed",
            "attribution": "gold_error",
            "difference": "The expected answer requires invisible content.",
            "reason": "The frozen decision basis excluded hidden expansion text.",
            "minimal_fix": "Adjudicate the expected record against visible source.",
            "gold_challenge": challenge,
        },
        responding_to=explanation.current_evolution.evolution_entry_id,
    )
    while inventory_dispenser.get_status(voyage_id).terminal_result is None:
        status = inventory_dispenser.get_status(voyage_id)
        assert status.instruction is None
        inventory_dispenser.advance(voyage_id)

    inventory_dispenser.release(voyage_id)
    archive = json.loads(
        (
            run_dir
            / "artifacts"
            / run_prefix
            / "diagnostics"
            / f"{voyage_id}.reckoning.json"
        ).read_text(encoding="utf-8")
    )
    diagnostic_runs = [
        run
        for run in archive["completed_runs"].values()
        if run["rutter_id"] == "diagnose-answer"
    ]
    report_record = next(
        record
        for record in archive["root"]["history"]
        if record["state_id"] == "report"
    )
    assert report_record["response"]["evidence"][
        "decision_basis"
    ] == "Only source-visible content was inventoried."
    assert len(diagnostic_runs) == 1
    result = diagnostic_runs[0]["history"][-1]["result"]
    assert result["outcome"] == "different"
    assert result["value"]["detail"] == {
        "attribution": "gold_error",
        "difference": "The expected answer requires invisible content.",
        "reason": "The frozen decision basis excluded hidden expansion text.",
        "minimal_fix": "Adjudicate the expected record against visible source.",
        "gold_challenge": challenge,
    }


def test_debug_release_preserves_working_state_when_archival_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "main.md"
    source.write_text("# Result\nThe result holds.\n", encoding="utf-8")
    gold_path = run_dir / "gold.json"
    gold_path.write_text(
        json.dumps(_gold_inventory(files=["main.md"])),
        encoding="utf-8",
    )
    aliases_path = run_dir / "source-aliases.json"
    aliases_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    run_prefix = "debug-release-failure"

    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        run_prefix=run_prefix,
        doc_entrypoint=str(source),
        chunk_count="1",
        inventory_gold_standard=str(gold_path),
        inventory_source_aliases=str(aliases_path),
    )
    _finish_empty_debug_voyage(inventory_dispenser, voyage_id)
    voyage_dir = run_dir / "voyages" / run_prefix / voyage_id
    reckoning_path = voyage_dir / "inventory-voyage.reckoning.json"
    expected = reckoning_path.read_bytes()

    def fail_archive(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("archive failed")

    monkeypatch.setattr(support, "atomic_replace_bytes", fail_archive)

    with pytest.raises(OSError, match="archive failed"):
        inventory_dispenser.release(voyage_id)

    assert voyage_dir.is_dir()
    assert reckoning_path.read_bytes() == expected


def test_debug_setup_freezes_supplied_gold_and_attaches_diagnosis(
    inventory_run: tuple[Path, Path],
    inventory_gold_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    aliases_path = inventory_gold_path.with_name("source-aliases.json")
    aliases_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()

    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
        inventory_gold_standard=str(inventory_gold_path),
        inventory_source_aliases=str(aliases_path),
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
    assert charter["inventory_source_aliases_text"] == "{}"


def test_debug_report_requires_pre_reference_decision_basis_without_changing_default(
    inventory_run: tuple[Path, Path],
    inventory_gold_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the debug-only basis, or adding it to production reports, breaks isolation."""

    doc_entrypoint, run_dir = inventory_run
    aliases_path = inventory_gold_path.with_name("source-aliases.json")
    aliases_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()

    (default_id,) = inventory_dispenser.initiate_voyages(
        "default",
        run_prefix="default-schema",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
    )
    (debug_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        run_prefix="debug-schema",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
        inventory_gold_standard=str(inventory_gold_path),
        inventory_source_aliases=str(aliases_path),
    )

    default_schema = _advance_to_message(
        inventory_dispenser, default_id
    ).instruction.instructions["response_schema"]
    debug_status = _advance_to_message(inventory_dispenser, debug_id)
    debug_schema = debug_status.instruction.instructions["response_schema"]

    assert default_schema["required"] == ("outcome", "nodes", "edges", "gaps")
    assert "decision_basis" not in default_schema["properties"]
    assert debug_schema["required"] == (
        "outcome",
        "nodes",
        "edges",
        "gaps",
        "decision_basis",
    )
    assert debug_schema["properties"]["decision_basis"] == {
        "type": "string",
        "minLength": 1,
    }
    payload = debug_status.instruction.data["payload"]
    assert isinstance(payload, str)
    invalid = inventory_dispenser.validate(
        debug_id,
        _empty_inventory(decision_basis="   "),
        responding_to=debug_status.current_evolution.evolution_entry_id,
    )
    assert invalid.valid is False
    assert {issue.code for issue in invalid.issues} == {"empty-decision-basis"}


def test_debug_diagnosis_freezes_basis_and_supplies_attribution_guidance(
    inventory_run: tuple[Path, Path],
    inventory_gold_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the contemporaneous basis or visibility challenge route loses causality."""

    doc_entrypoint, run_dir = inventory_run
    aliases_path = inventory_gold_path.with_name("source-aliases.json")
    aliases_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)
    inventory_dispenser = dispenser.make_voyage_dispenser()
    (voyage_id,) = inventory_dispenser.initiate_voyages(
        "debug",
        run_prefix="diagnosis-contract",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
        inventory_gold_standard=str(inventory_gold_path),
        inventory_source_aliases=str(aliases_path),
    )
    report_status = _advance_to_message(inventory_dispenser, voyage_id)
    payload = report_status.instruction.data["payload"]
    assert isinstance(payload, str)
    decision_basis = (
        "I retained only entities whose identity and mathematical content were "
        "visible in this packet."
    )

    inventory_dispenser.advance(
        voyage_id,
        _empty_inventory(decision_basis=decision_basis),
        responding_to=report_status.current_evolution.evolution_entry_id,
    )
    comparison = inventory_dispenser.get_status(voyage_id)
    diagnosis_payload = comparison.instruction.data["payload"]
    metadata = diagnosis_payload["metadata"]

    assert comparison.current_evolution.rutter_id == "diagnose-answer"
    assert comparison.current_evolution.evolution_id == "compare"
    assert set(metadata) == {
        "packet_id",
        "decision_basis",
        "covered_coordinates",
        "diagnosis_guidance",
    }
    assert metadata["decision_basis"] == decision_basis
    assert metadata["packet_id"] == "inventory-001-packet-001"
    assert metadata["diagnosis_guidance"] == support._DIAGNOSIS_GUIDANCE
    visibility_rule = next(
        rule
        for rule in metadata["diagnosis_guidance"]
        if "worker-invisible" in rule
    )
    assert all(
        attribution in visibility_rule
        for attribution in ("gold_error", "allowed_difference", "unresolved")
    )

    inventory_dispenser.advance(
        voyage_id,
        {"outcome": "no"},
        responding_to=comparison.current_evolution.evolution_entry_id,
    )
    explanation = inventory_dispenser.get_status(voyage_id)
    schema = explanation.instruction.instructions["response_schema"]
    assert schema["required"] == (
        "outcome",
        "attribution",
        "difference",
        "reason",
        "minimal_fix",
        "gold_challenge",
    )
    challenge_schema = schema["properties"]["gold_challenge"]["anyOf"][1]
    assert challenge_schema["required"] == (
        "target",
        "coordinates",
        "policy",
        "actual_support",
        "owner",
    )
    challenge = {
        "target": "gold node hidden behind an opaque macro",
        "coordinates": [
            {
                "source_file": "main.md",
                "line": 1,
            }
        ],
        "policy": "Inventory workers may not invent hidden macro expansion content.",
        "actual_support": "Only the opaque invocation is visible in the packet.",
        "owner": "gold",
    }
    validated = inventory_dispenser.validate(
        voyage_id,
        {
            "outcome": "diagnosed",
            "attribution": "gold_error",
            "difference": "The gold includes worker-invisible statement content.",
            "reason": "The pre-reference basis limited claims to visible evidence.",
            "minimal_fix": "Adjudicate the gold expansion outside this debug run.",
            "gold_challenge": challenge,
        },
        responding_to=explanation.current_evolution.evolution_entry_id,
    )
    assert validated.valid is True


def test_disjoint_gold_sources_require_explicit_aliases() -> None:
    with pytest.raises(ValueError, match="does not map to an inventory source"):
        support._resolve_gold_source_map(
            ["sections/main.md"],
            ["appendix/main.md"],
            {},
        )


def test_gold_source_map_rejects_ambiguous_or_non_bijective_matches() -> None:
    with pytest.raises(ValueError, match="maps ambiguously"):
        support._resolve_gold_source_map(
            ["main.md"],
            ["first/main.md", "second/main.md"],
            {},
        )
    with pytest.raises(ValueError, match="must be one-to-one"):
        support._resolve_gold_source_map(
            ["sections/first.md", "sections/second.md"],
            ["appendix/shared.md"],
            {
                "sections/first.md": "appendix/shared.md",
                "sections/second.md": "appendix/shared.md",
            },
        )


def test_gold_projection_uses_validated_source_aliases() -> None:
    gold = _gold_inventory(files=["sections/main.md"], with_node=True)
    source_map = support._resolve_gold_source_map(
        gold["files"],
        ["appendix/main.md"],
        {"sections/main.md": "appendix/main.md"},
    )

    projected = support._project_gold(
        gold,
        {("appendix/main.md", 1)},
        source_map,
        chunk_id="inventory-001",
        files=["appendix/main.md"],
    )

    assert [node["local_id"] for node in projected["nodes"]] == ["n1"]
    assert projected["chunk_id"] == "inventory-001"
    assert projected["files"] == ["appendix/main.md"]


def test_gold_projection_requires_complete_primary_and_nested_locations() -> None:
    gold = {
        "ir_version": 3,
        "chunk_id": "gold",
        "files": ["gold/second.md", "gold/first.md"],
        "nodes": [
            {
                "local_id": "n1",
                "statement_location": [0, 185, 208],
                "provenance": "explicit",
                "type_hint": "result",
                "summary": "A cross-packet result.",
            },
            {
                "local_id": "n2",
                "statement_location": [1, 1, 1],
                "provenance": "explicit",
                "type_hint": "assumption",
                "kind_hint": "local",
                "scope_hint": {
                    "starts_at": [1, 2, 2],
                    "ends_at": [1, 3, 3],
                },
                "summary": "A scoped assumption.",
            },
        ],
        "edges": [],
        "gaps": [],
    }
    source_map = {
        "gold/second.md": "source/second.md",
        "gold/first.md": "source/first.md",
    }
    partial = {
        *(("source/second.md", line) for line in range(185, 189)),
        ("source/first.md", 1),
        ("source/first.md", 2),
    }

    projected = support._project_gold(
        gold,
        partial,
        source_map,
        chunk_id="inventory-007",
        files=["source/first.md", "source/second.md"],
    )

    assert projected == {
        "ir_version": 3,
        "chunk_id": "inventory-007",
        "files": ["source/first.md", "source/second.md"],
        "nodes": [],
        "edges": [],
        "gaps": [],
    }

    complete = partial | {
        *(("source/second.md", line) for line in range(189, 209)),
        ("source/first.md", 3),
    }
    projected = support._project_gold(
        gold,
        complete,
        source_map,
        chunk_id="inventory-007",
        files=["source/first.md", "source/second.md"],
    )

    assert [node["statement_location"] for node in projected["nodes"]] == [
        [1, 185, 208],
        [0, 1, 1],
    ]
    assert projected["nodes"][1]["scope_hint"] == {
        "starts_at": [0, 2, 2],
        "ends_at": [0, 3, 3],
    }


def test_gold_projection_uses_exact_mapped_source_identity() -> None:
    gold = _gold_inventory(files=["gold/main.md"], with_node=True)

    projected = support._project_gold(
        gold,
        {("draft/appendix/main.md", 1)},
        {"gold/main.md": "appendix/main.md"},
        chunk_id="inventory-001",
        files=["appendix/main.md"],
    )

    assert projected["nodes"] == []


def test_gold_projection_closes_nested_edge_and_gap_dependencies() -> None:
    gold = {
        "ir_version": 3,
        "chunk_id": "gold",
        "files": ["gold/main.md"],
        "nodes": [
            {
                "local_id": "n1",
                "statement_location": [0, 1, 1],
                "provenance": "explicit",
                "type_hint": "result",
                "summary": "First result.",
            },
            {
                "local_id": "n2",
                "statement_location": [0, 2, 3],
                "provenance": "explicit",
                "type_hint": "result",
                "summary": "Second result.",
            },
        ],
        "edges": [
            {
                "local_id": "d1",
                "from": {"local_node": "n1"},
                "to": {"local_node": "n2"},
                "type": "supports",
                "basis": "explicit-reference",
                "assertion": "explicit",
                "location": [0, 4, 4],
                "reference": {"location": [0, 5, 5], "locator": {"name": "n1"}},
                "description": "The first result supports the second.",
                "confidence": "Verified",
            },
            {
                "local_id": "d2",
                "from": {
                    "unresolved": {
                        "title": "Local assumption",
                        "statement": "An unresolved scoped assumption.",
                        "resolution_kind": "implicit-entity",
                        "type_hint": "assumption",
                        "kind_hint": "local",
                        "scope_hint": {
                            "starts_at": [0, 6, 6],
                            "ends_at": [0, 7, 7],
                        },
                    }
                },
                "to": {"local_node": "n1"},
                "type": "supports",
                "basis": "explicit-prose",
                "assertion": "explicit",
                "location": [0, 6, 6],
                "description": "The local assumption supports the first result.",
                "confidence": "Verified",
            },
        ],
        "gaps": [
            {
                "local_id": "g1",
                "category": "reference",
                "location": [0, 8, 8],
                "subject": {"local_node": "n2"},
                "reference": {"location": [0, 9, 9], "locator": {"name": "n2"}},
                "description": "The reference target needs checking.",
            }
        ],
    }
    source_map = {"gold/main.md": "source/main.md"}

    projected = support._project_gold(
        gold,
        {("source/main.md", line) for line in (1, 2, 4, 6, 8)},
        source_map,
        chunk_id="inventory-001",
        files=["source/main.md"],
    )

    assert [node["local_id"] for node in projected["nodes"]] == ["n1"]
    assert projected["edges"] == []
    assert projected["gaps"] == []

    projected = support._project_gold(
        gold,
        {("source/main.md", line) for line in range(1, 10)},
        source_map,
        chunk_id="inventory-001",
        files=["source/main.md"],
    )

    assert [edge["local_id"] for edge in projected["edges"]] == ["d1", "d2"]
    assert [gap["local_id"] for gap in projected["gaps"]] == ["g1"]


def test_debug_setup_freezes_explicit_source_aliases(
    inventory_run: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_entrypoint, run_dir = inventory_run
    gold_path = run_dir / "gold.json"
    gold_path.write_text(
        json.dumps(_gold_inventory(files=["sections/main.md"], with_node=True)),
        encoding="utf-8",
    )
    aliases_path = run_dir / "source-aliases.json"
    aliases_path.write_text(
        json.dumps({"sections/main.md": "main.md"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dispenser, "_STATE_ROOT", run_dir)

    (voyage_id,) = dispenser.make_voyage_dispenser().initiate_voyages(
        "debug",
        doc_entrypoint=str(doc_entrypoint),
        chunk_count="1",
        inventory_gold_standard=str(gold_path),
        inventory_source_aliases=str(aliases_path),
    )

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
    assert charter["inventory_source_aliases_text"] == aliases_path.read_text(
        encoding="utf-8"
    )
    assert len(charter["inventory_source_aliases_sha256"]) == 64
    assert json.loads(charter["inventory_gold_source_map_text"]) == {
        "sections/main.md": "main.md"
    }


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

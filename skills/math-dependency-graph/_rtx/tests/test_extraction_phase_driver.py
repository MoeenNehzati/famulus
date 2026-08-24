#!/usr/bin/env python3
"""Tests for inventory pooling followed by one extract job."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))
sys.path.insert(0, str(Path(__file__).parent))

import _extraction_phase_driver as driver  # noqa: E402
from _inventory_unit_iterator import (  # noqa: E402
    next_inventory_unit,
    setup_inventory_iterator,
)
from _run_diagnostics import RunDiagnostics  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    """Persist one test-controlled orchestration artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _entrypoint(tmp_path: Path) -> Path:
    """Create one source file that can identify and initialize a test run."""

    path = tmp_path / "main.tex"
    path.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Setup}\n"
        "An admissible object is fixed.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    return path


def _initialize_diagnostics(tmp_path: Path, entrypoint: Path) -> None:
    """Publish the durable report expected by post-prepare driver phases."""

    RunDiagnostics.initialize(tmp_path, entrypoint=entrypoint)


def _setup_prepared_run(
    tmp_path: Path, *, workers: int = 2, window_chars: int = 40
) -> tuple[Path, dict]:
    """Prepare source, execute its one setup assignment, and return durable state."""

    run_dir = tmp_path / "run"
    report = driver.prepare(_entrypoint(tmp_path), run_dir)
    job = report["next_job"]
    summary = setup_inventory_iterator(
        Path(job["source_packet"]),
        Path(job["state_dir"]),
        requested_workers=workers,
        window_chars=window_chars,
    )
    return run_dir, summary


def _complete_worker(state_dir: Path, worker_index: int) -> None:
    """Acknowledge every durable unit for one test worker."""

    response = next_inventory_unit(state_dir, worker_index)
    while response["state"] == "unit":
        response = next_inventory_unit(
            state_dir, worker_index, ack=response["unit"]["id"]
        )
    assert response == {"state": "complete"}


def _completion_rows(state_dir: Path) -> list[tuple[int, int]]:
    """Read durable worker completion without deriving source units."""

    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
        return connection.execute(
            "SELECT worker_index, complete FROM assignments ORDER BY worker_index"
        ).fetchall()


def test_prepare_returns_one_iterator_setup_assignment_without_inventory_jobs(
    tmp_path: Path,
) -> None:
    """Prepare must hand off one deterministic setup call, never source-bearing workers."""

    run_dir = tmp_path / "run"

    report = driver.prepare(_entrypoint(tmp_path), run_dir)

    assert report["next_job"] == {
        "operation": "setup-inventory-iterator",
        "source_packet": str((run_dir / "source-packet.txt").resolve()),
        "state_dir": str((run_dir / "inventory-iterator").resolve()),
    }
    assert "next_jobs" not in report
    assert not (run_dir / "inventory-chunks.json").exists()
    assert not (run_dir / "inventory-packets").exists()
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    assert state["inventory_iterator_state"] == report["next_job"]["state_dir"]


def test_setup_assignment_produces_only_effective_nonempty_workers(tmp_path: Path) -> None:
    """Executing prepare's setup handoff returns only effective worker ranges."""

    _run_dir, summary = _setup_prepared_run(tmp_path, workers=8, window_chars=40)

    assert summary["effective_workers"] == len(summary["assignments"])
    assert summary["effective_workers"] < summary["requested_workers"]
    assert [assignment["worker_index"] for assignment in summary["assignments"]] == list(
        range(1, summary["effective_workers"] + 1)
    )
    assert all(assignment["unit_count"] > 0 for assignment in summary["assignments"])


def test_advance_inventory_fails_closed_until_every_worker_is_complete(
    tmp_path: Path,
) -> None:
    """One completed worker cannot authorize pooling while another cursor is open."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    assert summary["effective_workers"] == 2
    _complete_worker(state_dir, 1)

    with pytest.raises(ValueError, match=r"inventory workers are incomplete: \[2\]"):
        driver.advance_inventory(state_dir, run_dir)

    assert _completion_rows(state_dir) == [(1, 1), (2, 0)]
    assert not (run_dir / "inventory-ir.json").exists()


def test_advance_inventory_rejects_state_other_than_the_prepared_iterator(
    tmp_path: Path,
) -> None:
    """A different durable iterator directory cannot be substituted at pooling time."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    other_state = tmp_path / "other-iterator"
    setup_inventory_iterator(
        Path(summary["source_packet_path"]),
        other_state,
        requested_workers=2,
        window_chars=40,
    )

    with pytest.raises(ValueError, match="does not match prepared iterator state"):
        driver.advance_inventory(other_state, run_dir)

    assert not (run_dir / "inventory-ir.json").exists()


def test_advance_inventory_rejects_mismatched_private_controller_state(
    tmp_path: Path,
) -> None:
    """Pooling must authenticate each private packet against durable worker ownership."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    for assignment in summary["assignments"]:
        _complete_worker(state_dir, assignment["worker_index"])
    controller_path = Path(summary["assignments"][0]["controller_packet_path"])
    controller = json.loads(controller_path.read_text(encoding="utf-8"))
    controller["unit_ids"] = []
    _write_json(controller_path, controller)

    with pytest.raises(ValueError, match="controller packet does not match worker 1"):
        driver.advance_inventory(state_dir, run_dir)

    assert not (run_dir / "inventory-ir.json").exists()


def test_completed_iterator_pools_private_controller_packets_and_worker_inventories(
    tmp_path: Path,
) -> None:
    """Pooling provenance is controller-only and successful output enters extract unchanged."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    for assignment in summary["assignments"]:
        _complete_worker(state_dir, assignment["worker_index"])

    report = driver.advance_inventory(state_dir, run_dir)

    assert report["inventory_ir"] == str((run_dir / "inventory-ir.json").resolve())
    assert report["next_job"]["chunk_id"] == "extract-001"
    assert report["next_job"]["source_packet"] == str(
        (run_dir / "source-packet.txt").resolve()
    )
    pooled = json.loads((run_dir / "inventory-ir.json").read_text(encoding="utf-8"))
    assert pooled["chunk_id"] == "pooled"
    diagnostics = RunDiagnostics.open(run_dir).payload
    artifacts = [
        artifact for artifact in diagnostics["artifacts"]
        if artifact["kind"] == "inventory-packet"
    ]
    assert [artifact["path"] for artifact in artifacts] == [
        assignment["controller_packet_path"] for assignment in summary["assignments"]
    ]
    assert all(
        artifact["path"] != summary["source_packet_path"] for artifact in artifacts
    )


def test_completed_iterator_publishes_bounded_iterator_diagnostics(
    tmp_path: Path,
) -> None:
    """Pooling without the durable iterator aggregates loses traversal evidence."""

    run_dir, summary = _setup_prepared_run(tmp_path, workers=2, window_chars=40)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    for assignment in summary["assignments"]:
        _complete_worker(state_dir, assignment["worker_index"])

    driver.advance_inventory(state_dir, run_dir)

    iterator_summary = RunDiagnostics.open(run_dir).payload["iterator"]
    assert iterator_summary["setup"]["unit_count"] == len(summary["units"])
    assert iterator_summary["setup"]["worker_count"] == summary["effective_workers"]
    assert iterator_summary["setup"]["assigned_characters"] == sum(
        assignment["character_count"] for assignment in summary["assignments"]
    )
    assert iterator_summary["next"]["acknowledgements"] == len(summary["units"])
    assert iterator_summary["next"]["failures"] == 0
    assert iterator_summary["next"]["open_sequence"] == {
        "count": 0,
        "unit_count": 0,
        "character_count": 0,
        "maximum_elapsed_ms": 0,
    }


def test_advance_inventory_rejects_valid_content_changed_after_final_ack(
    tmp_path: Path,
) -> None:
    """Unacknowledged post-completion inventory content cannot enter pooling."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    for assignment in summary["assignments"]:
        _complete_worker(state_dir, assignment["worker_index"])
    first_assignment = summary["assignments"][0]
    first_unit = next(
        unit for unit in summary["units"]
        if unit["id"] == first_assignment["first_unit_id"]
    )
    coordinate = first_unit["coordinates"][0]
    inventory_path = Path(first_assignment["inventory_path"])
    changed = json.loads(inventory_path.read_text(encoding="utf-8"))
    changed["nodes"] = [
        {
            "local_id": "n1",
            "location": [
                changed["files"].index(coordinate["source"]),
                coordinate["line"],
                coordinate["line"],
            ],
            "provenance": "explicit",
            "type_hint": "setup",
            "summary": "A valid but unacknowledged post-completion candidate.",
        }
    ]
    _write_json(inventory_path, changed)

    with pytest.raises(ValueError, match="does not match its final acknowledgement"):
        driver.advance_inventory(state_dir, run_dir)

    assert not (run_dir / "inventory-ir.json").exists()
    diagnostics = RunDiagnostics.open(run_dir).payload
    assert all(stage["operation"] != "pooling" for stage in diagnostics["stages"])


def test_advance_inventory_pools_completed_fragment_after_authorized_reack(
    tmp_path: Path,
) -> None:
    """A public validation retry authenticates one corrected final worker artifact."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    assignment = summary["assignments"][0]
    for other in summary["assignments"][1:]:
        _complete_worker(state_dir, other["worker_index"])
    first_unit = next(
        unit for unit in summary["units"]
        if unit["id"] == assignment["first_unit_id"]
    )
    coordinate = first_unit["coordinates"][0]
    inventory_path = Path(assignment["inventory_path"])
    rejected = json.loads(inventory_path.read_text(encoding="utf-8"))
    rejected["nodes"] = [
        {
            "local_id": "n1",
            "location": [
                rejected["files"].index(coordinate["source"]),
                coordinate["line"],
                coordinate["line"],
            ],
            "provenance": "explicit",
            "type_hint": "setup",
            "summary": "The first candidate at a duplicated source anchor.",
        },
        {
            "local_id": "n2",
            "location": [
                rejected["files"].index(coordinate["source"]),
                coordinate["line"],
                coordinate["line"],
            ],
            "provenance": "explicit",
            "type_hint": "result",
            "summary": "The duplicate candidate rejected during pooling.",
        },
    ]
    _write_json(inventory_path, rejected)

    response = next_inventory_unit(state_dir, assignment["worker_index"])
    while response["state"] == "unit":
        response = next_inventory_unit(
            state_dir,
            assignment["worker_index"],
            ack=response["unit"]["id"],
        )
    assert response == {"state": "complete"}
    with pytest.raises(ValueError, match="candidate anchor emitted more than once"):
        driver.advance_inventory(state_dir, run_dir)

    unchanged = next_inventory_unit(
        state_dir,
        assignment["worker_index"],
        ack=assignment["last_unit_id"],
        retry_code="validation-failed",
    )
    assert unchanged["state"] == "failure"
    assert unchanged["error"]["code"] == "retry-artifact-unchanged"
    conflicting = next_inventory_unit(
        state_dir,
        assignment["worker_index"],
        ack=assignment["last_unit_id"],
        wrap=True,
        retry_code="validation-failed",
    )
    assert conflicting["state"] == "failure"
    assert conflicting["error"]["code"] == "conflicting-retry"
    other = summary["assignments"][1]
    cross_worker = next_inventory_unit(
        state_dir,
        other["worker_index"],
        ack=other["last_unit_id"],
        retry_code="validation-failed",
    )
    assert cross_worker["state"] == "failure"
    assert cross_worker["error"]["code"] == "unauthorized-retry"

    corrected = {**rejected, "nodes": rejected["nodes"][:1]}
    _write_json(inventory_path, corrected)
    assert next_inventory_unit(
        state_dir,
        assignment["worker_index"],
        ack=assignment["last_unit_id"],
        retry_code="validation-failed",
    ) == {"state": "complete"}
    report = driver.advance_inventory(state_dir, run_dir)

    assert report["inventory_ir"] == str((run_dir / "inventory-ir.json").resolve())
    authenticated = json.loads(
        (
            run_dir
            / "authenticated-inventory-fragments"
            / f"iterator-worker-{assignment['worker_index']:03d}.json"
        ).read_text(encoding="utf-8")
    )
    assert authenticated == corrected
    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
        original_ack = connection.execute(
            "SELECT content_sha256 FROM acknowledgements WHERE worker_index = ?",
            (assignment["worker_index"],),
        ).fetchone()[0]
        reauthentication = connection.execute(
            "SELECT prior_content_sha256, content_sha256 "
            "FROM acknowledgement_reauthentications WHERE worker_index = ?",
            (assignment["worker_index"],),
        ).fetchone()
        authorization = connection.execute(
            "SELECT unit_id, wrapped, retry_code, inventory_path, consumed_at, "
            "replacement_content_sha256 FROM completed_retry_authorizations "
            "WHERE worker_index = ?",
            (assignment["worker_index"],),
        ).fetchone()
    assert reauthentication[0] == original_ack
    assert reauthentication[1] != original_ack
    assert authorization[:4] == (
        assignment["last_unit_id"],
        0,
        "validation-failed",
        str(inventory_path.resolve()),
    )
    assert authorization[4] is not None
    assert authorization[5] == reauthentication[1]


def test_advance_inventory_never_rereads_worker_files_after_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pooling and diagnostics use sealed authenticated content, not worker files."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    for assignment in summary["assignments"]:
        _complete_worker(state_dir, assignment["worker_index"])
    worker_paths = [
        Path(assignment["inventory_path"]) for assignment in summary["assignments"]
    ]
    acknowledged = [
        json.loads(path.read_text(encoding="utf-8")) for path in worker_paths
    ]
    real_verify = driver.verify_completed_inventories

    def verify_then_delete(state_path: Path) -> dict:
        verification = real_verify(state_path)
        for path in worker_paths:
            path.unlink()
        return verification

    monkeypatch.setattr(driver, "verify_completed_inventories", verify_then_delete)

    report = driver.advance_inventory(state_dir, run_dir)

    assert report["inventory_ir"] == str((run_dir / "inventory-ir.json").resolve())
    sealed_paths = [
        run_dir
        / "authenticated-inventory-fragments"
        / f"iterator-worker-{assignment['worker_index']:03d}.json"
        for assignment in summary["assignments"]
    ]
    assert [
        json.loads(path.read_text(encoding="utf-8")) for path in sealed_paths
    ] == acknowledged
    diagnostics = RunDiagnostics.open(run_dir).payload
    fragment_artifacts = [
        artifact
        for artifact in diagnostics["artifacts"]
        if artifact["kind"] == "inventory-fragment"
    ]
    assert [artifact["path"] for artifact in fragment_artifacts] == [
        str(path.resolve()) for path in sealed_paths
    ]
    pooling_stage = next(
        stage for stage in diagnostics["stages"]
        if stage["operation"] == "pooling"
    )
    assert all(
        str(path.resolve()) not in pooling_stage["input_paths"]
        for path in worker_paths
    )
    assert all(
        str(path.resolve()) in pooling_stage["input_paths"] for path in sealed_paths
    )


def test_advance_inventory_resumes_after_crash_from_same_durable_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pooling crash may retry from SQLite state without source planning or setup."""

    run_dir, summary = _setup_prepared_run(tmp_path)
    state_dir = Path(summary["assignments"][0]["inventory_path"]).parents[2]
    for assignment in summary["assignments"]:
        _complete_worker(state_dir, assignment["worker_index"])
    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
        durable_units = connection.execute(
            "SELECT id, ordinal, metadata_json FROM units ORDER BY ordinal"
        ).fetchall()
    real_pool = driver.pool_inventory_fragments
    attempts = 0

    def crash_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected pooling crash")
        return real_pool(*args, **kwargs)

    monkeypatch.setattr(driver, "pool_inventory_fragments", crash_once)

    with pytest.raises(RuntimeError, match="injected pooling crash"):
        driver.advance_inventory(state_dir, run_dir)
    report = driver.advance_inventory(state_dir, run_dir)

    with sqlite3.connect(state_dir / "iterator.sqlite3") as connection:
        assert connection.execute(
            "SELECT id, ordinal, metadata_json FROM units ORDER BY ordinal"
        ).fetchall() == durable_units
    assert attempts == 2
    assert report["next_job"]["chunk_id"] == "extract-001"
    assert not hasattr(driver, "plan_inventory_chunks")


@pytest.mark.parametrize("retired_mode", ("advance-entities", "finalize", "finalize-semantic"))
def test_cli_rejects_retired_semantic_routes(tmp_path: Path, retired_mode: str) -> None:
    """Only the inventory-to-extract finalization route remains public."""

    with pytest.raises(SystemExit) as failure:
        driver.main(
            [
                retired_mode,
                str(tmp_path / "fragments.json"),
                "--run-dir",
                str(tmp_path),
            ]
        )

    assert failure.value.code == 2


def test_finalize_extract_persists_invalid_extract_for_localized_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconciliation failure must return an executable fail-closed repair handoff."""

    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    inventory_path = tmp_path / "inventory-ir.json"
    _write_json(
        inventory_path,
        {
            "ir_version": 2,
            "chunk_id": "pooled",
            "files": ["section.tex"],
            "evidence": [
                {
                    "id": "inventory-001::e1",
                    "location": [0, 1, 1],
                    "role": "statement",
                }
            ],
            "references": [],
            "candidates": [
                {
                    "id": "section.tex:1",
                    "location": [0, 1, 1],
                    "provenance": "explicit",
                    "type_hint": "setup",
                    "evidence_ids": ["inventory-001::e1"],
                    "summary": "An admissible object is fixed.",
                }
            ],
            "unresolved_entities": [],
            "relationship_hints": [],
            "reference_decisions": [],
            "gaps": [],
        },
    )
    source_packet = tmp_path / "source-packet.txt"
    source_packet.write_text("@@ source: section.tex\n0001 | source\n", encoding="utf-8")
    packet_path = tmp_path / "extract-packets/extract-001.json"
    sidecar_path = tmp_path / "extract-sidecars/extract-001.json"
    _write_json(packet_path, {"mode": "extract"})
    _write_json(sidecar_path, {"sidecar_version": 1})
    semantic_path = tmp_path / "extract-001.json"
    _write_json(
        semantic_path,
        {
            "ir_version": 2,
            "document": {"source_file": "main.tex"},
            "inventory": {"candidate_ids": ["section.tex:1"], "candidate_count": 1},
            "entities": [],
            "exclusions": [],
            "unresolved_resolutions": [],
            "relationships": [],
            "hint_decisions": [],
            "reference_decisions": [],
            "gap_decisions": [],
            "gaps": [],
        },
    )
    manifest_path = tmp_path / "extract-fragments.json"
    _write_json(manifest_path, {"fragments": [str(semantic_path)]})
    extract_manifest_path = tmp_path / "extract-chunks.json"
    _write_json(
        extract_manifest_path,
        {
            "chunks": [
                {
                    "chunk_id": "extract-001",
                    "packet_path": str(packet_path),
                    "fragment_path": str(semantic_path),
                    "sidecar_path": str(sidecar_path),
                    "source_packet_path": str(source_packet),
                    "entrypoint_path": str(entrypoint),
                    "progress_path": str(
                        tmp_path / "progress" / "extract-001.progress.md"
                    ),
                }
            ]
        },
    )
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(source_packet),
            "inventory_ir": str(inventory_path),
            "extract_manifest": str(extract_manifest_path),
        },
    )
    monkeypatch.setattr(
        driver,
        "_compile_and_render",
        lambda *_args, **_kwargs: pytest.fail("invalid extract reached compilation"),
    )

    report = driver.finalize_extract(manifest_path, tmp_path, None)

    assert report["status"] == "correction-required"
    assert report["validation_diagnostics"] == [
        {"code": "unreconciled-candidate", "path": [], "message": "unreconciled candidate"}
    ]
    assert report["diagnostics"] == str(tmp_path / "run-diagnostics.json")
    repair_base = Path(report["repair_base"])
    assert repair_base == tmp_path / "semantic-repair-base.json"
    correction_report_path = Path(report["correction_report"])
    assert correction_report_path == tmp_path / "semantic-correction.json"
    assert json.loads(correction_report_path.read_text(encoding="utf-8")) == report
    assert json.loads(repair_base.read_text(encoding="utf-8")) == json.loads(
        semantic_path.read_text(encoding="utf-8")
    )
    correction_job = report["next_job"]
    assert correction_job == {
        "chunk_id": "extract-001-correction-001",
        "instruction": str(SKILL_DIR / "instructions/extract.md"),
        "schema": str(SKILL_DIR / "semantic-repair.schema.json"),
        "base": str(SKILL_DIR / "base.json"),
        "repair_base": str(repair_base),
        "inventory": str(inventory_path),
        "packet": str(packet_path),
        "sidecar": str(sidecar_path),
        "source_packet": str(source_packet),
        "entrypoint": str(entrypoint),
        "progress_path": str(tmp_path / "progress" / "extract-001.progress.md"),
        "validation_diagnostics": report["validation_diagnostics"],
        "output": str(tmp_path / "extract-fragments/extract-001-correction-001.json"),
    }
    assert not (tmp_path / "semantic-ir.json").exists()
    assert not (tmp_path / "dependency-graph.json").exists()
    state = json.loads((tmp_path / "run-state.json").read_text(encoding="utf-8"))
    assert state["semantic_repair_base"] == str(repair_base)
    assert state["semantic_correction_report"] == str(correction_report_path)

    diagnostics = json.loads(
        (tmp_path / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["stages"][0]["operation"] == "validation"
    assert diagnostics["stages"][0]["status"] == "failure"
    assert diagnostics["stages"][1]["operation"] == "planning"
    assert diagnostics["stages"][1]["status"] == "success"
    assert diagnostics["run"]["status"] == "running"
    assert diagnostics["counts"]["validation_errors"] == 1

    repair = {
        "repair_version": 2,
        "remove_entity_ids": [],
        "upsert_entities": [
            {
                "candidate_ids": ["section.tex:1"],
                "id": "definition",
                "type": "setup",
                "kind": "definition",
                "short_title": "Definition",
                "description": "An admissible object is fixed.",
                "source": "explicit",
            }
        ],
        "remove_exclusion_candidate_ids": [],
        "upsert_exclusions": [],
        "remove_unresolved_ids": [],
        "upsert_unresolved_resolutions": [],
        "remove_relationships": [],
        "upsert_relationships": [],
        "remove_hint_ids": [],
        "upsert_hint_decisions": [],
        "remove_reference_ids": [],
        "upsert_reference_decisions": [],
            "remove_gap_ids": [],
            "upsert_gaps": [],
            "remove_inventory_gap_ids": [],
            "upsert_gap_decisions": [],
    }
    repair_path = Path(correction_job["output"])
    _write_json(repair_path, repair)
    _write_json(semantic_path, {"replaced_after_handoff": True})
    repair_manifest_path = tmp_path / "repair-fragments.json"
    _write_json(repair_manifest_path, {"fragments": [str(repair_path)]})
    captured: dict = {}

    def capture_compile(repaired: dict, *_args, **_kwargs) -> dict:
        captured["semantic"] = repaired
        return {"status": "captured"}

    monkeypatch.setattr(driver, "_compile_and_render", capture_compile)

    repaired_report = driver.finalize_extract(repair_manifest_path, tmp_path, None)

    assert repaired_report["status"] == "captured"
    assert captured["semantic"]["entities"][0]["id"] == "definition"
    assert json.loads((tmp_path / "semantic-ir.json").read_text(encoding="utf-8")) == captured[
        "semantic"
    ]


@pytest.mark.parametrize(
    ("fragment_text", "diagnostic"),
    [
        (
            "{",
            {
                "code": "invalid-fragment-json",
                "path": [],
                "message": "extract fragment is not valid JSON",
            },
        ),
        (
            "[]",
            {
                "code": "invalid-fragment-shape",
                "path": [],
                "message": "extract fragment must be a JSON object",
            },
        ),
        (
            '"scalar"',
            {
                "code": "invalid-fragment-shape",
                "path": [],
                "message": "extract fragment must be a JSON object",
            },
        ),
    ],
)
def test_finalize_extract_retries_malformed_or_nonobject_worker_output(
    tmp_path: Path,
    fragment_text: str,
    diagnostic: dict,
) -> None:
    """Unreadable whole-document output must return a fresh normal extract job."""

    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    inventory_path = tmp_path / "inventory-ir.json"
    _write_json(
        inventory_path,
        {
            "ir_version": 2,
            "chunk_id": "pooled",
            "files": ["section.tex"],
            "evidence": [],
            "references": [],
            "candidates": [],
            "unresolved_entities": [],
            "relationship_hints": [],
            "reference_decisions": [],
            "gaps": [],
        },
    )
    source_packet = tmp_path / "source-packet.txt"
    source_packet.write_text(
        "@@ source: section.tex\n0001 | source\n", encoding="utf-8"
    )
    packet_path = tmp_path / "extract-packets/extract-001.json"
    sidecar_path = tmp_path / "extract-sidecars/extract-001.json"
    _write_json(packet_path, {"mode": "extract"})
    _write_json(sidecar_path, {"sidecar_version": 1})
    fragment_path = tmp_path / "extract-fragments/extract-001.json"
    fragment_path.parent.mkdir(parents=True, exist_ok=True)
    fragment_path.write_text(fragment_text, encoding="utf-8")
    extract_manifest_path = tmp_path / "extract-chunks.json"
    _write_json(
        extract_manifest_path,
        {
            "chunks": [
                {
                    "chunk_id": "extract-001",
                    "packet_path": str(packet_path),
                    "fragment_path": str(fragment_path),
                    "sidecar_path": str(sidecar_path),
                    "source_packet_path": str(source_packet),
                    "entrypoint_path": str(entrypoint),
                    "progress_path": str(
                        tmp_path / "progress" / "extract-001.progress.md"
                    ),
                }
            ]
        },
    )
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(source_packet),
            "inventory_ir": str(inventory_path),
            "extract_manifest": str(extract_manifest_path),
        },
    )
    manifest_path = tmp_path / "extract-fragments.json"
    _write_json(manifest_path, {"fragments": [str(fragment_path)]})

    report = driver.finalize_extract(manifest_path, tmp_path, None)

    assert report["status"] == "retry-required"
    assert report["validation_diagnostics"] == [diagnostic]
    assert report["next_job"]["schema"] == str(
        SKILL_DIR / "semantic-graph.schema.json"
    )
    assert report["next_job"]["output"] == str(
        tmp_path / "extract-fragments/extract-001-retry-001.json"
    )
    assert json.loads(
        (tmp_path / "semantic-retry.json").read_text(encoding="utf-8")
    ) == report
    durable = RunDiagnostics.open(tmp_path).payload
    assert durable["run"]["status"] == "running"
    assert durable["counts"]["validation_errors"] == 1


def test_finalize_extract_applies_repair_with_saved_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The phase driver cannot call repair application without exact inventory."""

    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    inventory = {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["section.tex"],
        "evidence": [
            {
                "id": "inventory-001::e1",
                "location": [0, 1, 1],
                "role": "statement",
            }
        ],
        "references": [],
        "candidates": [
            {
                "id": "section.tex:1",
                "location": [0, 1, 1],
                "provenance": "explicit",
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
                "summary": "An admissible object is fixed.",
            }
        ],
        "unresolved_entities": [],
        "relationship_hints": [],
        "reference_decisions": [],
        "gaps": [],
    }
    semantic = {
        "ir_version": 2,
        "document": {"source_file": "main.tex"},
        "inventory": {"candidate_ids": ["section.tex:1"], "candidate_count": 1},
        "entities": [
            {
                "candidate_ids": ["section.tex:1"],
                "id": "definition",
                "type": "setup",
                "kind": "definition",
                "short_title": "Definition",
                "description": "An admissible object is fixed.",
                "source": "explicit",
            }
        ],
        "exclusions": [],
        "unresolved_resolutions": [],
        "relationships": [],
        "hint_decisions": [],
        "reference_decisions": [],
        "gap_decisions": [],
        "gaps": [],
    }
    inventory_path = tmp_path / "inventory-ir.json"
    _write_json(inventory_path, inventory)
    _write_json(tmp_path / "semantic-ir.json", semantic)
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": "/tmp/source-packet.txt",
            "inventory_ir": str(inventory_path),
        },
    )
    repair = {
        "repair_version": 2,
        "remove_entity_ids": ["definition"],
        "upsert_entities": [
            {
                **semantic["entities"][0],
                "description": "A repaired admissible object is fixed.",
            }
        ],
        "remove_exclusion_candidate_ids": [],
        "upsert_exclusions": [],
        "remove_unresolved_ids": [],
        "upsert_unresolved_resolutions": [],
        "remove_relationships": [],
        "upsert_relationships": [],
        "remove_hint_ids": [],
        "upsert_hint_decisions": [],
        "remove_reference_ids": [],
        "upsert_reference_decisions": [],
        "remove_gap_ids": [],
        "upsert_gaps": [],
        "remove_inventory_gap_ids": [],
        "upsert_gap_decisions": [],
    }
    repair_path = tmp_path / "repair.json"
    _write_json(repair_path, repair)
    manifest_path = tmp_path / "repair-fragments.json"
    _write_json(manifest_path, {"fragments": [str(repair_path)]})
    captured: dict = {}

    def capture_compile(
        repaired: dict,
        _semantic_path: Path,
        used_inventory: dict,
        *_args,
    ) -> dict:
        captured["semantic"] = repaired
        captured["inventory"] = used_inventory
        return {"status": "captured"}

    monkeypatch.setattr(driver, "_compile_and_render", capture_compile)

    report = driver.finalize_extract(manifest_path, tmp_path, None)

    assert report == {
        "status": "captured",
        "diagnostics": str(tmp_path / "run-diagnostics.json"),
    }
    assert captured["inventory"] == inventory
    assert captured["semantic"]["entities"][0]["description"].startswith("A repaired")
    diagnostics = json.loads(
        (tmp_path / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["stages"][0]["operation"] == "correction-application"
    assert diagnostics["stages"][0]["status"] == "success"
    artifacts = {artifact["kind"]: artifact for artifact in diagnostics["artifacts"]}
    assert artifacts["semantic-fragment"]["counts"] == {
        "entities": 2,
        "exclusions": 0,
        "unresolved_resolutions": 0,
        "relationships": 0,
        "hint_decisions": 0,
        "reference_decisions": 0,
        "gap_decisions": 0,
        "gaps": 0,
    }
    assert artifacts["semantic-ir"]["counts"]["entities"] == 1


def test_prepare_initializes_and_instruments_source_and_planning(tmp_path: Path) -> None:
    """A run that starts without diagnostics cannot support later performance analysis."""

    run_dir = tmp_path / "run"

    report = driver.prepare(_entrypoint(tmp_path), run_dir)

    diagnostics = json.loads(
        (run_dir / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["run"]["status"] == "running"
    assert [stage["operation"] for stage in diagnostics["stages"]] == [
        "source-preparation",
        "planning",
    ]
    assert {artifact["kind"] for artifact in diagnostics["artifacts"]} == {
        "active-source",
    }
    assert report["diagnostics"] == str(run_dir / "run-diagnostics.json")
    assert report["next_job"]["state_dir"] == str(
        (run_dir / "inventory-iterator").resolve()
    )


def test_extract_retry_and_correction_preserve_progress_sidecar(tmp_path: Path) -> None:
    """Sequential attempts append to one durable logical-job progress file."""

    progress_path = str((tmp_path / "progress" / "extract-001.progress.md").resolve())
    chunk = {
        "chunk_id": "extract-001",
        "packet_path": str(tmp_path / "extract-packets" / "extract-001.json"),
        "fragment_path": str(tmp_path / "extract-fragments" / "extract-001.json"),
        "sidecar_path": str(tmp_path / "extract-sidecars" / "extract-001.json"),
        "source_packet_path": str(tmp_path / "source-packet.txt"),
        "entrypoint_path": str(tmp_path / "main.tex"),
        "progress_path": progress_path,
    }

    normal = driver._extract_job(chunk)
    retry = driver._extract_retry_job(
        chunk,
        output_path=tmp_path / "extract-fragments" / "extract-001-retry-001.json",
    )
    correction = driver._correction_job(
        chunk,
        repair_base_path=tmp_path / "semantic-repair-base.json",
        inventory_path=tmp_path / "inventory-ir.json",
        validation_diagnostics=[],
        output_path=tmp_path / "extract-fragments" / "extract-001-correction-001.json",
    )

    assert normal["progress_path"] == progress_path
    assert retry["progress_path"] == progress_path
    assert correction["progress_path"] == progress_path


def test_phase_failure_marks_run_failed_and_preserves_earlier_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later deterministic failure must not erase an earlier successful stage."""

    def fail_planning(*_args, **_kwargs):
        raise RuntimeError("injected planning failure")

    monkeypatch.setattr(driver, "_iterator_setup_job", fail_planning)
    run_dir = tmp_path / "run"

    with pytest.raises(RuntimeError, match="injected planning failure"):
        driver.prepare(_entrypoint(tmp_path), run_dir)

    diagnostics = json.loads(
        (run_dir / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert [stage["status"] for stage in diagnostics["stages"]] == [
        "success",
        "failure",
    ]
    assert diagnostics["run"]["status"] == "failure"
    assert diagnostics["run"]["diagnostic"]["exception_type"] == "RuntimeError"


def test_finalize_records_artifacts_ratios_counts_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final success without semantic/graph measurements is incomplete diagnostics."""

    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    inventory = {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["section.tex"],
        "evidence": [
            {
                "id": "inventory-001::e1",
                "location": [0, 1, 1],
                "role": "statement",
            }
        ],
        "references": [],
        "candidates": [
            {
                "id": "section.tex:1",
                "location": [0, 1, 1],
                "provenance": "explicit",
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
                "summary": "An admissible object is fixed.",
            }
        ],
        "unresolved_entities": [],
        "relationship_hints": [],
        "reference_decisions": [],
        "gaps": [],
    }
    semantic = {
        "ir_version": 2,
        "document": {"source_file": "main.tex"},
        "inventory": {"candidate_ids": ["section.tex:1"], "candidate_count": 1},
        "entities": [
            {
                "candidate_ids": ["section.tex:1"],
                "id": "definition",
                "type": "setup",
                "kind": "definition",
                "short_title": "Definition",
                "description": "An admissible object is fixed.",
                "source": "explicit",
            }
        ],
        "exclusions": [],
        "unresolved_resolutions": [],
        "relationships": [],
        "hint_decisions": [],
        "reference_decisions": [],
        "gap_decisions": [],
        "gaps": [],
    }
    inventory_path = tmp_path / "inventory-ir.json"
    semantic_fragment_path = tmp_path / "extract-001.json"
    _write_json(inventory_path, inventory)
    _write_json(semantic_fragment_path, semantic)
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(tmp_path / "source-packet.txt"),
            "inventory_ir": str(inventory_path),
        },
    )
    manifest_path = tmp_path / "extract-fragments.json"
    _write_json(manifest_path, {"fragments": [str(semantic_fragment_path)]})

    def compile_after_counts(*_args) -> dict:
        durable = RunDiagnostics.open(tmp_path).payload
        assert durable["counts"]["entities"] == 1
        assert durable["counts"]["relationships"] == 0
        return {"metadata": {}, "entities": [], "relationships": []}

    monkeypatch.setattr(driver, "compile_semantic_graph", compile_after_counts)
    monkeypatch.setattr(
        driver, "extract_macros", lambda _entrypoint: {"RR": "\\mathbb{R}"}
    )

    def render(args: list[str]) -> None:
        html_path = Path(args[args.index("--html-out") + 1])
        html_path.write_text("<html>graph</html>\n", encoding="utf-8")

    monkeypatch.setattr(driver, "render_graph", render)

    result = driver.finalize_extract(manifest_path, tmp_path, None)

    diagnostics = json.loads(
        (tmp_path / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert [stage["operation"] for stage in diagnostics["stages"]] == [
        "validation",
        "compilation",
        "macro-extraction",
        "rendering",
    ]
    assert [ratio["kind"] for ratio in diagnostics["ratios"]] == [
        "semantic-ir-to-pooled-inventory",
        "renderer-json-to-semantic-ir",
    ]
    assert {artifact["kind"] for artifact in diagnostics["artifacts"]} == {
        "pooled-inventory",
        "semantic-fragment",
        "semantic-ir",
        "renderer-json",
        "macro-file",
        "html",
    }
    assert diagnostics["counts"]["entities"] == 1
    assert diagnostics["counts"]["relationships"] == 0
    artifacts = {artifact["kind"]: artifact for artifact in diagnostics["artifacts"]}
    expected_semantic_counts = {
        "entities": 1,
        "exclusions": 0,
        "unresolved": 0,
        "unresolved_resolutions": 0,
        "relationships": 0,
        "hint_decisions": 0,
        "reference_decisions": 0,
        "gap_decisions": 0,
        "gaps": 0,
    }
    assert artifacts["pooled-inventory"]["counts"]["candidates"] == 1
    assert artifacts["semantic-fragment"]["counts"] == expected_semantic_counts
    assert artifacts["semantic-ir"]["counts"] == expected_semantic_counts
    assert artifacts["renderer-json"]["counts"] == {
        "entities": 0,
        "relationships": 0,
    }
    assert artifacts["macro-file"]["counts"] == {"macros": 1}
    assert "counts" not in artifacts["html"]
    assert diagnostics["run"]["status"] == "success"
    assert result["diagnostics"] == str(tmp_path / "run-diagnostics.json")


def _proof_transition() -> tuple[dict, dict]:
    """Return one reconciled inventory and transitional proof graph."""

    from test_proof_normalizer import _semantic_ir

    semantic = _semantic_ir()
    semantic["entities"][3]["kind"] = "informal"
    semantic["entities"].append(
        {
            "candidate_ids": [],
            "id": "proof-alternative",
            "type": "proof",
            "kind": "sketch",
            "short_title": "Alternative proof of R",
            "description": "A genuinely alternative argument for R.",
            "source": "explicit",
        }
    )
    semantic["relationships"].extend(
        [
            {
                "from": "assumption-a", "to": "proof-alternative", "type": "supports",
                "description": "The alternative proof uses A.", "hint_ids": ["pool::h6"],
                "evidence_ids": ["pool::e6"], "implicit": False, "confidence": "High",
            },
            {
                "from": "proof-alternative", "to": "result-r", "type": "proves",
                "description": "The alternative argument proves R.", "hint_ids": ["pool::h7"],
                "evidence_ids": ["pool::e7"], "implicit": False, "confidence": "Verified",
            },
        ]
    )
    for relationship in semantic["relationships"]:
        relationship["implicit"] = False
    candidate_ids = [
        "candidate-a", "candidate-r", "candidate-proof-formal",
        "candidate-proof-informal", "candidate-proof-alternative",
    ]
    for entity, candidate_id in zip(semantic["entities"], candidate_ids, strict=True):
        entity["candidate_ids"] = [candidate_id]
    semantic["inventory"] = {
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
    }
    inventory = {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["main.tex"],
        "evidence": [
            {"id": f"pool::e{index}", "location": [0, index, index], "role": "statement"}
            for index in range(1, 8)
        ],
        "references": [
            {"id": "pool::r1", "location": [0, 1, 1], "raw": "A", "kind": "label"}
        ],
        "candidates": [
            {
                "id": candidate_id,
                "location": [0, index, index],
                "provenance": "explicit",
                "type_hint": "proof" if "proof" in candidate_id else ("result" if candidate_id.endswith("r") else "assumption"),
                "evidence_ids": [f"pool::e{min(index, 5)}"],
                "summary": candidate_id,
            }
            for index, candidate_id in enumerate(candidate_ids, 1)
        ],
        "unresolved_entities": [],
        "relationship_hints": [
            {
                "id": f"pool::h{index}",
                "from": {"candidate_id": source},
                "to": {"candidate_id": target},
                "type": edge_type,
                "basis": "explicit-prose",
                "assertion": "explicit",
                "evidence_ids": [f"pool::e{index}"],
                "confidence": "Verified",
            }
            for index, (source, target, edge_type) in enumerate(
                (
                    (candidate_ids[0], candidate_ids[2], "supports"),
                    (candidate_ids[0], candidate_ids[3], "supports"),
                    (candidate_ids[2], candidate_ids[1], "proves"),
                    (candidate_ids[3], candidate_ids[1], "proves"),
                    (candidate_ids[0], candidate_ids[1], "supports"),
                    (candidate_ids[0], candidate_ids[4], "supports"),
                    (candidate_ids[4], candidate_ids[1], "proves"),
                ),
                1,
            )
        ],
        "reference_decisions": [],
        "gaps": [],
    }
    return inventory, semantic


def _install_proof_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Persist the immutable artifacts needed by proof finalization."""

    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    inventory, semantic = _proof_transition()
    inventory_path = tmp_path / "inventory-ir.json"
    fragment_path = tmp_path / "extract-001.json"
    source_packet = tmp_path / "source-packet.txt"
    source_packet.write_text(
        "@@ source: main.tex\n"
        "0001 | Assumption A.\n"
        "0002 | Result R.\n"
        "0003 | Formal proof.\n"
        "0004 | Proof sketch.\n"
        "0005 | Direct dependency.\n"
        "0006 | The alternative argument uses A.\n"
        "0007 | The alternative argument proves R.\n",
        encoding="utf-8",
    )
    sidecar = tmp_path / "extract-sidecar.json"
    _write_json(sidecar, {"coordinates": []})
    _write_json(inventory_path, inventory)
    _write_json(fragment_path, semantic)
    _write_json(
        tmp_path / "extract-chunks.json",
        {"chunks": [{
            "chunk_id": "extract-001",
            "packet_path": str(tmp_path / "extract-packet.json"),
            "fragment_path": str(fragment_path),
            "sidecar_path": str(sidecar),
            "source_packet_path": str(source_packet),
            "entrypoint_path": str(entrypoint),
            "progress_path": str(tmp_path / "progress" / "extract-001.progress.md"),
        }]},
    )
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(source_packet),
            "inventory_ir": str(inventory_path),
            "extract_manifest": str(tmp_path / "extract-chunks.json"),
        },
    )
    manifest = tmp_path / "extract-fragments.json"
    _write_json(manifest, {"fragments": [str(fragment_path)]})
    return manifest, inventory_path, fragment_path


def test_finalize_extract_routes_proof_ir_to_one_reconciliation_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof entities must pause before canonical compilation and expose bounded inputs."""

    manifest, inventory_path, _fragment = _install_proof_run(tmp_path)
    monkeypatch.setattr(driver, "_compile_and_render", lambda *_args: pytest.fail("compiled transitional proof IR"))

    report = driver.finalize_extract(manifest, tmp_path, None)

    assert report["status"] == "proof-reconciliation-required"
    job = report["next_job"]
    assert job["chunk_id"] == "proof-reconciliation-001"
    assert job["instruction"].endswith("/instructions/proof-reconciliation.md")
    assert job["schema"].endswith("/proof-normalization.schema.json")
    assert "inventory" not in job
    assert "source_packet" not in job
    assert "sidecar" not in job
    assert "base" not in job
    assert "semantic_ir" not in job
    assert job["packet"].endswith("/proof-reconciliation-packet.json")
    assert job["progress_path"].endswith("/progress/proof-reconciliation-001.progress.md")
    packet = json.loads(Path(job["packet"]).read_text(encoding="utf-8"))
    assert {item["id"] for item in packet["proof_entities"]} == {
        "proof-formal", "proof-sketch", "proof-alternative"
    }
    assert all(
        edge["from"] in {"proof-formal", "proof-sketch", "proof-alternative"}
        or edge["to"] in {"proof-formal", "proof-sketch", "proof-alternative"}
        for edge in packet["incident_relationships"]
    )
    assert RunDiagnostics.open(tmp_path).payload["run"]["status"] == "running"


@pytest.mark.parametrize("ownership", ["missing", "multiple", "invalid-target"])
def test_finalize_extract_corrects_invalid_proof_ownership_before_reconciliation(
    tmp_path: Path, ownership: str
) -> None:
    """Record-local proof targeting defects belong to extraction correction."""

    manifest, _inventory_path, fragment_path = _install_proof_run(tmp_path)
    semantic = json.loads(fragment_path.read_text(encoding="utf-8"))
    if ownership == "missing":
        semantic["relationships"] = [
            item
            for item in semantic["relationships"]
            if not (item["from"] == "proof-sketch" and item["type"] == "proves")
        ]
    elif ownership == "multiple":
        semantic["relationships"].append(
            {
                **semantic["relationships"][3],
                "hint_ids": [],
                "evidence_ids": ["pool::e4"],
            }
        )
    else:
        semantic["relationships"][3]["to"] = "assumption-a"
    _write_json(fragment_path, semantic)

    report = driver.finalize_extract(manifest, tmp_path, None)

    assert report["status"] == "correction-required"
    assert any(
        item["code"] == "proof-ownership"
        for item in report["validation_diagnostics"]
    )
    assert not (tmp_path / "proof-reconciliation-packet.json").exists()


@pytest.mark.parametrize("ownership", ["missing", "multiple", "invalid-target"])
def test_semantic_repair_fails_closed_on_invalid_proof_ownership(
    tmp_path: Path, ownership: str
) -> None:
    """A correction response cannot bypass proof ownership before reconciliation."""

    manifest, _inventory_path, fragment_path = _install_proof_run(tmp_path)
    state_path = tmp_path / "run-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["semantic_repair_base"] = str(fragment_path)
    _write_json(state_path, state)
    repair = {
        "repair_version": 2,
        "remove_entity_ids": [],
        "upsert_entities": [],
        "remove_exclusion_candidate_ids": [],
        "upsert_exclusions": [],
        "remove_unresolved_ids": [],
        "upsert_unresolved_resolutions": [],
        "remove_relationships": [],
        "upsert_relationships": [],
        "remove_hint_ids": [],
        "upsert_hint_decisions": [],
        "remove_reference_ids": [],
        "upsert_reference_decisions": [],
        "remove_gap_ids": [],
        "upsert_gaps": [],
        "remove_inventory_gap_ids": [],
        "upsert_gap_decisions": [],
    }
    proves_key = {"from": "proof-sketch", "to": "result-r", "type": "proves"}
    if ownership in {"missing", "invalid-target"}:
        repair["remove_relationships"] = [proves_key]
        repair["upsert_hint_decisions"] = [
            {
                "hint_id": "pool::h4",
                "decision": "rejected",
                "reason": "The correction failed to retain a valid ownership edge.",
            }
        ]
    if ownership in {"multiple", "invalid-target"}:
        repair["upsert_relationships"] = [
            {
                "from": "proof-sketch",
                "to": "assumption-a",
                "type": "proves",
                "description": "The malformed correction targets an ineligible entity.",
                "hint_ids": [],
                "evidence_ids": ["pool::e4"],
                "implicit": False,
                "confidence": "Verified",
            }
        ]
    repair_path = tmp_path / "extract-fragments" / "extract-001-correction-001.json"
    _write_json(repair_path, repair)
    repair_manifest = tmp_path / "repair-fragments.json"
    _write_json(repair_manifest, {"fragments": [str(repair_path)]})

    with pytest.raises(driver.ValidationReportError, match="proof ownership"):
        driver.finalize_extract(repair_manifest, tmp_path, None)

    diagnostics = RunDiagnostics.open(tmp_path).payload
    assert diagnostics["run"]["status"] == "failure"
    assert any(
        item["code"] == "proof-ownership"
        for item in diagnostics["validation_diagnostics"]
    )
    assert not (tmp_path / "proof-reconciliation-packet.json").exists()


def test_finalize_proofs_normalizes_then_compiles_once_and_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted complementary and alternative bundles disappear but remain traceable."""

    from test_proof_normalizer import _decisions

    manifest, _inventory_path, _fragment = _install_proof_run(tmp_path)
    first = driver.finalize_extract(manifest, tmp_path, None)
    decisions = _decisions()
    decisions["input_identity"] = first["next_job"]["input_identity"]
    decisions["decisions"].append(
        {
            "proof_id": "proof-alternative",
            "disposition": "accepted",
            "bundle_id": "bundle-r-alternative",
            "target_id": "result-r",
            "reason": "A materially different argument is an alternative bundle.",
        }
    )
    decision_path = Path(first["next_job"]["output"])
    _write_json(decision_path, decisions)
    decision_manifest = tmp_path / "proof-decisions-manifest.json"
    _write_json(decision_manifest, {"fragments": [str(decision_path)]})
    captured: dict = {}

    def capture_compile(semantic: dict, semantic_path: Path, inventory: dict, *_args) -> dict:
        captured.update(semantic=semantic, semantic_path=semantic_path, inventory=inventory)
        return {"status": "compiled", "semantic_ir": str(semantic_path)}

    monkeypatch.setattr(driver, "_compile_and_render", capture_compile)

    report = driver.finalize_proofs(decision_manifest, tmp_path, None)

    assert report["status"] == "compiled"
    assert all(item["type"] != "proof" for item in captured["semantic"]["entities"])
    assert all(item["type"] != "proves" for item in captured["semantic"]["relationships"])
    provenance = json.loads((tmp_path / "proof-provenance.json").read_text(encoding="utf-8"))
    assert {item["bundle_id"] for item in provenance["bundles"]} == {
        "bundle-r-main", "bundle-r-alternative"
    }
    redirected = provenance["relationships"][0]
    assert redirected["proof_ids"] == [
        "proof-formal", "proof-sketch", "proof-alternative"
    ]
    assert redirected["bundle_ids"] == ["bundle-r-main", "bundle-r-alternative"]
    assert redirected["source_relationships"][0]["relationship"]["evidence_ids"] == ["pool::e1"]
    assert redirected["source_relationships"][-1]["bundle_id"] == "bundle-r-alternative"
    assert redirected["source_relationships"][-1]["relationship"]["evidence_ids"] == ["pool::e6"]
    diagnostics = RunDiagnostics.open(tmp_path).payload
    assert diagnostics["run"]["status"] == "success"
    provenance_artifact = next(
        item for item in diagnostics["artifacts"] if item["kind"] == "proof-provenance"
    )
    assert provenance_artifact["counts"] == {
        "proof_entities": 3,
        "accepted_proofs": 3,
        "proof_bundles": 2,
        "alternative_bundles": 2,
        "proof_targets": 1,
        "proof_exclusions": 0,
        "redirected_relationships": 1,
        "total_redirected_relationships": 1,
    }
    assert diagnostics["proof_metrics"] == {
        "accepted_proofs": 3,
        "alternative_bundles": 2,
        "total_redirected_relationships": 1,
        "redirected_relationships": [
            {
                "from": "assumption-a",
                "to": "result-r",
                "type": "supports",
                "proof_ids": ["proof-formal", "proof-sketch", "proof-alternative"],
                "bundle_ids": ["bundle-r-main", "bundle-r-alternative"],
                "routes": [
                    {"proof_id": "proof-formal", "bundle_id": "bundle-r-main", "evidence_ids": ["pool::e1"]},
                    {"proof_id": "proof-sketch", "bundle_id": "bundle-r-main", "evidence_ids": ["pool::e2"]},
                    {"proof_id": "proof-alternative", "bundle_id": "bundle-r-alternative", "evidence_ids": ["pool::e6"]},
                ],
            }
        ],
    }


def test_invalid_proof_decisions_return_one_immutable_retry(tmp_path: Path) -> None:
    """Incomplete reconciliation fails closed without changing its semantic inputs."""

    manifest, _inventory_path, _fragment = _install_proof_run(tmp_path)
    first = driver.finalize_extract(manifest, tmp_path, None)
    decisions = {
        "document_kind": "proof-normalization-decisions",
        "ir_version": 1,
        "input_identity": first["next_job"]["input_identity"],
        "decisions": [],
    }
    decision_path = Path(first["next_job"]["output"])
    _write_json(decision_path, decisions)
    decision_manifest = tmp_path / "proof-decisions-manifest.json"
    _write_json(decision_manifest, {"fragments": [str(decision_path)]})

    report = driver.finalize_proofs(decision_manifest, tmp_path, None)

    assert report["status"] == "proof-reconciliation-retry-required"
    assert "semantic_ir" not in report["next_job"]
    assert report["next_job"]["packet"] == first["next_job"]["packet"]
    assert report["next_job"]["input_identity"] == first["next_job"]["input_identity"]
    assert report["next_job"]["output"].endswith("proof-reconciliation-001-retry-001.json")
    assert RunDiagnostics.open(tmp_path).payload["run"]["status"] == "running"


@pytest.mark.parametrize("artifact", ["semantic", "inventory", "source", "packet"])
def test_finalize_proofs_rejects_mutated_original_inputs(
    tmp_path: Path, artifact: str
) -> None:
    """A worker result cannot normalize against artifacts changed after assignment."""

    from test_proof_normalizer import _decisions

    manifest, inventory_path, _fragment = _install_proof_run(tmp_path)
    first = driver.finalize_extract(manifest, tmp_path, None)
    decisions = _decisions()
    decisions["input_identity"] = first["next_job"]["input_identity"]
    decision_path = Path(first["next_job"]["output"])
    _write_json(decision_path, decisions)
    decision_manifest = tmp_path / "proof-decisions-manifest.json"
    _write_json(decision_manifest, {"fragments": [str(decision_path)]})
    targets = {
        "semantic": tmp_path / "semantic-ir.json",
        "inventory": inventory_path,
        "source": tmp_path / "source-packet.txt",
        "packet": Path(first["next_job"]["packet"]),
    }
    with targets[artifact].open("ab") as stream:
        stream.write(b"\n")

    with pytest.raises(ValueError, match="identity"):
        driver.finalize_proofs(decision_manifest, tmp_path, None)


@pytest.mark.parametrize("identity_case", ["missing", "replayed"])
def test_decision_identity_uses_one_retry_then_fails_closed(
    tmp_path: Path, identity_case: str
) -> None:
    """Worker identity defects get one immutable retry; a second defect exhausts it."""

    from test_proof_normalizer import _decisions

    manifest, _inventory_path, _fragment = _install_proof_run(tmp_path)
    first = driver.finalize_extract(manifest, tmp_path, None)
    decisions = _decisions()
    if identity_case == "missing":
        del decisions["input_identity"]
    else:
        decisions["input_identity"] = {
            **first["next_job"]["input_identity"],
            "packet_payload_sha256": "f" * 64,
        }
    decision_path = Path(first["next_job"]["output"])
    _write_json(decision_path, decisions)
    decision_manifest = tmp_path / "proof-decisions-manifest.json"
    _write_json(decision_manifest, {"fragments": [str(decision_path)]})

    retry = driver.finalize_proofs(decision_manifest, tmp_path, None)

    assert retry["status"] == "proof-reconciliation-retry-required"
    assert retry["next_job"]["input_identity"] == first["next_job"]["input_identity"]
    retry_decision_path = Path(retry["next_job"]["output"])
    _write_json(retry_decision_path, decisions)
    retry_manifest = tmp_path / "proof-decisions-retry-manifest.json"
    _write_json(retry_manifest, {"fragments": [str(retry_decision_path)]})

    with pytest.raises(ValueError, match="exhausted its bounded retry"):
        driver.finalize_proofs(retry_manifest, tmp_path, None)
    assert RunDiagnostics.open(tmp_path).payload["run"]["status"] == "failure"


def test_finalize_proofs_rejects_replayed_stored_artifact_path(tmp_path: Path) -> None:
    """The stored tuple must remain bound to this run's canonical artifact paths."""

    from test_proof_normalizer import _decisions

    manifest, _inventory_path, _fragment = _install_proof_run(tmp_path)
    first = driver.finalize_extract(manifest, tmp_path, None)
    decisions = _decisions()
    decisions["input_identity"] = first["next_job"]["input_identity"]
    decision_path = Path(first["next_job"]["output"])
    _write_json(decision_path, decisions)
    decision_manifest = tmp_path / "proof-decisions-manifest.json"
    _write_json(decision_manifest, {"fragments": [str(decision_path)]})
    replay_path = tmp_path / "replayed-semantic-ir.json"
    replay_path.write_bytes((tmp_path / "semantic-ir.json").read_bytes())
    state_path = tmp_path / "run-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["proof_reconciliation_original"]["artifacts"]["semantic_ir"]["path"] = str(
        replay_path
    )
    _write_json(state_path, state)

    with pytest.raises(ValueError, match="artifact path identity"):
        driver.finalize_proofs(decision_manifest, tmp_path, None)


def test_malformed_proof_decisions_return_the_same_bounded_retry(tmp_path: Path) -> None:
    """Malformed worker JSON is a retryable worker failure, not a partial finalization."""

    manifest, _inventory_path, _fragment = _install_proof_run(tmp_path)
    first = driver.finalize_extract(manifest, tmp_path, None)
    decision_path = Path(first["next_job"]["output"])
    decision_path.write_text('{"decisions": [', encoding="utf-8")
    decision_manifest = tmp_path / "proof-decisions-manifest.json"
    _write_json(decision_manifest, {"fragments": [str(decision_path)]})

    report = driver.finalize_proofs(decision_manifest, tmp_path, None)

    assert report["status"] == "proof-reconciliation-retry-required"
    assert report["next_job"]["chunk_id"] == "proof-reconciliation-001"
    assert report["next_job"]["packet"] == first["next_job"]["packet"]
    assert RunDiagnostics.open(tmp_path).payload["run"]["status"] == "running"


def test_diagnostics_initialization_failure_aborts_before_source_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continuing after a diagnostics write failure would create an unmeasured run."""

    def fail_initialize(*_args, **_kwargs):
        raise OSError("diagnostics unavailable")

    monkeypatch.setattr(driver.RunDiagnostics, "initialize", fail_initialize)
    monkeypatch.setattr(
        driver,
        "collect_source_packet",
        lambda *_args: pytest.fail("source preparation ran without diagnostics"),
    )

    with pytest.raises(OSError, match="diagnostics unavailable"):
        driver.prepare(_entrypoint(tmp_path), tmp_path / "run")

#!/usr/bin/env python3
"""Tests for inventory pooling followed by one extract job."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

import _extraction_phase_driver as driver  # noqa: E402
from _run_diagnostics import RunDiagnostics  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    """Persist one test-controlled orchestration artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _packet_snapshot(path: Path) -> tuple[str, int]:
    """Return the manifest hash and exact numbered-line bytes for a test packet."""

    raw = path.read_bytes()
    owned = sum(
        len(line)
        for line in raw.splitlines(keepends=True)
        if b" | " in line and line.split(b" | ", 1)[0].isdigit()
    )
    return hashlib.sha256(raw).hexdigest(), owned


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


def _fragment() -> dict:
    """Return a compact valid inventory fragment for the only owned chunk."""

    return {
        "ir_version": 3,
        "chunk_id": "inventory-001",
        "files": ["section.tex"],
        "nodes": [
            {
                "local_id": "n1",
                "location": [0, 1, 1],
                "environment": "definition",
                "provenance": "explicit",
                "type_hint": "setup",
                "summary": "An admissible object is fixed.",
            }
        ],
        "edges": [],
        "gaps": [],
    }


def test_advance_inventory_writes_pooled_ir_and_one_extract_job(tmp_path: Path) -> None:
    """The old candidate-ledger wave must be replaced by exactly one extract handoff."""

    source_path = tmp_path / "source-packet.txt"
    source_path.write_text(
        "@@ source: section.tex\n"
        + "\n".join(f"{line:04d} | " + "x" * 240 for line in range(1, 21))
        + "\n",
        encoding="utf-8",
    )
    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(source_path),
            "inventory_manifest": str(tmp_path / "inventory-chunks.json"),
        },
    )
    fragment_path = tmp_path / "inventory-fragments/inventory-001.json"
    _write_json(fragment_path, _fragment())
    source_sha256, owned_bytes = _packet_snapshot(source_path)
    _write_json(
        tmp_path / "inventory-chunks.json",
        {
            "plan_version": 1,
            "mode": "inventory",
            "source": str(source_path),
            "source_sha256": source_sha256,
            "target_tokens": 60_000,
            "hard_max_tokens": 95_000,
            "chunks": [
                {
                    "chunk_id": "inventory-001",
                    "estimated_tokens": 100,
                    "packet_path": str(source_path),
                    "packet_sha256": source_sha256,
                    "owned_bytes": owned_bytes,
                    "anchors": [],
                    "fragment_path": str(fragment_path),
                    "spans": [
                        {"source_file": "section.tex", "start_line": 1, "end_line": 20}
                    ],
                }
            ],
        },
    )
    fragment_manifest = tmp_path / "inventory-fragments.json"
    _write_json(fragment_manifest, {"fragments": [str(fragment_path)]})

    report = driver.advance_inventory(fragment_manifest, tmp_path)

    assert report["inventory_ir"].endswith("inventory-ir.json")
    assert report["next_job"]["chunk_id"] == "extract-001"
    assert Path(report["next_job"]["instruction"]) == (
        SKILL_DIR / "instructions" / "extract.md"
    ).resolve()
    assert Path(report["next_job"]["schema"]) == (
        SKILL_DIR / "semantic-graph.schema.json"
    ).resolve()
    assert report["next_job"]["source_packet"] == str(source_path.resolve())
    assert report["next_job"]["entrypoint"] == str(entrypoint.resolve())
    assert report["next_job"]["progress_path"] == str(
        (tmp_path / "progress" / "extract-001.progress.md").resolve()
    )
    pooled = json.loads((tmp_path / "inventory-ir.json").read_text(encoding="utf-8"))
    assert pooled["chunk_id"] == "pooled"
    assert pooled["evidence"][0]["id"] == "inventory-001::e1"
    state = json.loads((tmp_path / "run-state.json").read_text(encoding="utf-8"))
    assert state["inventory_ir"] == str((tmp_path / "inventory-ir.json").resolve())
    assert state["extract_manifest"] == str((tmp_path / "extract-chunks.json").resolve())
    diagnostics = json.loads(
        (tmp_path / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert [stage["operation"] for stage in diagnostics["stages"]] == [
        "pooling",
        "planning",
    ]
    assert [ratio["kind"] for ratio in diagnostics["ratios"]] == [
        "inventory-fragment-to-owned-packet",
        "pooled-canonical-fragments-to-owned-packets",
        "pooled-inventory-to-active-source",
    ]
    artifacts = {artifact["kind"]: artifact for artifact in diagnostics["artifacts"]}
    expected_fragment_counts = {
        "files": 1,
        "nodes": 1,
        "edges": 0,
        "gaps": 0,
    }
    expected_pooled_counts = {
        "files": 1,
        "evidence": 1,
        "references": 0,
        "candidates": 1,
        "unresolved_entities": 0,
        "relationship_hints": 0,
        "reference_decisions": 0,
        "gaps": 0,
    }
    assert artifacts["inventory-fragment"]["counts"] == expected_fragment_counts
    assert artifacts["pooled-inventory"]["counts"] == expected_pooled_counts
    assert report["diagnostics"] == str(tmp_path / "run-diagnostics.json")


def test_rejected_inventory_fragment_keeps_run_open_for_bounded_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker-local pooling rejection must not close the retryable run."""

    source_path = tmp_path / "source-packet.txt"
    source_path.write_text("@@ source: section.tex\n0001 | source\n", encoding="utf-8")
    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(source_path),
            "inventory_manifest": str(tmp_path / "inventory-chunks.json"),
        },
    )
    fragment_path = tmp_path / "inventory-fragments/inventory-001.json"
    _write_json(fragment_path, _fragment())
    source_sha256, owned_bytes = _packet_snapshot(source_path)
    _write_json(
        tmp_path / "inventory-chunks.json",
        {
            "plan_version": 1,
            "mode": "inventory",
            "source": str(source_path),
            "source_sha256": source_sha256,
            "target_tokens": 60_000,
            "hard_max_tokens": 95_000,
            "chunks": [
                {
                    "chunk_id": "inventory-001",
                    "estimated_tokens": 10,
                    "packet_path": str(source_path),
                    "packet_sha256": source_sha256,
                    "owned_bytes": owned_bytes,
                    "anchors": [],
                    "fragment_path": str(fragment_path),
                    "spans": [
                        {"source_file": "section.tex", "start_line": 1, "end_line": 1}
                    ],
                }
            ],
        },
    )
    manifest = tmp_path / "inventory-fragments.json"
    _write_json(manifest, {"fragments": [str(fragment_path)]})

    def reject_fragment(*_args, **_kwargs):
        raise ValueError("inventory fragment misses visible environment anchor")

    monkeypatch.setattr(driver, "pool_inventory_fragments", reject_fragment)

    with pytest.raises(ValueError, match="misses visible environment anchor"):
        driver.advance_inventory(manifest, tmp_path)

    diagnostics = json.loads(
        (tmp_path / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostics["run"]["status"] == "running"
    assert diagnostics["stages"][-1]["operation"] == "pooling"
    assert diagnostics["stages"][-1]["status"] == "failure"


def test_advance_inventory_pairs_reversed_manifest_fragments_by_chunk_id(
    tmp_path: Path,
) -> None:
    """Ratio ownership must follow each fragment chunk id, never manifest list order."""

    entrypoint = _entrypoint(tmp_path)
    _initialize_diagnostics(tmp_path, entrypoint)
    packet_paths: dict[str, Path] = {}
    fragment_paths: dict[str, Path] = {}
    chunks: list[dict] = []
    for ordinal in (1, 2):
        chunk_id = f"inventory-{ordinal:03d}"
        source_file = f"section-{ordinal}.tex"
        packet_path = tmp_path / f"packet-{ordinal}.txt"
        packet_path.write_text(
            f"@@ source: {source_file}\n"
            + "".join(
                f"{line:04d} | " + ("x" * (160 + ordinal)) + "\n"
                for line in range(1, 21)
            ),
            encoding="utf-8",
        )
        fragment = deepcopy(_fragment())
        fragment["chunk_id"] = chunk_id
        fragment["files"] = [source_file]
        fragment_path = tmp_path / f"fragment-{ordinal}.json"
        _write_json(fragment_path, fragment)
        packet_paths[chunk_id] = packet_path
        fragment_paths[chunk_id] = fragment_path
        chunks.append(
            {
                "chunk_id": chunk_id,
                "estimated_tokens": 100,
                "packet_path": str(packet_path),
                "fragment_path": str(fragment_path),
                "anchors": [],
                "spans": [
                    {"source_file": source_file, "start_line": 1, "end_line": 20}
                ],
            }
            )
        packet_sha256, owned_bytes = _packet_snapshot(packet_path)
        chunks[-1]["packet_sha256"] = packet_sha256
        chunks[-1]["owned_bytes"] = owned_bytes
    source_sha256, _owned = _packet_snapshot(packet_paths["inventory-001"])
    _write_json(
        tmp_path / "run-state.json",
        {
            "entrypoint": str(entrypoint),
            "source_packet": str(packet_paths["inventory-001"]),
            "inventory_manifest": str(tmp_path / "inventory-chunks.json"),
        },
    )
    _write_json(
        tmp_path / "inventory-chunks.json",
        {
            "plan_version": 1,
            "mode": "inventory",
            "source": str(packet_paths["inventory-001"]),
            "source_sha256": source_sha256,
            "target_tokens": 60_000,
            "hard_max_tokens": 95_000,
            "chunks": chunks,
        },
    )
    fragment_manifest = tmp_path / "inventory-fragments.json"
    _write_json(
        fragment_manifest,
        {
            "fragments": [
                str(fragment_paths["inventory-002"]),
                str(fragment_paths["inventory-001"]),
            ]
        },
    )

    driver.advance_inventory(fragment_manifest, tmp_path)

    diagnostics = RunDiagnostics.open(tmp_path).payload
    artifacts = {artifact["id"]: artifact for artifact in diagnostics["artifacts"]}
    ownership_ratios = [
        ratio
        for ratio in diagnostics["ratios"]
        if ratio["kind"] == "inventory-fragment-to-owned-packet"
    ]
    assert [ratio["job_id"] for ratio in ownership_ratios] == [
        "inventory-001",
        "inventory-002",
    ]
    for ratio in ownership_ratios:
        chunk_id = ratio["job_id"]
        assert artifacts[ratio["numerator_artifact"]]["path"] == str(
            fragment_paths[chunk_id].resolve()
        )
        assert artifacts[ratio["denominator_artifact"]]["path"] == str(
            packet_paths[chunk_id].resolve()
        )


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
        "inventory-packet",
    }
    assert report["diagnostics"] == str(run_dir / "run-diagnostics.json")
    assert report["next_jobs"]
    for job in report["next_jobs"]:
        assert job["progress_path"] == str(
            (run_dir / "progress" / f"{job['chunk_id']}.progress.md").resolve()
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

    monkeypatch.setattr(driver, "plan_inventory_chunks", fail_planning)
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

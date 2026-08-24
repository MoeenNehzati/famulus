"""Focused tests for deterministic inventory-chunk pooling."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

from _inventory_pipeline import _chunk_pooler as pooler  # noqa: E402

def _fragment(chunk_id: str, source_file: str) -> dict:
    """Return one independently schema-valid discovery fragment."""

    return {
        "ir_version": 3,
        "chunk_id": chunk_id,
        "files": [source_file],
        "nodes": [
            {
                "local_id": "n1",
                "statement_location": [0, 1, 2],
                "environment": "theorem",
                "provenance": "explicit",
                "type_hint": "result",
                "summary": "The stated result is available for later reconciliation.",
            }
        ],
        "edges": [
            {
                "local_id": "d1",
                "from": {
                    "unresolved": {
                        "title": "A referenced prerequisite",
                        "statement": "A prerequisite remains unresolved in this source chunk.",
                        "resolution_kind": "remote-label",
                        "locators": [{"label": f"lbl:{source_file}"}],
                        "type_hint": "result",
                    }
                },
                "to": {"local_node": "n1"},
                "type": "supports",
                "basis": "explicit-reference",
                "assertion": "explicit",
                "location": [0, 5, 6],
                "reference": {
                    "location": [0, 6, 6],
                    "locator": {"label": f"lbl:{source_file}"},
                },
                "description": "The proof invokes the referenced prerequisite.",
                "confidence": "Verified",
            }
        ],
        "gaps": [
            {
                "local_id": "g1",
                "category": "identity",
                "location": [0, 5, 5],
                "description": "The reference may resolve to a candidate in another chunk.",
            }
        ],
    }


def _source_chunk(files: list[str], *, line_count: int, line_width: int) -> str:
    """Return a packet with enough owned bytes for the valid compact fragments."""

    lines: list[str] = []
    for source_file in files:
        lines.append(f"@@ source: {source_file}")
        for line_number in range(1, line_count + 1):
            lines.append(f"{line_number:04d} | {source_file} " + "x" * line_width)
    return "\n".join(lines) + "\n"


def _manifest(
    tmp_path: Path,
    files: list[str] | None = None,
    *,
    line_count: int = 100,
    line_width: int = 240,
) -> dict:
    """Write the immutable packet and return source-ordered chunk ownership."""

    source_files = files or ["a.tex", "b.tex"]
    source_packet = tmp_path / "source-packet.txt"
    source_packet.write_text(
        _source_chunk(source_files, line_count=line_count, line_width=line_width),
        encoding="utf-8",
    )
    payload = {
        "plan_version": 1,
        "mode": "inventory",
        "source": str(source_packet),
        "source_sha256": hashlib.sha256(source_packet.read_bytes()).hexdigest(),
        "target_tokens": 60_000,
        "hard_max_tokens": 95_000,
        "source_files": [
            {
                "path": str(source_packet),
                "source_file": source_file,
                "sha256": hashlib.sha256(source_packet.read_bytes()).hexdigest(),
            }
            for source_file in source_files
        ],
        "chunks": [
            {
                "chunk_id": f"inventory-{index:03d}",
                "estimated_tokens": 1,
                "chunk_path": str(source_packet),
                "chunk_sha256": hashlib.sha256(source_packet.read_bytes()).hexdigest(),
                "owned_bytes": sum(
                    len(
                        (
                            f"{line_number:04d} | {source_file} "
                            + "x" * line_width
                            + "\n"
                        ).encode("utf-8")
                    )
                    for line_number in range(1, line_count + 1)
                ),
                "anchors": [],
                "fragment_path": str(tmp_path / f"inventory-{index:03d}.json"),
                "spans": [
                    {
                        "source_file": source_file,
                        "start_line": 1,
                        "end_line": line_count,
                    }
                ],
            }
            for index, source_file in enumerate(source_files, 1)
        ],
    }
    return payload


def _pool(fragments: list[dict], manifest: dict) -> dict:
    """Use the public pooler with its production ownership checks."""

    return pooler.pool_inventory_fragments(fragments, chunk_manifest=manifest)


def test_pooler_qualifies_cross_chunk_handles_and_remaps_files(tmp_path: Path) -> None:
    """Pooling derives rich qualified records from inline discovery fragments."""

    pooled = _pool(
        [_fragment("inventory-002", "b.tex"), _fragment("inventory-001", "a.tex")],
        _manifest(tmp_path),
    )

    assert pooled["chunk_id"] == "pooled"
    assert pooled["files"] == ["a.tex", "b.tex"]
    assert [item["id"] for item in pooled["evidence"]] == [
        "inventory-001::e1",
        "inventory-001::e2",
        "inventory-001::e3",
        "inventory-002::e1",
        "inventory-002::e2",
        "inventory-002::e3",
    ]
    assert pooled["evidence"][3]["location"] == [1, 1, 2]
    assert pooled["references"][1]["id"] == "inventory-002::r1"
    assert pooled["unresolved_entities"][1]["key"] == "inventory-002::u1"
    assert pooled["relationship_hints"][1]["id"] == "inventory-002::h1"
    assert pooled["relationship_hints"][1]["from"] == {
        "unresolved_key": "inventory-002::u1"
    }
    assert pooled["relationship_hints"][1]["evidence_ids"] == ["inventory-002::e2"]
    assert pooled["relationship_hints"][1]["reference_ids"] == ["inventory-002::r1"]
    assert pooled["gaps"][1]["id"] == "inventory-002::g1"
    assert "subject" not in pooled["gaps"][1]
    assert pooled["candidates"][0]["id"] == "a.tex:1"
    assert pooled["candidates"][1]["id"] == "b.tex:1"


def test_pooler_rejects_unknown_local_node_endpoint(tmp_path: Path) -> None:
    """An edge cannot reference a node that the discovery fragment did not report."""

    fragment = _fragment("inventory-001", "a.tex")
    fragment["edges"][0]["to"] = {"local_node": "n99"}

    with pytest.raises(ValueError, match="unknown local node endpoint"):
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))


def test_pooler_rejects_missing_visible_environment_anchor(tmp_path: Path) -> None:
    """Post-inventory coverage checking catches an omitted formal environment."""

    fragment = _fragment("inventory-001", "a.tex")
    fragment["nodes"] = []
    fragment["edges"] = []
    manifest = _manifest(tmp_path, ["a.tex"])
    manifest["chunks"][0]["anchors"] = [
        {
            "source_file": "a.tex",
            "start_line": 1,
            "end_line": 2,
            "environment": "theorem",
        }
    ]

    with pytest.raises(ValueError, match="misses visible environment anchor"):
        _pool([fragment], manifest)


def test_pooler_rejects_location_outside_owned_chunk_span(tmp_path: Path) -> None:
    """A worker cannot attribute a finding to another inventory worker's source."""

    fragment = _fragment("inventory-001", "a.tex")
    fragment["nodes"][0]["statement_location"] = [0, 101, 101]

    with pytest.raises(ValueError, match="outside its owned chunk span"):
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))


def test_pooler_rejects_duplicate_local_ids(tmp_path: Path) -> None:
    """Distinct discovery nodes cannot silently reuse one local handle."""

    fragment = _fragment("inventory-001", "a.tex")
    duplicate = deepcopy(fragment["nodes"][0])
    duplicate["statement_location"] = [0, 7, 7]
    fragment["nodes"].append(duplicate)

    with pytest.raises(ValueError, match="duplicate node id"):
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))


def test_pooler_rejects_duplicate_document_wide_candidate_anchors(tmp_path: Path) -> None:
    """Two nodes at one source start cannot silently become one candidate."""

    fragment = _fragment("inventory-001", "a.tex")
    duplicate = deepcopy(fragment["nodes"][0])
    duplicate["local_id"] = "n2"
    fragment["nodes"].append(duplicate)

    with pytest.raises(
        pooler.InventoryFragmentValidationError,
        match="candidate anchor emitted more than once",
    ) as raised:
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))
    assert raised.value.chunk_id == "inventory-001"


def test_pooler_distinguishes_nodes_with_same_start_and_different_spans(
    tmp_path: Path,
) -> None:
    """A local premise and its result may legitimately begin on the same line."""

    fragment = _fragment("inventory-001", "a.tex")
    second = deepcopy(fragment["nodes"][0])
    second["local_id"] = "n2"
    second["statement_location"] = [0, 1, 3]
    second["environment"] = "proposition"
    second["summary"] = "The local premise enables the resulting construction."
    fragment["nodes"].append(second)

    pooled = _pool([fragment], _manifest(tmp_path, ["a.tex"]))

    assert {candidate["id"] for candidate in pooled["candidates"]} == {
        "a.tex:1-2",
        "a.tex:1-3",
    }


def test_pooler_rejects_chunk_mutation_after_extraction(tmp_path: Path) -> None:
    """Mutable chunk files cannot change a frozen inventory assignment."""

    manifest = _manifest(tmp_path, ["a.tex"])
    chunk_path = Path(manifest["chunks"][0]["chunk_path"])
    chunk_path.write_text(chunk_path.read_text(encoding="utf-8") + "padding\n", encoding="utf-8")

    with pytest.raises(ValueError, match="identity changed after chunk extraction"):
        _pool([_fragment("inventory-001", "a.tex")], manifest)

#!/usr/bin/env python3
"""Contract tests for deterministic inventory-fragment pooling."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import hashlib
import sys

import pytest


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

import _batch_ir_merger as merger  # noqa: E402


def _fragment(chunk_id: str, source_file: str) -> dict:
    """Return one independently schema-valid discovery fragment."""

    return {
        "ir_version": 3,
        "chunk_id": chunk_id,
        "files": [source_file],
        "nodes": [
            {
                "local_id": "n1",
                "location": [0, 1, 2],
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


def _source_packet(files: list[str], *, line_count: int, line_width: int) -> str:
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
        _source_packet(source_files, line_count=line_count, line_width=line_width),
        encoding="utf-8",
    )
    payload = {
        "plan_version": 1,
        "mode": "inventory",
        "source": str(source_packet),
        "source_sha256": hashlib.sha256(source_packet.read_bytes()).hexdigest(),
        "target_tokens": 60_000,
        "hard_max_tokens": 95_000,
        "chunks": [
            {
                "chunk_id": f"inventory-{index:03d}",
                "estimated_tokens": 1,
                "packet_path": str(source_packet),
                "packet_sha256": hashlib.sha256(source_packet.read_bytes()).hexdigest(),
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

    return merger.pool_inventory_fragments(fragments, chunk_manifest=manifest)


def _minimal_fragment(chunk_id: str, source_file: str) -> dict:
    """Return a lossless small fragment for aggregate-budget tests."""

    fragment = _fragment(chunk_id, source_file)
    fragment["edges"] = []
    fragment["gaps"] = []
    return fragment


def _manifest_near_fragment_ratio(
    tmp_path: Path, fragments: list[dict], target_ratio: float
) -> dict:
    """Authenticate equal owned packets sized near the first fragment's ratio."""

    line_count = 10
    first = fragments[0]
    first_bytes = len(merger.canonical_json_bytes(first))
    source_file = first["files"][0]
    line_overhead = len(f"0001 | {source_file} \n".encode("utf-8"))
    line_width = max(
        1,
        round(first_bytes / target_ratio / line_count - line_overhead),
    )
    return _manifest(
        tmp_path,
        [fragment["files"][0] for fragment in fragments],
        line_count=line_count,
        line_width=line_width,
    )


def _reconciliation_inventory() -> dict:
    """Return one pooled inventory covering every extract reconciliation route."""

    return {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": ["section.tex"],
        "evidence": [
            {"id": "inventory-001::e1", "location": [0, 10, 12], "role": "statement"},
            {"id": "inventory-001::e2", "location": [0, 20, 22], "role": "proof-use"},
            {
                "id": "inventory-001::e3",
                "location": [0, 30, 30],
                "role": "explicit-reference",
            },
        ],
        "references": [
            {
                "id": f"inventory-001::r{index}",
                "location": [0, 30 + index, 30 + index],
                "locator": {"label": f"ref:{index}"},
            }
            for index in range(1, 4)
        ],
        "candidates": [
            {
                "id": "section.tex:10",
                "location": [0, 10, 12],
                "provenance": "explicit",
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
                "summary": "A reusable definition.",
            },
            {
                "id": "section.tex:20",
                "location": [0, 20, 22],
                "provenance": "explicit",
                "type_hint": "result",
                "evidence_ids": ["inventory-001::e2"],
                "summary": "A result using the definition.",
            },
            {
                "id": "section.tex:40",
                "location": [0, 40, 40],
                "provenance": "explicit",
                "type_hint": "exposition",
                "evidence_ids": ["inventory-001::e3"],
                "summary": "A navigation remark excluded from the graph.",
            },
        ],
        "unresolved_entities": [
            {
                "key": "inventory-001::u1",
                "title": "Definition alias",
                "statement": "A remote name for the definition.",
                "resolution_kind": "remote-label",
                "locators": [{"label": "def:object"}],
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e1"],
            },
            {
                "key": "inventory-001::u2",
                "title": "Spurious name",
                "statement": "A name that is not a graph entity.",
                "resolution_kind": "named-entity",
                "locators": [{"name": "spurious"}],
                "type_hint": "setup",
                "evidence_ids": ["inventory-001::e3"],
            },
        ],
        "relationship_hints": [
            {
                "id": "inventory-001::h1",
                "from": {"unresolved_key": "inventory-001::u1"},
                "to": {"candidate_id": "section.tex:20"},
                "type": "supports",
                "basis": "explicit-reference",
                "assertion": "explicit",
                "reference_ids": ["inventory-001::r1"],
                "evidence_ids": ["inventory-001::e2"],
                "confidence": "Verified",
            },
            {
                "id": "inventory-001::h2",
                "from": {"candidate_id": "section.tex:20"},
                "to": {"candidate_id": "section.tex:40"},
                "type": "supports",
                "basis": "mathematical-inference",
                "assertion": "inferred",
                "evidence_ids": ["inventory-001::e3"],
                "confidence": "Medium",
                "reason": "The remark resembles a consequence.",
            },
        ],
        "reference_decisions": [],
        "gaps": [
            {
                "id": "inventory-001::g1",
                "category": "reference",
                "reference_id": "inventory-001::r3",
                "evidence_ids": ["inventory-001::e3"],
                "description": "The explicit reference could not be resolved in its chunk.",
            }
        ],
    }


def _semantic_extract() -> dict:
    """Return one schema-v2 extract partitioning every inventory handle."""

    return {
        "ir_version": 2,
        "document": {"title": "Example", "source_file": "main.tex"},
        "inventory": {
            "candidate_ids": ["section.tex:10", "section.tex:20", "section.tex:40"],
            "candidate_count": 3,
        },
        "entities": [
            {
                "candidate_ids": ["section.tex:10"],
                "id": "definition",
                "type": "setup",
                "kind": "definition",
                "short_title": "Definition",
                "description": "A reusable definition.",
                "source": "explicit",
            },
            {
                "candidate_ids": ["section.tex:20"],
                "id": "result",
                "type": "result",
                "kind": "theorem",
                "short_title": "Result",
                "description": "The definition yields the result.",
                "source": "explicit",
            },
        ],
        "exclusions": [
            {"candidate_id": "section.tex:40", "reason": "Navigation only."}
        ],
        "unresolved_resolutions": [
            {
                "unresolved_id": "inventory-001::u1",
                "disposition": "matched",
                "entity_id": "definition",
            },
            {
                "unresolved_id": "inventory-001::u2",
                "disposition": "rejected",
                "reason": "The name does not denote a reusable mathematical object.",
            },
        ],
        "relationships": [
            {
                "from": "definition",
                "to": "result",
                "type": "supports",
                "description": "The result directly uses the definition.",
                "hint_ids": ["inventory-001::h1"],
                "evidence_ids": ["inventory-001::e2"],
                "reference_ids": ["inventory-001::r1"],
                "implicit": False,
                "confidence": "Verified",
            }
        ],
        "hint_decisions": [
            {
                "hint_id": "inventory-001::h2",
                "decision": "rejected",
                "reason": "The remark is not a mathematical consequence.",
            }
        ],
        "reference_decisions": [
            {
                "reference_id": "inventory-001::r2",
                "decision": "navigation",
                "evidence_ids": ["inventory-001::e3"],
            }
        ],
        "gap_decisions": [],
        "gaps": [
            {
                "id": "gap-reference",
                "category": "reference",
                "reference_id": "inventory-001::r3",
                "evidence_ids": ["inventory-001::e3"],
                "inventory_gap_ids": ["inventory-001::g1"],
                "description": "The bounded source does not resolve this reference.",
            }
        ],
    }


def _empty_semantic_repair() -> dict:
    """Return one schema-v2 repair that intentionally changes no keyed records."""

    return {
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
    fragment["nodes"][0]["location"] = [0, 101, 101]

    with pytest.raises(ValueError, match="outside its owned chunk span"):
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))


def test_pooler_rejects_duplicate_local_ids(tmp_path: Path) -> None:
    """Distinct discovery nodes cannot silently reuse one local handle."""

    fragment = _fragment("inventory-001", "a.tex")
    duplicate = deepcopy(fragment["nodes"][0])
    duplicate["location"] = [0, 7, 7]
    fragment["nodes"].append(duplicate)

    with pytest.raises(ValueError, match="duplicate node id"):
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))


def test_pooler_rejects_duplicate_document_wide_candidate_anchors(tmp_path: Path) -> None:
    """Two nodes at one source start cannot silently become one candidate."""

    fragment = _fragment("inventory-001", "a.tex")
    duplicate = deepcopy(fragment["nodes"][0])
    duplicate["local_id"] = "n2"
    fragment["nodes"].append(duplicate)

    with pytest.raises(ValueError, match="candidate anchor emitted more than once"):
        _pool([fragment], _manifest(tmp_path, ["a.tex"]))


def test_pooler_distinguishes_nodes_with_same_start_and_different_spans(
    tmp_path: Path,
) -> None:
    """A local premise and its result may legitimately begin on the same line."""

    fragment = _fragment("inventory-001", "a.tex")
    second = deepcopy(fragment["nodes"][0])
    second["local_id"] = "n2"
    second["location"] = [0, 1, 3]
    second["environment"] = "proposition"
    second["summary"] = "The local premise enables the resulting construction."
    fragment["nodes"].append(second)

    pooled = _pool([fragment], _manifest(tmp_path, ["a.tex"]))

    assert {candidate["id"] for candidate in pooled["candidates"]} == {
        "a.tex:1-2",
        "a.tex:1-3",
    }


def test_pooler_accepts_dense_local_fragment_within_aggregate_budget(tmp_path: Path) -> None:
    """A lossless 42-percent chunk is valid when the full inventory stays under 35 percent."""

    fragments = [
        _fragment("inventory-001", "a.tex"),
        _minimal_fragment("inventory-002", "b.tex"),
    ]
    manifest = _manifest_near_fragment_ratio(tmp_path, fragments, 0.42)
    owned = [merger._owned_packet_bytes(chunk) for chunk in manifest["chunks"]]
    canonical = [len(merger.canonical_json_bytes(fragment)) for fragment in fragments]

    assert 0.41 <= canonical[0] / owned[0] <= 0.43
    assert sum(canonical) / sum(owned) <= 0.35
    assert _pool(fragments, manifest)["chunk_id"] == "pooled"


def test_pooler_accepts_verbose_fragment_for_controller_quality_review(
    tmp_path: Path,
) -> None:
    """Size is an evaluation signal and must not trigger worker rewriting."""

    fragment = _fragment("inventory-001", "a.tex")
    manifest = _manifest_near_fragment_ratio(tmp_path, [fragment], 0.51)

    assert _pool([fragment], manifest)["chunk_id"] == "pooled"


def test_pooler_accepts_verbose_aggregate_for_controller_quality_review(
    tmp_path: Path,
) -> None:
    """An oversized aggregate continues to extract while diagnostics retain its ratio."""

    fragments = [
        _fragment("inventory-001", "a.tex"),
        _fragment("inventory-002", "b.tex"),
    ]
    manifest = _manifest_near_fragment_ratio(tmp_path, fragments, 0.36)

    assert _pool(fragments, manifest)["chunk_id"] == "pooled"


def test_pooler_has_no_worker_compactness_override_api(tmp_path: Path) -> None:
    """Callers cannot turn an evaluation ratio into a worker-facing rewrite threshold."""

    fragment = _fragment("inventory-001", "a.tex")
    manifest = _manifest(tmp_path, ["a.tex"], line_count=20, line_width=100)

    with pytest.raises(TypeError, match="compactness_ratio"):
        merger.pool_inventory_fragments(
            [fragment],
            chunk_manifest=manifest,
            compactness_ratio=0.5,
        )
    with pytest.raises(TypeError, match="aggregate_compactness_ratio"):
        merger.pool_inventory_fragments(
            [fragment],
            chunk_manifest=manifest,
            aggregate_compactness_ratio=0.35,
        )


def test_pooler_rejects_packet_mutation_after_planning(tmp_path: Path) -> None:
    """Mutable packet files cannot inflate a planned fragment allowance."""

    manifest = _manifest(tmp_path, ["a.tex"])
    packet_path = Path(manifest["chunks"][0]["packet_path"])
    packet_path.write_text(packet_path.read_text(encoding="utf-8") + "padding\n", encoding="utf-8")

    with pytest.raises(ValueError, match="identity changed after planning"):
        _pool([_fragment("inventory-001", "a.tex")], manifest)


def test_extract_reconciliation_accepts_exact_whole_document_partitions() -> None:
    """The complete candidate/u/h/r partition is the valid handoff to compilation."""

    merger.validate_extract_reconciliation(
        _semantic_extract(), _reconciliation_inventory()
    )


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["exclusions"].clear(),
        "unreconciled candidate.*section.tex:40",
    ),
    (
        lambda payload: payload["exclusions"].append(
            {"candidate_id": "section.tex:10", "reason": "duplicate"}
        ),
        "candidate reconciled more than once.*section.tex:10",
    ),
    (
        lambda payload: payload["exclusions"].append(
            {"candidate_id": "section.tex:99", "reason": "unknown"}
        ),
        "unknown candidate.*section.tex:99",
    ),
])
def test_extract_reconciliation_rejects_inexact_candidate_partition(
    mutation, expected: str
) -> None:
    """Every inventory candidate has exactly one known entity/exclusion outcome."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["unresolved_resolutions"].pop(),
        "unreconciled unresolved handle.*inventory-001::u2",
    ),
    (
        lambda payload: payload["unresolved_resolutions"].append(
            deepcopy(payload["unresolved_resolutions"][0])
        ),
        "unresolved handle reconciled more than once.*inventory-001::u1",
    ),
    (
        lambda payload: payload["unresolved_resolutions"].append(
            {
                "unresolved_id": "inventory-001::u99",
                "disposition": "rejected",
                "reason": "unknown",
            }
        ),
        "unknown unresolved handle.*inventory-001::u99",
    ),
])
def test_extract_reconciliation_rejects_inexact_unresolved_partition(
    mutation, expected: str
) -> None:
    """Qualified unresolved handles cannot disappear, duplicate, or appear de novo."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("disposition", ["matched", "created"])
def test_extract_schema_requires_entity_for_retained_unresolved_handle(
    disposition: str,
) -> None:
    """A retained unresolved record without an entity cannot preserve its endpoint."""

    payload = _semantic_extract()
    payload["unresolved_resolutions"][0] = {
        "unresolved_id": "inventory-001::u1",
        "disposition": disposition,
    }

    with pytest.raises(ValueError, match="entity_id.*required"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["hint_decisions"].clear(),
        "unreconciled hint handle.*inventory-001::h2",
    ),
    (
        lambda payload: payload["hint_decisions"].append(
            {
                "hint_id": "inventory-001::h1",
                "decision": "rejected",
                "reason": "duplicate",
            }
        ),
        "hint handle reconciled more than once.*inventory-001::h1",
    ),
    (
        lambda payload: payload["hint_decisions"].append(
            {
                "hint_id": "inventory-001::h99",
                "decision": "rejected",
                "reason": "unknown",
            }
        ),
        "unknown hint handle.*inventory-001::h99",
    ),
])
def test_extract_reconciliation_rejects_inexact_hint_partition(
    mutation, expected: str
) -> None:
    """Each qualified relationship hint becomes one edge input or one decision."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload: payload["gaps"].clear(),
        "unreconciled reference handle.*inventory-001::r3",
    ),
    (
        lambda payload: payload["reference_decisions"].append(
            {
                "reference_id": "inventory-001::r1",
                "decision": "navigation",
                "evidence_ids": ["inventory-001::e3"],
            }
        ),
        "reference handle reconciled more than once.*inventory-001::r1",
    ),
    (
        lambda payload: payload["reference_decisions"].append(
            {
                "reference_id": "inventory-001::r99",
                "decision": "navigation",
                "evidence_ids": ["inventory-001::e3"],
            }
        ),
        "unknown reference handle.*inventory-001::r99",
    ),
])
def test_extract_reconciliation_rejects_inexact_reference_partition(
    mutation, expected: str
) -> None:
    """References partition across edges, non-edge decisions, and reference gaps."""

    payload = _semantic_extract()
    mutation(payload)

    with pytest.raises(ValueError, match=expected):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_extract_reconciliation_requires_exact_inventory_gap_partition() -> None:
    """Every pooled g* finding must survive as a final gap or an explicit decision."""

    payload = _semantic_extract()
    del payload["gaps"][0]["inventory_gap_ids"]

    with pytest.raises(ValueError, match="unreconciled inventory gap.*inventory-001::g1"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())

    payload["gap_decisions"] = [
        {
            "gap_id": "inventory-001::g1",
            "disposition": "resolved",
            "reason": "The document-wide pass matched the reference.",
        }
    ]
    merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


@pytest.mark.parametrize("disposition", ["rejected", "unresolved"])
def test_extract_schema_forbids_entity_on_nonretained_resolution(disposition: str) -> None:
    """Rejected or unresolved records cannot inject provenance into a retained entity."""

    payload = _semantic_extract()
    payload["unresolved_resolutions"][1]["disposition"] = disposition
    payload["unresolved_resolutions"][1]["entity_id"] = "definition"

    with pytest.raises(ValueError, match="entity_id"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_extract_reconciliation_reports_independent_record_errors_together() -> None:
    """One correction job needs every independently detectable record failure."""

    payload = _semantic_extract()
    payload["relationships"][0]["evidence_ids"] = ["inventory-001::e99"]
    payload["relationships"][0]["to"] = "missing"

    with pytest.raises(ValueError) as raised:
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())

    diagnostic = str(raised.value)
    assert "unknown evidence handle" in diagnostic
    assert "unknown relationship target" in diagnostic


def test_extract_reconciliation_rejects_changed_hint_endpoints() -> None:
    """An accepted hint must retain its resolved prerequisite and dependent."""

    payload = _semantic_extract()
    payload["relationships"][0]["from"] = "result"

    with pytest.raises(ValueError, match="does not retain resolved hint endpoints"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_extract_reconciliation_rejects_self_and_duplicate_direct_edges() -> None:
    """Final direct edges are unique ordered endpoint/type records and never loops."""

    payload = _semantic_extract()
    duplicate = deepcopy(payload["relationships"][0])
    duplicate["from"] = "result"
    duplicate["to"] = "result"
    duplicate["hint_ids"] = []
    duplicate["reference_ids"] = []
    payload["relationships"].append(duplicate)
    payload["relationships"].append(deepcopy(payload["relationships"][0]))

    with pytest.raises(ValueError) as raised:
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())

    diagnostic = str(raised.value)
    assert "self-edge" in diagnostic
    assert "duplicate direct relationship" in diagnostic


def test_extract_reconciliation_rejects_explicit_inferred_mismatch() -> None:
    """A final edge cannot call a source-explicit accepted hint implicit."""

    payload = _semantic_extract()
    payload["relationships"][0]["implicit"] = True

    with pytest.raises(ValueError, match="explicit/inferred mismatch"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_extract_reconciliation_rejects_out_of_bounds_registered_evidence() -> None:
    """Qualified evidence is not usable unless its pooled source bounds are valid."""

    inventory = _reconciliation_inventory()
    inventory["evidence"][1]["location"] = [0, 22, 20]

    with pytest.raises(ValueError, match="evidence inventory-001::e2 location.*line bounds"):
        merger.validate_extract_reconciliation(_semantic_extract(), inventory)


@pytest.mark.parametrize("mutation, expected", [
    (
        lambda payload, _inventory: payload["entities"][1].update(
            {"type": "external-result"}
        ),
        "external-result entity 'result' has no outgoing supports edge",
    ),
    (
        lambda _payload, inventory: inventory["candidates"][2].update(
            {
                "type_hint": "external-result",
                "external_identity": {"name": "Named theorem"},
                "retention_reasons": ["named-indispensable-external-result"],
            }
        ),
        "required external-result candidate section.tex:40 is not retained",
    ),
    (
        lambda payload, _inventory: payload["entities"][1].update(
            {"type": "exposition", "kind": "example"}
        ),
        "example entity 'result' has no incoming illustrated-by edge",
    ),
    (
        lambda payload, inventory: (
            inventory["unresolved_entities"].append(
                {
                    "key": "inventory-001::u3",
                    "title": "Created object",
                    "statement": "An implicit object is required.",
                    "resolution_kind": "implicit-entity",
                    "type_hint": "setup",
                    "evidence_ids": ["inventory-001::e3"],
                }
            ),
            payload["unresolved_resolutions"].append(
                {
                    "unresolved_id": "inventory-001::u3",
                    "disposition": "created",
                    "entity_id": "isolated",
                }
            ),
            payload["entities"].append(
                {
                    "candidate_ids": [],
                    "id": "isolated",
                    "type": "setup",
                    "kind": "definition",
                    "short_title": "Isolated",
                    "description": "An implicit object.",
                    "source": "inferred",
                }
            ),
        ),
        "isolated semantic entity: isolated",
    ),
])
def test_extract_reconciliation_enforces_final_graph_retention_invariants(
    mutation, expected: str
) -> None:
    """External results, examples, and all retained entities remain graph-connected."""

    payload = _semantic_extract()
    inventory = _reconciliation_inventory()
    mutation(payload, inventory)

    with pytest.raises(ValueError, match=expected):
        merger.validate_extract_reconciliation(payload, inventory)


def test_extract_reconciliation_allows_only_one_direct_type_per_endpoint_pair() -> None:
    """One ordered endpoint pair cannot masquerade as two different direct links."""

    payload = _semantic_extract()
    second = deepcopy(payload["relationships"][0])
    second["type"] = "illustrated-by"
    second["hint_ids"] = []
    second.pop("reference_ids")
    second["implicit"] = True
    payload["relationships"].append(second)

    with pytest.raises(ValueError, match="multiple relationship types"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())


def test_semantic_repair_updates_new_records_without_touching_unaffected_records() -> None:
    """Narrow u/h corrections preserve every record outside their keyed upserts."""

    payload = _semantic_extract()
    original_relationships = deepcopy(payload["relationships"])
    original_reference_decisions = deepcopy(payload["reference_decisions"])
    original_gaps = deepcopy(payload["gaps"])
    repair = _empty_semantic_repair()
    repair["remove_unresolved_ids"] = ["inventory-001::u2"]
    repair["upsert_unresolved_resolutions"] = [
        {
            "unresolved_id": "inventory-001::u2",
            "disposition": "unresolved",
            "reason": "More source context is required.",
        }
    ]
    repair["remove_hint_ids"] = ["inventory-001::h2"]
    repair["upsert_hint_decisions"] = [
        {
            "hint_id": "inventory-001::h2",
            "decision": "unresolved",
            "reason": "The directness question remains open.",
        }
    ]
    repair["remove_inventory_gap_ids"] = ["inventory-001::g1"]
    repair["remove_gap_ids"] = ["gap-reference"]
    repair["upsert_gap_decisions"] = [
        {
            "gap_id": "inventory-001::g1",
            "disposition": "resolved",
            "reason": "The document-wide pass resolved the ambiguity.",
        }
    ]
    repair["upsert_reference_decisions"] = [
        {
            "reference_id": "inventory-001::r3",
            "decision": "non-dependency",
            "evidence_ids": ["inventory-001::e3"],
            "reason": "The document-wide pass resolved the ambiguity as a non-edge.",
        }
    ]

    repaired = merger.apply_semantic_repair(
        payload, repair, _reconciliation_inventory()
    )

    assert repaired["relationships"] == original_relationships
    assert repaired["reference_decisions"][:1] == original_reference_decisions
    assert repaired["reference_decisions"][1]["reference_id"] == "inventory-001::r3"
    assert original_gaps[0]["inventory_gap_ids"] == ["inventory-001::g1"]
    assert repaired["gaps"] == []
    assert repaired["unresolved_resolutions"][1]["disposition"] == "unresolved"
    assert repaired["hint_decisions"][0]["decision"] == "unresolved"
    assert repaired["gap_decisions"][0]["disposition"] == "resolved"


def test_semantic_repair_requires_inventory_and_cannot_bypass_reconciliation() -> None:
    """Every correction returns only after exact pooled-inventory validation."""

    payload = _semantic_extract()
    with pytest.raises(TypeError, match="inventory"):
        merger.apply_semantic_repair(payload, _empty_semantic_repair())

    incomplete = _empty_semantic_repair()
    incomplete["remove_unresolved_ids"] = ["inventory-001::u2"]
    with pytest.raises(ValueError, match="unreconciled unresolved handle"):
        merger.apply_semantic_repair(
            payload, incomplete, _reconciliation_inventory()
        )


@pytest.mark.parametrize(
    "field, records",
    [
        ("upsert_entities", lambda payload: [payload["entities"][0]] * 2),
        ("upsert_exclusions", lambda payload: [payload["exclusions"][0]] * 2),
        (
            "upsert_unresolved_resolutions",
            lambda payload: [payload["unresolved_resolutions"][0]] * 2,
        ),
        ("upsert_relationships", lambda payload: [payload["relationships"][0]] * 2),
        ("upsert_hint_decisions", lambda payload: [payload["hint_decisions"][0]] * 2),
        (
            "upsert_reference_decisions",
            lambda payload: [payload["reference_decisions"][0]] * 2,
        ),
        ("upsert_gaps", lambda payload: [payload["gaps"][0]] * 2),
    ],
)
def test_semantic_repair_rejects_duplicate_logical_upsert_keys(
    field: str, records
) -> None:
    """Two upserts may not compete for the same stable logical record key."""

    payload = _semantic_extract()
    repair = _empty_semantic_repair()
    repair[field] = deepcopy(records(payload))

    with pytest.raises(ValueError, match="duplicate repair upsert key"):
        merger.apply_semantic_repair(
            payload, repair, _reconciliation_inventory()
        )


def test_superseded_hint_decisions_require_a_reason_in_extract_and_repair() -> None:
    """Supersession is a semantic disposition and must remain auditable."""

    payload = _semantic_extract()
    payload["hint_decisions"][0] = {
        "hint_id": "inventory-001::h2",
        "decision": "superseded",
    }
    with pytest.raises(ValueError, match="reason is required"):
        merger.validate_extract_reconciliation(payload, _reconciliation_inventory())

    repair = _empty_semantic_repair()
    repair["remove_hint_ids"] = ["inventory-001::h2"]
    repair["upsert_hint_decisions"] = [
        {"hint_id": "inventory-001::h2", "decision": "superseded"}
    ]
    with pytest.raises(ValueError, match="reason is required"):
        merger.apply_semantic_repair(
            _semantic_extract(), repair, _reconciliation_inventory()
        )

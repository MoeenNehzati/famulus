"""Exercise storage-version-3 Reckoning persistence and confinement."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from threading import Barrier, Event, Thread

import pytest

import officina.rutter.model as model_module
from officina.common.atomic_files import AtomicWriteError
from officina.rutter.model import (
    MachineResult,
    ActiveChild,
    ActiveRun,
    Charter,
    EnteredEvolution,
    Reckoning,
    RutterDefinitionError,
    RutterStateError,
)
from officina.rutter.storage import (
    ReckoningStore,
    _canonical_reckoning_bytes,
    _confined_reckoning_path,
    _decode_reckoning,
)
import officina.rutter.storage as storage_module


def _active(
    *,
    run_id: str = "root-run",
    entry_id: str = "entry-root",
    evolution_id: str = "review",
    history: tuple[object, ...] = (),
    active_child: ActiveChild | None = None,
) -> ActiveRun:
    return ActiveRun(
        run_id,
        "example",
        1,
        Charter({"artifact": "draft.md"}),
        EnteredEvolution(entry_id, evolution_id),
        history,
        active_child,
    )


def _example_reckoning(
    *, global_revision: int = 0, evolution_id: str = "review"
) -> Reckoning:
    return Reckoning(
        3, global_revision, _active(evolution_id=evolution_id), {}, None, None
    )


def _valid_mapping() -> dict[str, object]:
    """Return a hand-authored v3 record independent of the storage codec."""

    return {
        "storage_version": 3,
        "global_revision": 2,
        "root": {
            "run_id": "root-run",
            "rutter_id": "example",
            "definition_version": 1,
            "charter": {"artifact": "draft.md"},
            "entered_node": {"entry_id": "entry-root", "state_id": "review"},
            "history": [
                {
                    "call_id": "call-child",
                    "node_entry_id": "entry-delegate",
                    "site_kind": "explicit_call",
                    "site_id": "delegate",
                    "attached_to_edge_id": None,
                    "completed_run_id": "child-run",
                }
            ],
            "active_child": None,
        },
        "completed_runs": {
            "child-run": {
                "run_id": "child-run",
                "rutter_id": "child",
                "definition_version": 1,
                "charter": {"item": "A"},
                "history": [
                    {
                        "record_id": "done-child",
                        "node_entry_id": "entry-child-done",
                        "state_id": "complete",
                        "result": {"outcome": "completed", "value": {"item": "A"}},
                    }
                ],
            }
        },
        "active_effect": None,
        "fault": None,
    }


def _bytes(mapping: dict[str, object]) -> bytes:
    return json.dumps(mapping, separators=(",", ":")).encode("utf-8")


def _call(
    call_id: str,
    completed_run_id: str,
    *,
    evolution_entry_id: str | None = None,
    site_kind: str = "explicit_call",
    site_id: str = "delegate",
    transition_id: str | None = None,
) -> dict[str, object]:
    if evolution_entry_id is None:
        evolution_entry_id = (
            "entry-root" if site_kind == "attached_case" else f"entry-{site_id}"
        )
    return {
        "call_id": call_id,
        "node_entry_id": evolution_entry_id,
        "site_kind": site_kind,
        "site_id": site_id,
        "attached_to_edge_id": transition_id,
        "completed_run_id": completed_run_id,
    }


def _turn(
    record_id: str = "turn-root",
    *,
    evolution_entry_id: str = "entry-root",
    evolution_id: str = "review",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "node_entry_id": evolution_entry_id,
        "state_id": evolution_id,
        "revision": 2,
        "message": {
            "instructions": {"text": "Review.", "answer": {"reviewed": {}}},
            "data": {
                "state": {
                    "id": evolution_id,
                    "entry_id": evolution_entry_id,
                    "revision": 2,
                },
                "payload": {},
            },
        },
        "response": {"revision": 2, "outcome": "reviewed", "evidence": {}},
    }


def _action(
    record_id: str = "action-root",
    *,
    evolution_entry_id: str = "entry-root",
    evolution_id: str = "review",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "action_id": f"issued-{record_id}",
        "node_entry_id": evolution_entry_id,
        "state_id": evolution_id,
        "mode": "pure",
        "result": {"outcome": "stored", "value": {}},
    }


def _active_attached_child(
    edge_id: str,
    *,
    child_history: list[dict[str, object]] | None = None,
    site_id: str = "maker-active",
) -> dict[str, object]:
    return {
        "call_id": "call-active",
        "kind": "attached_case",
        "site": site_id,
        "attached_to_edge_id": edge_id,
        "run": {
            "run_id": "active-child",
            "rutter_id": "child",
            "definition_version": 1,
            "charter": {},
            "entered_node": {"entry_id": "entry-active", "state_id": "start"},
            "history": [] if child_history is None else child_history,
            "active_child": None,
        },
    }


def test_codec_round_trips_one_stable_compact_utf8_record() -> None:
    mapping = _valid_mapping()
    expected = (
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")

    decoded = _decode_reckoning(_bytes(mapping))

    assert _canonical_reckoning_bytes(decoded) == expected
    assert _decode_reckoning(expected) == decoded


@pytest.mark.parametrize("storage_version", (1, 2))
def test_decode_rejects_legacy_storage_versions_with_one_stable_error(
    storage_version: int,
) -> None:
    mapping = {
        "storage_version": storage_version,
        "charter": {"legacy": True},
        "fix": {"legacy": True},
    }

    with pytest.raises(RutterStateError) as error:
        _decode_reckoning(_bytes(mapping))

    assert str(error.value) == "unsupported Reckoning storage_version; expected 3"


def test_decode_rejects_duplicate_keys() -> None:
    with pytest.raises(RutterStateError, match="duplicate key"):
        _decode_reckoning(b'{"storage_version":3,"storage_version":3}\n')


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_decode_rejects_nonfinite_numbers(constant: str) -> None:
    with pytest.raises(RutterStateError, match="non-finite"):
        _decode_reckoning((f'{{"storage_version":{constant}}}').encode("ascii"))


@pytest.mark.parametrize("data", (b"\xff\n", b"{not json}\n", b"[]\n"))
def test_decode_rejects_corrupt_json(data: bytes) -> None:
    with pytest.raises(RutterStateError):
        _decode_reckoning(data)


def test_decode_constructs_structure_before_running_bound_semantics() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    del root["entered_node"]
    seen: list[Reckoning] = []

    with pytest.raises(RutterStateError, match="fields"):
        _decode_reckoning(_bytes(mapping), semantic_validator=seen.append)

    assert seen == []


def test_decode_runs_bound_semantics_after_internal_validation() -> None:
    seen: list[Reckoning] = []

    def reject(reckoning: Reckoning) -> None:
        seen.append(reckoning)
        raise RutterStateError("definition mismatch")

    with pytest.raises(RutterStateError, match="definition mismatch"):
        _decode_reckoning(_bytes(_valid_mapping()), semantic_validator=reject)
    assert len(seen) == 1


def test_decode_rejects_excessive_active_child_depth() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    current = root
    for index in range(65):
        child = {
            "run_id": f"run-{index}",
            "rutter_id": "child",
            "definition_version": 1,
            "charter": {},
            "entered_node": {"entry_id": f"entry-{index}", "state_id": "start"},
            "history": [],
            "active_child": None,
        }
        current["active_child"] = {
            "call_id": f"call-{index}",
            "kind": "explicit_call",
            "site": "delegate",
            "attached_to_edge_id": None,
            "run": child,
        }
        current = child

    with pytest.raises(RutterStateError, match="nesting is too deep"):
        _decode_reckoning(_bytes(mapping))


def test_encoder_rejects_an_active_path_that_cannot_be_reopened() -> None:
    run = _active(run_id="run-64", entry_id="entry-64")
    for index in reversed(range(64)):
        run = _active(
            run_id=f"run-{index}",
            entry_id=f"entry-{index}",
            active_child=ActiveChild(
                f"call-{index}", "explicit_call", "delegate", None, run
            ),
        )
    with pytest.raises(RutterStateError, match="nesting is too deep"):
        Reckoning(3, 0, run, {}, None, None)


def test_decode_rejects_wrong_active_effect_owner() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    root["active_child"] = {
        "call_id": "call-active",
        "kind": "explicit_call",
        "site": "delegate",
        "attached_to_edge_id": None,
        "run": {
            "run_id": "active-child",
            "rutter_id": "child",
            "definition_version": 1,
            "charter": {},
            "entered_node": {"entry_id": "entry-child", "state_id": "act"},
            "history": [],
            "active_child": None,
        },
    }
    mapping["active_effect"] = {
        "action_id": "action-1",
        "owner_run_id": "root-run",
        "node_entry_id": "entry-root",
        "state_id": "review",
        "mode": "repeat-safe",
        "disposition": "planned",
        "result": None,
    }

    with pytest.raises(RutterStateError, match="deepest active run"):
        _decode_reckoning(_bytes(mapping))


@pytest.mark.parametrize(
    ("disposition", "result"),
    (
        ("planned", {"outcome": "ok", "value": {}}),
        ("uncertain", {"outcome": "ok", "value": {}}),
        ("completed", None),
    ),
)
def test_decode_rejects_inconsistent_effect_recovery(
    disposition: str, result: object
) -> None:
    mapping = _valid_mapping()
    mapping["active_effect"] = {
        "action_id": "action-1",
        "owner_run_id": "root-run",
        "node_entry_id": "entry-root",
        "state_id": "review",
        "mode": "repeat-safe",
        "disposition": disposition,
        "result": result,
    }
    with pytest.raises(RutterStateError, match="effect recovery"):
        _decode_reckoning(_bytes(mapping))


def test_decode_recovers_typed_effect_and_opaque_fault_values() -> None:
    mapping = _valid_mapping()
    effect = {
        "action_id": "action-1",
        "owner_run_id": "root-run",
        "node_entry_id": "entry-root",
        "state_id": "review",
        "mode": "non-repeat-safe",
        "disposition": "uncertain",
        "result": None,
    }
    fault = {"legacy": {"coordinates": {"run": "root-run"}}}
    mapping["active_effect"] = effect
    mapping["fault"] = fault

    decoded = _decode_reckoning(_bytes(mapping))

    assert type(decoded.active_effect).__name__ == "_EffectRecovery"
    assert decoded.active_effect is not None
    assert (
        decoded.active_effect.machine_id,
        decoded.active_effect.owner_run_id,
        decoded.active_effect.evolution_entry_id,
        decoded.active_effect.evolution_id,
        decoded.active_effect.mode,
        decoded.active_effect.disposition,
        decoded.active_effect.result,
    ) == (
        "action-1",
        "root-run",
        "entry-root",
        "review",
        "non-repeat-safe",
        "uncertain",
        None,
    )
    assert type(decoded.fault).__name__ == "OpaqueFault"
    assert decoded.fault.wire == fault


@pytest.mark.parametrize(
    "fault",
    (
        {"category": "routing"},
        {
            "category": "routing",
            "run_id": "",
            "state_id": "review",
            "node_entry_id": "entry-root",
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "review",
            "node_entry_id": 3,
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "review",
            "node_entry_id": "entry-root",
            "target_state_id": [],
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "review",
            "node_entry_id": "entry-root",
            "target_state_id": None,
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "review",
            "node_entry_id": "entry-root",
            "case_maker_ids": ["maker", 3],
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "review",
            "node_entry_id": "entry-root",
            "case_maker_ids": [],
        },
    ),
)
def test_decode_rejects_malformed_known_fault_coordinates(
    fault: dict[str, object],
) -> None:
    """Treating malformed current fault authority as opaque must fail."""

    mapping = _valid_mapping()
    mapping["fault"] = fault

    with pytest.raises(RutterStateError, match="fault"):
        _decode_reckoning(_bytes(mapping))


def test_typed_recovery_and_known_fault_encode_to_exact_v3_bytes() -> None:
    """Adding type tags or changing any established v3 key must fail."""

    mapping = _valid_mapping()
    mapping["active_effect"] = {
        "action_id": "action-1",
        "owner_run_id": "root-run",
        "node_entry_id": "entry-root",
        "state_id": "review",
        "mode": "repeat-safe",
        "disposition": "completed",
        "result": {"outcome": "stored", "value": {"count": 2}},
    }
    mapping["fault"] = {
        "category": "routing",
        "run_id": "root-run",
        "state_id": "review",
        "node_entry_id": "entry-root",
        "target_state_id": "done",
        "case_maker_ids": ["maker-a", "maker-b"],
    }
    expected = (
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")

    decoded = _decode_reckoning(expected)

    assert type(decoded.active_effect).__name__ == "_EffectRecovery"
    assert type(decoded.fault).__name__ == "KnownFault"
    assert _canonical_reckoning_bytes(decoded) == expected


def test_typed_recovery_and_known_fault_own_their_invariants() -> None:
    """Malformed typed authority must fail at its construction boundary."""

    with pytest.raises(RutterStateError, match="effect recovery"):
        model_module._EffectRecovery(
            "action-1",
            "root-run",
            "entry-root",
            "review",
            "repeat-safe",
            "planned",
            MachineResult("stored", {}),
        )
    with pytest.raises(RutterStateError, match="fault"):
        model_module.KnownFault(
            "routing",
            "root-run",
            "review",
            "",
            None,
            (),
        )


def test_opaque_fault_reopen_and_rewrite_preserves_exact_bytes(tmp_path: Path) -> None:
    """Discarding or normalizing private legacy payload during rewrite must fail."""

    mapping = _valid_mapping()
    mapping["fault"] = {
        "legacy": {"detail": ["opaque", {"attempt": 2}]},
        "vendor_flag": True,
    }
    expected = (
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    target = tmp_path / "opaque-fault.reckoning.json"
    target.write_bytes(expected)
    store = ReckoningStore(target)

    with store.transaction() as current:
        assert type(current.fault).__name__ == "OpaqueFault"
        store.replace(current, current)

    assert target.read_bytes() == expected


@pytest.mark.parametrize("disposition", ("planned", "completed", "uncertain"))
@pytest.mark.parametrize(
    "corruption",
    ("owner", "entrance", "state", "mode", "active-child", "consumed"),
)
def test_decode_rejects_complete_effect_recovery_corruption_matrix(
    disposition: str,
    corruption: str,
) -> None:
    """Accepting any corrupt recovery coordinate or reused authority must fail."""

    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    effect = {
        "action_id": "action-1",
        "owner_run_id": "root-run",
        "node_entry_id": "entry-root",
        "state_id": "review",
        "mode": "repeat-safe",
        "disposition": disposition,
        "result": (
            {"outcome": "stored", "value": {}}
            if disposition == "completed"
            else None
        ),
    }
    mapping["active_effect"] = effect

    if corruption == "owner":
        effect["owner_run_id"] = "other-run"
    elif corruption == "entrance":
        effect["node_entry_id"] = "other-entry"
    elif corruption == "state":
        effect["state_id"] = "other-state"
    elif corruption == "mode":
        effect["mode"] = "pure"
    elif corruption == "active-child":
        root["active_child"] = {
            "call_id": "call-active",
            "kind": "explicit_call",
            "site": "delegate",
            "attached_to_edge_id": None,
            "run": {
                "run_id": "active-child",
                "rutter_id": "child",
                "definition_version": 1,
                "charter": {},
                "entered_node": {"entry_id": "entry-child", "state_id": "act"},
                "history": [],
                "active_child": None,
            },
        }
    else:
        consumed = _action()
        consumed["action_id"] = effect["action_id"]
        root["history"] = [consumed]

    with pytest.raises(RutterStateError):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_dangling_completed_run_map_identity() -> None:
    mapping = _valid_mapping()
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["wrong-key"] = completed.pop("child-run")

    with pytest.raises(RutterStateError, match="key must match"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_duplicate_active_run_ids() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    child = json.loads(json.dumps(root))
    child["entered_node"] = {"entry_id": "entry-child", "state_id": "start"}
    root["active_child"] = {
        "call_id": "call-active",
        "kind": "explicit_call",
        "site": "delegate",
        "attached_to_edge_id": None,
        "run": child,
    }

    with pytest.raises(RutterStateError, match="run IDs"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_duplicate_call_ids_across_active_and_history() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["active_child"] = {
        "call_id": "call-child",
        "kind": "explicit_call",
        "site": "delegate",
        "attached_to_edge_id": None,
        "run": {
            "run_id": "active-child",
            "rutter_id": "child",
            "definition_version": 1,
            "charter": {},
            "entered_node": {"entry_id": "entry-active", "state_id": "start"},
            "history": [],
            "active_child": None,
        },
    }

    with pytest.raises(RutterStateError, match="duplicate call ID"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_duplicate_record_ids_across_runs() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"].insert(
        0,
        {
            "record_id": "done-child",
            "action_id": "action-root",
            "node_entry_id": "entry-root",
            "state_id": "review",
            "mode": "pure",
            "result": {"outcome": "ok", "value": {}},
        },
    )

    with pytest.raises(RutterStateError, match="duplicate history record ID"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_duplicate_active_entrance_ids() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    root["active_child"] = {
        "call_id": "call-active",
        "kind": "explicit_call",
        "site": "delegate",
        "attached_to_edge_id": None,
        "run": {
            "run_id": "active-child",
            "rutter_id": "child",
            "definition_version": 1,
            "charter": {},
            "entered_node": {"entry_id": "entry-root", "state_id": "start"},
            "history": [],
            "active_child": None,
        },
    }

    with pytest.raises(RutterStateError, match="entrance owner is not unique"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_call_record_referencing_no_completed_run() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"][0]["completed_run_id"] = "missing-run"

    with pytest.raises(RutterStateError, match="unknown completed run"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_more_than_one_done_record() -> None:
    mapping = _valid_mapping()
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["child-run"]["history"].append(
        {
            "record_id": "done-child-again",
            "node_entry_id": "entry-child-again",
            "state_id": "complete",
            "result": {"outcome": "completed", "value": {}},
        }
    )

    with pytest.raises(RutterStateError, match="TerminalRecord"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_active_child_field_under_completed_run() -> None:
    mapping = _valid_mapping()
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["child-run"]["active_child"] = None

    with pytest.raises(RutterStateError, match="fields"):
        _decode_reckoning(_bytes(mapping))


@pytest.mark.parametrize("reference_count", (0, 2))
def test_decode_rejects_completed_run_without_exactly_one_reference(
    reference_count: int,
) -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _call(f"call-{index}", "child-run") for index in range(reference_count)
    ]

    with pytest.raises(RutterStateError, match="exactly one SubRutterRecord"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_cyclic_completed_run_references() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["child-run"]["history"].insert(
        0,
        _call(
            "call-grand",
            "grand-run",
            evolution_entry_id="entry-child-delegate",
        ),
    )
    completed["grand-run"] = {
        "run_id": "grand-run",
        "rutter_id": "child",
        "definition_version": 1,
        "charter": {},
        "history": [
            _call(
                "call-child",
                "child-run",
                evolution_entry_id="entry-grand-delegate",
            ),
            {
                "record_id": "done-grand",
                "node_entry_id": "entry-grand-done",
                "state_id": "complete",
                "result": {"outcome": "completed", "value": {}},
            },
        ],
    }

    with pytest.raises(RutterStateError, match="cyclic"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_nonattached_record_after_done() -> None:
    mapping = _valid_mapping()
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["child-run"]["history"].append(
        {
            "record_id": "action-after-done",
            "action_id": "late-action",
            "node_entry_id": "entry-child-done",
            "state_id": "complete",
            "mode": "pure",
            "result": {"outcome": "ok", "value": {}},
        }
    )

    with pytest.raises(RutterStateError, match="TerminalRecord"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_explicit_call_with_attached_edge_provenance() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"][0]["attached_to_edge_id"] = "edge-1"

    with pytest.raises(RutterStateError, match="provenance"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_attached_call_without_edge_provenance() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"][0]["site_kind"] = "attached_case"

    with pytest.raises(RutterStateError, match="provenance"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_duplicate_attachment_authority() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    completed = mapping["completed_runs"]
    assert isinstance(root, dict) and isinstance(completed, dict)
    root["history"] = [
        _turn("edge-1"),
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-1",
        ),
        _call(
            "call-grand",
            "grand-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-1",
        ),
    ]
    completed["grand-run"] = {
        "run_id": "grand-run",
        "rutter_id": "child",
        "definition_version": 1,
        "charter": {},
        "history": [
            {
                "record_id": "done-grand",
                "node_entry_id": "entry-grand-done",
                "state_id": "complete",
                "result": {"outcome": "completed", "value": {}},
            }
        ],
    }

    with pytest.raises(RutterStateError, match="duplicate attachment authority"):
        _decode_reckoning(_bytes(mapping))


@pytest.mark.parametrize("source_kind", ("turn", "action", "explicit_call", "done"))
def test_decode_accepts_attached_call_bound_to_valid_earlier_source(
    source_kind: str,
) -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    completed = mapping["completed_runs"]
    assert isinstance(root, dict) and isinstance(completed, dict)
    if source_kind == "turn":
        source = _turn("edge-source")
    elif source_kind == "action":
        source = _action("edge-source")
    elif source_kind == "explicit_call":
        source = _call(
            "edge-source",
            "source-run",
            evolution_entry_id="entry-root",
            site_id="review",
        )
        completed["source-run"] = {
            "run_id": "source-run",
            "rutter_id": "child",
            "definition_version": 1,
            "charter": {},
            "history": [
                {
                    "record_id": "done-source",
                    "node_entry_id": "entry-source-done",
                    "state_id": "complete",
                    "result": {"outcome": "completed", "value": {}},
                }
            ],
        }
    else:
        source = {
            "record_id": "edge-source",
            "node_entry_id": "entry-root",
            "state_id": "review",
            "result": {"outcome": "completed", "value": {}},
        }
    root["history"] = [
        source,
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-source",
        ),
    ]

    decoded = _decode_reckoning(_bytes(mapping))

    assert decoded.root.history[-1].attached_to_transition_id == "edge-source"


def test_decode_rejects_dangling_attached_edge_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="missing-edge",
        )
    ]

    with pytest.raises(RutterStateError, match="attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_future_attached_edge_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="future-edge",
        ),
        _turn("future-edge"),
    ]

    with pytest.raises(RutterStateError, match="attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_wrong_run_attached_edge_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="done-child",
        )
    ]

    with pytest.raises(RutterStateError, match="attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_attached_edge_source_from_another_entrance() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _turn("edge-source"),
        _call(
            "call-child",
            "child-run",
            evolution_entry_id="entry-other",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-source",
        ),
    ]

    with pytest.raises(RutterStateError, match="source entrance"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_attached_call_as_an_edge_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    completed = mapping["completed_runs"]
    assert isinstance(root, dict) and isinstance(completed, dict)
    completed["grand-run"] = {
        "run_id": "grand-run",
        "rutter_id": "child",
        "definition_version": 1,
        "charter": {},
        "history": [
            {
                "record_id": "done-grand",
                "node_entry_id": "entry-grand-done",
                "state_id": "complete",
                "result": {"outcome": "completed", "value": {}},
            }
        ],
    }
    root["history"] = [
        _turn("edge-source"),
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-source",
        ),
        _call(
            "call-grand",
            "grand-run",
            site_kind="attached_case",
            site_id="maker-2",
            transition_id="call-child",
        ),
    ]

    with pytest.raises(RutterStateError, match="attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_ambiguous_attached_edge_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _turn("edge-source"),
        _action("edge-source"),
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-source",
        ),
    ]

    with pytest.raises(RutterStateError, match="duplicate history record ID"):
        _decode_reckoning(_bytes(mapping))


def test_decode_allows_multiple_attached_calls_from_one_source_entrance() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    completed = mapping["completed_runs"]
    assert isinstance(root, dict) and isinstance(completed, dict)
    completed["grand-run"] = {
        "run_id": "grand-run",
        "rutter_id": "child",
        "definition_version": 1,
        "charter": {},
        "history": [
            {
                "record_id": "done-grand",
                "node_entry_id": "entry-grand-done",
                "state_id": "complete",
                "result": {"outcome": "completed", "value": {}},
            }
        ],
    }
    root["history"] = [
        _turn("edge-source"),
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-source",
        ),
        _call(
            "call-grand",
            "grand-run",
            site_kind="attached_case",
            site_id="maker-2",
            transition_id="edge-source",
        ),
    ]

    decoded = _decode_reckoning(_bytes(mapping))

    assert len(decoded.root.history) == 3


def test_decode_rejects_one_entrance_with_conflicting_historical_states() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"].insert(0, _action(evolution_id="other-state"))

    with pytest.raises(RutterStateError, match="entrance state"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_historical_entrance_reused_by_another_run() -> None:
    mapping = _valid_mapping()
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["child-run"]["history"][0]["node_entry_id"] = "entry-root"
    completed["child-run"]["history"][0]["state_id"] = "review"

    with pytest.raises(RutterStateError, match="entrance owner"):
        _decode_reckoning(_bytes(mapping))


@pytest.mark.parametrize("source_kind", ("turn", "action", "explicit_call", "done"))
def test_decode_accepts_active_attached_child_bound_to_parent_source(
    source_kind: str,
) -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    completed = mapping["completed_runs"]
    assert isinstance(root, dict) and isinstance(completed, dict)
    if source_kind == "turn":
        completed.clear()
        source = _turn("edge-source")
    elif source_kind == "action":
        completed.clear()
        source = _action("edge-source")
    elif source_kind == "explicit_call":
        source = _call(
            "edge-source",
            "child-run",
            evolution_entry_id="entry-root",
            site_id="review",
        )
    else:
        completed.clear()
        source = {
            "record_id": "edge-source",
            "node_entry_id": "entry-root",
            "state_id": "review",
            "result": {"outcome": "completed", "value": {}},
        }
    root["history"] = [source]
    root["active_child"] = _active_attached_child("edge-source")

    decoded = _decode_reckoning(_bytes(mapping))

    assert decoded.root.active_child is not None
    assert decoded.root.active_child.attached_to_transition_id == "edge-source"


def test_decode_rejects_active_attached_child_with_dangling_source() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    root["active_child"] = _active_attached_child("missing-edge")

    with pytest.raises(RutterStateError, match="active attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_active_attached_child_with_future_source() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = []
    root["active_child"] = _active_attached_child(
        "future-edge",
        child_history=[
            _turn(
                "future-edge",
                evolution_entry_id="entry-active",
                evolution_id="start",
            )
        ],
    )

    with pytest.raises(RutterStateError, match="active attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_active_attached_child_with_wrong_run_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["active_child"] = _active_attached_child("done-child")

    with pytest.raises(RutterStateError, match="active attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_active_attached_child_with_wrong_source_entrance() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _turn("edge-source", evolution_entry_id="entry-past", evolution_id="past")
    ]
    root["active_child"] = _active_attached_child("edge-source")

    with pytest.raises(RutterStateError, match="source entrance"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_active_attached_child_with_attached_source() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _turn("edge-source"),
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-1",
            transition_id="edge-source",
        ),
    ]
    root["active_child"] = _active_attached_child("call-child")

    with pytest.raises(RutterStateError, match="active attached edge source"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_ambiguous_active_attached_child_source() -> None:
    mapping = _valid_mapping()
    mapping["completed_runs"] = {}
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [_turn("edge-source"), _action("edge-source")]
    root["active_child"] = _active_attached_child("edge-source")

    with pytest.raises(RutterStateError, match="duplicate history record ID"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_active_duplicate_attachment_authority() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _turn("edge-source"),
        _call(
            "call-child",
            "child-run",
            site_kind="attached_case",
            site_id="maker-active",
            transition_id="edge-source",
        ),
    ]
    root["active_child"] = _active_attached_child("edge-source")

    with pytest.raises(RutterStateError, match="duplicate attachment authority"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_explicit_call_reusing_another_state_entrance() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"][0]["node_entry_id"] = "entry-root"

    with pytest.raises(RutterStateError, match="entrance state"):
        _decode_reckoning(_bytes(mapping))


def test_decode_rejects_completed_explicit_call_with_conflicting_state() -> None:
    mapping = _valid_mapping()
    completed = mapping["completed_runs"]
    assert isinstance(completed, dict)
    completed["child-run"]["history"].insert(
        0,
        _call(
            "call-grand",
            "grand-run",
            evolution_entry_id="entry-child-done",
            site_id="delegate",
        ),
    )
    completed["grand-run"] = {
        "run_id": "grand-run",
        "rutter_id": "child",
        "definition_version": 1,
        "charter": {},
        "history": [
            {
                "record_id": "done-grand",
                "node_entry_id": "entry-grand-done",
                "state_id": "complete",
                "result": {"outcome": "completed", "value": {}},
            }
        ],
    }

    with pytest.raises(RutterStateError, match="entrance state"):
        _decode_reckoning(_bytes(mapping))


def test_decode_allows_explicit_call_sharing_its_state_entrance() -> None:
    mapping = _valid_mapping()
    root = mapping["root"]
    assert isinstance(root, dict)
    root["history"] = [
        _turn("turn-review"),
        _call(
            "call-child",
            "child-run",
            evolution_entry_id="entry-root",
            site_id="review",
        ),
    ]

    decoded = _decode_reckoning(_bytes(mapping))

    assert len(decoded.root.history) == 2


@pytest.mark.parametrize(
    "candidate",
    (
        Path("../escape.reckoning"),
        Path("nested/../../escape.reckoning"),
        Path("/tmp/x.reckoning"),
        Path("."),
    ),
)
def test_confined_path_rejects_out_of_root_aliases(
    tmp_path: Path, candidate: Path
) -> None:
    with pytest.raises(RutterDefinitionError, match="relative path"):
        _confined_reckoning_path(tmp_path, candidate)


def test_confined_path_requires_named_reckoning_json_suffix(tmp_path: Path) -> None:
    with pytest.raises(RutterDefinitionError, match=r"\.reckoning\.json"):
        _confined_reckoning_path(tmp_path, Path("paper.json"))


def _store(tmp_path: Path) -> ReckoningStore:
    root = tmp_path / "reckonings"
    path = _confined_reckoning_path(root, Path("jobs/paper.reckoning.json"))
    return ReckoningStore(path)


def test_store_create_read_and_lock_modes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    reckoning = _example_reckoning()

    store.create(reckoning)

    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    assert store.read() == reckoning
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        lock = target.with_name(target.name + ".lock")
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_store_create_collision_preserves_first_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _example_reckoning()
    store.create(first)
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    before = target.read_bytes()

    with pytest.raises(RutterStateError, match="already exists"):
        store.create(_example_reckoning(global_revision=1))

    assert target.read_bytes() == before
    assert store.read() == first


def test_store_rejects_symlink_parent(tmp_path: Path) -> None:
    root = tmp_path / "reckonings"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "jobs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RutterStateError, match="symbolic link"):
        _store(tmp_path).create(_example_reckoning())

    assert not (outside / "paper.reckoning.json").exists()


def test_store_rejects_symlink_lock(tmp_path: Path) -> None:
    root = tmp_path / "reckonings/jobs"
    root.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("safe", encoding="utf-8")
    (root / "paper.reckoning.json.lock").symlink_to(victim)

    with pytest.raises(AtomicWriteError, match="symbolic link"):
        with _store(tmp_path).transaction():
            pass

    assert victim.read_text(encoding="utf-8") == "safe"


def test_store_rejects_symlink_and_nonregular_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.reckoning.json"
    outside.write_bytes(_canonical_reckoning_bytes(_example_reckoning()))
    target.symlink_to(outside)

    with pytest.raises(RutterStateError, match="cannot read"):
        store.read()
    assert outside.exists()

    target.unlink()
    target.mkdir()
    with pytest.raises(RutterStateError, match="cannot read"):
        store.read()


def test_replace_requires_live_locked_predecessor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    previous = _example_reckoning()
    replacement = _example_reckoning(global_revision=1)
    store.create(previous)

    with pytest.raises(RutterStateError, match="active transaction"):
        store.replace(previous, replacement)

    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    target.write_text(
        json.dumps(json.loads(target.read_bytes()), indent=2) + "\n",
        encoding="utf-8",
    )
    before = target.read_bytes()
    with store.transaction():
        with pytest.raises(RutterStateError, match="changed"):
            store.replace(previous, replacement)
    assert target.read_bytes() == before


def test_atomic_replace_reopens_as_one_complete_reckoning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    previous = _example_reckoning()
    replacement = _example_reckoning(global_revision=1, evolution_id="complete")
    store.create(previous)

    with store.transaction() as loaded:
        assert loaded == previous
        store.replace(loaded, replacement)

    assert store.read() == replacement
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    assert target.read_bytes() == _canonical_reckoning_bytes(replacement)


def test_identity_save_preserves_canonical_fixture_bytes(tmp_path: Path) -> None:
    """A no-advance reopen/save must not rewrite canonical v3 authority bytes."""

    reckoning_path = tmp_path / "reckonings/jobs/paper.reckoning.json"
    reckoning_path.parent.mkdir(parents=True)
    reckoning_path.write_bytes(
        b'{"active_effect":null,"completed_runs":{},"fault":null,'
        b'"global_revision":0,"root":{"active_child":null,'
        b'"charter":{"artifact":"draft.md"},"definition_version":1,'
        b'"entered_node":{"entry_id":"entry-root","state_id":"review"},'
        b'"history":[],"run_id":"root-run","rutter_id":"example"},'
        b'"storage_version":3}\n'
    )
    before = reckoning_path.read_bytes()

    store = ReckoningStore(reckoning_path)
    with store.transaction() as current:
        store.replace(current, current)

    assert reckoning_path.read_bytes() == before


def test_two_store_instances_cannot_publish_one_predecessor_twice(
    tmp_path: Path,
) -> None:
    first = _store(tmp_path)
    second = _store(tmp_path)
    previous = _example_reckoning()
    winner = _example_reckoning(global_revision=1, evolution_id="winner")
    loser = _example_reckoning(global_revision=1, evolution_id="loser")
    first.create(previous)

    with first.transaction():
        first.replace(previous, winner)
    with second.transaction():
        with pytest.raises(RutterStateError, match="changed"):
            second.replace(previous, loser)

    assert first.read() == winner


def test_concurrent_same_predecessor_publishes_exactly_one_successor(
    tmp_path: Path,
) -> None:
    first = _store(tmp_path)
    second = _store(tmp_path)
    previous = _example_reckoning()
    successors = (
        _example_reckoning(global_revision=1, evolution_id="winner"),
        _example_reckoning(global_revision=1, evolution_id="loser"),
    )
    first.create(previous)
    start = Barrier(3)
    outcomes: list[str] = []

    def publish(store: ReckoningStore, successor: Reckoning) -> None:
        start.wait()
        try:
            with store.transaction():
                store.replace(previous, successor)
        except RutterStateError as error:
            assert "changed" in str(error)
            outcomes.append("lost")
        else:
            outcomes.append("published")

    threads = (
        Thread(target=publish, args=(first, successors[0])),
        Thread(target=publish, args=(second, successors[1])),
    )
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=1)

    assert sorted(outcomes) == ["lost", "published"]
    assert first.read() in successors


def test_transactions_serialize_real_store_instances(tmp_path: Path) -> None:
    first = _store(tmp_path)
    second = _store(tmp_path)
    first.create(_example_reckoning())
    attempted = Event()
    entered = Event()

    def enter_second() -> None:
        attempted.set()
        with second.transaction():
            entered.set()

    with first.transaction():
        thread = Thread(target=enter_second)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not entered.wait(timeout=0.1)
    thread.join(timeout=1)

    assert entered.is_set()


def test_failed_replacement_preserves_exact_predecessor_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    previous = _example_reckoning()
    replacement = _example_reckoning(global_revision=1)
    store.create(previous)
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    before = target.read_bytes()

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise AtomicWriteError("injected replacement failure")

    monkeypatch.setattr(
        storage_module.atomic_files, "atomic_replace_bytes", fail_replace
    )
    with store.transaction():
        with pytest.raises(RutterStateError, match="reopen and inspect"):
            store.replace(previous, replacement)

    assert target.read_bytes() == before


def test_late_durability_error_requires_reopen_to_resolve_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    previous = _example_reckoning()
    replacement = _example_reckoning(global_revision=1)
    store.create(previous)
    real_replace = storage_module.atomic_files.atomic_replace_bytes

    def replace_then_fail(*args: object, **kwargs: object) -> None:
        real_replace(*args, **kwargs)
        raise AtomicWriteError("injected late durability failure")

    monkeypatch.setattr(
        storage_module.atomic_files, "atomic_replace_bytes", replace_then_fail
    )
    with store.transaction():
        with pytest.raises(RutterStateError, match="reopen and inspect"):
            store.replace(previous, replacement)

    assert store.read() == replacement

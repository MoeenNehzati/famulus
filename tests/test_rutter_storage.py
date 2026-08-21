"""Exercise strict canonical Reckoning persistence and confinement."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from threading import Barrier, Event, Thread

import pytest

from officina.rutter.model import (
    Reckoning,
    RutterDefinitionError,
    RutterStateError,
)
from officina.rutter.storage import (
    _ReckoningStore,
    _canonical_reckoning_bytes,
    _confined_reckoning_path,
    _decode_reckoning,
    _json_value,
    _reckoning_from_mapping,
    _reckoning_to_mapping,
)
import officina.rutter.storage as storage_module
from officina.common.atomic_files import AtomicWriteError
from test_support.rutter_fixtures import example_reckoning


_CANONICAL = (
    b'{"charter":{"data":{"artifact":"draft.md","options":["careful"]},'
    b'"definition_version":1,"rutter_id":"example"},"fix":{'
    b'"current_state_id":"review","diagnostics":[],"effect":null,'
    b'"lifecycle":"active","revision":0},"storage_version":1}\n'
)


def _literal_mapping() -> dict[str, object]:
    """Return an independently decoded editable copy of the expected record."""

    value = json.loads(_CANONICAL)
    assert isinstance(value, dict)
    return value


def test_codec_round_trips_one_stable_compact_utf8_record() -> None:
    """A changed field map, ordering, spacing, or newline breaks canonical bytes."""

    reckoning = example_reckoning()

    assert _reckoning_from_mapping(_reckoning_to_mapping(reckoning)) == reckoning
    assert _decode_reckoning(_CANONICAL) == reckoning
    assert _canonical_reckoning_bytes(reckoning) == _CANONICAL
    assert _CANONICAL.endswith(b"\n")
    assert _CANONICAL.count(b"\n") == 1
    assert b", " not in _CANONICAL and b": " not in _CANONICAL


def test_codec_round_trips_private_recovery_and_diagnostic_records() -> None:
    """Dropping or reflecting either private nested record breaks authority."""

    mapping = _literal_mapping()
    fix = mapping["fix"]
    assert isinstance(fix, dict)
    fix["effect"] = {
        "state_id": "review",
        "revision": 0,
        "disposition": "uncertain",
        "repeat_safe": False,
    }
    fix["diagnostics"] = [
        {
            "path": "effect",
            "code": "uncertain_effect",
            "message": "external completion is unknown",
        }
    ]

    reckoning = _reckoning_from_mapping(mapping)
    encoded = _canonical_reckoning_bytes(reckoning)

    assert _reckoning_to_mapping(reckoning) == mapping
    assert _decode_reckoning(encoded) == reckoning


@pytest.mark.parametrize(
    ("level", "missing_key"),
    (
        ("root", "storage_version"),
        ("charter", "rutter_id"),
        ("fix", "lifecycle"),
        ("effect", "disposition"),
        ("diagnostic", "code"),
    ),
)
def test_codec_rejects_missing_and_unknown_keys_at_every_record_level(
    level: str,
    missing_key: str,
) -> None:
    """Every explicit record rejects schema drift instead of ignoring it."""

    mapping = _literal_mapping()
    fix = mapping["fix"]
    assert isinstance(fix, dict)
    fix["effect"] = {
        "state_id": "review",
        "revision": 0,
        "disposition": "planned",
        "repeat_safe": True,
    }
    fix["diagnostics"] = [
        {"path": "outcome", "code": "invalid", "message": "invalid outcome"}
    ]
    records = {
        "root": mapping,
        "charter": mapping["charter"],
        "fix": fix,
        "effect": fix["effect"],
        "diagnostic": fix["diagnostics"][0],
    }
    record = records[level]
    assert isinstance(record, dict)
    missing = json.loads(json.dumps(mapping))
    missing_record = {
        "root": missing,
        "charter": missing["charter"],
        "fix": missing["fix"],
        "effect": missing["fix"]["effect"],
        "diagnostic": missing["fix"]["diagnostics"][0],
    }[level]
    del missing_record[missing_key]

    with pytest.raises(RutterStateError, match="fields are invalid"):
        _reckoning_from_mapping(missing)

    record["unknown"] = True
    with pytest.raises(RutterStateError, match="fields are invalid"):
        _reckoning_from_mapping(mapping)


@pytest.mark.parametrize(
    "data",
    (
        b'{"storage_version":1,"storage_version":1}\n',
        b'{"storage_version":NaN}\n',
        b'{"storage_version":Infinity}\n',
        b"\xff\n",
        b"{not json}\n",
        b"[]\n",
    ),
)
def test_codec_rejects_duplicate_nonfinite_and_corrupt_json(data: bytes) -> None:
    """Malformed input cannot produce a partial or permissively decoded value."""

    with pytest.raises(RutterStateError):
        _decode_reckoning(data)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_encoder_rejects_nonfinite_values_at_its_own_boundary(value: float) -> None:
    """The codec remains strict even if an in-memory model was compromised."""

    with pytest.raises(RutterStateError, match="non-finite"):
        _json_value(value, label="test value")


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("storage_version",), True),
        (("storage_version",), 0),
        (("storage_version",), 2),
        (("charter", "definition_version"), True),
        (("fix", "revision"), True),
        (("fix", "effect", "revision"), True),
    ),
)
def test_codec_rejects_boolean_integers_and_wrong_versions(
    path: tuple[str, ...],
    value: object,
) -> None:
    """Boolean integer aliases and all unsupported versions fail closed."""

    mapping = _literal_mapping()
    fix = mapping["fix"]
    assert isinstance(fix, dict)
    fix["effect"] = {
        "state_id": "review",
        "revision": 0,
        "disposition": "planned",
        "repeat_safe": True,
    }
    target = mapping
    for component in path[:-1]:
        nested = target[component]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    with pytest.raises(RutterStateError):
        _reckoning_from_mapping(mapping)


def test_codec_runs_final_semantic_validator_after_structural_validation() -> None:
    """A structurally valid but definition-mismatched Reckoning is rejected."""

    seen = []

    def reject_identity(reckoning: object) -> None:
        seen.append(reckoning)
        raise RutterStateError("Fix and Charter do not match the definition")

    with pytest.raises(RutterStateError, match="Fix and Charter"):
        _decode_reckoning(_CANONICAL, semantic_validator=reject_identity)

    assert seen == [example_reckoning()]


@pytest.mark.parametrize(
    "candidate",
    (
        Path("../escape.reckoning"),
        Path("nested/../../escape.reckoning"),
        Path("/tmp/escape.reckoning"),
        Path("."),
    ),
)
def test_confined_path_rejects_absolute_and_out_of_root_aliases(
    tmp_path: Path,
    candidate: Path,
) -> None:
    """Callers cannot alias a Reckoning outside its configured root."""

    with pytest.raises(RutterDefinitionError, match="relative path"):
        _confined_reckoning_path(tmp_path, candidate)


def test_confined_path_returns_an_absolute_lexical_descendant(tmp_path: Path) -> None:
    """A valid relative name is bound beneath exactly one configured root."""

    assert _confined_reckoning_path(
        tmp_path, Path("jobs/paper.reckoning.json")
    ) == (
        tmp_path.absolute() / "jobs" / "paper.reckoning.json"
    )


@pytest.mark.parametrize(
    "candidate",
    (
        Path("jobs/paper.json"),
        Path("jobs/paper.reckoning.json.lock"),
        Path("jobs/.reckoning.json"),
    ),
)
def test_reckoning_paths_require_a_named_reckoning_json_suffix(
    tmp_path: Path,
    candidate: Path,
) -> None:
    """Only named ``*.reckoning.json`` files can hold durable authority."""

    with pytest.raises(RutterDefinitionError, match=r"\.reckoning\.json"):
        _confined_reckoning_path(tmp_path, candidate)


def test_reckoning_lock_appends_lock_to_the_complete_filename(
    tmp_path: Path,
) -> None:
    """The lock is ``<reckoning-path>.lock`` and cannot itself be authority."""

    root = tmp_path / "reckonings"
    paper = _ReckoningStore(
        _confined_reckoning_path(root, Path("jobs/paper.reckoning.json"))
    )
    paper.create(example_reckoning())
    assert paper.read() == example_reckoning()
    lock = root / "jobs/paper.reckoning.json.lock"
    lock_inode = lock.stat().st_ino

    with pytest.raises(RutterDefinitionError, match=r"\.reckoning\.json"):
        _confined_reckoning_path(root, Path("jobs/paper.reckoning.json.lock"))
    with pytest.raises(RutterDefinitionError, match=r"\.reckoning\.json"):
        _ReckoningStore(lock.absolute())

    assert lock.stat().st_ino == lock_inode
    assert paper.read() == example_reckoning()


def _store(tmp_path: Path, name: str = "jobs/paper.reckoning.json") -> _ReckoningStore:
    """Return one store bound through the public confinement seam."""

    root = tmp_path / "reckonings"
    return _ReckoningStore(_confined_reckoning_path(root, Path(name)))


def test_store_creates_reads_and_modes_one_canonical_reckoning(
    tmp_path: Path,
) -> None:
    """Create must not omit the Charter, relax mode, or alter canonical bytes."""

    store = _store(tmp_path)
    reckoning = example_reckoning()

    store.create(reckoning)

    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    assert store.read() == reckoning
    assert target.read_bytes() == _CANONICAL
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_store_create_collision_preserves_the_first_reckoning(tmp_path: Path) -> None:
    """Create-only construction can never replace existing authority."""

    store = _store(tmp_path)
    first = example_reckoning()
    store.create(first)
    before = (tmp_path / "reckonings/jobs/paper.reckoning.json").read_bytes()

    with pytest.raises(RutterStateError, match="already exists"):
        store.create(example_reckoning(state_id="complete", revision=1))

    assert (tmp_path / "reckonings/jobs/paper.reckoning.json").read_bytes() == before
    assert store.read() == first


def test_store_rejects_missing_symlink_and_non_regular_reckonings(
    tmp_path: Path,
) -> None:
    """Reads accept only the configured no-follow regular file."""

    store = _store(tmp_path)
    with pytest.raises(RutterStateError, match="cannot read"):
        store.read()

    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.reckoning.json"
    outside.write_bytes(_CANONICAL)
    target.symlink_to(outside)
    with pytest.raises(RutterStateError, match="cannot read"):
        store.read()
    assert outside.read_bytes() == _CANONICAL

    target.unlink()
    target.mkdir()
    with pytest.raises(RutterStateError, match="cannot read"):
        store.read()


def test_missing_root_cannot_become_an_unlocked_successful_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A creator racing the missing-root check cannot supply unlocked authority."""

    store = _store(tmp_path)
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    unlocked_reads = []

    def create_during_unlocked_read() -> bytes:
        unlocked_reads.append(1)
        target.parent.mkdir(parents=True)
        target.write_bytes(_CANONICAL)
        return _CANONICAL

    monkeypatch.setattr(store, "_read_bytes", create_during_unlocked_read)

    with pytest.raises(RutterStateError, match="cannot read"):
        store.read()

    assert unlocked_reads == []
    assert not target.exists()


def test_store_rejects_symlinked_parent_and_lock_without_writing_outside(
    tmp_path: Path,
) -> None:
    """Neither parent creation nor the sibling lock may follow a symlink."""

    root = tmp_path / "reckonings"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "jobs").symlink_to(outside, target_is_directory=True)
    store = _store(tmp_path)

    with pytest.raises(RutterStateError, match="symbolic link"):
        store.create(example_reckoning())
    assert not (outside / "paper.reckoning.json").exists()

    (root / "jobs").unlink()
    (root / "jobs").mkdir()
    lock = root / "jobs/paper.reckoning.json.lock"
    victim = outside / "lock"
    victim.write_text("safe", encoding="utf-8")
    lock.symlink_to(victim)
    with pytest.raises(AtomicWriteError, match="symbolic link|reparse point"):
        with store.transaction():
            pass
    assert victim.read_text(encoding="utf-8") == "safe"


def test_store_semantic_validation_rejects_read_create_and_replace(
    tmp_path: Path,
) -> None:
    """Definition mismatches fail before new authority can be published."""

    def require_review(reckoning: object) -> None:
        assert hasattr(reckoning, "fix")
        if reckoning.fix.current_state_id != "review":
            raise RutterStateError("Fix and Charter identity mismatch")

    store = _ReckoningStore(
        _confined_reckoning_path(tmp_path / "root", Path("paper.reckoning.json")),
        semantic_validator=require_review,
    )
    invalid = example_reckoning(state_id="complete", revision=1)
    with pytest.raises(RutterStateError, match="identity mismatch"):
        store.create(invalid)
    assert not (tmp_path / "root/paper.reckoning.json").exists()

    valid = example_reckoning()
    store.create(valid)
    before = (tmp_path / "root/paper.reckoning.json").read_bytes()
    with store.transaction():
        with pytest.raises(RutterStateError, match="identity mismatch"):
            store.replace(valid, invalid)
    assert (tmp_path / "root/paper.reckoning.json").read_bytes() == before

    (tmp_path / "root/paper.reckoning.json").write_bytes(
        _canonical_reckoning_bytes(invalid)
    )
    with pytest.raises(RutterStateError, match="identity mismatch"):
        store.read()


def test_replace_requires_the_exact_canonical_predecessor_bytes(
    tmp_path: Path,
) -> None:
    """Equivalent but externally reformatted JSON is not the predecessor token."""

    store = _store(tmp_path)
    previous = example_reckoning()
    replacement = example_reckoning(state_id="complete", revision=1)
    store.create(previous)
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    target.write_text(json.dumps(_literal_mapping()) + "\n", encoding="utf-8")
    before = target.read_bytes()

    with store.transaction():
        with pytest.raises(RutterStateError, match="changed"):
            store.replace(previous, replacement)

    assert target.read_bytes() == before


def test_two_store_instances_cannot_overwrite_one_predecessor(
    tmp_path: Path,
) -> None:
    """The losing serialized caller detects that disk authority changed."""

    first = _store(tmp_path)
    second = _store(tmp_path)
    previous = example_reckoning()
    winner = example_reckoning(state_id="complete", revision=1)
    loser = example_reckoning(state_id="revision-required", revision=1)
    first.create(previous)

    with first.transaction():
        assert first.read() == previous
        first.replace(previous, winner)
    with second.transaction():
        with pytest.raises(RutterStateError, match="changed"):
            second.replace(previous, loser)

    assert first.read() == winner


def test_replace_requires_transaction_ownership(tmp_path: Path) -> None:
    """A direct caller cannot race after comparing an unlocked predecessor."""

    store = _store(tmp_path)
    previous = example_reckoning()
    replacement = example_reckoning(state_id="complete", revision=1)
    store.create(previous)

    with pytest.raises(RutterStateError, match="active transaction"):
        store.replace(previous, replacement)

    assert store.read() == previous


def test_concurrent_same_predecessor_publishes_exactly_one_successor(
    tmp_path: Path,
) -> None:
    """Two real callers cannot both commit from one predecessor authority."""

    first = _store(tmp_path)
    second = _store(tmp_path)
    previous = example_reckoning()
    replacements = (
        example_reckoning(state_id="complete", revision=1),
        example_reckoning(state_id="revision-required", revision=1),
    )
    first.create(previous)
    start = Barrier(3)
    outcomes = []

    def publish(store: _ReckoningStore, replacement: Reckoning) -> None:
        start.wait()
        try:
            with store.transaction():
                store.replace(previous, replacement)
        except RutterStateError as exc:
            assert "changed" in str(exc)
            outcomes.append("lost")
        else:
            outcomes.append("published")

    threads = (
        Thread(target=publish, args=(first, replacements[0])),
        Thread(target=publish, args=(second, replacements[1])),
    )
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=1)

    assert sorted(outcomes) == ["lost", "published"]
    assert first.read() in replacements


def test_transaction_serializes_real_concurrent_store_instances(
    tmp_path: Path,
) -> None:
    """A second OS-sidecar caller cannot enter while the first holds the lock."""

    first = _store(tmp_path)
    second = _store(tmp_path)
    first.create(example_reckoning())
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


def test_transaction_yields_the_authoritative_reckoning_loaded_under_lock(
    tmp_path: Path,
) -> None:
    """Task 3 must consume a reload taken only after lock acquisition."""

    store = _store(tmp_path)
    authoritative = example_reckoning(state_id="complete", revision=3)
    store.create(authoritative)

    with store.transaction() as loaded:
        assert loaded == authoritative

    lock = tmp_path / "reckonings/jobs/paper.reckoning.json.lock"
    if os.name == "posix":
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_direct_reader_observes_no_intermediate_pure_fix(tmp_path: Path) -> None:
    """Even direct reads wait for the writer's complete locked publication."""

    writer = _store(tmp_path)
    reader = _store(tmp_path)
    predecessor = example_reckoning()
    final = example_reckoning(state_id="complete", revision=2)
    writer.create(predecessor)
    writer_entered = Event()
    allow_commit = Event()
    observed = []

    def write_final() -> None:
        with writer.transaction():
            writer_entered.set()
            assert allow_commit.wait(timeout=1)
            writer.replace(predecessor, final)

    def read_after_writer() -> None:
        observed.append(reader.read())

    writer_thread = Thread(target=write_final)
    writer_thread.start()
    assert writer_entered.wait(timeout=1)
    reader_thread = Thread(target=read_after_writer)
    reader_thread.start()
    assert not observed
    allow_commit.set()
    writer_thread.join(timeout=1)
    reader_thread.join(timeout=1)

    assert observed == [final]


def test_failed_replacement_preserves_exact_predecessor_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure before publication cannot expose computed successor authority."""

    store = _store(tmp_path)
    previous = example_reckoning()
    replacement = example_reckoning(state_id="complete", revision=1)
    store.create(previous)
    target = tmp_path / "reckonings/jobs/paper.reckoning.json"
    before = target.read_bytes()

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise AtomicWriteError("injected replacement failure")

    monkeypatch.setattr(
        storage_module.atomic_files,
        "atomic_replace_bytes",
        fail_replace,
    )
    with store.transaction():
        with pytest.raises(RutterStateError, match="reopen and inspect"):
            store.replace(previous, replacement)

    assert target.read_bytes() == before


def test_late_durability_error_requires_reopen_to_resolve_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-publication error diagnoses uncertainty while retaining whole bytes."""

    store = _store(tmp_path)
    previous = example_reckoning()
    replacement = example_reckoning(state_id="complete", revision=1)
    store.create(previous)
    real_replace = storage_module.atomic_files.atomic_replace_bytes

    def replace_then_fail(*args: object, **kwargs: object) -> None:
        real_replace(*args, **kwargs)
        raise AtomicWriteError("injected late durability failure")

    monkeypatch.setattr(
        storage_module.atomic_files,
        "atomic_replace_bytes",
        replace_then_fail,
    )
    with store.transaction():
        with pytest.raises(RutterStateError, match="reopen and inspect"):
            store.replace(previous, replacement)

    assert store.read() == replacement

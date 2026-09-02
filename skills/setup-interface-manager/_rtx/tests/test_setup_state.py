from __future__ import annotations

import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from officina.common import atomic_files
from officina.runtime.python_machine_interface import logical_python_package_name
from officina.runtime.python_machine_interface_runner import load_interface


SCRIPT_DIR = Path(__file__).resolve().parents[1]
LOGICAL_PACKAGE = logical_python_package_name("setup-interface-manager._rtx")
previous_cwd = Path.cwd()
try:
    os.chdir(SCRIPT_DIR)
    _status_interface = load_interface(
        "_setup_manager.py",
        "StatusInterface",
        logical_package=LOGICAL_PACKAGE,
        logical_entrypoint=f"{LOGICAL_PACKAGE}._setup_manager",
    )
finally:
    os.chdir(previous_cwd)
_manager_globals = _status_interface.__class__.run.__globals__
state = SimpleNamespace(**_manager_globals["LedgerStore"].__init__.__globals__)


def _store(path: Path, files: state.AtomicFileAdapter) -> state.LedgerStore:
    return state.LedgerStore._from_atomic_files(path, files)


class CommonAtomicFiles:
    """Task-4-shaped adapter around the registered common atomic-file API."""

    def __init__(self) -> None:
        self.compare_calls: list[tuple[bytes | None, int | None, bytes]] = []

    def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None:
        try:
            atomic_files.ensure_private_directory(path.parent, allowed_root=allowed_root)
        except atomic_files.AtomicWriteError as exc:
            raise state.LedgerPathError(str(exc)) from exc

    @contextmanager
    def exclusive_file_lock(self, path: Path, *, allowed_root: Path, mode: int):
        try:
            with atomic_files.exclusive_file_lock(
                path, allowed_root=allowed_root, mode=mode
            ) as lock:
                yield lock
        except atomic_files.AtomicWriteError as exc:
            raise state.LedgerPathError(str(exc)) from exc

    def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes:
        try:
            return atomic_files.read_regular_file_bytes(path, allowed_root=allowed_root)
        except atomic_files.AtomicWriteError as exc:
            raise state.LedgerPathError(str(exc)) from exc

    def atomic_compare_and_replace_bytes(
        self,
        path: Path,
        data: bytes,
        *,
        expected_previous_bytes: bytes | None,
        expected_previous_mode: int | None,
        mode: int,
        allowed_root: Path,
    ) -> None:
        self.compare_calls.append((expected_previous_bytes, expected_previous_mode, data))
        try:
            atomic_files.atomic_compare_and_replace_bytes(
                path,
                data,
                expected_previous_bytes=expected_previous_bytes,
                expected_previous_mode=expected_previous_mode,
                allowed_root=allowed_root,
                mode=mode,
            )
        except atomic_files.AtomicWriteError as exc:
            if "compare predecessor mismatch" in str(exc) or "destination changed" in str(exc):
                raise state.LedgerConflict(str(exc)) from exc
            raise state.LedgerPathError(str(exc)) from exc


def _receipt(version: int = 1, *roots: str) -> state.SetupReceipt:
    return state.SetupReceipt(version=version, required_by=frozenset(roots))


def _flow(flow_id: str = "flow-1") -> state.ActiveFlow:
    return state.ActiveFlow(
        flow_id=flow_id,
        operation="setup",
        root="root.interface.setup",
        current_step="leaf.interface.setup",
        verified_steps=("leaf.interface.setup",),
        continuation=state.ContinuationIdentity(
            caller="mcp", interface="root.interface.run", version=1
        ),
    )


def test_ledger_store_public_constructor_accepts_only_the_getter_path(tmp_path: Path) -> None:
    """Catches reintroducing an adapter or caller-supplied capability argument."""
    path = tmp_path / "private" / "state" / "ledger.json"
    files = CommonAtomicFiles()

    with pytest.raises(TypeError):
        state.LedgerStore(path, files)
    assert _store(path, files).read() == state.SetupLedger.empty()


def test_read_creates_missing_parent_and_ledger_with_restrictive_modes(tmp_path: Path) -> None:
    """Catches creation without the promised confined private modes."""
    path = tmp_path / "private" / "setup" / "ledger.json"
    files = CommonAtomicFiles()

    ledger = _store(path, files).read()

    assert ledger == state.SetupLedger.empty()
    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert files.compare_calls == [(None, None, state.encode_ledger(ledger))]


def test_read_rejects_existing_final_parent_without_mode_0700(tmp_path: Path) -> None:
    """Catches accepting a ledger directory other local users may traverse."""
    private = tmp_path / "private"
    atomic_files.ensure_private_directory(private, allowed_root=tmp_path)
    parent = private / "state"
    parent.mkdir()
    if sys.platform == "win32":
        result = subprocess.run(
            ["icacls", str(parent), "/grant", "*S-1-1-0:F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            # famulus-skip: category=platform-contract; reason=ACL mutation is unavailable on this Windows host; alternate=the native restrictive-parent positive contract runs separately
            pytest.skip(f"Windows ACL mutation unavailable: {result.stderr}")
    else:
        os.chmod(parent, 0o755)

    with pytest.raises(
        state.LedgerPathError, match="mode|restrictive native ACL"
    ):
        _store(parent / "ledger.json", CommonAtomicFiles()).read()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":3,"interfaces":{},"active_flow":null}\n',
        b'{"active_flow":null,"interfaces":{},"schema_version":true}\n',
        b'{"schema_version":1,"interfaces":{},"active_flow":null,"extra":true}\n',
        b'{"schema_version":1,"interfaces":{"leaf.interface.setup":{"version":1,"required_by":["root","root"]}},"active_flow":null}\n',
        b'{"schema_version":1,"interfaces":{},"active_flow":{"flow_id":"f","operation":"setup","root":"r","current_step":"s","verified_steps":[],"continuation":{"caller":"c","interface":"i","version":1},"arguments":{}}}\n',
    ],
)
def test_parse_rejects_malformed_or_unsupported_ledger(raw: bytes) -> None:
    """Catches accepting data the manager cannot safely interpret."""
    with pytest.raises(state.LedgerFormatError):
        state.parse_ledger(raw)


def test_encoding_is_deterministic_and_strictly_required() -> None:
    """Catches a change which lets equivalent but noncanonical bytes bypass CAS."""
    ledger = state.SetupLedger(
        interfaces={
            "root.interface.setup": _receipt(2, "z.root", "a.root"),
            "leaf.interface.setup": _receipt(),
        },
        active_flow=None,
    )
    encoded = state.encode_ledger(ledger)

    assert encoded == (
        b'{"active_flow":null,"interfaces":{"leaf.interface.setup":{"required_by":[],"version":1},'
        b'"root.interface.setup":{"required_by":["a.root","z.root"],"version":2}},'
        b'"schema_version":2}\n'
    )
    assert state.parse_ledger(encoded) == ledger
    with pytest.raises(state.LedgerFormatError, match="canonical"):
        state.parse_ledger(encoded.replace(b'"active_flow":null', b'"active_flow": null'))


def test_canonical_v1_is_read_without_rewriting_and_migrates_on_mutation() -> None:
    """Catches rejecting old ledgers or retaining v1 after their first mutation."""
    raw = b'{"active_flow":{"continuation":{"caller":"mcp","interface":"root.interface.run","version":1},"current_step":"leaf.interface.setup","flow_id":"flow-1","operation":"setup","root":"root.interface.setup","verified_steps":[]},"interfaces":{"leaf.interface.setup":{"required_by":[],"version":1}},"schema_version":1}\n'
    ledger = state.parse_ledger(raw)

    assert state.encode_ledger(ledger) == raw
    migrated = state.claim_receipts(ledger, "root.interface.setup", ("leaf.interface.setup",))
    assert b'"schema_version":2' in state.encode_ledger(migrated) and migrated.active_flow == ledger.active_flow


def test_v2_round_trips_ordinary_and_global_flows() -> None:
    """Catches losing operation-dependent nullable fields in schema v2."""
    ordinary = state.SetupLedger(interfaces={}, active_flow=_flow())
    global_ = state.SetupLedger(
        interfaces={"leaf.interface.setup": _receipt()},
        active_flow=state.ActiveFlow(
            "global", "teardown-all", None, "leaf.interface.setup", (), None
        ),
    )

    assert state.parse_ledger(state.encode_ledger(ordinary)) == ordinary
    assert state.parse_ledger(state.encode_ledger(global_)) == global_


def test_flow_context_is_operation_dependent() -> None:
    """Catches global context/history or missing ordinary context."""
    continuation = state.ContinuationIdentity("c", "i", 1)
    for operation, root, history, resume in (
        ("teardown-all", "root.interface.setup", (), None),
        ("teardown-all", None, (), continuation),
        ("teardown-all", None, ("done.interface.setup",), None),
        ("setup", None, (), continuation), ("teardown", "root.interface.setup", (), None),
    ):
        with pytest.raises(state.LedgerFormatError):
            state.ActiveFlow("f", operation, root, "leaf.interface.setup", history, resume)


@pytest.mark.parametrize("target_kind", ["symlink", "directory"])
def test_read_rejects_non_regular_ledger_target(tmp_path: Path, target_kind: str) -> None:
    """Catches following a link or treating another object as manager state."""
    parent = tmp_path / "private" / "state"
    atomic_files.ensure_private_directory(parent, allowed_root=tmp_path)
    path = parent / "ledger.json"
    if target_kind == "symlink":
        path.symlink_to(tmp_path / "elsewhere.json")
    else:
        path.mkdir()

    with pytest.raises(state.LedgerPathError):
        _store(path, CommonAtomicFiles()).read()


def test_read_rejects_symlinked_or_non_directory_parent_component(tmp_path: Path) -> None:
    """Catches escaping the getter-selected containment path through a parent."""
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(state.LedgerPathError):
        _store(linked / "ledger.json", CommonAtomicFiles()).read()

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("not a directory")
    with pytest.raises(state.LedgerPathError):
        _store(blocked / "ledger.json", CommonAtomicFiles()).read()


def test_read_rechecks_a_parent_created_during_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a symlink race between checking a missing parent and making it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    raced_parent = tmp_path / "raced"
    path = raced_parent / "ledger.json"
    original_mkdir = os.mkdir

    def race_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        if name == "raced":
            os.symlink(outside, name, dir_fd=dir_fd)
            raise FileExistsError
        original_mkdir(name, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", race_mkdir)

    with pytest.raises(state.LedgerPathError):
        _store(path, CommonAtomicFiles()).read()
    assert not (outside / "ledger.json").exists()


@pytest.mark.parametrize("swap", ["parent", "target"])
def test_adapter_fails_closed_when_a_validated_path_is_swapped_before_use(
    tmp_path: Path, swap: str
) -> None:
    """Catches a path swap redirecting a later read or CAS outside the ledger tree."""
    private = tmp_path / "private" / "state"
    path = private / "ledger.json"
    seed = _store(path, CommonAtomicFiles())
    previous = seed.read()
    next_ = state.SetupLedger(
        interfaces={"leaf.interface.setup": _receipt()}, active_flow=None
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "ledger.json"
    outside_target.write_bytes(b"outside authority")

    class SwappingFiles(CommonAtomicFiles):
        def __init__(self) -> None:
            super().__init__()
            self.swapped = False

        def read_regular_file_bytes(self, inspected: Path, *, allowed_root: Path) -> bytes:
            if not self.swapped and swap == "target":
                self.swapped = True
                path.rename(tmp_path / "held-ledger.json")
                path.symlink_to(outside_target)
            return super().read_regular_file_bytes(inspected, allowed_root=allowed_root)

        def exclusive_file_lock(
            self, inspected: Path, *, allowed_root: Path, mode: int
        ):
            if not self.swapped and swap == "parent":
                self.swapped = True
                private.rename(tmp_path / "held-private")
                private.symlink_to(outside, target_is_directory=True)
            return super().exclusive_file_lock(
                inspected, allowed_root=allowed_root, mode=mode
            )

    with pytest.raises(state.LedgerPathError):
        _store(path, SwappingFiles()).compare_and_update(previous, next_)
    assert outside_target.read_bytes() == b"outside authority"


def test_compare_and_update_requires_the_exact_predecessor_bytes(tmp_path: Path) -> None:
    """Catches a stale writer replacing state after another writer won."""
    path = tmp_path / "private" / "state" / "ledger.json"
    files = CommonAtomicFiles()
    store = _store(path, files)
    empty = store.read()
    previous = state.SetupLedger(
        interfaces={"leaf.interface.setup": _receipt()}, active_flow=None
    )
    store.compare_and_update(empty, previous)
    next_ = state.claim_receipts(
        previous, "root.interface.setup", ("leaf.interface.setup",)
    )

    store.compare_and_update(previous, next_)

    assert files.compare_calls[-1] == (
        state.encode_ledger(previous),
        0o600,
        state.encode_ledger(next_),
    )
    with pytest.raises(state.LedgerConflict):
        store.compare_and_update(previous, next_)


def test_update_recovers_after_a_late_noncooperating_clobber(tmp_path: Path) -> None:
    """Catches claiming success when a writer clobbers bytes after publication."""
    path = tmp_path / "private" / "state" / "ledger.json"
    baseline = _store(path, CommonAtomicFiles())
    previous = baseline.read()
    intended = state.SetupLedger(
        interfaces={"leaf.interface.setup": _receipt()}, active_flow=None
    )
    concurrent = state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt(),
            "orphan.interface.setup": _receipt(9, "other.root"),
        },
        active_flow=None,
    )

    class LateClobberFiles(CommonAtomicFiles):
        def __init__(self) -> None:
            super().__init__()
            self.clobbered = False

        def atomic_compare_and_replace_bytes(self, *args: object, **kwargs: object) -> None:
            super().atomic_compare_and_replace_bytes(*args, **kwargs)
            if not self.clobbered:
                self.clobbered = True
                atomic_files.atomic_replace_bytes(
                    args[0], state.encode_ledger(concurrent),
                    allowed_root=kwargs["allowed_root"], mode=0o600,
                )

    store = _store(path, LateClobberFiles())
    with pytest.raises(state.LedgerConflict, match="post-write"):
        store.compare_and_update(previous, intended)

    recovered = store.update(
        lambda ledger: state.claim_receipts(
            ledger, "root.interface.setup", ("leaf.interface.setup",)
        )
    )
    assert recovered.interfaces["orphan.interface.setup"] == _receipt(9, "other.root")
    assert recovered.interfaces["leaf.interface.setup"] == _receipt(1, "root.interface.setup")


def test_store_runs_with_a_portable_adapter_without_posix_capabilities(tmp_path: Path) -> None:
    """Catches `_state` reaching for POSIX descriptor-only APIs itself."""
    class PortableFiles:
        def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)

        def exclusive_file_lock(self, path: Path, *, allowed_root: Path, mode: int):
            from contextlib import nullcontext

            return nullcontext()

        def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes:
            return path.read_bytes()

        def atomic_compare_and_replace_bytes(self, path: Path, data: bytes, **kwargs: object) -> None:
            if kwargs["expected_previous_bytes"] is not None and path.read_bytes() != kwargs["expected_previous_bytes"]:
                raise state.LedgerConflict("changed")
            path.write_bytes(data)

    assert _store(tmp_path / "portable" / "ledger.json", PortableFiles()).read() == state.SetupLedger.empty()


def test_update_retries_a_compare_conflict_with_fresh_state(tmp_path: Path) -> None:
    """Catches losing an unrelated concurrent receipt while retrying a claim."""
    path = tmp_path / "private" / "state" / "ledger.json"

    class RacingFiles(CommonAtomicFiles):
        def __init__(self) -> None:
            super().__init__()
            self.raced = False
            self.armed = False

        def atomic_compare_and_replace_bytes(self, path: Path, data: bytes, **kwargs: object) -> None:
            if self.armed and kwargs["expected_previous_bytes"] is not None and not self.raced:
                self.raced = True
                concurrent = state.SetupLedger(
                    interfaces={
                        "leaf.interface.setup": _receipt(),
                        "orphan.interface.setup": _receipt(9, "other.root"),
                    },
                    active_flow=None,
                )
                CommonAtomicFiles().atomic_compare_and_replace_bytes(
                    path,
                    state.encode_ledger(concurrent),
                    expected_previous_bytes=kwargs["expected_previous_bytes"],
                    expected_previous_mode=kwargs["expected_previous_mode"],
                    mode=0o600,
                    allowed_root=kwargs["allowed_root"],
                )
                raise state.LedgerConflict("ledger predecessor changed")
            super().atomic_compare_and_replace_bytes(path, data, **kwargs)

    files = RacingFiles()
    store = _store(path, files)
    empty = store.read()
    store.compare_and_update(
        empty,
        state.SetupLedger(
            interfaces={"leaf.interface.setup": _receipt()}, active_flow=None
        ),
    )
    files.armed = True
    updated = store.update(
        lambda ledger: state.claim_receipts(
            ledger, "root.interface.setup", ("leaf.interface.setup",)
        )
    )

    assert updated.interfaces["leaf.interface.setup"] == _receipt(1, "root.interface.setup")
    assert updated.interfaces["orphan.interface.setup"] == _receipt(9, "other.root")


def test_claims_are_idempotent_and_preserve_structurally_valid_orphans() -> None:
    """Catches dropping unknown receipts or duplicating a root's shared claim."""
    ledger = state.SetupLedger(
        interfaces={
            "known.interface.setup": _receipt(1),
            "orphan.interface.setup": _receipt(9, "other.root"),
        },
        active_flow=None,
    )

    claimed = state.claim_receipts(
        ledger, "root.interface.setup", ("known.interface.setup",)
    )

    assert claimed.interfaces["known.interface.setup"] == _receipt(1, "root.interface.setup")
    assert claimed.interfaces["orphan.interface.setup"] == _receipt(9, "other.root")
    assert state.claim_receipts(
        claimed, "root.interface.setup", ("known.interface.setup",)
    ) == claimed


def test_begin_flow_allows_only_one_active_flow() -> None:
    """Catches duplicate managed actions running against the same ledger."""
    started = state.begin_flow(state.SetupLedger.empty(), _flow())

    assert started.active_flow == _flow()
    with pytest.raises(state.FlowConflict):
        state.begin_flow(started, _flow("flow-2"))
